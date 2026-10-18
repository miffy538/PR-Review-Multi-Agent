from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_pool
from app.deps import GitHubFactory, LLMFactory, get_github_factory, get_llm_factory
from app.integrations.github_client import parse_pr_url
from app.services.ingest import create_review, run_ingestion
from app.services.pipeline import ingest_then_run

router = APIRouter(prefix="/reviews", tags=["reviews"])


class CreateReview(BaseModel):
    pr_url: str
    exec_mode: Literal["sequential", "parallel", "routed"] = "routed"
    # Phase 2: also run an LLM system once ingestion finishes. None = ingest only (Phase 1 behaviour).
    run: Literal["baseline_a"] | None = None


@router.post("", status_code=202)
async def start_review(body: CreateReview, bg: BackgroundTasks,
                       factory: GitHubFactory = Depends(get_github_factory),
                       llm_factory: LLMFactory = Depends(get_llm_factory)):
    try:
        ref = parse_pr_url(body.pr_url)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    review_id = await create_review(ref, body.pr_url, body.exec_mode)
    if body.run:
        bg.add_task(ingest_then_run, review_id, ref, factory, llm_factory, body.run)
    else:
        bg.add_task(run_ingestion, review_id, ref, factory)
    return {"id": review_id, "status": "queued"}


@router.get("")
async def list_reviews(limit: int = 20):
    rows = await get_pool().fetch(
        "SELECT id, repo, pr_number, title, status, created_at FROM reviews "
        "ORDER BY created_at DESC LIMIT $1", min(max(limit, 1), 100))
    return [dict(r) for r in rows]


@router.get("/{review_id}")
async def get_review(review_id: UUID):
    pool = get_pool()
    review = await pool.fetchrow("SELECT * FROM reviews WHERE id=$1", review_id)
    if review is None:
        raise HTTPException(404, "Review not found")
    files = await pool.fetch(
        "SELECT path, status, language, reviewable, additions, deletions, parse_note, "
        "jsonb_array_length(changed_functions) AS n_functions "
        "FROM review_files WHERE review_id=$1 ORDER BY path", review_id)
    files = [dict(f) for f in files]
    return {
        **dict(review),
        "summary": {
            "files": len(files),
            "reviewable_files": sum(f["reviewable"] for f in files),
            "changed_functions": sum(f["n_functions"] for f in files),
        },
        "files": files,
    }


@router.get("/{review_id}/files")
async def get_review_files(review_id: UUID):
    """Full detail: hunks and enclosing functions (with source) per file."""
    pool = get_pool()
    if await pool.fetchval("SELECT 1 FROM reviews WHERE id=$1", review_id) is None:
        raise HTTPException(404, "Review not found")
    rows = await pool.fetch("SELECT * FROM review_files WHERE review_id=$1 ORDER BY path", review_id)
    return [dict(r) for r in rows]
