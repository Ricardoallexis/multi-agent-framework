-- ORQ-4 A: idempotent, auditable task snapshots. Existing rows keep native defaults.
ALTER TABLE tasks ADD COLUMN lock_policy_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE tasks ADD COLUMN workspace_domain TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN snapshot_epoch_pending INTEGER NOT NULL DEFAULT 0
    CHECK (snapshot_epoch_pending IN (0, 1));

CREATE TABLE IF NOT EXISTS task_snapshots (
    source_key TEXT PRIMARY KEY,
    source_ref TEXT NOT NULL,
    snapshot_sha256 TEXT NOT NULL CHECK (length(snapshot_sha256) = 64),
    actor TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    event_seq INTEGER NOT NULL,
    state TEXT NOT NULL DEFAULT 'applied' CHECK (state IN ('applied', 'rolled_back')),
    rollback_actor TEXT NOT NULL DEFAULT '',
    rolled_back_at TEXT NOT NULL DEFAULT '',
    rollback_event_seq INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_task_snapshots_event_seq ON task_snapshots(event_seq);

-- Keep provenance after rollback. It is a source map, not a second task state store.
CREATE TABLE IF NOT EXISTS task_snapshot_objects (
    source_key TEXT NOT NULL REFERENCES task_snapshots(source_key),
    object_type TEXT NOT NULL CHECK (object_type IN ('group', 'task')),
    object_id TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    source_state TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (source_key, object_type, object_id)
);
CREATE INDEX IF NOT EXISTS idx_task_snapshot_objects_id ON task_snapshot_objects(object_type, object_id);
