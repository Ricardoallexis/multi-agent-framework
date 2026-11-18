-- CAP-T20 / S12.A1: project-scoped memory. Only additive: a new table, no existing row changes meaning.
-- An entry is one fact, decision or convention of a project, with who wrote it (source_*), how much it is
-- trusted (trust, computed from the source by the services, never taken from the request) and a confidence.
CREATE TABLE IF NOT EXISTS project_memory (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id),
    kind          TEXT NOT NULL CHECK (kind IN ('fact', 'decision', 'convention')),
    title         TEXT NOT NULL,
    body          TEXT NOT NULL,
    confidence    TEXT NOT NULL DEFAULT 'medium' CHECK (confidence IN ('low', 'medium', 'high')),
    source_kind   TEXT NOT NULL,                       -- user | model_output | executor_result (document_trust.SourceKind)
    source_actor  TEXT NOT NULL,                       -- user:ana, agent:codex/gpt, executor:codex_cli ...
    source_ref    TEXT NOT NULL DEFAULT '',            -- the run (run:<id>), task or document that produced it, if any
    trust         TEXT NOT NULL CHECK (trust IN ('trusted', 'untrusted')),
    sha256        TEXT NOT NULL,                       -- hash of title + body, as written
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_project_memory_project ON project_memory(project_id, created_at);
