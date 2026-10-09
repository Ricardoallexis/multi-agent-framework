-- ORQ-3 (contract in ORQ-T22): task context, results, blobs and consumer cursors.
-- Additive only: the three new columns of tasks have defaults and no existing table changes otherwise.
ALTER TABLE tasks ADD COLUMN base_commit TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN context_refs_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE tasks ADD COLUMN constraints_json TEXT NOT NULL DEFAULT '[]';

-- Content-addressed metadata; the bytes live in <data_dir>/blobs/<sha[:2]>/<sha>.
CREATE TABLE IF NOT EXISTS blobs (
    sha256 TEXT PRIMARY KEY CHECK (length(sha256) = 64),
    size INTEGER NOT NULL CHECK (size >= 0),
    media_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- One result per (task, attempt, patch); patch_sha256 is '' when the result has no patch.
CREATE TABLE IF NOT EXISTS task_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    attempt INTEGER NOT NULL,
    patch_sha256 TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL,
    result_json TEXT NOT NULL,
    result_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (task_id, attempt, patch_sha256)
);
CREATE INDEX IF NOT EXISTS idx_task_results_task ON task_results(task_id, attempt);

CREATE TABLE IF NOT EXISTS event_cursors (
    consumer TEXT NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    seq INTEGER NOT NULL DEFAULT 0 CHECK (seq >= 0),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (consumer, scope)
);