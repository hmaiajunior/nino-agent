import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # LLM
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

    # WhatsApp
    EVOLUTION_API_URL: str = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
    EVOLUTION_API_KEY: str = os.getenv("EVOLUTION_API_KEY", "")
    EVOLUTION_INSTANCE: str = os.getenv("EVOLUTION_INSTANCE", "ninoagent")

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

    # Negócio
    LOJA_NOME: str = os.getenv("LOJA_NOME", "")
    LOJA_WHATSAPP: str = os.getenv("LOJA_WHATSAPP", "")
    GRUPO_LINK: str = os.getenv("GRUPO_LINK", "")
    HORARIO_ATENDIMENTO: str = os.getenv("HORARIO_ATENDIMENTO", "seg-sex 8h-18h")
    SESSION_TIMEOUT_MINUTES: int = int(os.getenv("SESSION_TIMEOUT_MINUTES", 30))


settings = Settings()
