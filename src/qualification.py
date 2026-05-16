"""Qualificação de cliente — chamada direta ao Groq (Llama), sem CrewAI."""

import json
import re
import groq
from src.config import settings
from src.llm_retry import groq_retry
from src.observability import new_trace
from src.storage import store
from src.whatsapp import enviar_botoes, enviar_whatsapp_async


# Mensagens vagas demais para classificar: usamos botões em vez de gastar LLM
# e arriscar erro de classificação. Cobre saudações secas e "?".
_RE_MSG_VAGA = re.compile(
    r"^\s*(oi+|olá+|ola+|hello+|hi+|hey+|"
    r"bom\s+dia|boa\s+tarde|boa\s+noite|"
    r"\?+|\.+)\s*[!?.]*\s*$",
    re.IGNORECASE,
)


def _eh_mensagem_vaga(mensagem: str) -> bool:
    return bool(_RE_MSG_VAGA.match(mensagem or ""))


async def _enviar_qualificacao_botoes(numero: str, nome_perfil: str | None) -> None:
    saudacao_nome = f", {nome_perfil}" if nome_perfil else ""
    texto = (
        f"Olá{saudacao_nome}! Seja bem-vindo(a) à PlayBeKids 👶🏻👕 "
        f"Sou a Bia. Pra te direcionar certinho:"
    )
    await enviar_botoes(
        numero,
        texto,
        [
            {"id": "qual:atacado", "title": "Sou lojista"},
            {"id": "qual:varejo", "title": "Compra própria"},
        ],
    )
    # Registra a saudação no histórico para o monitor mostrar contexto
    store.append_historico(numero, {"role": "agente", "text": texto + " [botões: Sou lojista | Compra própria]"})
    store.salvar_mensagem_sessao(numero, "agente", text=texto, type="text")

_client = groq.AsyncGroq(api_key=settings.GROQ_API_KEY)
_LLM_TIMEOUT = 20  # segundos


@groq_retry
async def _completion(**kwargs):
    """Chamada Groq com retry + timeout — protege contra 5xx/timeout transitórios."""
    return await _client.chat.completions.create(timeout=_LLM_TIMEOUT, **kwargs)

_SYSTEM = (
    "Você é a Bia da PlayBeKids, loja de moda masculina infantil (0-12 anos). "
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
    sessao_atual = store.buscar_sessao(numero_whatsapp) or {}
    nome_perfil = sessao_atual.get("nome_perfil")

    # H5: cliente novo + mensagem vaga ("oi", "olá"…) → envia botões. Evita
    # 1 chamada Groq e classifica sem ambiguidade. Mensagens já específicas
    # ("quero atacado", "tem catálogo?") seguem pelo caminho LLM normal.
    if not cliente and _eh_mensagem_vaga(mensagem):
        await _enviar_qualificacao_botoes(numero_whatsapp, nome_perfil)
        store.merge_sessao(
            numero_whatsapp,
            aguardando_qualificacao=True,
            origem=origem,
            cliente_recorrente=False,
        )
        return {"aguardando_botao": True}

    if cliente:
        resposta = await _saudacao_cliente_conhecido(
            nome=cliente["nome"] or nome_perfil,
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
        resposta, tipo_detectado = await _saudacao_novo_cliente(
            mensagem, nome_perfil=nome_perfil, trace=trace,
        )
        contexto = {
            "tipo_cliente": tipo_detectado,
            "cliente_recorrente": False,
            "origem": origem,
        }

    await enviar_whatsapp_async(numero_whatsapp, resposta)
    store.append_historico(numero_whatsapp, {"role": "agente", "text": resposta})
    store.merge_sessao(numero_whatsapp, **contexto)
    store.salvar_mensagem_sessao(numero_whatsapp, "agente", text=resposta, type="text")
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
    completion = await _completion(
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


async def _saudacao_novo_cliente(mensagem: str, nome_perfil: str | None = None, trace=None) -> tuple[str, str | None]:
    """Retorna (resposta_para_enviar, tipo_detectado | None)."""
    instrucao_nome = (
        f"O nome do cliente no perfil WhatsApp é '{nome_perfil}' — use na saudação. "
        if nome_perfil else
        "Não use nome próprio na saudação (o cliente não se apresentou)."
    )
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Novo cliente enviou: '{mensagem}'. "
                f"{instrucao_nome}"
                "Se a mensagem deixar claro se é lojista ou consumidor final, registre o tipo. "
                "Envie uma saudação de boas-vindas à PlayBeKids e, se o tipo for indefinido, "
                "pergunte diretamente se é lojista ou consumidor final."
            ),
        },
    ]
    completion = await _completion(
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
