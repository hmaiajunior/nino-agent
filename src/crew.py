"""Orquestração principal do NinoAgent."""

import uuid
from datetime import date
from crewai import Crew, Task, Process

import src.observability  # noqa: F401 — ativa litellm.success_callback para LangFuse
from src.agents.agents import build_wholesale_agent, build_insight_agent
from src.config import settings
from src.llm_summary import resumir_historico
from src.qualification import run_qualification
from src.storage.store import buscar_sessao, merge_sessao, site_em_cooldown


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
        resultado = await run_qualification(numero_whatsapp, mensagem, origem)
        # Caminhos da qualification:
        # - {"aguardando_botao": True} → cliente novo sem sinal: enviou botões, encerra aqui.
        # - {"direct_to_wholesale": True} → recorrente ou inferido por campanha:
        #   cai através e segue para o Wholesale logo abaixo, com tipo_cliente já setado.
        if not resultado.get("direct_to_wholesale"):
            return "qualificado"
        sessao = buscar_sessao(numero_whatsapp) or sessao  # recarrega o tipo_cliente fresco

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

    # Primeira interação = não há mensagem do agente ainda na sessão atual.
    # Quando o cliente cai direto no Wholesale (recorrente sem dia novo, ou
    # inferido por campanha), precisamos avisar a Bia pra cumprimentar — não
    # tem mais a qualification fazendo isso antes.
    primeira_interacao = not any(
        m.get("role") in ("agente", "humano") for m in sessao.get("historico", [])
    )
    nota_saudacao = (
        "Esta é a PRIMEIRA mensagem que você responde a este cliente — comece "
        "com saudação curta e simpática (\"Oi! Que bom te ver por aqui\") "
        "ANTES de responder à dúvida. NÃO se apresente como Bia (cliente já vê "
        "a foto da loja); só seja calorosa.\n" if primeira_interacao else ""
    )

    # Cooldown do link do site: se já enviei nos últimos 30min, NÃO mando de novo.
    # Defesa em código também remove URL se passar, mas instruir é mais limpo.
    nota_cooldown_site = (
        "ATENÇÃO: você JÁ enviou o link do site para este cliente nesta conversa "
        "(há menos de 30 minutos). NÃO INCLUA o URL na sua resposta. Se precisar "
        "referenciar, use frase curta tipo \"no site que te passei mais cedo\" "
        "ou \"lá no site\" sem repetir o link.\n"
        if site_em_cooldown(numero_whatsapp) else ""
    )

    wholesale = build_wholesale_agent()
    site_url = settings.SITE_URL
    task_wholesale = Task(
        description=(
            f"Cliente {numero_whatsapp} (tipo={tipo}, recorrente={sessao.get('cliente_recorrente', False)}, "
            f"membro_grupo={membro_grupo}) enviou: '{mensagem}'.\n"
            f"{contexto_historico}\n"
            + nota_saudacao
            + nota_cooldown_site +

            "ESTILO: vendedora consultiva no WhatsApp. Valide a dúvida ('faz sentido', "
            "'boa pergunta'), responda com benefício concreto (não feature seca), conduza "
            f"para {site_url} com naturalidade. Acompanhe o tom do cliente. Máx 1-2 emojis.\n"

            "QUEBRA DE OBJEÇÕES (use o estilo, adapte ao contexto):\n"
            f"• 'é caro' → qualidade do algodão, durabilidade, sai em conta a longo prazo; ver {site_url}\n"
            "• 'vou pensar' → ofereça sugestão de combo, mencione que lançamentos saem rápido\n"
            f"• 'prazo?' → depende de região/transportadora escolhida no checkout em {site_url}\n"
            f"• 'tem desconto?' → PIX à vista tem condição especial; ver no carrinho do site\n"
            f"• 'é confiável?' → loja oficial, anos no mercado, moda infantil masculina; {site_url}\n"
            f"• 'o site funciona?' / 'tem site?' / 'site está pronto?' → SIM, está no ar e "
            f"é nosso canal oficial de compras: {site_url}. Tem catálogo completo, fotos, "
            "tamanhos e formas de pagamento.\n"

            "FONTES PERMITIDAS DE INFORMAÇÃO (responda APENAS com base nelas):\n"
            "1) O que está nesta task description e no seu backstory.\n"
            "2) Retorno das suas ferramentas (consultar_cliente, buscar_contexto_sessao).\n"
            "3) O retorno da ferramenta consultar_site — única forma de saber o que há no "
            "site. Se o cliente perguntar sobre produto/categoria/condição publicada, "
            "chame consultar_site ANTES de responder e afirme só o que vier nela. NÃO "
            "descreva conteúdo do site de memória; se não chamou a ferramenta, não sabe. "
            "consultar_site NÃO traz preço/estoque — para esses, encaminhe ao site.\n"
            "Se a pergunta cai FORA destas fontes, NÃO INVENTE. Responda no formato:\n"
            f"  \"Essa informação fica atualizada no site: {site_url} 😊 Lá você confere "
            "com detalhe.\"\n"

            "O QUE VOCÊ PODE AFIRMAR (está no prompt):\n"
            "- Faixa etária: 0 a 12 anos, moda masculina infantil.\n"
            "- Atacado: pedido mínimo R$ 300, frete por conta do cliente, pagamento via "
            "PIX/transferência/cartão (com acréscimo), separação em até 48h após confirmação.\n"
            "- Para COMPRAR COM PREÇO DE ATACADO: cliente precisa do cadastro de "
            "REVENDEDOR aprovado no site. Sem aprovação, vê preços de varejo. "
            "Análise do cadastro: até 48h (geralmente sai antes).\n"
            "- Catálogo: exclusivo para lojistas; varejo compra no site.\n"
            f"- Site {site_url} é o canal oficial e está plenamente operacional. "
            "Ecommerce moderno com checkout, fotos, tabela de tamanhos, formas de "
            "pagamento, cálculo de frete. Fale dele com propriedade, sem comparações "
            "redutoras ('parece site normal', 'tipo loja', 'como se fosse e-commerce').\n"

            "O QUE VOCÊ NUNCA PODE AFIRMAR SEM CONSULTAR O SITE (REDIRECIONE):\n"
            "- Preço de peça específica, valor unitário, valor de combo, total estimado.\n"
            "- Cor, tamanho ou estampa disponível agora.\n"
            "- Estoque de modelo específico.\n"
            "- Prazo de entrega exato (varia por região/transportadora — sai no checkout).\n"
            "- Política exata de troca/devolução (regulamento mora no site).\n"
            "- Promoções/cupons/descontos vigentes (mudam — confere no carrinho).\n"
            "- Composição/tecido detalhado de modelo específico.\n"
            "- Endereço físico, telefone, CNPJ.\n"
            "Para QUALQUER item acima, a resposta é redirecionar ao site, NÃO chutar.\n"

            "REGRAS:\n"
            "- NÃO se apresente como 'Bia' nem repita o nome da loja. NÃO pergunte se é lojista — já qualificado.\n"
            "- Responda só o que foi perguntado. Máx 3 linhas (exceto condições de atacado).\n"
            "- Pergunta FACTUAL (quando, quem, onde, sobre a conversa) → responda direto, sem CTA forçado.\n"
            f"- Envie o link do site APENAS quando a pergunta REALMENTE pede (produto, "
            "preço, prazo, finalizar compra, cadastro de revendedor, ver catálogo). "
            "NÃO mande o link em saudação, agradecimento, despedida, dúvida factual "
            "ou conversa de manutenção. Repetir o link cansa o cliente.\n"
            "- Link do site UMA ÚNICA VEZ por sessão (já é o suficiente). Se já enviou, "
            "referencie com 'lá no site que te passei' sem repetir o URL.\n"
            "- NUNCA chame o cliente pelo nome a menos que ELE tenha dito o nome no texto desta conversa. "
            "Profile do WhatsApp NÃO conta — pode ser apelido/marca.\n"
            "- NUNCA invente memória ('novamente', 'da última vez', 'que bom ter você de volta') "
            "sem evidência clara no histórico.\n"
            "- NUNCA invente FATO ALGUM (preço, cor, prazo exato, estoque, política, "
            "tecido específico, peso, promoção). Se não está nas FONTES PERMITIDAS, "
            f"redirecione: \"essa info tá no site {site_url}\".\n"
            "- Se em dúvida sobre se pode afirmar algo: NÃO afirme. Mande pro site.\n"
            f"- O SITE {site_url} ESTÁ NO AR e funcionando. NUNCA diga que está em "
            "manutenção, em desenvolvimento, fora do ar ou 'em breve'. É o canal "
            "oficial de compras agora.\n"
            "- NUNCA use comparações redutoras sobre o site ('parece uma loja online "
            "normal', 'tipo um site', 'como se fosse', 'meio que um e-commerce'). "
            "Fale dele com propriedade: é um ecommerce moderno e completo.\n"
            "- Quando explicar atacado para cliente novo, deixe CLARO que precisa "
            "fazer cadastro de revendedor + aguardar análise pra ver preços de atacado. "
            "Não dê a entender que entrar no site já mostra preços especiais.\n"
            "- enviar_mensagem UMA vez por execução. Convite ao grupo, se aplicável, DENTRO da mesma despedida.\n"

            "POR TIPO:\n"
            f"• VAREJO: compra no site {site_url}. Dúvidas de tamanho/tecido/lavagem/frete: responda. "
            "NÃO fale de pedido mínimo nem condições de atacado.\n"
            "• VAREJO pedindo catálogo: NÃO chame enviar_catalogo. Responda algo como: "
            f"\"Nosso catálogo é exclusivo pra lojistas (preços de atacado). Você vê todos os produtos no site: {site_url} 😊 "
            "Se você revende ou tem interesse, faça o cadastro de revendedor lá no site. "
            "A análise leva até 48h (geralmente sai antes); depois disso você compra com preço de atacado!\"\n"
            "• ATACADO confirmado: envie EXATAMENTE (preservando quebras):\n"
            "\"Vou passar as informações atualizadas do nosso atacado:\n\n"
            " ▶️ Pedido mínimo: R$ 300,00;\n"
            " ▶️ Frete por conta do cliente (você escolhe a transportadora);\n"
            " ▶️ Pagamento via PIX, transferência ou link de cartão com acréscimo;\n"
            " ▶️ Após confirmação do pagamento, separamos em até 48h.\n\n"
            "Para comprar com preço de atacado é preciso fazer o cadastro de "
            "revendedor no site e aguardar a análise (até 48h, geralmente sai antes). "
            f"Depois é só montar o pedido por lá: {site_url}\"\n"
            "• CATÁLOGO (lojista): use enviar_catalogo com o numero_whatsapp.\n"

            "PASSOS (nesta ordem):\n"
            "1. enviar_mensagem EXATAMENTE UMA VEZ.\n"
            f"2. {encerramento}\n"
            f"3. registrar_conversa(numero_whatsapp='{numero_whatsapp}', tipo_cliente='{tipo}', "
            f"origem='{sessao.get('origem', 'organico')}', status='resolvido')."
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
