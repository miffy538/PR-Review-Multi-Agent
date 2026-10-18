-- Phase 1 changes. Safe to run on an existing Phase 0 database (idempotent):
--   docker compose exec -T db psql -U prreview -d prreview < db/migrations/001_phase1.sql
ALTER TABLE reviews ADD COLUMN IF NOT EXISTS title TEXT;

CREATE TABLE IF NOT EXISTS review_files (
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
CREATE INDEX IF NOT EXISTS review_files_review_idx ON review_files (review_id);
