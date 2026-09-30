-- ORQ-1 (contract in ORQ-T02): task groups, tasks, dependencies and the team
-- event log. Additive only: existing tables are not touched.
-- "blocked" and "ready" are computed from task_deps and never stored in tasks.state.
-- gate_json, execution_json and exclusive_resources_json are reserved for later stages.
CREATE TABLE IF NOT EXISTS task_groups (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authorized INTEGER NOT NULL DEFAULT 0,
    default_requires TEXT NOT NULL DEFAULT 'accepted',
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL REFERENCES task_groups(id),
    title TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open',
    assignee TEXT NOT NULL DEFAULT '',
    preferred TEXT NOT NULL DEFAULT '',
    requirements_json TEXT NOT NULL DEFAULT '[]',
    file_scope_json TEXT NOT NULL DEFAULT '[]',
    exclusive_resources_json TEXT NOT NULL DEFAULT '[]',
    gate_json TEXT NOT NULL DEFAULT '[]',
    execution_json TEXT NOT NULL DEFAULT '{}',
    attempt INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_group_state ON tasks(group_id, state);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee, state);

CREATE TABLE IF NOT EXISTS task_deps (
    task_id TEXT NOT NULL REFERENCES tasks(id),
    depends_on_id TEXT NOT NULL REFERENCES tasks(id),
    requires TEXT NOT NULL DEFAULT 'accepted',
    created_at TEXT NOT NULL,
    PRIMARY KEY (task_id, depends_on_id),
    CHECK (task_id <> depends_on_id)
);
CREATE INDEX IF NOT EXISTS idx_task_deps_depends_on ON task_deps(depends_on_id);

-- seq: one team-wide sequence, strictly increasing and never reused (AUTOINCREMENT).
CREATE TABLE IF NOT EXISTS team_events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL,
    group_id TEXT NOT NULL DEFAULT '',
    task_id TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL,
    cause TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_team_events_task ON team_events(task_id, seq);
CREATE INDEX IF NOT EXISTS idx_team_events_group ON team_events(group_id, seq);
