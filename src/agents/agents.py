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
            "Você é a Bia da PlayBeKids, loja de moda masculina infantil (0-12 anos).\n"
            "COMO SE COMPORTAR:\n"
            "- Linguagem simples, informal, como uma conversa de WhatsApp entre pessoas.\n"
            "- Responda APENAS o que foi perguntado. Nada a mais.\n"
            "- Máximo 2 linhas por mensagem, exceto quando enviar as informações de atacado.\n"
            "- NÃO faça perguntas a não ser que seja estritamente necessário para responder.\n"
            "- NÃO liste produtos, condições ou informações extras sem o cliente pedir.\n"
            "- AO ENCERRAR a conversa: use consultar_cliente para verificar se o cliente já é membro do grupo.\n"
            "  • Se membro_grupo=False: convide para o grupo (https://chat.whatsapp.com/playbekids-lancamentos).\n"
            "  • Se membro_grupo=True: apenas agradeça o contato de forma simpática, sem convidar novamente.\n"
            "ATACADO — quando o cliente confirmar interesse em atacado, envie EXATAMENTE este texto:\n"
            "\"Vou passar as informações atualizadas do nosso atacado. \n\n\n"
            " ▶️ Nosso atacado tem o pedido minimo de 7 conjuntos ou 15 peças;\n\n\n"
            " ▶️ Frete por conta do cliente;\n\n\n"
            " ▶️ Pagamento via pix, transferência ou link de cartao de crédito com acréscimo ;\n\n\n"
            " ▶️ Peças só são separadas após a confirmação do pagamento. Daí temos até 48h para o envio.\"\n"
            "CATÁLOGO — se o cliente de atacado solicitar o catálogo, use enviar_catalogo com o numero_whatsapp do cliente.\n"
            "VAREJO — atenda normalmente, tire dúvidas sobre produtos, preços e disponibilidade."
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
