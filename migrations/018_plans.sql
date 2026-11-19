-- S8.D2: persistent plan proposals and their revision history. Dates are UTC ISO 8601 strings.
CREATE TABLE IF NOT EXISTS plan_proposals (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    planner TEXT NOT NULL,
    goal TEXT NOT NULL,
    proposal_json TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_channel TEXT NOT NULL,
    decided_by TEXT,
    decided_at TEXT,
    decided_channel TEXT
);
CREATE INDEX IF NOT EXISTS idx_plan_proposals_group_status ON plan_proposals(group_id, status);
CREATE TABLE IF NOT EXISTS plan_proposal_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id TEXT NOT NULL REFERENCES plan_proposals(id),
    kind TEXT NOT NULL CHECK (kind IN ('save', 'edit', 'reassign', 'approve', 'reject')),
    actor TEXT NOT NULL,
    channel TEXT NOT NULL,
    at TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    revision INTEGER NOT NULL CHECK (revision >= 1),
    changes_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_plan_proposal_history_proposal ON plan_proposal_history(proposal_id, id);
