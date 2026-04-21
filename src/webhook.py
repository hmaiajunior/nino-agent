"""
Webhook handler — recebe mensagens da Evolution API e aciona o crew de atendimento.
Execute com: uvicorn src.webhook:app --host 0.0.0.0 --port 8002
"""

import time
import threading
from fastapi import FastAPI, Request, BackgroundTasks
from src.crew import run_atendimento
from src.storage.store import buscar_sessao, salvar_sessao, salvar_conversa

app = FastAPI(title="NinoAgent Webhook")

# Controle de timers por número — aguarda 30s acumulando mensagens
_timers: dict[str, threading.Timer] = {}
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
        # Agente não registrou — salva com dados da sessão
        conversa_id = salvar_conversa({
            "numero_whatsapp": numero,
            "tipo_cliente": sessao.get("tipo_cliente", "desconhecido"),
            "cliente_recorrente": sessao.get("cliente_recorrente", False),
            "origem": sessao.get("origem", "organico"),
            "status": "resolvido",
        })
        sessao["conversa_id"] = conversa_id
        salvar_sessao(numero, sessao)

    # Sempre indexa/atualiza no Qdrant com o histórico completo
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


def _processar(numero: str, origem: str):
    _timers.pop(numero, None)
    sessao = buscar_sessao(numero) or {}
    historico = sessao.get("historico", [])
    ultimo_idx = sessao.get("ultimo_processado_idx", 0)

    # Pega apenas mensagens do cliente desde o último processamento
    msgs_novas = [
        m["text"] for m in historico[ultimo_idx:]
        if m["role"] == "cliente"
    ]
    if not msgs_novas:
        return

    mensagem_consolidada = " ".join(msgs_novas)

    # Atualiza índice antes de processar
    sessao["ultimo_processado_idx"] = len(historico)
    salvar_sessao(numero, sessao)

    run_atendimento(numero, mensagem_consolidada, origem)
    _garantir_conversa_registrada(numero)


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    payload = await request.json()

    event = payload.get("event", "")
    if event != "messages.upsert":
        return {"status": "ignored"}

    data = payload.get("data", {})
    mensagem = data.get("message", {}).get("conversation", "")
    numero = data.get("key", {}).get("remoteJid", "").replace("@s.whatsapp.net", "")
    from_me = data.get("key", {}).get("fromMe", False)

    if from_me or not mensagem or not numero:
        return {"status": "ignored"}

    origem = data.get("pushName", "organico")

    # Acumula mensagem no histórico
    _acumular_historico(numero, "cliente", mensagem)

    # Cancela timer anterior e reinicia — processa 30s após a última mensagem
    if numero in _timers:
        _timers[numero].cancel()

    timer = threading.Timer(_DELAY, _processar, args=[numero, origem])
    _timers[numero] = timer
    timer.start()

    return {"status": "aguardando"}


@app.get("/health")
def health():
    return {"status": "ok"}
