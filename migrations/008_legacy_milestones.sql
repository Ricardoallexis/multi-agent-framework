-- ORQ-4 B: legacy release milestones, kept separate from native task acceptance.
-- Snapshot receipts imported before B retain the version-1 normalization contract.
ALTER TABLE task_snapshots ADD COLUMN contract_version INTEGER NOT NULL DEFAULT 1
    CHECK (contract_version IN (1, 2));

ALTER TABLE task_groups ADD COLUMN legacy_release_mode TEXT
    CHECK (legacy_release_mode IS NULL OR legacy_release_mode IN ('terminada', 'probada', 'integrada'));

CREATE TABLE IF NOT EXISTS legacy_release_dependencies (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    depends_on_id TEXT NOT NULL REFERENCES tasks(id),
    source_ref TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on_id),
    CHECK (task_id <> depends_on_id)
);
CREATE INDEX IF NOT EXISTS idx_legacy_release_dependencies_depends_on
    ON legacy_release_dependencies(depends_on_id);

CREATE TABLE IF NOT EXISTS legacy_release_facts (
    task_id TEXT PRIMARY KEY REFERENCES tasks(id),
    delivery_ref TEXT NOT NULL,
    delivery_sha256 TEXT NOT NULL CHECK (length(delivery_sha256) = 64),
    has_patch INTEGER NOT NULL CHECK (has_patch IN (0, 1)),
    patchset_sha256 TEXT NOT NULL DEFAULT '',
    test_verdict TEXT CHECK (test_verdict IS NULL OR test_verdict IN ('passed', 'failed')),
    test_patchset_sha256 TEXT NOT NULL DEFAULT '',
    test_ref TEXT NOT NULL DEFAULT '',
    integration_commit TEXT NOT NULL DEFAULT '',
    integration_ref TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK ((has_patch = 1 AND length(patchset_sha256) = 64) OR
           (has_patch = 0 AND patchset_sha256 = '')),
    CHECK ((test_verdict IS NULL AND test_patchset_sha256 = '' AND test_ref = '') OR
           (test_verdict IS NOT NULL AND length(test_patchset_sha256) = 64 AND test_ref <> '')),
    CHECK ((integration_commit = '' AND integration_ref = '') OR
           (integration_commit <> '' AND integration_ref <> ''))
);
