"""Utilitário de envio de mensagens via Meta WhatsApp Business API."""

import httpx
from src.config import settings


def enviar_whatsapp(numero: str, texto: str) -> None:
    to = numero if numero.startswith("+") else f"+{numero}"
    url = f"https://graph.facebook.com/v19.0/{settings.WHATSAPP_PHONE_NUMBER_ID}/messages"
    httpx.post(
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
