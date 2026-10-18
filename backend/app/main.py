from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI

from app.api import reviews, runs, webhook
from app.config import settings
from app.db import close_pool, init_pool

EXPECTED_TABLES = {
    "reviews", "findings", "review_files", "webhook_deliveries",
    "code_chunks", "kb_chunks", "agent_events", "review_runs",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="AI PR Review Agent", version="0.3.0", lifespan=lifespan)
app.include_router(reviews.router)
app.include_router(runs.router)
app.include_router(webhook.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/db")
async def health_db():
    """Confirms DB connectivity, the pgvector extension, and that the schema exists."""
    conn = await asyncpg.connect(settings.database_url)
    try:
        has_vector = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")
    finally:
        await conn.close()
    missing = sorted(EXPECTED_TABLES - {r["table_name"] for r in rows})
    return {"pgvector": has_vector, "missing_tables": missing, "ok": has_vector and not missing}
