"""LangFuse observability — graceful no-op when keys are absent.

- CrewAI/LiteLLM calls: traced automatically via litellm.success_callback
- Direct Groq calls (qualification, sentiment): traced via new_trace()
"""

import os
from src.config import settings

_enabled = bool(settings.LANGFUSE_PUBLIC_KEY and settings.LANGFUSE_SECRET_KEY)
_langfuse = None

if _enabled:
    os.environ.setdefault("LANGFUSE_PUBLIC_KEY", settings.LANGFUSE_PUBLIC_KEY)
    os.environ.setdefault("LANGFUSE_SECRET_KEY", settings.LANGFUSE_SECRET_KEY)
    os.environ.setdefault("LANGFUSE_HOST", settings.LANGFUSE_HOST)

    import litellm
    if "langfuse" not in litellm.success_callback:
        litellm.success_callback.append("langfuse")
    if "langfuse" not in litellm.failure_callback:
        litellm.failure_callback.append("langfuse")

    from langfuse import Langfuse
    _langfuse = Langfuse(
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_HOST,
    )


class _Noop:
    """Drop-in noop — absorbs any attribute access and call chain."""
    def __getattr__(self, _):
        return lambda *args, **kwargs: self


def new_trace(name: str, user_id: str | None = None, session_id: str | None = None):
    """Return a LangFuse trace, or a _Noop when observability is disabled."""
    if _langfuse:
        return _langfuse.trace(name=name, user_id=user_id, session_id=session_id)
    return _Noop()
