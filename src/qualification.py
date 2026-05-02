"""Qualificação de cliente — chamada direta ao Groq (Llama), sem CrewAI."""

import json
import groq
from src.config import settings
from src.observability import new_trace
from src.storage import store
from src.whatsapp import enviar_whatsapp

_client = groq.AsyncGroq(api_key=settings.GROQ_API_KEY)

_SYSTEM = (
    "Você é a Ana da PlayBeKids, loja de moda masculina infantil (0-12 anos). "
    "Responda de forma curta e natural, como uma atendente simpática no WhatsApp. "
    "Nunca mencione preços, produtos ou condições. Máximo 2 linhas por mensagem."
)

_TOOL_NOVO = {
    "type": "function",
    "function": {
        "name": "qualificar",
        "description": "Retorna a saudação a enviar e o tipo de cliente inferido da mensagem.",
        "parameters": {
            "type": "object",
            "properties": {
                "resposta": {
                    "type": "string",
                    "description": "Mensagem de boas-vindas a enviar ao cliente pelo WhatsApp",
                },
                "tipo_detectado": {
                    "type": "string",
                    "enum": ["atacado", "varejo", "indefinido"],
                    "description": "Tipo de cliente inferido da primeira mensagem; 'indefinido' se não for possível determinar",
                },
            },
            "required": ["resposta", "tipo_detectado"],
        },
    },
}


async def run_qualification(numero_whatsapp: str, mensagem: str, origem: str) -> dict:
    """Qualifica cliente, envia saudação e salva contexto no Redis."""
    trace = new_trace("qualification", user_id=numero_whatsapp, session_id=numero_whatsapp)
    cliente = store.buscar_cliente(numero_whatsapp)

    if cliente:
        resposta = await _saudacao_cliente_conhecido(
            nome=cliente["nome"],
            tipo=cliente["tipo"],
            mensagem=mensagem,
            trace=trace,
        )
        contexto = {
            "tipo_cliente": cliente["tipo"],
            "cliente_recorrente": True,
            "origem": origem,
        }
    else:
        resposta, tipo_detectado = await _saudacao_novo_cliente(mensagem, trace=trace)
        contexto = {
            "tipo_cliente": tipo_detectado,
            "cliente_recorrente": False,
            "origem": origem,
        }

    enviar_whatsapp(numero_whatsapp, resposta)
    historico = [{"role": "agente", "text": resposta}]
    store.salvar_sessao(numero_whatsapp, {**contexto, "historico": historico})
    return contexto


async def _saudacao_cliente_conhecido(nome: str, tipo: str, mensagem: str, trace=None) -> str:
    tipo_label = "lojista" if tipo == "atacado" else "cliente"
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Cliente recorrente chamado {nome} ({tipo_label}) enviou: '{mensagem}'. "
                "Cumprimente pelo nome de forma breve e calorosa, sem se apresentar novamente."
            ),
        },
    ]
    completion = await _client.chat.completions.create(
        model=settings.GROQ_MODEL,
        max_tokens=150,
        messages=messages,
    )
    output = completion.choices[0].message.content
    if trace is not None:
        trace.generation(
            name="saudacao_cliente_conhecido",
            model=settings.GROQ_MODEL,
            input=messages,
            output=output,
            usage={
                "input": completion.usage.prompt_tokens,
                "output": completion.usage.completion_tokens,
            },
        )
    return output


async def _saudacao_novo_cliente(mensagem: str, trace=None) -> tuple[str, str | None]:
    """Retorna (resposta_para_enviar, tipo_detectado | None)."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Novo cliente enviou: '{mensagem}'. "
                "Se a mensagem deixar claro se é lojista ou consumidor final, registre o tipo. "
                "Envie uma saudação de boas-vindas à PlayBeKids e, se o tipo for indefinido, "
                "pergunte diretamente se é lojista ou consumidor final."
            ),
        },
    ]
    completion = await _client.chat.completions.create(
        model=settings.GROQ_MODEL,
        max_tokens=200,
        messages=messages,
        tools=[_TOOL_NOVO],
        tool_choice={"type": "function", "function": {"name": "qualificar"}},
    )
    args = json.loads(completion.choices[0].message.tool_calls[0].function.arguments)
    if trace is not None:
        trace.generation(
            name="saudacao_novo_cliente",
            model=settings.GROQ_MODEL,
            input=messages,
            output=args,
            usage={
                "input": completion.usage.prompt_tokens,
                "output": completion.usage.completion_tokens,
            },
        )
    tipo_raw = args["tipo_detectado"]
    return args["resposta"], (tipo_raw if tipo_raw != "indefinido" else None)
