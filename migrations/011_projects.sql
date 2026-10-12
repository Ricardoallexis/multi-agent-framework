-- PRY-T01 / PRY-T02: projects over the existing tables. Only additive: no existing row changes meaning.
-- The projects table already exists (001) and every run points to it by name; it is extended, not replaced.
ALTER TABLE projects ADD COLUMN title TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN visibility TEXT NOT NULL DEFAULT 'normal'
    CHECK (visibility IN ('normal', 'private'));
ALTER TABLE projects ADD COLUMN archived_at TEXT;                    -- NULL = active
ALTER TABLE projects ADD COLUMN created_by TEXT NOT NULL DEFAULT '';
ALTER TABLE projects ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
UPDATE projects SET title = name WHERE title = '';

ALTER TABLE task_groups ADD COLUMN project_id TEXT REFERENCES projects(id);  -- NULL = no project
ALTER TABLE task_groups ADD COLUMN position INTEGER CHECK (position IS NULL OR position >= 1);
ALTER TABLE task_groups ADD COLUMN phases_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE task_groups ADD COLUMN planned_start TEXT
    CHECK (planned_start IS NULL OR planned_start GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]');
ALTER TABLE task_groups ADD COLUMN planned_end TEXT
    CHECK (planned_end IS NULL OR planned_end GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]');
-- Amendment 1 (P-518): a group is a project stage or the project's activities; the behaviour is PRY-T09.
ALTER TABLE task_groups ADD COLUMN kind TEXT NOT NULL DEFAULT 'stage' CHECK (kind IN ('stage', 'activities'));
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_groups_project_position
    ON task_groups(project_id, position) WHERE project_id IS NOT NULL;

ALTER TABLE tasks ADD COLUMN phase TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks ADD COLUMN due_at TEXT
    CHECK (due_at IS NULL OR due_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]');
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(due_at) WHERE due_at IS NOT NULL;
-- Amendment 1 (P-518): a task is planned work or an activity (a chat linked to the project); the behaviour is PRY-T09.
ALTER TABLE tasks ADD COLUMN kind TEXT NOT NULL DEFAULT 'task' CHECK (kind IN ('task', 'activity'));

-- Optional link from a run (and its telemetry) to a task. No foreign key: bundle runs live in another database.
ALTER TABLE runs ADD COLUMN task_id TEXT;
CREATE INDEX IF NOT EXISTS idx_runs_task ON runs(task_id) WHERE task_id IS NOT NULL;
ALTER TABLE telemetry ADD COLUMN task_id TEXT;       -- copy of runs.task_id written with the telemetry row
CREATE INDEX IF NOT EXISTS idx_telemetry_task ON telemetry(task_id, id) WHERE task_id IS NOT NULL;

-- Chats of other applications ("<source>:<native_id>", never "framework:") linked to a task.
CREATE TABLE IF NOT EXISTS task_session_links (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    session_id TEXT NOT NULL,
    linked_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, session_id)
);
CREATE INDEX IF NOT EXISTS idx_task_session_links_session ON task_session_links(session_id);
