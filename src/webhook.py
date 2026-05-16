"""
Webhook handler — recebe mensagens da API oficial da Meta (WhatsApp Business API).
Execute com: uvicorn src.webhook:app --host 0.0.0.0 --port 8002
"""

import asyncio
import hashlib
import hmac
import logging
import time
from fastapi import FastAPI, HTTPException, Request, Query

logging.basicConfig(level=logging.INFO)
from fastapi.responses import PlainTextResponse
from src.config import settings
from src.crew import run_atendimento
from src.sentiment import run_sentiment
from src.storage.store import (
    adquirir_lock_numero,
    append_historico,
    buscar_conversa_do_dia,
    buscar_sessao,
    liberar_lock_numero,
    merge_sessao,
    msg_ja_processada,
    renovar_ttl_sessao,
    salvar_conversa,
    salvar_mensagem,
    salvar_mensagem_sessao,
)

from src.horario import aviso_horario_se_fora
from src.monitor import router as monitor_router
from src.whatsapp import enviar_whatsapp

logger = logging.getLogger(__name__)
app = FastAPI(title="NinoAgent Webhook")
app.include_router(monitor_router, prefix="/monitor")

_tasks: dict[str, asyncio.Task] = {}

# Debounce adaptativo: timer curto para msg única, estende em rajadas. Mantém
# proteção contra responder no meio da rajada (cancel() durante sleep é confiável)
# e elimina os 30s fixos como gargalo de UX.
_BASE_DELAY = 5       # mensagem única: responde rápido
_EXTEND_DELAY = 6     # extensão por mensagem adicional na rajada
_MAX_DELAY = 18       # cap para não regredir ao bug do timer infinito


def _calcular_delay(numero: str) -> int:
    """Calcula delay baseado em quantas mensagens do cliente estão pendentes."""
    sessao = buscar_sessao(numero) or {}
    historico = sessao.get("historico", [])
    ultimo_idx = sessao.get("ultimo_processado_idx", 0)
    pendentes = sum(1 for m in historico[ultimo_idx:] if m.get("role") == "cliente")
    if pendentes <= 1:
        return _BASE_DELAY
    return min(_MAX_DELAY, _BASE_DELAY + _EXTEND_DELAY * (pendentes - 1))


def _garantir_conversa_registrada(numero: str):
    """Fallback: se o agente não registrou a conversa, faz pelo webhook."""
    from src.storage import store as st
    sessao = buscar_sessao(numero)
    if not sessao:
        return

    conversa_id = sessao.get("conversa_id")
    historico = sessao.get("historico", [])
    texto = " | ".join([f"{m['role']}: {m['text']}" for m in historico])

    if not conversa_id:
        # Reutiliza conversa já aberta hoje para este número
        conversa_id = buscar_conversa_do_dia(numero)
    if not conversa_id:
        conversa_id = salvar_conversa({
            "numero_whatsapp": numero,
            "tipo_cliente": sessao.get("tipo_cliente", "atacado"),
            "cliente_recorrente": sessao.get("cliente_recorrente", False),
            "origem": sessao.get("origem", "organico"),
            "status": "resolvido",
        })
        merge_sessao(numero, conversa_id=conversa_id)
        sessao["conversa_id"] = conversa_id

    # Reconcilia mensagens órfãs (gravadas antes do conversa_id existir).
    st.associar_mensagens_orfas(numero, conversa_id)

    if texto:
        st.indexar_conversa(
            conversa_id=conversa_id,
            texto=texto,
            metadata={
                "conversa_id": conversa_id,
                "numero_whatsapp": numero,
                "tipo_cliente": sessao.get("tipo_cliente", "atacado"),
                "origem": sessao.get("origem", "organico"),
            },
        )


async def _processar(numero: str, origem: str, delay: int):
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return  # nova mensagem chegou antes do timer, timer reiniciado

    _tasks.pop(numero, None)

    # Lock por número: previne execução concorrente quando a task anterior
    # já passou do sleep e o cancel() não consegue mais interromper (causa
    # documentada de respostas duplicadas).
    if not adquirir_lock_numero(numero, ttl=180):
        logger.info("Skip _processar(%s): execução já em curso", numero)
        return

    try:
        sessao = buscar_sessao(numero) or {}
        if sessao.get("modo") == "humano":
            return

        historico = sessao.get("historico", [])
        ultimo_idx = sessao.get("ultimo_processado_idx", 0)

        msgs_novas = [
            m["text"] for m in historico[ultimo_idx:]
            if m["role"] == "cliente"
        ]
        if not msgs_novas:
            return

        mensagem_consolidada = " ".join(msgs_novas)
        merge_sessao(numero, ultimo_processado_idx=len(historico))

        try:
            await run_atendimento(numero, mensagem_consolidada, origem)
            _garantir_conversa_registrada(numero)
            # Gate de custo: só roda sentiment se houve resposta do agente e
            # não estamos em modo humano. Em conversas só de áudio (humano
            # assumiu) ou cliente que sumiu antes da resposta, evita 1 call
            # Groq + 1 upsert Postgres sem ganho analítico.
            sessao_pos = buscar_sessao(numero) or {}
            tem_resposta_agente = any(
                m.get("role") == "agente" for m in sessao_pos.get("historico", [])
            )
            if tem_resposta_agente and sessao_pos.get("modo") != "humano":
                await run_sentiment(numero)
        except Exception:
            logger.exception("Erro no atendimento de %s", numero)
        finally:
            _garantir_conversa_registrada(numero)
    finally:
        liberar_lock_numero(numero)


@app.get("/webhook/whatsapp")
async def verificar_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Verificação do webhook pela Meta."""
    if hub_mode == "subscribe" and hub_verify_token and settings.WHATSAPP_VERIFY_TOKEN \
            and hmac.compare_digest(hub_verify_token, settings.WHATSAPP_VERIFY_TOKEN):
        return PlainTextResponse(hub_challenge)
    return PlainTextResponse("Forbidden", status_code=403)


def _normalizar_numero(numero: str) -> str:
    if len(numero) == 12 and numero.startswith("55"):
        return numero[:4] + "9" + numero[4:]
    return numero


def _assumir_automatico(numero: str) -> None:
    """Coloca a sessão em modo humano e cancela o task pendente do agente."""
    sessao = buscar_sessao(numero)
    if sessao and sessao.get("modo") != "humano":
        merge_sessao(numero, modo="humano")
    if numero in _tasks:
        _tasks[numero].cancel()
        _tasks.pop(numero, None)


def _validar_assinatura(body: bytes, header_sig: str | None) -> bool:
    """Valida X-Hub-Signature-256 (HMAC-SHA256 com WHATSAPP_APP_SECRET).

    Se WHATSAPP_APP_SECRET não estiver configurado, retorna True (modo permissivo
    para compatibilidade com o setup atual). Em produção, configure o secret.
    """
    secret = settings.WHATSAPP_APP_SECRET
    if not secret:
        return True
    if not header_sig or not header_sig.startswith("sha256="):
        return False
    esperado = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(esperado, header_sig.split("=", 1)[1])


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    raw_body = await request.body()
    if not _validar_assinatura(raw_body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=403, detail="Assinatura inválida")

    import json as _json
    try:
        payload = _json.loads(raw_body)
    except ValueError:
        return {"status": "ignored"}

    try:
        entry = payload["entry"][0]
        changes = entry["changes"][0]["value"]
        messages = changes.get("messages")
        if not messages:
            return {"status": "ignored"}

        msg = messages[0]
        msg_id = msg.get("id")
        msg_type = msg.get("type")
        numero = _normalizar_numero(msg["from"])
        origem = changes.get("contacts", [{}])[0].get("profile", {}).get("name", "organico")

        # Idempotência: a Meta reenvia a mesma mensagem se não receber 200 a tempo.
        # SET NX com TTL de 24h é suficiente — após esse prazo o ID é descartado pela Meta.
        if msg_ja_processada(msg_id):
            logger.info("Mensagem duplicada ignorada: id=%s", msg_id)
            return {"status": "duplicate"}

        # Ignora retries antigos: mensagens com timestamp > 5 minutos no passado
        msg_ts = int(msg.get("timestamp", 0))
        if msg_ts and (time.time() - msg_ts) > 300:
            logger.warning("Mensagem ignorada por timestamp antigo: %s de %s (ts=%s)", msg_type, numero, msg_ts)
            return {"status": "ignored_old_message"}
    except (KeyError, IndexError):
        return {"status": "ignored"}

    if msg_type == "text":
        mensagem = msg["text"]["body"]
        append_historico(numero, {"role": "cliente", "text": mensagem})
        renovar_ttl_sessao(numero)  # atividade real do cliente → estende sessão
        salvar_mensagem_sessao(numero, "cliente", text=mensagem, type="text")

        if numero in _tasks:
            _tasks[numero].cancel()
        delay = _calcular_delay(numero)
        _tasks[numero] = asyncio.create_task(_processar(numero, origem, delay))
        return {"status": "aguardando", "delay": delay}

    if msg_type in ("audio", "video", "image", "document"):
        # Extrai o media_id do campo específico do tipo
        media_info = msg.get(msg_type, {})
        media_id = media_info.get("id", "")
        labels = {"audio": "[áudio 🎵]", "video": "[vídeo 🎬]", "image": "[imagem 🖼️]", "document": "[arquivo 📎]"}
        avisos = {
            "audio": "Recebi seu áudio 🎵 Vou ouvir e te respondo em instantes.",
            "video": "Recebi seu vídeo 🎬 Vou conferir e te respondo em instantes.",
            "image": "Recebi sua imagem 🖼️ Vou conferir e te respondo em instantes.",
            "document": "Recebi seu arquivo 📎 Vou conferir e te respondo em instantes.",
        }
        texto_exibicao = labels.get(msg_type, f"[{msg_type}]")

        append_historico(numero, {"role": "cliente", "type": msg_type, "media_id": media_id, "text": texto_exibicao})
        renovar_ttl_sessao(numero)  # atividade real do cliente → estende sessão
        salvar_mensagem_sessao(numero, "cliente", text=texto_exibicao, type=msg_type, media_id=media_id)

        # Mídia → assume humano e responde com aviso. O agente não processa
        # áudio/vídeo/imagem/documento; sem aviso, o cliente fica em silêncio
        # até alguém abrir o monitor. Resolve B1 (mídia ignorada) e H4.
        # H3: se a escalada cai fora do horário de atendimento humano, anexa
        # informação para o cliente entender que vai esperar até o expediente.
        _assumir_automatico(numero)
        aviso = avisos.get(msg_type, "Recebi sua mensagem! Em instantes te respondo.")
        aviso = aviso + aviso_horario_se_fora()
        enviar_whatsapp(numero, aviso)
        append_historico(numero, {"role": "agente", "text": aviso})
        salvar_mensagem_sessao(numero, "agente", text=aviso, type="text")
        logger.info("Conversa %s assumida automaticamente por %s recebido", numero, msg_type)
        return {"status": "assumido_automatico", "type": msg_type}

    return {"status": "ignored"}


@app.get("/health")
def health():
    """Health check ativo: verifica Redis e Postgres antes de declarar OK."""
    from fastapi.responses import JSONResponse
    from src.storage.store import pg_cursor, redis_conn
    body = {"status": "ok", "redis": "ok", "postgres": "ok"}
    try:
        redis_conn().ping()
    except Exception as e:
        body["redis"] = f"erro: {e}"
        body["status"] = "degraded"
    try:
        with pg_cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as e:
        body["postgres"] = f"erro: {e}"
        body["status"] = "degraded"
    return JSONResponse(body, status_code=200 if body["status"] == "ok" else 503)
