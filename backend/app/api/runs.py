from typing import Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from app.db import get_pool
from app.deps import LLMFactory, get_llm_factory
from app.services.baseline import SYSTEM_BASELINE_A, create_run, run_baseline_a

router = APIRouter(prefix="/reviews/{review_id}/runs", tags=["runs"])

RUN_COLUMNS = ("id, system, status, model, prompt_version, tokens_in, tokens_out, cost_usd, "
               "latency_ms, stats, error, created_at, finished_at")


class StartRun(BaseModel):
    system: Literal["baseline_a"] = SYSTEM_BASELINE_A


@router.post("", status_code=202)
async def start_run(review_id: UUID, bg: BackgroundTasks, body: StartRun | None = None,
                    factory: LLMFactory = Depends(get_llm_factory)):
    """Run an LLM system on an already-ingested review. Poll GET .../runs/{run_id}."""
    status = await get_pool().fetchval("SELECT status FROM reviews WHERE id=$1", review_id)
    if status is None:
        raise HTTPException(404, "Review not found")
    if status != "ingested":
        raise HTTPException(409, f"Review is '{status}'; it must be 'ingested' before running a system.")
    system = (body or StartRun()).system
    run_id = await create_run(review_id, system)
    bg.add_task(run_baseline_a, run_id, review_id, factory)
    return {"run_id": run_id, "status": "queued", "system": system}


@router.get("")
async def list_runs(review_id: UUID):
    rows = await get_pool().fetch(
        f"SELECT {RUN_COLUMNS} FROM review_runs WHERE review_id=$1 ORDER BY created_at", review_id)
    return [dict(r) for r in rows]


@router.get("/{run_id}")
async def get_run(review_id: UUID, run_id: UUID):
    pool = get_pool()
    run = await pool.fetchrow(
        f"SELECT {RUN_COLUMNS} FROM review_runs WHERE id=$1 AND review_id=$2", run_id, review_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    findings = await pool.fetch(
        "SELECT id, agent, category, severity, file, line_start, line_end, summary, suggestion, "
        "confidence, rationale, evidence, agreed_by, status FROM findings WHERE run_id=$1 "
        "ORDER BY CASE severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, "
        "confidence DESC", run_id)
    return {**dict(run), "findings": [dict(f) for f in findings]}
