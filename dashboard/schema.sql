-- Postgres schema for the deployed dashboard (Neon). Loaded by pipeline/load_db.py.
-- Holds processed outputs only; raw CSV and API keys are never stored here.

CREATE TABLE IF NOT EXISTS run_info (
    key   TEXT PRIMARY KEY,
    value JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS overall_metrics (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
    issue_id        TEXT PRIMARY KEY,
    rank            INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT,
    topic           TEXT NOT NULL,
    complaint_count INTEGER NOT NULL,
    severity_sum    INTEGER NOT NULL,
    mean_severity   TEXT NOT NULL,      -- exact 6-dp string from ranking.csv
    priority_score  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    review_id        TEXT PRIMARY KEY,
    review_text      TEXT NOT NULL,
    review_rating    INTEGER,
    review_timestamp TEXT,
    app_version      TEXT,
    topic            TEXT NOT NULL,
    intent           TEXT NOT NULL,
    severity         INTEGER NOT NULL,
    sentiment        REAL NOT NULL,
    evidence_quote   TEXT NOT NULL,
    needs_review     BOOLEAN NOT NULL,
    cache_source_id  TEXT,
    label_config     TEXT NOT NULL,
    issue_id         TEXT REFERENCES issues(issue_id)
);
CREATE INDEX IF NOT EXISTS reviews_issue_idx ON reviews(issue_id, severity DESC);

CREATE TABLE IF NOT EXISTS claims (
    claim_id TEXT PRIMARY KEY,
    issue_id TEXT NOT NULL REFERENCES issues(issue_id),
    metric   TEXT NOT NULL,
    value    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recommendations (
    position  INTEGER PRIMARY KEY,
    title     TEXT NOT NULL,
    rationale TEXT NOT NULL,
    claim_ids JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS monthly (
    month           TEXT PRIMARY KEY,
    classified      INTEGER NOT NULL,
    complaints      INTEGER NOT NULL
);
