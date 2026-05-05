"""Orquestração principal do NinoAgent."""

from datetime import date
from crewai import Crew, Task, Process

import src.observability  # noqa: F401 — ativa litellm.success_callback para LangFuse
from src.agents.agents import build_wholesale_agent, build_insight_agent
from src.config import settings
from src.qualification import run_qualification
from src.storage.store import buscar_sessao


def _build_crew(agents, tasks):
    tasks = [t for t in tasks if t is not None]
    return Crew(agents=agents, tasks=tasks, process=Process.sequential, verbose=True)


async def run_atendimento(numero_whatsapp: str, mensagem: str, origem: str = "organico") -> str:
    sessao = buscar_sessao(numero_whatsapp)

    if not sessao:
        await run_qualification(numero_whatsapp, mensagem, origem)
        return "qualificado"

    tipo = sessao.get("tipo_cliente", "atacado")

    # Histórico anterior (exceto as msgs novas que já vêm em `mensagem`)
    historico = sessao.get("historico", [])
    ultimo_idx = sessao.get("ultimo_processado_idx", 0)
    msgs_anteriores = historico[:ultimo_idx] if ultimo_idx > 0 else []
    contexto_historico = ""
    if msgs_anteriores:
        linhas = "\n".join(f"[{m['role'].upper()}] {m['text']}" for m in msgs_anteriores)
        contexto_historico = f"\nHISTÓRICO ANTERIOR DA CONVERSA:\n{linhas}\n"

    # Busca membro_grupo antecipadamente para não depender do agente chamar consultar_cliente
    from src.storage.store import buscar_cliente
    cliente = buscar_cliente(numero_whatsapp)
    # Verifica se o convite ao grupo já foi enviado nesta conversa
    historico_agente = [m["text"] for m in sessao.get("historico", []) if m.get("role") == "agente"]
    grupo_link = settings.GRUPO_LINK
    convite_ja_enviado = any(grupo_link and grupo_link in t for t in historico_agente)

    if convite_ja_enviado or membro_grupo:
        encerramento = "Cliente já recebeu o convite ou já é membro — NÃO mencione o grupo novamente."
    else:
        encerramento = (
            f"Se e SOMENTE SE o cliente se despedir explicitamente (ex: 'obrigado', 'tchau', 'até mais', 'valeu'), "
            f"inclua na mesma mensagem de despedida: '{grupo_link}'. "
            f"Se a conversa não terminou, NÃO mencione o grupo."
        )

    wholesale = build_wholesale_agent()
    task_wholesale = Task(
        description=(
            f"Nova mensagem do cliente {numero_whatsapp}: '{mensagem}'\n"
            f"Contexto: tipo={tipo}, recorrente={sessao.get('cliente_recorrente', False)}, "
            f"origem={sessao.get('origem', 'organico')}, membro_grupo={membro_grupo}."
            f"{contexto_historico}\n"
            "\nREGRAS DE RESPOSTA:\n"
            "- NÃO se apresente. NÃO pergunte se é lojista — já foi qualificado.\n"
            "- Responda APENAS o que foi perguntado. Nada a mais.\n"
            "- Máximo 2 linhas por mensagem, exceto ao enviar condições de atacado.\n"
            "- NÃO faça perguntas a não ser que seja estritamente necessário para responder.\n"
            "- NÃO liste produtos, condições ou informações extras sem o cliente pedir.\n"
            "- Chame enviar_mensagem UMA ÚNICA VEZ por execução. Se já chamou enviar_mensagem, pare — não chame de novo.\n"
            "- O convite ao grupo, se aplicável, deve estar DENTRO da mesma chamada enviar_mensagem da despedida.\n"
            "\nCONTEÚDO DA RESPOSTA POR TIPO:\n"
            "ATACADO — quando o cliente confirmar interesse em atacado, envie EXATAMENTE este texto:\n"
            "\"Vou passar as informações atualizadas do nosso atacado. \n\n\n"
            " ▶️ Nosso atacado tem o pedido minimo de 7 conjuntos ou 15 peças;\n\n\n"
            " ▶️ Frete por conta do cliente;\n\n\n"
            " ▶️ Pagamento via pix, transferência ou link de cartao de crédito com acréscimo ;\n\n\n"
            " ▶️ Peças só são separadas após a confirmação do pagamento. Daí temos até 48h para o envio.\"\n"
            "CATÁLOGO — se o cliente solicitar catálogo, use enviar_catalogo com o numero_whatsapp do cliente.\n"
            "VAREJO — atenda normalmente, tire dúvidas sobre produtos, preços e disponibilidade.\n"
            "\nPASSOS OBRIGATÓRIOS (execute nesta ordem):\n"
            f"1. Chame enviar_mensagem EXATAMENTE UMA VEZ.\n"
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
