"""Orquestração principal do NinoAgent."""

from datetime import date
from crewai import Crew, Task, Process

from src.agents.agents import build_wholesale_agent, build_insight_agent
from src.qualification import run_qualification
from src.storage.store import buscar_sessao


def _build_crew(agents, tasks):
    tasks = [t for t in tasks if t is not None]
    return Crew(agents=agents, tasks=tasks, process=Process.sequential, verbose=True)


async def run_atendimento(numero_whatsapp: str, mensagem: str, origem: str = "organico") -> str:
    sessao = buscar_sessao(numero_whatsapp)

    if not sessao:
        # Nova sessão: qualifica (envia saudação + salva contexto no Redis)
        await run_qualification(numero_whatsapp, mensagem, origem)
        sessao = buscar_sessao(numero_whatsapp) or {}

    tipo = sessao.get("tipo_cliente", "atacado")

    wholesale = build_wholesale_agent()
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
