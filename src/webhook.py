"""
Webhook handler — recebe mensagens da API oficial da Meta (WhatsApp Business API).
Execute com: uvicorn src.webhook:app --host 0.0.0.0 --port 8002
"""

import asyncio
import logging
from fastapi import FastAPI, Request, Query

logging.basicConfig(level=logging.INFO)
from fastapi.responses import PlainTextResponse
from src.config import settings
from src.crew import run_atendimento
from src.sentiment import run_sentiment
from src.storage.store import buscar_sessao, salvar_sessao, salvar_conversa

from src.monitor import router as monitor_router

logger = logging.getLogger(__name__)
app = FastAPI(title="NinoAgent Webhook")
app.include_router(monitor_router, prefix="/monitor")

_tasks: dict[str, asyncio.Task] = {}
_DELAY = 30  # segundos


def _acumular_historico(numero: str, role: str, texto: str):
    sessao = buscar_sessao(numero) or {}
    historico = sessao.get("historico", [])
    historico.append({"role": role, "text": texto})
    sessao["historico"] = historico
    salvar_sessao(numero, sessao)


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
        conversa_id = salvar_conversa({
            "numero_whatsapp": numero,
            "tipo_cliente": sessao.get("tipo_cliente", "desconhecido"),
            "cliente_recorrente": sessao.get("cliente_recorrente", False),
            "origem": sessao.get("origem", "organico"),
            "status": "resolvido",
        })
        sessao["conversa_id"] = conversa_id
        salvar_sessao(numero, sessao)

    if texto:
        st.indexar_conversa(
            conversa_id=conversa_id,
            texto=texto,
            metadata={
                "conversa_id": conversa_id,
                "numero_whatsapp": numero,
                "tipo_cliente": sessao.get("tipo_cliente", "desconhecido"),
                "origem": sessao.get("origem", "organico"),
            },
        )


async def _processar(numero: str, origem: str):
    try:
        await asyncio.sleep(_DELAY)
    except asyncio.CancelledError:
        return  # nova mensagem chegou antes dos 30s, timer reiniciado

    _tasks.pop(numero, None)

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
    sessao["ultimo_processado_idx"] = len(historico)
    salvar_sessao(numero, sessao)

    try:
        await run_atendimento(numero, mensagem_consolidada, origem)
        # Garante conversa_id antes do sentiment — mesmo que o agente não tenha
        # chamado registrar_conversa (ex.: falha de ferramenta ou primeira sessão).
        _garantir_conversa_registrada(numero)
        await run_sentiment(numero)
    except Exception:
        logger.exception("Erro no atendimento de %s", numero)
    finally:
        _garantir_conversa_registrada(numero)


@app.get("/webhook/whatsapp")
async def verificar_webhook(
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
):
    """Verificação do webhook pela Meta."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WHATSAPP_VERIFY_TOKEN:
        return PlainTextResponse(hub_challenge)
    return PlainTextResponse("Forbidden", status_code=403)


def _normalizar_numero(numero: str) -> str:
    if len(numero) == 12 and numero.startswith("55"):
        return numero[:4] + "9" + numero[4:]
    return numero


def _assumir_automatico(numero: str) -> None:
    """Coloca a sessão em modo humano e cancela o task pendente do agente."""
    sessao = buscar_sessao(numero) or {}
    if sessao.get("modo") != "humano":
        sessao["modo"] = "humano"
        salvar_sessao(numero, sessao)
    if numero in _tasks:
        _tasks[numero].cancel()
        _tasks.pop(numero, None)


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request):
    payload = await request.json()

    try:
        entry = payload["entry"][0]
        changes = entry["changes"][0]["value"]
        messages = changes.get("messages")
        if not messages:
            return {"status": "ignored"}

        msg = messages[0]
        msg_type = msg.get("type")
        numero = _normalizar_numero(msg["from"])
        origem = changes.get("contacts", [{}])[0].get("profile", {}).get("name", "organico")
    except (KeyError, IndexError):
        return {"status": "ignored"}

    if msg_type == "text":
        mensagem = msg["text"]["body"]
        _acumular_historico(numero, "cliente", mensagem)

        if numero in _tasks:
            _tasks[numero].cancel()
        _tasks[numero] = asyncio.create_task(_processar(numero, origem))
        return {"status": "aguardando"}

    if msg_type in ("audio", "video", "image", "document"):
        # Extrai o media_id do campo específico do tipo
        media_info = msg.get(msg_type, {})
        media_id = media_info.get("id", "")
        labels = {"audio": "[áudio 🎵]", "video": "[vídeo 🎬]", "image": "[imagem 🖼️]", "document": "[arquivo 📎]"}
        texto_exibicao = labels.get(msg_type, f"[{msg_type}]")

        sessao = buscar_sessao(numero) or {}
        historico = sessao.get("historico", [])
        historico.append({"role": "cliente", "type": msg_type, "media_id": media_id, "text": texto_exibicao})
        sessao["historico"] = historico
        salvar_sessao(numero, sessao)

        # Áudio → assume automaticamente (agente não processa áudio)
        if msg_type == "audio":
            _assumir_automatico(numero)
            logger.info("Conversa %s assumida automaticamente por áudio recebido", numero)
            return {"status": "assumido_automatico"}

        return {"status": "midia_registrada"}

    return {"status": "ignored"}


@app.get("/health")
def health():
    return {"status": "ok"}
