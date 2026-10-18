from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://prreview:prreview@localhost:5432/prreview"

    # --- LLM (Phase 2). Gemini is the supported provider for now. ---
    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    # Model ids churn fast (older ones get deprecated). "gemini-flash-latest" always points at the
    # newest Flash. For benchmark runs (Phase 9) pin an exact id so results are reproducible.
    gemini_model: str = "gemini-flash-latest"
    gemini_api_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_thinking_level: str = ""     # "", low, medium or high. Empty = model default.
    llm_temperature: float | None = None  # None = provider default (recommended for Gemini 3)
    llm_timeout_s: float = 90.0
    llm_max_retries: int = 3            # retries after the first attempt (429/5xx/timeouts only)
    llm_max_backoff_s: float = 30.0     # never sleep longer than this between retries
    llm_max_output_tokens: int = 16384  # includes hidden "thinking" tokens on Gemini 3
    # Cost ESTIMATE in USD per 1M tokens. The free tier bills $0 but we still log an estimate so
    # cost-per-PR comparisons in Phase 9 are meaningful. Update from Google's pricing page.
    llm_price_in_per_m: float = 0.30
    llm_price_out_per_m: float = 2.50

    # Prompt-building limits (keep latency and cost bounded)
    max_prompt_chars: int = 60_000
    max_findings: int = 15

    # Kept for later phases / other providers (unused in Phase 2)
    anthropic_api_key: str = ""
    openai_api_key: str = ""

    embedding_model: str = "BAAI/bge-small-en-v1.5"
    embedding_dim: int = 384

    github_token: str = ""
    github_webhook_secret: str = ""
    github_api_url: str = "https://api.github.com"

    # Ingestion limits (keep LLM cost and latency bounded)
    max_source_fetches: int = 30        # max files whose full source we fetch per PR
    max_file_bytes: int = 200_000       # skip function mapping for bigger files


settings = Settings()
