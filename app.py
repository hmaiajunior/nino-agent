"""
NinoAgent — Interface Chainlit
Execute: .venv/bin/chainlit run app.py --port 8001
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import chainlit as cl
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage, SystemMessage
from dotenv import load_dotenv

load_dotenv()

from src.config import settings
from src.storage.store import buscar_conversas_similares, pg_conn

SYSTEM_PROMPT = """Você é o assistente de dados da PlayBeKids, loja de moda masculina infantil (0-12 anos).
Você tem acesso a dados reais de atendimento: conversas, avaliações de sentimento, temas, origens e históricos.

Quando receber uma pergunta — seja específica ou aberta — use os dados do contexto para responder.
Se a pergunta for aberta (ex: "o que você tem a falar?"), apresente um resumo completo dos dados disponíveis.
Se a pergunta for específica, vá direto ao ponto com números e exemplos.
Nunca diga que não encontrou informações se os dados estiverem no contexto.
Responda sempre em português (pt-BR)."""


def _query_postgres(sql: str) -> list[dict]:
    with pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def _build_context(pergunta: str) -> str:
    parts = []

    try:
        resumo = _query_postgres("""
            SELECT
                COUNT(*) as total_conversas,
                SUM(CASE WHEN c.tipo_cliente='atacado' THEN 1 ELSE 0 END) as atacado,
                SUM(CASE WHEN c.tipo_cliente='varejo'  THEN 1 ELSE 0 END) as varejo,
                SUM(CASE WHEN c.aceitou_grupo=true     THEN 1 ELSE 0 END) as aceitou_grupo,
                ROUND(AVG(a.score_atendimento)::numeric,2)                as score_medio,
                SUM(CASE WHEN a.sentimento='positivo'  THEN 1 ELSE 0 END) as positivos,
                SUM(CASE WHEN a.sentimento='neutro'    THEN 1 ELSE 0 END) as neutros,
                SUM(CASE WHEN a.sentimento='negativo'  THEN 1 ELSE 0 END) as negativos,
                SUM(CASE WHEN a.interesse_compra=true  THEN 1 ELSE 0 END) as interesse_compra,
                SUM(CASE WHEN a.demanda_varejo=true    THEN 1 ELSE 0 END) as demanda_varejo
            FROM conversas c JOIN avaliacoes a ON a.conversa_id = c.id
        """)
        parts.append("RESUMO GERAL:\n" + json.dumps(resumo, ensure_ascii=False, default=str))

        # Conversas de hoje
        hoje = _query_postgres("""
            SELECT c.id, c.numero_whatsapp, c.tipo_cliente, c.status,
                   c.iniciada_em, a.sentimento, a.score_atendimento, a.tema_principal
            FROM conversas c
            LEFT JOIN avaliacoes a ON a.conversa_id = c.id
            WHERE DATE(c.iniciada_em) = CURRENT_DATE
            ORDER BY c.iniciada_em DESC
        """)
        parts.append("CONVERSAS DE HOJE:\n" + json.dumps(hoje, ensure_ascii=False, default=str))

        # Última conversa
        ultima = _query_postgres("""
            SELECT c.id, c.numero_whatsapp, c.tipo_cliente, c.status,
                   c.iniciada_em, a.sentimento, a.score_atendimento, a.tema_principal
            FROM conversas c
            LEFT JOIN avaliacoes a ON a.conversa_id = c.id
            ORDER BY c.iniciada_em DESC LIMIT 1
        """)
        parts.append("ÚLTIMA CONVERSA:\n" + json.dumps(ultima, ensure_ascii=False, default=str))

        temas = _query_postgres("""
            SELECT tema_principal, COUNT(*) as total
            FROM avaliacoes GROUP BY tema_principal ORDER BY total DESC LIMIT 10
        """)
        parts.append("TEMAS MAIS FREQUENTES:\n" + json.dumps(temas, ensure_ascii=False))

        origens = _query_postgres("""
            SELECT origem, COUNT(*) as total
            FROM conversas GROUP BY origem ORDER BY total DESC
        """)
        parts.append("ORIGENS:\n" + json.dumps(origens, ensure_ascii=False))

    except Exception as e:
        parts.append(f"Erro Postgres: {e}")

    try:
        similares = buscar_conversas_similares(pergunta, limit=5)
        if similares:
            trechos = [
                f"[{r.get('sentimento','?')} | tema={r.get('tema_principal','?')}] {r.get('historico','')[:200]}"
                for r in similares
            ]
            parts.append("CONVERSAS RELACIONADAS:\n" + "\n".join(trechos))
    except Exception as e:
        parts.append(f"Erro Qdrant: {e}")

    return "\n\n".join(parts)


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
            "Pergunte sobre os atendimentos, como:\n"
            "- Qual o sentimento geral dos clientes?\n"
            "- Quantos aceitaram o convite para o grupo?\n"
            "- Quais os temas mais frequentes?\n"
            "- O que clientes perguntam sobre entrega?\n"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    llm = cl.user_session.get("llm")

    async with cl.Step(name="Consultando Postgres e Qdrant..."):
        context = _build_context(message.content)

    response = await cl.make_async(llm.invoke)([
        SystemMessage(content=SYSTEM_PROMPT),
        HumanMessage(content=f"DADOS:\n{context}\n\nPERGUNTA: {message.content}"),
    ])
    await cl.Message(content=response.content).send()
