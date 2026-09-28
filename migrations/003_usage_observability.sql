-- Preserve existing telemetry while making provider usage nullable and adding
-- the execution identity needed by independent usage recording.
CREATE TABLE telemetry_v3 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    attempt INTEGER NOT NULL DEFAULT 1,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    model_digest TEXT NOT NULL DEFAULT '',
    prompt_id TEXT NOT NULL,
    prompt_version INTEGER NOT NULL,
    prompt_sha256 TEXT NOT NULL DEFAULT '',
    brand_version INTEGER,
    tokens_in INTEGER,
    tokens_out INTEGER,
    num_ctx INTEGER,
    latency_ms INTEGER,
    duration_ms INTEGER,
    estimated_cost_usd REAL,
    success INTEGER NOT NULL,
    error_type TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

INSERT INTO telemetry_v3(
    id,run_id,step_id,agent_id,attempt,provider,model,model_digest,prompt_id,
    prompt_version,prompt_sha256,brand_version,tokens_in,tokens_out,num_ctx,
    latency_ms,duration_ms,estimated_cost_usd,success,error_type,created_at
)
SELECT
    id,run_id,step_id,'',1,provider,model,model_digest,prompt_id,prompt_version,
    prompt_sha256,brand_version,tokens_in,tokens_out,num_ctx,latency_ms,latency_ms,
    estimated_cost_usd,success,error_type,created_at
FROM telemetry;

DROP TABLE telemetry;
ALTER TABLE telemetry_v3 RENAME TO telemetry;
CREATE INDEX IF NOT EXISTS idx_telemetry_run ON telemetry(run_id, id);
