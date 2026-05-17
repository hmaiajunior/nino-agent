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


async def _enviar_qualificacao_botoes(numero: str) -> None:
    # Não usamos profile_name aqui: pode ser apelido, marca ou qualquer texto
    # que o cliente colocou no perfil do WhatsApp. Cumprimentamos sem nome.
    texto = (
        "Olá! Seja bem-vindo(a) à PlayBeKids 👶🏻👕 "
        "Sou a Bia. Pra te direcionar certinho:"
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
    # "Recorrente" = tem conversa em DIA anterior (sinal real de relacionamento).
    # Não usamos `cliente is not None` porque o H1 cria registro em `clientes`
    # automaticamente com o profile_name do WhatsApp na 1ª mensagem.
    recorrente = store.tem_conversa_anterior(numero_whatsapp)

    # H5: mensagem vaga ("oi", "olá"…) → envia botões. Evita chamada Groq e
    # classifica sem ambiguidade. Aplicamos a clientes NOVOS (sem conversa
    # anterior). Recorrentes seguem pelo caminho LLM para saudação contextual.
    if not recorrente and _eh_mensagem_vaga(mensagem):
        await _enviar_qualificacao_botoes(numero_whatsapp)
        store.merge_sessao(
            numero_whatsapp,
            aguardando_qualificacao=True,
            origem=origem,
            cliente_recorrente=False,
        )
        return {"aguardando_botao": True}

    if recorrente and cliente and cliente.get("tipo"):
        # Cliente com conversa anterior E tipo já classificado → saudação contextual
        resposta = await _saudacao_cliente_conhecido(
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

    await enviar_whatsapp_async(numero_whatsapp, resposta)
    store.append_historico(numero_whatsapp, {"role": "agente", "text": resposta})
    store.merge_sessao(numero_whatsapp, **contexto)
    store.salvar_mensagem_sessao(numero_whatsapp, "agente", text=resposta, type="text")
    return contexto


async def _saudacao_cliente_conhecido(tipo: str, mensagem: str, trace=None) -> str:
    tipo_label = "lojista" if tipo == "atacado" else "consumidor"
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Cliente já classificado como {tipo_label} (teve conversa em dia anterior) "
                f"enviou: '{mensagem}'. Cumprimente de forma breve e direta, sem se "
                "reapresentar nem se apresentar de novo. "
                "IMPORTANTE: NÃO use nome próprio do cliente — você não confirmou "
                "como ele se chama. NÃO use expressões como 'novamente', 'de novo', "
                "'da última vez', 'que bom ter você de volta'."
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


async def _saudacao_novo_cliente(mensagem: str, trace=None) -> tuple[str, str | None]:
    """Retorna (resposta_para_enviar, tipo_detectado | None)."""
    messages = [
        {"role": "system", "content": _SYSTEM},
        {
            "role": "user",
            "content": (
                f"Novo cliente enviou: '{mensagem}'. "
                "Envie uma saudação de boas-vindas à PlayBeKids. "
                "Se a mensagem deixar claro se é lojista ou consumidor final, registre o tipo. "
                "Se o tipo for indefinido, pergunte diretamente se é lojista ou consumidor final. "
                "IMPORTANTE: NÃO use nome próprio do cliente — o cliente não se apresentou nesta "
                "conversa. Você só pode chamar pelo nome se ele tiver dito o nome no texto."
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
