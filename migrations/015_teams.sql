-- S6.A3 / EQP-T03: versioned team definitions with human approval. Dates are UTC ISO 8601 strings.
CREATE TABLE IF NOT EXISTS team_versions (
    id               TEXT PRIMARY KEY,
    bundle           TEXT NOT NULL,
    team_id          TEXT NOT NULL,
    version          INTEGER NOT NULL CHECK (version >= 1),
    team_json        TEXT NOT NULL,
    sha256           TEXT NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('draft', 'proposed', 'approved', 'rejected')),
    created_by       TEXT NOT NULL,
    created_channel  TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    proposed_by      TEXT,
    proposed_at      TEXT,
    proposed_channel TEXT,
    decided_by       TEXT,
    decided_at       TEXT,
    decided_channel  TEXT,
    note             TEXT NOT NULL DEFAULT '',
    approved_seq     INTEGER,
    UNIQUE (bundle, team_id, version)
);
CREATE INDEX IF NOT EXISTS idx_team_versions_bundle_version ON team_versions(bundle, version);
CREATE INDEX IF NOT EXISTS idx_team_versions_bundle_approved ON team_versions(bundle, status, approved_seq);
