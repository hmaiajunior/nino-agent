"""Análise de sentimento — chamada direta ao Groq (Llama), sem CrewAI."""

import json
import groq
from src.config import settings
from src.storage import store

_client = groq.AsyncGroq(api_key=settings.GROQ_API_KEY)

_SYSTEM = (
    "Analista de atendimento. Leia o histórico da conversa e classifique objetivamente. "
    "Em dúvida entre positivo e neutro, escolha neutro. "
    "Em dúvida entre neutro e negativo, escolha negativo."
)

_TOOL = {
    "type": "function",
    "function": {
        "name": "registrar_avaliacao",
        "description": "Avaliação estruturada da conversa de atendimento.",
        "parameters": {
            "type": "object",
            "properties": {
                "sentimento": {
                    "type": "string",
                    "enum": ["positivo", "neutro", "negativo"],
                },
                "score_atendimento": {"type": "integer", "minimum": 1, "maximum": 5},
                "tema_principal": {"type": "string"},
                "duvida_resolvida": {"type": "boolean"},
                "interesse_compra": {"type": "boolean"},
                "demanda_varejo": {"type": "boolean"},
                "observacoes": {"type": "string"},
            },
            "required": [
                "sentimento", "score_atendimento", "tema_principal",
                "duvida_resolvida", "interesse_compra", "demanda_varejo",
            ],
        },
    },
}


async def run_sentiment(numero_whatsapp: str) -> None:
    """Classifica sentimento da conversa e persiste avaliação no Postgres."""
    sessao = store.buscar_sessao(numero_whatsapp)
    if not sessao:
        return

    conversa_id = sessao.get("conversa_id")
    historico = sessao.get("historico", [])
    if not conversa_id or not historico:
        return

    historico_texto = "\n".join(
        f"[{m['role'].upper()}] {m['text']}" for m in historico
    )

    completion = await _client.chat.completions.create(
        model=settings.GROQ_MODEL,
        max_tokens=400,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": f"Histórico da conversa:\n\n{historico_texto}"},
        ],
        tools=[_TOOL],
        tool_choice={"type": "function", "function": {"name": "registrar_avaliacao"}},
    )

    dados = json.loads(completion.choices[0].message.tool_calls[0].function.arguments)

    store.salvar_avaliacao({
        "conversa_id": conversa_id,
        "sentimento": dados["sentimento"],
        "score_atendimento": dados["score_atendimento"],
        "tema_principal": str(dados["tema_principal"])[:50],
        "duvida_resolvida": dados["duvida_resolvida"],
        "interesse_compra": dados["interesse_compra"],
        "demanda_varejo": dados["demanda_varejo"],
        "observacoes": dados.get("observacoes"),
    })
