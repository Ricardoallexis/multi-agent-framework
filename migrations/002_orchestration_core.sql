ALTER TABLE runs ADD COLUMN brand_profile_id TEXT NOT NULL DEFAULT '';
ALTER TABLE runs ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'auto';
ALTER TABLE runs ADD COLUMN research_mode TEXT NOT NULL DEFAULT 'none';
ALTER TABLE runs ADD COLUMN pipeline_mode TEXT NOT NULL DEFAULT 'quick';
ALTER TABLE runs ADD COLUMN step_modes_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE runs ADD COLUMN request_fingerprint TEXT NOT NULL DEFAULT '';
ALTER TABLE runs ADD COLUMN waiting_reason TEXT NOT NULL DEFAULT '';
ALTER TABLE runs ADD COLUMN waiting_step TEXT NOT NULL DEFAULT '';
ALTER TABLE runs ADD COLUMN waiting_attempt INTEGER;
ALTER TABLE runs ADD COLUMN worker_id TEXT NOT NULL DEFAULT '';
ALTER TABLE runs ADD COLUMN started_at TEXT NOT NULL DEFAULT '';
ALTER TABLE runs ADD COLUMN heartbeat_at TEXT NOT NULL DEFAULT '';
ALTER TABLE runs ADD COLUMN active_seconds REAL NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN budget_exhausted INTEGER NOT NULL DEFAULT 0;
ALTER TABLE runs ADD COLUMN successful_llm_calls INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_runs_status_updated ON runs(status,updated_at);
CREATE INDEX IF NOT EXISTS idx_runs_project_status ON runs(project_id,status);

CREATE TABLE IF NOT EXISTS human_step_requests (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id),
    step_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    prompt_id TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    prompt_sha256 TEXT NOT NULL,
    prompt_base TEXT NOT NULL,
    expected_contract TEXT NOT NULL,
    context_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT '',
    UNIQUE(run_id,step_id,attempt)
);

CREATE TABLE IF NOT EXISTS human_step_submissions (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL REFERENCES human_step_requests(id),
    run_id TEXT NOT NULL REFERENCES runs(id),
    step_id TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    provenance TEXT NOT NULL DEFAULT 'human_reported',
    prompt_used TEXT NOT NULL DEFAULT '',
    raw_response TEXT NOT NULL,
    normalized_json TEXT NOT NULL DEFAULT '{}',
    validation_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_human_submissions_run ON human_step_submissions(run_id,step_id,attempt);

CREATE TABLE IF NOT EXISTS file_blobs (
    id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    internal_path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    bytes INTEGER NOT NULL,
    original_filename TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asset_references (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    blob_id TEXT NOT NULL REFERENCES file_blobs(id),
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    rejection_reason TEXT NOT NULL DEFAULT '',
    prompt_id TEXT NOT NULL DEFAULT '',
    prompt_version INTEGER,
    model_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    step_id TEXT NOT NULL DEFAULT '',
    attempt INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asset_refs_project ON asset_references(project_id,created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_asset_refs_project_blob ON asset_references(project_id,blob_id);

ALTER TABLE publications ADD COLUMN status TEXT NOT NULL DEFAULT 'published';

-- Backfill previous architecture assets into the current architecture physical-blob/logical-reference model.
INSERT OR IGNORE INTO file_blobs(id,sha256,internal_path,mime_type,bytes,original_filename,created_at)
SELECT id,sha256,internal_path,mime_type,bytes,original_filename,created_at FROM assets;

INSERT OR IGNORE INTO asset_references(
    id,project_id,blob_id,source,status,title,notes,tags_json,rejection_reason,prompt_id,prompt_version,model_id,run_id,step_id,attempt,created_at
)
SELECT id,project_id,id,source,status,title,notes,tags_json,rejection_reason,prompt_id,prompt_version,model_id,'','',NULL,created_at FROM assets;
