"""Utilitário de envio de mensagens via Meta WhatsApp Business API."""

import logging
import httpx
from src.config import settings

logger = logging.getLogger(__name__)


def _payload(numero: str, texto: str) -> tuple[str, dict, dict]:
    to = numero if numero.startswith("+") else f"+{numero}"
    url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": texto},
    }
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    return url, body, headers


def enviar_whatsapp(numero: str, texto: str) -> None:
    """Versão síncrona — para uso em handlers FastAPI sync e webhook callback."""
    url, body, headers = _payload(numero, texto)
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=10)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error("Falha ao enviar WhatsApp para %s: HTTP %s — %s", numero, e.response.status_code, e.response.text)
    except Exception as e:
        logger.error("Falha ao enviar WhatsApp para %s: %s", numero, e)


async def enviar_whatsapp_async(numero: str, texto: str) -> None:
    """Versão async — para uso em código async (qualification). Não bloqueia o event loop."""
    url, body, headers = _payload(numero, texto)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error("Falha ao enviar WhatsApp para %s: HTTP %s — %s", numero, e.response.status_code, e.response.text)
    except Exception as e:
        logger.error("Falha ao enviar WhatsApp para %s: %s", numero, e)


async def enviar_botoes(numero: str, texto: str, botoes: list[dict]) -> None:
    """Envia mensagem interativa com botões de resposta rápida (Meta API).

    botoes: lista de até 3 dicts no formato {"id": str, "title": str}. Cada
    `title` tem limite de 20 caracteres (regra da Meta). Quando o cliente
    toca um botão, o webhook recebe um evento `interactive.button_reply`
    com o `id` enviado aqui.
    """
    to = numero if numero.startswith("+") else f"+{numero}"
    url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    body = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": texto},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": b["id"], "title": b["title"]}}
                    for b in botoes
                ]
            },
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error("Falha enviar_botoes %s: HTTP %s — %s", numero, e.response.status_code, e.response.text)
    except Exception as e:
        logger.error("Falha enviar_botoes %s: %s", numero, e)


async def marcar_como_lida(msg_id: str, typing: bool = False) -> None:
    """Marca a msg do cliente como lida (✓✓ azul) e opcionalmente sinaliza 'digitando…'.

    A Meta combina os dois eventos no mesmo POST. O typing indicator é válido
    por ~25s; expira automaticamente se uma mensagem for enviada antes.
    Fire-and-forget — qualquer erro é apenas logado.
    """
    if not msg_id:
        return
    url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    body = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": msg_id,
    }
    if typing:
        body["typing_indicator"] = {"type": "text"}
    headers = {
        "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.post(url, json=body, headers=headers)
            r.raise_for_status()
    except Exception as e:
        # Não vale a pena ruidar o atendimento por falha em UX adornment
        logger.debug("Falha em marcar_como_lida(%s, typing=%s): %s", msg_id, typing, e)
