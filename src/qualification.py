"""Qualificação determinística do cliente — sem LLM.

Fluxo:
1. Cliente recorrente (tem conversa em dia anterior, com tipo conhecido) →
   pula qualificação. Wholesale entra direto, aproveitando o tipo herdado.
2. Cliente novo COM sinal de campanha (referral CTWA, keywords no anúncio
   ou na 1ª msg) → tipo inferido + vai direto pro Wholesale.
3. Cliente novo SEM sinal claro → enviar_botoes para classificação manual.

Não há mais chamada LLM aqui; o caminho LLM da qualification (Llama 8B via
Groq) era origem de saudações ruins e errantes ("Boa-feira!") sem ganho
proporcional. A saudação contextual fica por conta do Wholesale (Gemini
Flash), que já tem prompt e contexto melhores para isso.
"""

import re

from src.config import settings
from src.storage import store
from src.whatsapp import enviar_botoes


# ============================================================================
# Detecção de mensagem vaga (caminho dos botões)
# ============================================================================

# Parts vagas reconhecidas. Mensagens compostas só por estas + pontuação são
# tratadas como "sem intenção comercial clara" e vão para os botões.
_PARTES_VAGAS = re.compile(
    r"\b("
    r"oi+|olá+|ola+|hello+|hi+|hey+|"
    r"bom\s+dia|boa\s+tarde|boa\s+noite|"
    r"td\s*b[oe]m|tudo\s+bem|tudo\s+bom|tudo\s+ok|"
    r"como\s+vai|como\s+est[aá]"
    r")\b",
    re.IGNORECASE,
)
_RESIDUO_PERMITIDO = re.compile(r"^[\s,.!?]*$")


def _eh_mensagem_vaga(mensagem: str) -> bool:
    """True se a mensagem só contém saudações reconhecidas + pontuação."""
    if not mensagem or not mensagem.strip():
        return False
    sem_vagas = _PARTES_VAGAS.sub("", mensagem)
    return bool(_RESIDUO_PERMITIDO.match(sem_vagas))


# ============================================================================
# Inferência de tipo via campanha
# ============================================================================

_RE_ATACADO = re.compile(
    r"\b(atacado|lojista|lojistas|revend\w*|loja\s+de\s+roupa|"
    r"quero\s+revender|para\s+revenda)\b",
    re.IGNORECASE,
)
_RE_VAREJO = re.compile(
    r"\b(uso\s+pr[óo]prio|pessoa\s+f[ií]sica|consumidor\s+final|"
    r"meu\s+filho|minha\s+filha|para\s+mim|para\s+(o\s+)?meu\s+filho)\b",
    re.IGNORECASE,
)


def _ids_configurados(spec: str) -> set[str]:
    return {s.strip() for s in (spec or "").split(",") if s.strip()}


def _inferir_tipo_campanha(referral: dict, mensagem: str) -> str | None:
    """Tenta deduzir 'atacado' ou 'varejo' do contexto da chegada do cliente.

    Camadas (mais confiável → mais permissiva):
    1. source_id do referral bate com lista de IDs configurada
    2. headline/body do anúncio contém keyword forte
    3. própria mensagem do cliente tem keyword forte
    Retorna None se não há sinal claro — cliente vai para botões.
    """
    source_id = (referral or {}).get("source_id", "")
    if source_id:
        if source_id in _ids_configurados(settings.CAMPANHAS_ATACADO_IDS):
            return "atacado"
        if source_id in _ids_configurados(settings.CAMPANHAS_VAREJO_IDS):
            return "varejo"

    texto = " ".join([
        (referral or {}).get("headline", "") or "",
        (referral or {}).get("body", "") or "",
        mensagem or "",
    ])

    # Prioriza varejo quando ambos batem (ex: "uso próprio, sem revenda") —
    # consumidor final é o caso mais "estreito"; lojista normalmente é claro.
    if _RE_VAREJO.search(texto):
        return "varejo"
    if _RE_ATACADO.search(texto):
        return "atacado"
    return None


# ============================================================================
# Botões
# ============================================================================

async def _enviar_qualificacao_botoes(numero: str) -> None:
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
    store.append_historico(numero, {
        "role": "agente",
        "text": texto + " [botões: Sou lojista | Compra própria]",
    })
    store.salvar_mensagem_sessao(numero, "agente", text=texto, type="text")


# ============================================================================
# Orquestração
# ============================================================================

async def run_qualification(numero_whatsapp: str, mensagem: str, origem: str) -> dict:
    """Qualifica o cliente sem chamar LLM. Decide entre 3 caminhos."""
    sessao = store.buscar_sessao(numero_whatsapp) or {}
    referral = sessao.get("referral_inicial") or {}

    cliente = store.buscar_cliente(numero_whatsapp)
    recorrente = store.tem_conversa_anterior(numero_whatsapp)

    # 1) Recorrente com tipo conhecido → Wholesale herda o tipo
    if recorrente and cliente and cliente.get("tipo"):
        store.merge_sessao(
            numero_whatsapp,
            tipo_cliente=cliente["tipo"],
            cliente_recorrente=True,
            origem=origem,
        )
        return {"tipo_cliente": cliente["tipo"], "direct_to_wholesale": True}

    # 2) Sinal de campanha (CTWA + keywords) → tipo inferido direto
    tipo_inferido = _inferir_tipo_campanha(referral, mensagem)
    if tipo_inferido:
        store.merge_sessao(
            numero_whatsapp,
            tipo_cliente=tipo_inferido,
            cliente_recorrente=recorrente,
            origem=origem,
        )
        return {"tipo_cliente": tipo_inferido, "direct_to_wholesale": True, "via": "campanha"}

    # 3) Sem sinal → botões (sempre, mesmo para msg vaga ou específica).
    #    Eliminar o LLM aqui evita "Boa-feira!" e similares.
    await _enviar_qualificacao_botoes(numero_whatsapp)
    store.merge_sessao(
        numero_whatsapp,
        aguardando_qualificacao=True,
        origem=origem,
        cliente_recorrente=False,
    )
    return {"aguardando_botao": True}
