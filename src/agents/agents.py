"""Definição dos 4 agentes NinoAgent com CrewAI."""

import os
from crewai import Agent

from src.config import settings
from src.tools.agent_tools import (
    consultar_cliente,
    enviar_mensagem,
    salvar_contexto_sessao,
    buscar_contexto_sessao,
    registrar_conversa,
    registrar_avaliacao,
    buscar_avaliacoes_do_dia,
    buscar_temas_recorrentes,
    enviar_catalogo,
)

os.environ["ANTHROPIC_API_KEY"] = settings.ANTHROPIC_API_KEY


def build_qualification_agent() -> Agent:
    return Agent(
        role="QualificationAgent",
        goal="Identificar se o cliente é lojista ou consumidor final com o mínimo de mensagens",
        backstory=(
            "Você é a Ana da PlayBeKids, loja de moda masculina infantil (0-12 anos). "
            "Responda de forma curta e natural, como uma pessoa real no WhatsApp. "
            "AO RECEBER UMA MENSAGEM: primeiro use consultar_cliente para verificar se o número já está na base. "
            "Se já for cliente: cumprimente pelo nome, salve o contexto com os dados existentes e passe para o próximo agente — NÃO pergunte se é lojista. "
            "Se for novo: faça UMA pergunta direta para descobrir se é lojista (atacado) ou consumidor final (varejo), "
            "depois salve o contexto com tipo_cliente, cliente_recorrente e origem."
        ),
        llm="anthropic/claude-haiku-4-5",
        tools=[consultar_cliente, enviar_mensagem, salvar_contexto_sessao],
        verbose=True,
        max_iter=5,
    )


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
        llm="anthropic/claude-sonnet-4-5",
        tools=[consultar_cliente, buscar_contexto_sessao, enviar_mensagem, enviar_catalogo, salvar_contexto_sessao, registrar_conversa],
        verbose=True,
        max_iter=10,
    )


def build_sentiment_agent() -> Agent:
    return Agent(
        role="SentimentAgent",
        goal="Analisar sentimento da conversa e registrar avaliação estruturada em JSON",
        backstory=(
            "Analista silencioso. Recupere a sessão do cliente para obter o conversa_id. "
            "Use SEMPRE o campo 'conversa_id' da sessão — nunca use o número do WhatsApp como ID. "
            "Classifique: sentimento (positivo/neutro/negativo), score 1-5, tema principal, "
            "duvida_resolvida, interesse_compra, demanda_varejo. "
            "Registre a avaliação com o conversa_id correto."
        ),
        llm="anthropic/claude-haiku-4-5",
        tools=[buscar_contexto_sessao, registrar_avaliacao],
        verbose=True,
        max_iter=3,
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
        llm="anthropic/claude-sonnet-4-5",
        tools=[buscar_avaliacoes_do_dia, buscar_temas_recorrentes, enviar_mensagem],
        verbose=True,
        max_iter=5,
    )
