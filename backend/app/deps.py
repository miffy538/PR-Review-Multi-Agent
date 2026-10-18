from collections.abc import Callable

from app.config import settings
from app.integrations.github_client import GitHubClient
from app.llm import LLMClient, build_client

GitHubFactory = Callable[[], GitHubClient]
LLMFactory = Callable[[], LLMClient]


def get_github_factory() -> GitHubFactory:
    """FastAPI dependency. Tests override this to inject a fake GitHub transport."""
    return lambda: GitHubClient(settings.github_token, settings.github_api_url)


def get_llm_factory() -> LLMFactory:
    """FastAPI dependency. Tests override this to inject a fake LLM (no network, no cost)."""
    return build_client
