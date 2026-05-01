"""Utilitário de envio de mensagens via Meta WhatsApp Business API."""

import logging
import httpx
from src.config import settings

logger = logging.getLogger(__name__)


def enviar_whatsapp(numero: str, texto: str) -> None:
    to = numero if numero.startswith("+") else f"+{numero}"
    url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    try:
        r = httpx.post(
            url,
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": texto},
            },
            headers={
                "Authorization": f"Bearer {settings.WHATSAPP_TOKEN}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error("Falha ao enviar WhatsApp para %s: HTTP %s — %s", numero, e.response.status_code, e.response.text)
    except Exception as e:
        logger.error("Falha ao enviar WhatsApp para %s: %s", numero, e)
