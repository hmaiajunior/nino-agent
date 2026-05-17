"""Orquestração principal do NinoAgent."""

import uuid
from datetime import date
from crewai import Crew, Task, Process

import src.observability  # noqa: F401 — ativa litellm.success_callback para LangFuse
from src.agents.agents import build_wholesale_agent, build_insight_agent
from src.config import settings
from src.llm_summary import resumir_historico
from src.qualification import run_qualification
from src.storage.store import buscar_sessao, merge_sessao


# Janela deslizante: últimas N msgs no prompt; resumo cacheado das anteriores.
# Reduz custo de tokens em conversas longas sem perder contexto histórico.
_JANELA_RECENTES = 12   # nº de msgs recentes mantidas literais no prompt
_LIMITE_RESUMO = 16     # acima disso, ativa o esquema com resumo
_TRIGGER_REGERAR = 8    # regenera resumo quando histórico cresceu N msgs além do ponto resumido


async def _resumo_de_conversas_antigas(numero: str, sessao: dict) -> str:
    """H6 — Resumo de interações anteriores (sessões já encerradas).

    Cliente que volta após dias deve perceber que somos contínuos. Persistido
    em `contexto_anterior` da sessão: regerado só uma vez por sessão Redis.
    """
    if "contexto_anterior" in sessao:
        return sessao["contexto_anterior"] or ""
    from src.storage.store import buscar_mensagens
    total = buscar_mensagens(numero, limite=200)
    n_atual = len(sessao.get("historico", []))
    n_antigas = max(0, len(total) - n_atual)
    antigas = total[:n_antigas]
    if len(antigas) >= 4:
        antigas_fmt = [{"role": m["role"], "text": m.get("text", "")} for m in antigas if m.get("text")]
        resumo = await resumir_historico(antigas_fmt) if antigas_fmt else ""
    else:
        resumo = ""
    merge_sessao(numero, contexto_anterior=resumo)
    return resumo


async def _montar_contexto_historico(numero: str, msgs_anteriores: list[dict], sessao: dict) -> str:
    """Constrói o trecho de histórico para o prompt — H6 (conversas anteriores) + H9 (janela)."""
    partes: list[str] = []

    # H6 — conversas anteriores (de antes da sessão atual)
    contexto_anterior = await _resumo_de_conversas_antigas(numero, sessao)
    if contexto_anterior:
        partes.append(
            "ESTE CLIENTE JÁ CONVERSOU COM VOCÊ ANTES. "
            "Resumo das interações passadas: " + contexto_anterior +
            " NÃO recomece a apresentação — retome de onde parou."
        )

    # H9 — janela da sessão atual
    if msgs_anteriores:
        if len(msgs_anteriores) <= _LIMITE_RESUMO:
            linhas = "\n".join(f"[{m['role'].upper()}] {m['text']}" for m in msgs_anteriores)
            partes.append("HISTÓRICO ANTERIOR DA CONVERSA:\n" + linhas)
        else:
            resumo = sessao.get("resumo_historico")
            resumo_ate = sessao.get("resumo_ate_idx", 0)
            antigas = msgs_anteriores[:-_JANELA_RECENTES]
            precisa_regerar = (not resumo) or (len(antigas) - resumo_ate >= _TRIGGER_REGERAR)
            if precisa_regerar:
                resumo = await resumir_historico(antigas)
                merge_sessao(numero, resumo_historico=resumo, resumo_ate_idx=len(antigas))
            recentes = msgs_anteriores[-_JANELA_RECENTES:]
            linhas = "\n".join(f"[{m['role'].upper()}] {m['text']}" for m in recentes)
            partes.append("RESUMO DAS TROCAS ANTERIORES:\n" + resumo + "\nHISTÓRICO RECENTE:\n" + linhas)

    return ("\n\n".join(partes) + "\n") if partes else ""


def _build_crew(agents, tasks):
    tasks = [t for t in tasks if t is not None]
    return Crew(agents=agents, tasks=tasks, process=Process.sequential, verbose=True)


async def run_atendimento(numero_whatsapp: str, mensagem: str, origem: str = "organico") -> str:
    sessao = buscar_sessao(numero_whatsapp) or {}

    # Sessão sem tipo_cliente = primeira interação. O webhook cria a sessão
    # com `historico` ao receber a mensagem (antes do timer), então `not sessao`
    # não diferencia "novo" de "já qualificado".
    if not sessao.get("tipo_cliente"):
        await run_qualification(numero_whatsapp, mensagem, origem)
        return "qualificado"

    tipo = sessao.get("tipo_cliente", "atacado")

    # Defesa em código contra dupla `enviar_mensagem` por turno: gera um exec_id
    # que `EnviarMensagemTool` usa como contador no Redis. Se o LLM tentar chamar
    # duas vezes, a 2ª chamada é bloqueada antes de tocar a Meta API.
    exec_id = uuid.uuid4().hex
    merge_sessao(numero_whatsapp, current_exec_id=exec_id)

    # Histórico anterior (exceto as msgs novas que já vêm em `mensagem`)
    historico = sessao.get("historico", [])
    ultimo_idx = sessao.get("ultimo_processado_idx", 0)
    msgs_anteriores = historico[:ultimo_idx] if ultimo_idx > 0 else []
    contexto_historico = await _montar_contexto_historico(numero_whatsapp, msgs_anteriores, sessao)

    # Busca membro_grupo antecipadamente para não depender do agente chamar consultar_cliente
    from src.storage.store import buscar_cliente
    cliente = buscar_cliente(numero_whatsapp)
    membro_grupo = cliente["membro_grupo"] if cliente else False
    # Não injetamos profile_name como "nome do cliente". O profile do WhatsApp
    # é autodeclarado e pode ser apelido/marca/qualquer texto. A Bia só pode
    # chamar pelo nome se o cliente disser o nome no histórico desta conversa.
    # Flag persistente setada por EnviarMensagemTool quando o link é enviado.
    # Mais robusto que escanear texto do histórico (que falha se o link muda no .env).
    grupo_link = settings.GRUPO_LINK
    convite_ja_enviado = bool(sessao.get("grupo_convidado"))

    if convite_ja_enviado or membro_grupo:
        encerramento = "Cliente já recebeu o convite ou já é membro — NÃO mencione o grupo novamente."
    else:
        encerramento = (
            f"Se e SOMENTE SE o cliente se despedir explicitamente (ex: 'obrigado', 'tchau', 'até mais', 'valeu'), "
            f"inclua na mesma mensagem de despedida: '{grupo_link}'. "
            f"Se a conversa não terminou, NÃO mencione o grupo."
        )

    wholesale = build_wholesale_agent()
    site_url = settings.SITE_URL
    task_wholesale = Task(
        description=(
            f"Nova mensagem do cliente {numero_whatsapp}: '{mensagem}'\n"
            f"Contexto: tipo={tipo}, recorrente={sessao.get('cliente_recorrente', False)}, "
            f"origem={sessao.get('origem', 'organico')}, membro_grupo={membro_grupo}."
            f"{contexto_historico}\n"

            "\nESTILO DE ATENDIMENTO (vendedora consultiva):\n"
            "- Foco da conversa: levar o cliente até a compra no site " + site_url + ".\n"
            "- Antes de responder, valide a preocupação do cliente em 1 frase curta "
            "(\"entendo!\", \"faz sentido\", \"boa pergunta\"). Em seguida traga o BENEFÍCIO "
            "concreto (não a feature seca). Ex.: em vez de \"frete por conta do cliente\", "
            "diga \"você escolhe a transportadora que fica mais em conta pra sua região\".\n"
            "- Conduza para o site com naturalidade: \"você vê tudinho lá com foto e tabela "
            f"de tamanhos: {site_url}\". Sem pressão.\n"
            "- Use a linguagem que o cliente usa. Se ele é informal, seja informal. Se ele "
            "manda áudio formal, mantenha respeitoso.\n"
            "- 1-2 emojis no máximo por mensagem. Nunca robótico (\"prezado\", \"conforme solicitado\").\n"

            "\nQUEBRA DE OBJEÇÕES — guia de bolso:\n"
            "- \"é caro\" → \"é peça de algodão de boa procedência, modelagem que dura "
            "lavagem após lavagem; sai mais em conta a longo prazo. Dá uma olhada nos "
            f"detalhes: {site_url}\"\n"
            "- \"vou pensar\" → \"claro! Posso te separar uma sugestão de combo enquanto "
            "isso? Os lançamentos saem rápido.\"\n"
            "- \"prazo de entrega?\" → \"depende da sua região e da transportadora que "
            f"você escolher no checkout. Você vê o prazo exato no site na hora da compra: {site_url}\"\n"
            "- \"tem desconto?\" → \"o site tem condição de pagamento à vista no PIX; "
            f"dá uma olhada no carrinho que aparece o desconto: {site_url}\"\n"
            "- \"é confiável?\" / \"é loja de verdade?\" → \"sim! Estamos no mercado há "
            f"anos, somos especializados em moda infantil masculina. Site oficial: {site_url}\"\n"

            "\nREGRAS DE RESPOSTA:\n"
            "- NÃO se apresente. NÃO pergunte se é lojista — já foi qualificado.\n"
            "- Responda à dúvida do cliente. Não despeje informação não pedida.\n"
            "- Máximo 3 linhas por mensagem, exceto ao enviar condições completas de atacado.\n"
            "- Se o cliente fizer pergunta FACTUAL/DIRETA (quando, quem, onde, sobre a "
            "própria conversa), responda à pergunta primeiro. NÃO empurre CTA do site "
            "sobre pergunta factual.\n"
            "- Use o CTA do site (" + site_url + ") apenas quando fluir natural: cliente "
            "perguntou sobre produto, preço, prazo ou intenção de compra.\n"
            "- NUNCA chame o cliente pelo nome a menos que ele tenha dito o nome dele "
            "explicitamente no histórico desta conversa (frases como 'meu nome é X', "
            "'sou o X', 'me chamo X', 'aqui é o X'). O nome no perfil do WhatsApp NÃO "
            "conta — pode ser apelido, marca ou qualquer coisa.\n"
            "- NUNCA invente memória de interações passadas. NÃO use 'novamente', "
            "'de novo', 'da última vez', 'que bom ter você de volta', 'como você disse "
            "antes' a menos que haja evidência clara no histórico desta conversa.\n"
            "- NÃO invente preço, prazo ou política. Redirecione ao site quando não souber.\n"
            "- Chame enviar_mensagem UMA ÚNICA VEZ por execução. Se já chamou enviar_mensagem, pare.\n"
            "- O convite ao grupo, se aplicável, deve estar DENTRO da mesma chamada enviar_mensagem da despedida.\n"

            "\nCONTEÚDO POR TIPO DE CLIENTE:\n"
            f"VAREJO (consumidor final) — pode comprar normalmente no site {site_url}. "
            "Tire dúvidas sobre tamanho, tecido, lavagem, frete. Sempre direcione "
            f"para finalizar a compra em {site_url}. Não fale de pedido mínimo nem "
            "condições de atacado — não se aplicam.\n"
            "VAREJO + pedido de catálogo — NÃO chame enviar_catalogo (nosso catálogo "
            "tem preços de atacado e é exclusivo para lojistas). Em vez disso, "
            "responda algo como:\n"
            "\"Nosso catálogo é exclusivo para lojistas (preços de atacado). Você consegue "
            f"ver todos os produtos com fotos e tamanhos no site: {site_url} 😊\n"
            "Se você revende ou tem interesse, faça o cadastro de revendedor lá no site mesmo. "
            "A análise leva até 48h (mas geralmente sai antes); depois disso você "
            "passa a comprar com preço de atacado!\"\n"
            "ATACADO (lojista) — quando o cliente confirmar interesse em atacado, "
            "envie estas condições EXATAMENTE (preserve quebras de linha):\n"
            "\"Vou passar as informações atualizadas do nosso atacado:\n\n"
            " ▶️ Pedido mínimo: R$ 300,00;\n"
            " ▶️ Frete por conta do cliente (você escolhe a transportadora);\n"
            " ▶️ Pagamento via PIX, transferência ou link de cartão com acréscimo;\n"
            " ▶️ Após confirmação do pagamento, separamos em até 48h.\n\n"
            f"Você consegue montar seu pedido direto pelo site: {site_url}\"\n"
            "CATÁLOGO — se o cliente solicitar catálogo, use enviar_catalogo com o numero_whatsapp do cliente.\n"

            "\nPASSOS OBRIGATÓRIOS (execute nesta ordem):\n"
            "1. Chame enviar_mensagem EXATAMENTE UMA VEZ.\n"
            f"2. {encerramento}\n"
            f"3. Chame registrar_conversa com numero_whatsapp='{numero_whatsapp}', tipo_cliente='{tipo}', "
            f"origem='{sessao.get('origem', 'organico')}' e status='resolvido'."
        ),
        agent=wholesale,
        expected_output="Resposta enviada e conversa registrada com conversa_id",
    )

    return await _build_crew([wholesale], [task_wholesale]).kickoff_async()


def run_relatorio_diario(data: str | None = None) -> str:
    from pathlib import Path
    from src.relatorio_html import gerar_html

    data = data or str(date.today())

    insight = build_insight_agent()
    task_relatorio = Task(
        description=(
            f"Analise os atendimentos do dia {data}.\n"
            "1. Busque todas as avaliações do dia.\n"
            "2. Busque temas recorrentes no Qdrant.\n"
            "3. Escreva uma análise em português com:\n"
            "   - Destaques positivos do dia\n"
            "   - Pontos de atenção ou reclamações\n"
            "   - Padrões identificados nos temas\n"
            "   - Recomendações acionáveis para o próximo dia\n"
            "4. Tente enviar um resumo curto (máx 5 linhas) via WhatsApp para o dono da loja.\n"
            "5. Retorne a análise completa no Final Answer."
        ),
        agent=insight,
        expected_output="Análise textual completa do dia",
    )

    result = Crew(
        agents=[insight],
        tasks=[task_relatorio],
        process=Process.sequential,
        verbose=False,
    ).kickoff()

    relatorio_dir = Path(__file__).parent.parent / "relatorios"
    relatorio_dir.mkdir(exist_ok=True)
    html = gerar_html(data, analise=str(result))
    caminho = relatorio_dir / f"relatorio_{data}.html"
    caminho.write_text(html, encoding="utf-8")

    return str(caminho)
