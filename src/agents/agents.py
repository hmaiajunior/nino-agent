"""Definição dos agentes NinoAgent com CrewAI (WholesaleAgent e InsightAgent)."""

import os
from crewai import Agent

from src.config import settings
from src.tools.agent_tools import (
    consultar_cliente,
    enviar_mensagem,
    salvar_contexto_sessao,
    buscar_contexto_sessao,
    registrar_conversa,
    buscar_avaliacoes_do_dia,
    buscar_temas_recorrentes,
    enviar_catalogo,
)

os.environ["OPENROUTER_API_KEY"] = settings.OPENROUTER_API_KEY
# Mantido como fallback para chamadas diretas à Anthropic fora do OpenRouter
os.environ["ANTHROPIC_API_KEY"] = settings.ANTHROPIC_API_KEY


def build_wholesale_agent() -> Agent:
    return Agent(
        role="WholesaleAgent",
        goal="Responder o que o cliente perguntou, de forma simples e direta",
        backstory=(
            "Você é a Bia da PlayBeKids, loja de moda masculina infantil (0-12 anos). "
            "Linguagem simples, informal, como uma conversa de WhatsApp entre pessoas. "
            "REGRAS IMPORTANTES: "
            "1) Seu nome é Bia — NUNCA chame o cliente de Bia ou qualquer nome que ele não tenha dito explicitamente no histórico. "
            "2) NUNCA envie o link do grupo para quem já disse ser membro — verifique o histórico. "
            "3) NUNCA confirme o envio de um arquivo antes de a ferramenta retornar sucesso. Se falhar, avise honestamente."
        ),
        llm=settings.WHOLESALE_MODEL,
        tools=[consultar_cliente, buscar_contexto_sessao, enviar_mensagem, enviar_catalogo, salvar_contexto_sessao, registrar_conversa],
        verbose=True,
        max_iter=10,
    )


def build_insight_agent() -> Agent:
    return Agent(
        role="InsightAgent",
        goal="Gerar relatório diário com métricas e recomendações acionáveis",
        backstory=(
            "Analista de negócios da PlayBeKids. Consolida atendimentos do dia: "
            "volume, sentimento, temas, adesão ao grupo, demanda varejo. "
            "Relatório objetivo em menos de 3 minutos de leitura."
        ),
        llm=settings.INSIGHT_MODEL,
        tools=[buscar_avaliacoes_do_dia, buscar_temas_recorrentes, enviar_mensagem],
        verbose=True,
        max_iter=5,
    )
