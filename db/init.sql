-- Runs once, on first start of an empty volume.
-- To re-run after changes: docker compose down -v && docker compose up --build
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;  -- gen_random_uuid()

-- ---------- Truth: reviews and findings ----------
CREATE TABLE reviews (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo               TEXT NOT NULL,              -- "owner/name"
    pr_number          INT  NOT NULL,
    pr_url             TEXT,
    head_sha           TEXT,
    title              TEXT,
    status             TEXT NOT NULL DEFAULT 'queued',   -- queued|running|ingested|done|failed
    exec_mode          TEXT NOT NULL DEFAULT 'routed',   -- sequential|parallel|routed
    overall_confidence NUMERIC(4,3),
    gate_decision      TEXT,                        -- auto_post|needs_human|posted|rejected
    error              TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- A run = one system reviewing one PR (Phase 2). Findings and events hang off a run.
CREATE TABLE review_runs (
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
CREATE INDEX review_runs_review_idx ON review_runs (review_id, created_at);

CREATE TABLE findings (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id   UUID NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    run_id      UUID REFERENCES review_runs(id) ON DELETE CASCADE,
    agent       TEXT NOT NULL,        -- security|quality|tests|docs
    category    TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('Critical','High','Medium','Low')),
    file        TEXT NOT NULL,
    line_start  INT  NOT NULL,
    line_end    INT  NOT NULL,
    summary     TEXT NOT NULL,
    suggestion  TEXT,
    confidence  NUMERIC(4,3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    rationale   TEXT,
    evidence    JSONB NOT NULL DEFAULT '[]',
    agreed_by   TEXT[] NOT NULL DEFAULT '{}',   -- other agents that raised the same issue
    status      TEXT NOT NULL DEFAULT 'pending', -- pending|approved|rejected|posted
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX findings_review_idx ON findings (review_id);
CREATE INDEX findings_run_idx ON findings (run_id);

-- One row per changed file in a PR: parsed hunks + enclosing functions (Phase 1)
CREATE TABLE review_files (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    review_id         UUID NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    path              TEXT NOT NULL,
    old_path          TEXT,
    status            TEXT NOT NULL,            -- added|modified|removed|renamed
    language          TEXT,
    reviewable        BOOLEAN NOT NULL DEFAULT false,
    additions         INT NOT NULL DEFAULT 0,
    deletions         INT NOT NULL DEFAULT 0,
    hunks             JSONB NOT NULL DEFAULT '[]',
    changed_functions JSONB NOT NULL DEFAULT '[]',
    parse_note        TEXT,
    UNIQUE (review_id, path)
);
CREATE INDEX review_files_review_idx ON review_files (review_id);

-- Idempotency for GitHub webhooks (X-GitHub-Delivery)
CREATE TABLE webhook_deliveries (
    delivery_id TEXT PRIMARY KEY,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- Memory: RAG indexes (embedding dim 384 = bge-small) ----------
CREATE TABLE code_chunks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    repo         TEXT NOT NULL,
    path         TEXT NOT NULL,
    symbol       TEXT,                    -- function/class name
    chunk_index  INT  NOT NULL,
    start_line   INT,
    end_line     INT,
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,           -- skip re-embedding unchanged chunks
    embedding    vector(384) NOT NULL,
    content_tsv  tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (repo, path, chunk_index)
);
CREATE INDEX code_chunks_emb_idx ON code_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX code_chunks_fts_idx ON code_chunks USING gin (content_tsv);

CREATE TABLE kb_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source      TEXT NOT NULL,            -- cwe|owasp|pep8|pep257|testing|...
    doc_id      TEXT NOT NULL,            -- e.g. "CWE-89"
    title       TEXT,
    content     TEXT NOT NULL,
    embedding   vector(384) NOT NULL,
    content_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    UNIQUE (source, doc_id)
);
CREATE INDEX kb_chunks_emb_idx ON kb_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX kb_chunks_fts_idx ON kb_chunks USING gin (content_tsv);

-- ---------- Time: agent events (trace + cost) ----------
CREATE TABLE agent_events (
    id         BIGSERIAL PRIMARY KEY,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    review_id  UUID REFERENCES reviews(id) ON DELETE CASCADE,
    run_id     UUID REFERENCES review_runs(id) ON DELETE CASCADE,
    agent      TEXT NOT NULL,             -- security|quality|tests|docs|router|aggregator|...
    event_type TEXT NOT NULL,             -- span.start|span.end|llm.call|tool.call|decision|error
    model      TEXT,
    tokens_in  INT,
    tokens_out INT,
    cost_usd   NUMERIC(10,6),
    latency_ms INT,
    outcome    TEXT,
    payload    JSONB
);
CREATE INDEX agent_events_review_ts_idx ON agent_events (review_id, ts);
CREATE INDEX agent_events_run_idx ON agent_events (run_id);
