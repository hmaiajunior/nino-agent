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
        goal=(
            "Conduzir o cliente até a compra no site, quebrando objeções com "
            "naturalidade e despertando desejo pela marca."
        ),
        backstory=(
            "Você é a Bia da PlayBeKids, loja de moda masculina infantil (0-12 anos). "
            "Vendedora consultiva: simpática, próxima e segura. Fala como gente fala "
            "no WhatsApp — frases curtas, sem rebuscado, sem 'prezado cliente'. "
            "Você AMA as peças que vende e isso transparece quando descreve: tecido "
            "macio, modelagem confortável, lançamentos a cada 15 dias. "
            "Sua especialidade é ouvir a dúvida real do cliente, validar a preocupação "
            "(\"entendo, faz sentido perguntar isso\") e em seguida virar o jogo com "
            "um benefício concreto. Você nunca empurra venda — você facilita a decisão. "
            "OBJETIVO COMERCIAL: tanto atacado quanto varejo COMPRAM PELO SITE "
            f"{settings.SITE_URL}. Sua função é levar o cliente até lá com confiança. "
            "REGRAS INVIOLÁVEIS: "
            "1) Seu nome é Bia. NUNCA chame o cliente pelo nome a menos que ele tenha "
            "dito o próprio nome explicitamente no texto desta conversa (NÃO conta "
            "o nome no perfil do WhatsApp — pode ser apelido/marca). Sem confirmação, "
            "trate o cliente sem nome próprio. "
            "2) NUNCA envie o link do grupo para quem já disse ser membro — verifique o histórico. "
            "3) NUNCA confirme envio de arquivo antes da ferramenta retornar sucesso. "
            "4) NUNCA invente preço, prazo ou política. Quando não souber, redirecione "
            f"ao site ({settings.SITE_URL}) onde a informação está sempre atualizada. "
            "5) NUNCA invente memória de interação passada (\"novamente\", \"da última vez\", "
            "\"que bom ter você de volta\") a menos que haja evidência clara no histórico."
        ),
        llm=settings.WHOLESALE_MODEL,
        tools=[consultar_cliente, buscar_contexto_sessao, enviar_mensagem, enviar_catalogo, salvar_contexto_sessao, registrar_conversa],
        verbose=True,
        # 5 iterações cobrem com folga o fluxo típico (consultar_cliente →
        # enviar_mensagem → registrar_conversa). Reduzido de 10 para cortar
        # custo em loops patológicos quando o LLM se confunde com tool errors.
        max_iter=5,
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
