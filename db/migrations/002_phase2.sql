-- Phase 2 changes. Safe to run on an existing Phase 1 database (idempotent):
--   docker compose exec -T db psql -U prreview -d prreview < db/migrations/002_phase2.sql
--
-- A "run" = one system (baseline_a, later baseline_b / multi_agent ...) reviewing one PR.
-- Keeping runs separate lets Phase 9 compare systems on the exact same PR.
CREATE TABLE IF NOT EXISTS review_runs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id      UUID NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    system         TEXT NOT NULL,                       -- baseline_a | baseline_b | multi_agent ...
    status         TEXT NOT NULL DEFAULT 'queued',      -- queued|running|done|failed
    model          TEXT,
    prompt_version TEXT,
    tokens_in      INT NOT NULL DEFAULT 0,
    tokens_out     INT NOT NULL DEFAULT 0,
    cost_usd       NUMERIC(10,6) NOT NULL DEFAULT 0,    -- estimate (see LLM_PRICE_* in .env)
    latency_ms     INT,
    stats          JSONB NOT NULL DEFAULT '{}',         -- kept/dropped counts, files shown, ...
    error          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS review_runs_review_idx ON review_runs (review_id, created_at);

ALTER TABLE findings     ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES review_runs(id) ON DELETE CASCADE;
ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS run_id UUID REFERENCES review_runs(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS findings_run_idx ON findings (run_id);
CREATE INDEX IF NOT EXISTS agent_events_run_idx ON agent_events (run_id);
