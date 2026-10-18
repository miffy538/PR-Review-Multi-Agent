"""Chains Phase 1 + Phase 2 for the one-call flow: POST /reviews {"run": "baseline_a"}."""
from uuid import UUID

from app.db import get_pool
from app.deps import GitHubFactory, LLMFactory
from app.integrations.github_client import PRRef
from app.services.baseline import SYSTEM_BASELINE_A, create_run, run_baseline_a
from app.services.ingest import run_ingestion


async def ingest_then_run(review_id: UUID, ref: PRRef, gh_factory: GitHubFactory,
                          llm_factory: LLMFactory, system: str = SYSTEM_BASELINE_A) -> None:
    await run_ingestion(review_id, ref, gh_factory)
    status = await get_pool().fetchval("SELECT status FROM reviews WHERE id=$1", review_id)
    if status != "ingested":          # ingestion failed; its error is already on the review
        return
    run_id = await create_run(review_id, system)
    await run_baseline_a(run_id, review_id, llm_factory)
