"""Provider-neutral LLM types. Agents talk to `LLMClient`, never to a vendor SDK."""
from dataclasses import dataclass
from typing import Protocol

from app.config import settings


class LLMError(Exception):
    """Any LLM failure. The message is safe to show to the user (never contains the API key)."""

    def __init__(self, message: str, *, retryable: bool = False, attempts: int = 1,
                 status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.attempts = attempts
        self.status = status
        self.retry_after = retry_after


class LLMConfigError(LLMError):
    """Missing key / unsupported provider."""


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int
    tokens_out: int          # includes hidden "thinking" tokens: they are billed as output
    latency_ms: int
    cost_usd: float          # estimate, see settings.llm_price_*
    attempts: int = 1
    finish_reason: str | None = None


class LLMClient(Protocol):
    model: str

    async def generate(self, *, system: str, user: str, json_mode: bool = True) -> LLMResponse: ...
    async def __aenter__(self) -> "LLMClient": ...
    async def __aexit__(self, *exc) -> None: ...


def estimate_cost(tokens_in: int, tokens_out: int) -> float:
    return round((tokens_in * settings.llm_price_in_per_m
                  + tokens_out * settings.llm_price_out_per_m) / 1_000_000, 6)
