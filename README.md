# AI PR Review Agent

Multi-agent PR review: 4 LangGraph agents (security, quality, tests, docs) + RAG over the repo
and a knowledge base, FastAPI backend, Postgres+pgvector, React dashboard (later).

## Quick start
```bash
cp .env.example .env
docker compose up --build
curl localhost:8000/health        # {"status":"ok"}
curl localhost:8000/health/db     # {"pgvector":true,"missing_tables":[],"ok":true}
```
Run tests without Docker: `cd backend && pip install -r requirements.txt && pytest`

If you change `db/init.sql`: `docker compose down -v && docker compose up --build`

## Phase 1: try it
```bash
# Upgrading from Phase 0 without wiping data:
docker compose exec -T db psql -U prreview -d prreview < db/migrations/001_phase1.sql
# (or wipe and recreate: docker compose down -v && docker compose up --build)

curl -X POST localhost:8000/reviews -H "Content-Type: application/json" \
     -d '{"pr_url":"https://github.com/<owner>/<repo>/pull/<n>"}'      # -> {"id": "...", "status": "queued"}
curl localhost:8000/reviews/<id>          # status queued -> running -> ingested (or failed + error), file summary
curl localhost:8000/reviews/<id>/files    # hunks with line numbers + enclosing functions (with source)
```
Or use the interactive docs at http://localhost:8000/docs. Public PRs work without a token, but GitHub
allows only 60 unauthenticated requests/hour, so set `GITHUB_TOKEN` in `.env` (a fine-grained token with
read access to Contents and Pull requests; needed for private repos). Restart after editing `.env`.

### Webhook (optional)
1. Generate a secret: `python -c "import secrets;print(secrets.token_hex(32))"` and put it in `.env` as `GITHUB_WEBHOOK_SECRET`.
2. Expose localhost, e.g. `npx smee-client --url https://smee.io/<your-channel> --target http://localhost:8000/webhook/github`
   (or ngrok).
3. In the GitHub repo: Settings > Webhooks > Add: payload URL = your smee/ngrok URL (smee forwards to `/webhook/github`),
   content type `application/json`, the same secret, event = "Pull requests".
Signatures are verified (HMAC-SHA256), and repeated deliveries of the same `X-GitHub-Delivery` id are dropped.

## Phase 2: LLM layer + Baseline A

One LLM prompt reviews the whole diff (no RAG, no agents). Every later system must beat this in Phase 9.

```bash
# 1. In .env set:  LLM_PROVIDER=gemini  and  GEMINI_API_KEY=<your key>   (see .env.example), then restart
docker compose up -d --build backend
# 2. Upgrading an existing database (skip if you recreate the volume):
docker compose exec -T db psql -U prreview -d prreview < db/migrations/002_phase2.sql
# 3. Check the key, list usable models, make one real call:
docker compose exec backend python -m scripts.smoke_llm
# 4. Review a PR in one call (ingest + LLM):
curl -X POST localhost:8000/reviews -H "Content-Type: application/json" \
     -d '{"pr_url":"https://github.com/<owner>/<repo>/pull/<n>","run":"baseline_a"}'     # -> {"id": "<review_id>"}
curl localhost:8000/reviews/<review_id>/runs                    # status: queued -> running -> done | failed
curl localhost:8000/reviews/<review_id>/runs/<run_id>           # findings + tokens + cost + validation stats
```
Or ingest first and run later (this also lets you run the same PR repeatedly while tuning the prompt):
`POST /reviews/<review_id>/runs` with body `{"system":"baseline_a"}`.

What to look at in a finished run: `stats.dropped` (hallucinations the validator removed, by reason),
`stats.relocated` (wrong line numbers it fixed), `tokens_*` / `cost_usd` / `latency_ms`, and
`prompt_version` (which prompt file produced it). `agent_events` has one row per LLM call.

Phase 2 files: `app/llm/` (Gemini client, retries, structured output), `app/prompts/` (versioned prompt files),
`app/services/review_input.py` (diff -> prompt), `validation.py` (hallucination guard), `baseline.py` (the run),
`app/schemas/categories.py` (shared category vocabulary), `app/api/runs.py`, `scripts/smoke_llm.py`.

Notes: on Google's free tier, prompts may be used to improve their products, so do not send private code,
and expect low rate limits (a few requests per minute). Model ids change often; if you get "model not found",
run the smoke script and set `GEMINI_MODEL`. To change the prompt, add `baseline_a.v2.md` and bump
`PROMPT_VERSION` in `services/baseline.py` instead of editing v1.

## Tests
`docker compose exec backend pytest` (58 tests; no real GitHub or LLM calls: both are faked. API tests need the db container).

## Layout
- `backend/app/integrations/github_client.py` - PR URL parsing, diff and file fetch, readable errors
- `backend/app/diff/parser.py` - unified diff -> per-file hunks with exact line numbers
- `backend/app/diff/functions.py` - changed lines -> enclosing Python function (ast)
- `backend/app/services/ingest.py` - the background ingestion pipeline
- `backend/app/api/reviews.py`, `runs.py`, `webhook.py` - REST endpoints
- `backend/app/schemas/finding.py` - the Finding contract every agent returns
- `backend/app/graph/state.py` - LangGraph state (graph built in Phase 5)
- `db/init.sql`, `db/migrations/` - schema
- `frontend/` (Phase 7), `eval/` (Phase 9)
