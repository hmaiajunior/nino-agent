"""
NinoAgent — Interface Chainlit
Conversa com o usuário e consulta Postgres + Qdrant em tempo real.

Execute:
    cd ninoagent
    .venv/bin/chainlit run src/app.py
"""

import json
import chainlit as cl
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage

from src.config import settings
from src.storage.store import (
    buscar_avaliacoes_dia,
    buscar_conversas_similares,
    pg_cursor,
)

SYSTEM_PROMPT = """Você é o assistente de dados da PlayBeKids, loja de moda masculina infantil.
Você tem acesso direto aos dados de atendimento da loja e responde perguntas analíticas.

Dados disponíveis:
- Postgres: conversas (tipo_cliente, origem, status, aceitou_grupo, duracao_segundos, iniciada_em)
             avaliacoes (sentimento, score_atendimento, tema_principal, duvida_resolvida, interesse_compra, demanda_varejo)
- Qdrant: embeddings dos históricos de conversa (busca semântica por tema/conteúdo)

Quando o usuário perguntar algo analítico (números, totais, médias), consulte os dados fornecidos no contexto.
Quando perguntar sobre temas ou conteúdo de conversas, use os resultados da busca semântica.
Responda sempre em português (pt-BR), de forma direta e analítica.
"""


def _query_postgres(sql: str) -> list[dict]:
    with pg_cursor() as cur:
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _build_context(pergunta: str) -> str:
    from datetime import date
    context_parts = [f"DATA ATUAL: {date.today().isoformat()} (use esta data para interpretar 'hoje', 'ontem', etc.)"]

    def resumo_periodo(label: str, where: str) -> None:
        try:
            rows = _query_postgres(f"""
                SELECT
                    COUNT(*) as total_conversas,
                    SUM(CASE WHEN c.tipo_cliente = 'atacado' THEN 1 ELSE 0 END) as atacado,
                    SUM(CASE WHEN c.tipo_cliente = 'varejo' THEN 1 ELSE 0 END) as varejo,
                    SUM(CASE WHEN c.aceitou_grupo = true THEN 1 ELSE 0 END) as aceitou_grupo,
                    ROUND(AVG(a.score_atendimento)::numeric, 2) as score_medio,
                    SUM(CASE WHEN a.sentimento = 'positivo' THEN 1 ELSE 0 END) as positivos,
                    SUM(CASE WHEN a.sentimento = 'neutro' THEN 1 ELSE 0 END) as neutros,
                    SUM(CASE WHEN a.sentimento = 'negativo' THEN 1 ELSE 0 END) as negativos,
                    SUM(CASE WHEN a.interesse_compra = true THEN 1 ELSE 0 END) as interesse_compra,
                    SUM(CASE WHEN a.demanda_varejo = true THEN 1 ELSE 0 END) as demanda_varejo
                FROM conversas c
                JOIN avaliacoes a ON a.conversa_id = c.id
                {where}
            """)
            context_parts.append(f"RESUMO {label}:\n{json.dumps(rows, ensure_ascii=False, default=str)}")
        except Exception as e:
            context_parts.append(f"Erro ao consultar {label}: {e}")

    resumo_periodo("HOJE",        "WHERE c.iniciada_em >= CURRENT_DATE")
    resumo_periodo("ONTEM",       "WHERE c.iniciada_em >= CURRENT_DATE - INTERVAL '1 day' AND c.iniciada_em < CURRENT_DATE")
    resumo_periodo("ÚLTIMOS 7 DIAS", "WHERE c.iniciada_em >= CURRENT_DATE - INTERVAL '7 days'")
    resumo_periodo("HISTÓRICO TOTAL", "")

    try:
        negativos = _query_postgres("""
            SELECT c.numero_whatsapp, c.iniciada_em::date as data,
                   a.sentimento, a.score_atendimento, a.tema_principal, a.duvida_resolvida
            FROM conversas c
            JOIN avaliacoes a ON a.conversa_id = c.id
            WHERE a.sentimento = 'negativo'
            ORDER BY c.iniciada_em DESC
            LIMIT 50
        """)
        context_parts.append(f"CONVERSAS NEGATIVAS (últimas 50):\n{json.dumps(negativos, ensure_ascii=False, default=str)}")
    except Exception as e:
        context_parts.append(f"Erro conversas negativas: {e}")

    try:
        temas = _query_postgres("""
            SELECT tema_principal, COUNT(*) as total
            FROM avaliacoes a
            JOIN conversas c ON c.id = a.conversa_id
            WHERE c.iniciada_em >= CURRENT_DATE
            GROUP BY tema_principal ORDER BY total DESC
        """)
        context_parts.append(f"TEMAS HOJE:\n{json.dumps(temas, ensure_ascii=False)}")
    except Exception as e:
        context_parts.append(f"Erro temas: {e}")

    # Busca semântica no Qdrant
    try:
        similares = buscar_conversas_similares(pergunta, limit=5)
        if similares:
            trechos = [
                f"- [{r.get('sentimento','?')} | score={r.get('score_atendimento','?')} | tema={r.get('tema_principal','?')}] {r.get('historico','')[:200]}"
                for r in similares
            ]
            context_parts.append("CONVERSAS RELACIONADAS À PERGUNTA:\n" + "\n".join(trechos))
    except Exception as e:
        context_parts.append(f"Erro na busca semântica: {e}")

    return "\n\n".join(context_parts)


@cl.on_chat_start
async def start():
    cl.user_session.set("llm", ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=settings.GROQ_API_KEY,
        temperature=0.3,
    ))
    await cl.Message(
        content=(
            "👋 Olá! Sou o assistente de dados da **PlayBeKids**.\n\n"
            "Posso responder perguntas sobre os atendimentos realizados, como:\n"
            "- Qual o sentimento geral dos clientes?\n"
            "- Quantos aceitaram o convite para o grupo?\n"
            "- Quais os temas mais frequentes nas conversas?\n"
            "- O que os clientes perguntam sobre entrega?\n\n"
            "O que você quer saber?"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    llm = cl.user_session.get("llm")
    pergunta = message.content

    async with cl.Step(name="Consultando bancos de dados...") as step:
        context = _build_context(pergunta)
        step.output = "Postgres e Qdrant consultados."

    messages = [
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"DADOS DO SISTEMA:\n{context}\n\nPERGUNTA DO USUÁRIO: {pergunta}"),
    ]

    response = await cl.make_async(llm.invoke)(messages)
    await cl.Message(content=response.content).send()
