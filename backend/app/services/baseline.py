"""Baseline A: ONE LLM prompt reviews the whole diff (no RAG, no agents, no tools).

This is the system the multi-agent design has to beat in Phase 9, so it is kept simple and fair:
same validator, same Finding schema, same model and cost accounting as the later systems.
"""
import logging
import time
from uuid import UUID

from app.config import settings
from app.db import get_pool
from app.deps import LLMFactory
from app.llm.base import LLMError
from app.llm.structured import generate_list
from app.prompts import load_prompt
from app.schemas.llm_output import RawFinding
from app.services.events import log_event
from app.services.review_input import ReviewFile, render_input
from app.services.validation import validate_findings

log = logging.getLogger(__name__)

SYSTEM_BASELINE_A = "baseline_a"
PROMPT_NAME, PROMPT_VERSION = "baseline_a", "v1"

INSERT_FINDING = """
INSERT INTO findings (review_id, run_id, agent, category, severity, file, line_start, line_end,
                      summary, suggestion, confidence, rationale, evidence, agreed_by)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
"""


async def create_run(review_id: UUID, system: str = SYSTEM_BASELINE_A) -> UUID:
    return await get_pool().fetchval(
        "INSERT INTO review_runs (review_id, system) VALUES ($1,$2) RETURNING id", review_id, system)


async def _fail(run_id: UUID, review_id: UUID, msg: str, started: float) -> None:
    await get_pool().execute(
        "UPDATE review_runs SET status='failed', error=$2, latency_ms=$3, finished_at=now() WHERE id=$1",
        run_id, msg[:500], int((time.perf_counter() - started) * 1000))
    await log_event(review_id=review_id, run_id=run_id, agent="baseline", event_type="error",
                    outcome="failed", payload={"error": msg[:500]})


async def run_baseline_a(run_id: UUID, review_id: UUID, factory: LLMFactory) -> None:
    pool = get_pool()
    started = time.perf_counter()
    try:
        await pool.execute("UPDATE review_runs SET status='running' WHERE id=$1", run_id)
        review = await pool.fetchrow("SELECT title FROM reviews WHERE id=$1", review_id)
        rows = await pool.fetch("SELECT * FROM review_files WHERE review_id=$1 ORDER BY path", review_id)
        rendered = render_input(review["title"], [ReviewFile.from_row(r) for r in rows],
                                settings.max_prompt_chars)
        prompt = load_prompt(PROMPT_NAME, PROMPT_VERSION)

        stats: dict = {"files_shown": sorted(rendered.shown), "files_skipped": rendered.skipped_files,
                       "context_truncated": rendered.context_truncated, "prompt_chars": len(rendered.text)}
        tokens_in = tokens_out = 0
        cost = 0.0
        model: str | None = None
        findings = []

        if not rendered.shown:                       # nothing reviewable: do not spend tokens
            stats["note"] = "no reviewable Python changes; LLM not called"
        else:
            async with factory() as llm:
                model = llm.model
                result = await generate_list(llm, system=prompt.text, user=rendered.text,
                                             item_model=RawFinding)
            for resp in result.responses:
                tokens_in += resp.tokens_in
                tokens_out += resp.tokens_out
                cost += resp.cost_usd
                model = resp.model
                await log_event(review_id=review_id, run_id=run_id, agent="baseline",
                                event_type="llm.call", model=resp.model, tokens_in=resp.tokens_in,
                                tokens_out=resp.tokens_out, cost_usd=resp.cost_usd,
                                latency_ms=resp.latency_ms, outcome="ok",
                                payload={"attempts": resp.attempts, "finish_reason": resp.finish_reason,
                                         "prompt": prompt.label})
            vr = validate_findings(result.items[:settings.max_findings * 2], rendered.shown)
            findings = vr.kept[:settings.max_findings]
            stats.update({"raw_findings": len(result.items), "schema_invalid": result.invalid,
                          "dropped": dict(vr.dropped), "relocated": vr.relocated,
                          "repaired_json": result.repaired, "kept": len(findings)})
            await log_event(review_id=review_id, run_id=run_id, agent="validator", event_type="decision",
                            outcome=f"kept {len(findings)} of {len(result.items)}",
                            payload={"dropped": vr.dropped_detail, "relocated": vr.relocated})

        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM findings WHERE run_id=$1", run_id)
            await conn.executemany(INSERT_FINDING, [
                (review_id, run_id, f.agent.value, f.category, f.severity.value, f.file, f.line_start,
                 f.line_end, f.summary, f.suggestion, f.confidence, f.rationale,
                 [e.model_dump() for e in f.evidence], [a.value for a in f.agreed_by])
                for f in findings])
            await conn.execute(
                "UPDATE review_runs SET status='done', model=$2, prompt_version=$3, tokens_in=$4, "
                "tokens_out=$5, cost_usd=$6, latency_ms=$7, stats=$8, error=NULL, finished_at=now() "
                "WHERE id=$1", run_id, model, prompt.label, tokens_in, tokens_out, round(cost, 6),
                int((time.perf_counter() - started) * 1000), stats)
    except Exception as e:  # noqa: BLE001 - record any failure on the run, never crash the worker
        log.exception("baseline run %s failed", run_id)
        msg = str(e) if isinstance(e, LLMError) else f"{type(e).__name__}: {e}"
        await _fail(run_id, review_id, msg, started)
