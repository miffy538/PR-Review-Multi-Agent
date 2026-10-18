from app.config import settings
from app.llm.base import LLMClient, LLMConfigError, LLMError, LLMResponse


def build_client() -> LLMClient:
    """Create the configured LLM client. Used as the default LLM factory (see app/deps.py)."""
    if settings.llm_provider.lower() == "gemini":
        from app.llm.gemini import GeminiClient
        return GeminiClient()
    raise LLMConfigError(
        f"LLM_PROVIDER='{settings.llm_provider}' is not supported yet. Set LLM_PROVIDER=gemini in .env.")


__all__ = ["LLMClient", "LLMConfigError", "LLMError", "LLMResponse", "build_client"]
