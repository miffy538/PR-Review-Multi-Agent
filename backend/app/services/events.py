"""agent_events writer: one row per LLM call / decision / error (feeds the trace + cost panel)."""
import logging
from uuid import UUID

from app.db import get_pool

log = logging.getLogger(__name__)


async def log_event(*, review_id: UUID, run_id: UUID | None, agent: str, event_type: str,
                    model: str | None = None, tokens_in: int | None = None,
                    tokens_out: int | None = None, cost_usd: float | None = None,
                    latency_ms: int | None = None, outcome: str | None = None,
                    payload: dict | None = None) -> None:
    """Observability must never break a review, so failures here are logged and swallowed."""
    try:
        await get_pool().execute(
            "INSERT INTO agent_events (review_id, run_id, agent, event_type, model, tokens_in, "
            "tokens_out, cost_usd, latency_ms, outcome, payload) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)",
            review_id, run_id, agent, event_type, model, tokens_in, tokens_out, cost_usd,
            latency_ms, outcome, payload)
    except Exception:  # noqa: BLE001
        log.exception("failed to write agent_event")
