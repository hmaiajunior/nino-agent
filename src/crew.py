"""Orquestração principal do NinoAgent."""

from datetime import date
from crewai import Crew, Task, Process

from src.agents.agents import (
    build_qualification_agent,
    build_wholesale_agent,
    build_sentiment_agent,
    build_insight_agent,
)
from src.storage.store import buscar_sessao


def _build_crew(agents, tasks):
    tasks = [t for t in tasks if t is not None]
    return Crew(agents=agents, tasks=tasks, process=Process.sequential, verbose=True)


def run_atendimento(numero_whatsapp: str, mensagem: str, origem: str = "organico") -> str:
    sessao = buscar_sessao(numero_whatsapp)
    wholesale = build_wholesale_agent()
    sentiment = build_sentiment_agent()

    if sessao:
        tipo = sessao.get("tipo_cliente", "atacado")
        task_wholesale = Task(
            description=(
                f"Nova mensagem do cliente {numero_whatsapp}: '{mensagem}'\n"
                f"Contexto: tipo={tipo}, recorrente={sessao.get('cliente_recorrente', False)}, "
                f"origem={sessao.get('origem', 'organico')}.\n"
                "NÃO se apresente. NÃO pergunte se é lojista — já foi qualificado.\n"
                "1. Responda diretamente à mensagem.\n"
                "2. Ao final, chame registrar_conversa com numero_whatsapp, tipo_cliente, origem e status."
            ),
            agent=wholesale,
            expected_output="Resposta enviada e conversa registrada com conversa_id",
        )
        result = _build_crew(
            [wholesale, sentiment],
            [task_wholesale, _build_sentiment_task(sentiment, numero_whatsapp, context_task=task_wholesale)],
        ).kickoff()
        return result

    # Primeira mensagem
    qualification = build_qualification_agent()

    task_qualify = Task(
        description=(
            f"Primeira mensagem de {numero_whatsapp} (origem: {origem}): '{mensagem}'\n"
            "1. Use consultar_cliente para verificar se o número já está na base.\n"
            "   - Se já for cliente: cumprimente pelo nome e salve o contexto com tipo_cliente, cliente_recorrente e origem.\n"
            "   - Se for novo: envie UMA mensagem de boas-vindas e pergunte se é lojista ou consumidor final, depois salve o contexto.\n"
            "2. Passe o contexto para o próximo agente."
        ),
        agent=qualification,
        expected_output="Contexto salvo no Redis com tipo_cliente identificado",
    )

    task_wholesale = Task(
        description=(
            f"Com base na qualificação do cliente {numero_whatsapp}, inicie o atendimento.\n"
            "Responda à mensagem inicial de forma natural, seja atacado ou varejo.\n"
            "Ao final, chame registrar_conversa com numero_whatsapp, tipo_cliente, origem e status."
        ),
        agent=wholesale,
        expected_output="Atendimento iniciado e conversa registrada com conversa_id",
        context=[task_qualify],
    )

    return _build_crew(
        [qualification, wholesale, sentiment],
        [task_qualify, task_wholesale, _build_sentiment_task(sentiment, numero_whatsapp, context_task=task_wholesale)],
    ).kickoff()


def _build_sentiment_task(sentiment, numero_whatsapp: str, context_task: Task = None) -> Task:
    return Task(
        description=(
            f"Analise a conversa do cliente {numero_whatsapp}.\n"
            f"1. Use buscar_contexto_sessao com numero='{numero_whatsapp}' para obter o conversa_id atual.\n"
            "2. Use o campo 'conversa_id' da sessão (inteiro) — NUNCA use o número do WhatsApp.\n"
            "3. Registre: sentimento (positivo/neutro/negativo), score (1-5), "
            "tema_principal (máx 50 chars), duvida_resolvida, interesse_compra, demanda_varejo."
        ),
        agent=sentiment,
        expected_output="avaliacao_salva",
        context=[context_task] if context_task else None,
    )


def run_relatorio_diario(data: str | None = None) -> str:
    from pathlib import Path
    from src.relatorio_html import gerar_html

    data = data or str(date.today())

    # InsightAgent gera análise textual
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

    # Gera HTML com análise injetada
    relatorio_dir = Path(__file__).parent.parent / "relatorios"
    relatorio_dir.mkdir(exist_ok=True)
    html = gerar_html(data, analise=str(result))
    caminho = relatorio_dir / f"relatorio_{data}.html"
    caminho.write_text(html, encoding="utf-8")

    return str(caminho)
