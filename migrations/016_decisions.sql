-- S13.B2: proposals and human decisions. Dates are UTC ISO 8601 strings.
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    proposed_by TEXT NOT NULL,
    decided_by TEXT,
    status TEXT NOT NULL CHECK (status IN ('proposed','accepted','rejected','superseded')),
    outcome TEXT,
    valid_from TEXT,
    valid_until TEXT,
    superseded_by TEXT REFERENCES decisions(id),
    run_id TEXT,
    step_id TEXT,
    attempt INTEGER,
    task_id TEXT,
    team_id TEXT,
    project TEXT,
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status, valid_from, valid_until);
CREATE INDEX IF NOT EXISTS idx_decisions_run ON decisions(run_id, step_id, attempt);
