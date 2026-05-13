"""Retry helpers para chamadas a provedores de LLM.

Cobre erros transitórios (5xx, timeout, rate limit, conexão) com backoff exponencial.
Não cobre erros de programação (BadRequest, Auth) — esses devem falhar rápido.
"""

import logging
import groq
import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

# Erros que valem retry. Tudo o que é transitório no upstream + conexão.
_GROQ_TRANSIENT = (
    groq.APIConnectionError,
    groq.APITimeoutError,
    groq.InternalServerError,
    groq.RateLimitError,
)
_HTTP_TRANSIENT = (
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadError,
    httpx.RemoteProtocolError,
)


groq_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception_type(_GROQ_TRANSIENT + _HTTP_TRANSIENT),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
"""Decorador para chamadas Groq async. Uso: `@groq_retry async def _call(...)`."""
