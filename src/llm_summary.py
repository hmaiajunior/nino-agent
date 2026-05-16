"""Resumo de histórico de conversa via Groq Llama (barato e rápido).

Usado por crew.py para janela deslizante: em vez de injetar o histórico
inteiro no prompt do Wholesale (que cresce linearmente com a conversa),
mantemos só as N mensagens recentes + um resumo cacheado das anteriores.
"""

import groq
from src.config import settings
from src.llm_retry import groq_retry

_client = groq.AsyncGroq(api_key=settings.GROQ_API_KEY)
_TIMEOUT = 15

_SYSTEM = (
    "Resuma o histórico de conversa abaixo em até 3 linhas, em português, "
    "focando em: o que o cliente quer, decisões já tomadas e informações já "
    "compartilhadas. Não repita saudações nem perguntas já respondidas."
)


@groq_retry
async def _completion(**kwargs):
    return await _client.chat.completions.create(timeout=_TIMEOUT, **kwargs)


async def resumir_historico(historico: list[dict]) -> str:
    """Recebe lista de mensagens (`role`, `text`) e devolve resumo curto em PT-BR."""
    if not historico:
        return ""
    linhas = "\n".join(
        f"[{m.get('role', '').upper()}] {m.get('text', '')}" for m in historico
    )
    completion = await _completion(
        model=settings.GROQ_MODEL,
        max_tokens=200,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": linhas},
        ],
    )
    return (completion.choices[0].message.content or "").strip()
