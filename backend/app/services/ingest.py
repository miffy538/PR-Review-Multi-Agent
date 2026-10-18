"""Phase 1 pipeline: fetch PR -> parse diff -> map changed functions -> store."""
import asyncio
import logging
from uuid import UUID

from app.config import settings
from app.db import get_pool
from app.deps import GitHubFactory
from app.diff.functions import map_changed_functions
from app.diff.parser import ParsedFile, parse_diff
from app.integrations.github_client import GitHubClient, GitHubError, PRRef

log = logging.getLogger(__name__)

INSERT_FILE = """
INSERT INTO review_files (review_id, path, old_path, status, language, reviewable,
                          additions, deletions, hunks, changed_functions, parse_note)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
"""


async def create_review(ref: PRRef, pr_url: str, exec_mode: str = "routed") -> UUID:
    return await get_pool().fetchval(
        "INSERT INTO reviews (repo, pr_number, pr_url, exec_mode) VALUES ($1,$2,$3,$4) RETURNING id",
        ref.full_name, ref.number, pr_url, exec_mode)


async def _file_row(gh: GitHubClient, ref: PRRef, sha: str, pf: ParsedFile,
                    fetch: bool, sem: asyncio.Semaphore, review_id: UUID) -> tuple:
    functions: list[dict] = []
    note: str | None = None
    if pf.reviewable and pf.touch_lines:
        if not fetch:
            note = f"source not fetched: more than {settings.max_source_fetches} reviewable files"
        else:
            async with sem:
                source = await gh.get_file(ref, pf.path, sha)
            if source is None:
                note = "file not found at head commit"
            elif len(source.encode()) > settings.max_file_bytes:
                note = "file too large; function mapping skipped"
            else:
                functions, note = map_changed_functions(source, pf.touch_lines)
    return (review_id, pf.path, pf.old_path, pf.status, pf.language, pf.reviewable,
            pf.additions, pf.deletions, pf.hunks, functions, note)


async def run_ingestion(review_id: UUID, ref: PRRef, factory: GitHubFactory) -> None:
    pool = get_pool()
    try:
        await pool.execute("UPDATE reviews SET status='running', updated_at=now() WHERE id=$1", review_id)
        async with factory() as gh:
            pr = await gh.get_pr(ref)
            head_sha = pr["head"]["sha"]
            await pool.execute("UPDATE reviews SET head_sha=$2, title=$3, updated_at=now() WHERE id=$1",
                               review_id, head_sha, pr.get("title"))
            parsed = parse_diff(await gh.get_diff(ref))

            sem = asyncio.Semaphore(5)
            budget = settings.max_source_fetches
            tasks, used = [], 0
            for pf in parsed:
                wants = pf.reviewable and bool(pf.touch_lines)
                fetch = wants and used < budget
                used += 1 if wants else 0
                tasks.append(_file_row(gh, ref, head_sha, pf, fetch, sem, review_id))
            rows = await asyncio.gather(*tasks)

        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM review_files WHERE review_id=$1", review_id)
            await conn.executemany(INSERT_FILE, rows)
            await conn.execute(
                "UPDATE reviews SET status='ingested', error=NULL, updated_at=now() WHERE id=$1", review_id)
    except Exception as e:  # noqa: BLE001 - record any failure on the review
        log.exception("ingestion failed for review %s", review_id)
        msg = str(e) if isinstance(e, GitHubError) else f"{type(e).__name__}: {e}"
        await pool.execute("UPDATE reviews SET status='failed', error=$2, updated_at=now() WHERE id=$1",
                           review_id, msg[:500])
