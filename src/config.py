import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # LLM — chaves de API
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")

    # LLM — modelos (trocáveis via env var, sem alterar código)
    # Qualification e Sentiment: tarefas simples → Groq Llama (barato e rápido)
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
    # WholesaleAgent e InsightAgent: atendimento e relatório → Sonnet via OpenRouter
    WHOLESALE_MODEL: str = os.getenv("WHOLESALE_MODEL", "openrouter/anthropic/claude-sonnet-4-5")
    INSIGHT_MODEL: str = os.getenv("INSIGHT_MODEL", "openrouter/anthropic/claude-sonnet-4-5")

    # WhatsApp — Meta API oficial
    WHATSAPP_TOKEN: str = os.getenv("WHATSAPP_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    # App Secret do Meta for Developers — usado para validar X-Hub-Signature-256.
    # Se vazio, validação de assinatura é desabilitada (modo permissivo).
    WHATSAPP_APP_SECRET: str = os.getenv("WHATSAPP_APP_SECRET", "")

    # Postgres
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST", "localhost")
    POSTGRES_PORT: int = int(os.getenv("POSTGRES_PORT", 5432))
    POSTGRES_DB: str = os.getenv("POSTGRES_DB", "ninoagent")
    POSTGRES_USER: str = os.getenv("POSTGRES_USER", "ninoagent")
    POSTGRES_PASSWORD: str = os.getenv("POSTGRES_PASSWORD", "ninoagent")

    # Qdrant
    QDRANT_URL: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "ninoagent_conversas")

    # Redis
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")

    # LangFuse
    LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
    LANGFUSE_HOST: str = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")

    # Google Drive
    GOOGLE_SA_CREDENTIALS_PATH: str = os.getenv("GOOGLE_SA_CREDENTIALS_PATH", "credentials/google-drive-sa.json")
    GOOGLE_DRIVE_CATALOG_FOLDER_ID: str = os.getenv("GOOGLE_DRIVE_CATALOG_FOLDER_ID", "")

    # Monitor
    MONITOR_TOKEN: str = os.getenv("MONITOR_TOKEN", "")

    # Negócio
    LOJA_NOME: str = os.getenv("LOJA_NOME", "")
    LOJA_WHATSAPP: str = os.getenv("LOJA_WHATSAPP", "")
    GRUPO_LINK: str = os.getenv("GRUPO_LINK", "")
    HORARIO_ATENDIMENTO: str = os.getenv("HORARIO_ATENDIMENTO", "seg-sex 8h-18h")
    SESSION_TIMEOUT_MINUTES: int = int(os.getenv("SESSION_TIMEOUT_MINUTES", 30))


settings = Settings()
