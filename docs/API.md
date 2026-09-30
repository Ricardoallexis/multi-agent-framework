# Python and HTTP API

Multi-Agent Framework exposes one application-services facade for Python and an
HTTP API built on that same facade. Both interfaces share workflow validation,
run operations, task operations, and structured errors.

## Python facade

Create the facade and inspect available workflows:

```python
from multiagent.services import ApplicationServices

services = ApplicationServices.create()
workflows = services.definitions.list_workflows()
workflow = services.definitions.get_workflow("social_post")
```

`ApplicationServices.create()` accepts optional `Settings`, trusted bundle
registrations, and the same system options as `build_system`. The facade exposes:

| Service | Operations |
| --- | --- |
| `services.definitions` | `list_workflows`, `get_workflow`, `list_agents`, `list_bundles`, `validate_bundle` |
| `services.runs` | `create`, `get`, `list`, `approve`, `request_changes`, `regenerate`, `reject`, `cancel`, `human_next`, `human_submit`, `artifacts`, `events`, `usage` |
| `services.tasks` | `create_group`, `update_group`, `get_group`, `list_groups`, `create_task`, `get_task`, `list_tasks`, `ready_tasks`, `add_dependency`, `remove_dependency`, `transition`, `events` |
| `services.gates` | `set_task_gate`, `set_group_gate`, `get_gate`, `review`, `run_gate`, `abort_gate`, `results`, `project_commands` |

For example, create a built-in social-post run:

```python
from multiagent.contracts import SocialPostRequest
from multiagent.services import ApplicationServices, ServiceError

services = ApplicationServices.create()
try:
    run = services.runs.create(
        SocialPostRequest(
            project_name="API example",
            objective="Explain a useful technology concept",
            topic="workflow automation",
        )
    )
except ServiceError as error:
    print(error.status, error.to_payload())
else:
    print(run["id"], run["status"])
```

Runs are queued for the system worker. A Python host that needs runs to execute
must start and stop `services.system.worker` as part of its own lifecycle. Bundle
runtimes manage their own workers when `worker_enabled` is enabled; call
`services.runs.close()` when shutting the facade down to stop those workers.

`ServiceError` provides a stable `code`, human-readable `message`, HTTP-like
`status`, and JSON-ready `to_payload()` with `code`, `message`, and `details`.

## HTTP server

Run the API with the repository's entry point:

```powershell
.venv\Scripts\python.exe main.py
```

On Linux or macOS:

```bash
.venv/bin/python main.py
```

The default address is `http://127.0.0.1:8000`. The ASGI application is also
available as `multiagent.api:create_app()`. The API manages the system worker
and bundle-worker shutdown through its application lifespan.

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Version, database schema, provider health, and the effective `dry_run` flag. |
| `GET` | `/api/v1/workflows` | List workflows; optional `?bundle=<name>`. |
| `GET` | `/api/v1/workflows/{workflow_id}` | Get and validate one workflow; optional `?bundle=<name>`. |
| `GET` | `/api/v1/agents` | List agents; optional `?bundle=<name>`. |
| `GET` | `/api/v1/bundles` | List bundles registered by the server. |
| `POST` | `/api/v1/bundles/{name}/validate` | Validate a registered bundle. |
| `POST` | `/api/v1/runs` | Queue a run; optional `?bundle=<name>`. Returns `202 Accepted`. |
| `GET` | `/api/v1/runs` | List runs; optional `limit` (1–500), `status`, and `project` query parameters. |
| `GET` | `/api/v1/runs/{run_id}` | Get a run with its events and artifacts. |
| `GET` | `/api/v1/runs/{run_id}/artifacts` | List the run's artifacts. |
| `GET` | `/api/v1/runs/{run_id}/events` | List the run's events; optional `after` (integer ≥ 0) returns only events with a greater `seq`. |
| `GET` | `/api/v1/runs/{run_id}/usage` | List local token-usage records and totals by run, agent, and step; costs are estimates. |
| `POST` | `/api/v1/runs/{run_id}/approve` | Approve the current human review. |
| `POST` | `/api/v1/runs/{run_id}/changes` | Request changes with `{"feedback": "..."}`. |
| `POST` | `/api/v1/runs/{run_id}/regenerate` | Regenerate the item under review. |
| `POST` | `/api/v1/runs/{run_id}/reject` | Reject the run with `{"feedback": "..."}`. |
| `POST` | `/api/v1/runs/{run_id}/cancel` | Cancel or request cancellation; optional `?force=true`. |
| `GET` | `/api/v1/runs/{run_id}/human-next` | Get the next pending human-guided step. |
| `POST` | `/api/v1/runs/{run_id}/human-submit` | Submit a human response. |
| `GET` | `/api/v1/task-groups` | List task groups with task counts per status. |
| `POST` | `/api/v1/task-groups` | Create a task group with `{"id", "title", "actor", "authorized"?, "default_requires"?}`. Returns `201 Created`. |
| `GET` | `/api/v1/task-groups/{group_id}` | Get a task group with task counts per status. |
| `PATCH` | `/api/v1/task-groups/{group_id}` | Change `title`, `authorized`, or `default_requires`; requires `actor`. |
| `GET` | `/api/v1/tasks` | List tasks; optional `group`, `status`, and `assignee` query parameters. |
| `POST` | `/api/v1/tasks` | Create a task with `{"group_id", "id", "title", "actor", "objective"?, "depends_on"?, "file_scope"?, "requirements"?, "preferred"?}`. Returns `201 Created`. |
| `GET` | `/api/v1/tasks/ready` | List ready tasks of authorized groups; optional `group`. |
| `GET` | `/api/v1/tasks/{task_id}` | Get a task with its status, blockers, dependencies, and dependents. |
| `POST` | `/api/v1/tasks/{task_id}/dependencies` | Add a dependency with `{"depends_on_id", "actor", "requires"?}`. |
| `DELETE` | `/api/v1/tasks/{task_id}/dependencies/{depends_on_id}` | Remove a dependency; requires `?actor=`. |
| `POST` | `/api/v1/tasks/{task_id}/transitions` | Change a task's state with `{"action", "actor", "cause"?, "expected_revision"?}`. |
| `GET` | `/api/v1/team-events` | List team events; optional `after`, `group`, `task`, and `limit` (1–1000). |
| `GET` | `/api/v1/tasks/{task_id}/gate` | Get the task's effective gate, its source (task, group, or none), and its last run. |
| `PUT` | `/api/v1/tasks/{task_id}/gate` | Set the task's own gate with `{"checks", "actor"}`; an empty list removes it. |
| `PUT` | `/api/v1/task-groups/{group_id}/gate` | Set the group's default gate with `{"checks", "actor"}`. |
| `POST` | `/api/v1/tasks/{task_id}/gate/run` | Run the gate of a submitted task with `{"actor", "cause"?}`; waits until it ends. |
| `POST` | `/api/v1/tasks/{task_id}/gate/abort` | Return a task stuck in `validating` to `submitted` with `{"actor", "cause"}`. |
| `GET` | `/api/v1/tasks/{task_id}/gate/results` | List stored check results; optional `attempt`. |
| `POST` | `/api/v1/tasks/{task_id}/reviews` | Approve or reject the current attempt with `{"actor", "approved", "comment"?}`. |
| `GET` | `/api/v1/project-commands` | List configured project commands (names, timeouts, folders; never arguments). |

The run endpoint accepts either a `SocialPostRequest` or a generic
`RunRequest`. A generic request selects a workflow and supplies its inputs:

```json
{
  "workflow_id": "social_post",
  "project_name": "API example",
  "inputs": {
    "objective": "Explain a useful technology concept",
    "topic": "workflow automation",
    "platform": "LinkedIn"
  },
  "execution_mode": "auto"
}
```

To run a bundle workflow, send a generic `RunRequest` and provide the registered
bundle name in the `bundle` query parameter. Bundle filesystem paths are
configured by the server, not supplied by API clients.

See [`TELEMETRY.md`](TELEMETRY.md) for the local-only usage model, CLI
export, nullable token semantics, and the future OpenTelemetry mapping.

See [`TASKS.md`](TASKS.md) for task groups, dependencies, states, transitions,
file locks, gates, project commands, team events, the matching CLI commands, and
examples. Task and gate request bodies reject unknown fields with
`422 invalid_request`, so a request can never bring a command to run.

For a human response, send a JSON object with a non-empty `raw_response` string;
`provider`, `model`, `prompt_used`, and `notes` are optional. The review and
human-response endpoints validate their request bodies and return the same
structured error format as other routes.

### Event sequence

Every run event carries `seq`, which numbers that run's events 1, 2, 3… in
order, without gaps. Events are never changed or removed, so an event keeps its
`seq`. A client that already shows events up to `seq` N can ask only for newer
ones with `GET /api/v1/runs/{run_id}/events?after=N`, `services.runs.events(run_id, after=N)`,
or `multiagent events <run_id> --after N`.

If a client sees a gap, a repeated number, or a copy that ends before events it
already has, its copy is inconsistent, for example because responses arrived out
of order. It should reload the run (`GET /api/v1/runs/{run_id}`) instead of
guessing its state. The Stage 0 run view does this and shows a notice while it
reloads. `seq` is additive: existing fields, including the event `id`, keep
their meaning. An `after` below 0 or not an integer returns 422 `invalid_request`.

Team events (`GET /api/v1/team-events`) use one sequence for the whole team
rather than one per run: an unfiltered read has no gaps, and a read filtered by
group or task skips the events of other groups or tasks. See [`TASKS.md`](TASKS.md#team-events).

## Structured errors

HTTP errors use the response shape:

```json
{
  "detail": {
    "code": "invalid_request",
    "message": "Invalid request",
    "details": {
      "errors": []
    }
  }
}
```

The Python facade returns the same inner object from `ServiceError.to_payload()`
without the HTTP `detail` wrapper. Validation errors use status `422`; missing
resources use `404`; invalid state or operation errors use `409`. Common stable
codes include `not_found`, `invalid_request`, `invalid_definition`,
`invalid_state`, `invalid_operation`, `budget_exceeded`,
`idempotency_conflict`, `human_step_unavailable`, and
`human_submission_invalid`.

## Mock mode

Set `MOCK_MODE=true` in the server environment (or its `.local/.env`) before
starting the application to use the existing `dry_run` path. The router uses
the FakeAdapter fixtures instead of contacting model providers, so no provider
API keys or network access are required. The default is `false`; without the
setting, provider routing is unchanged.

Windows PowerShell:

```powershell
$env:MOCK_MODE = "true"
.venv\Scripts\python.exe main.py
```

Linux or macOS:

```bash
MOCK_MODE=true .venv/bin/python main.py
```

Check `GET /api/v1/health`: `dry_run` is `true` when mock mode or another
dry-run option is active. Mock outputs are synthetic and intended for
development, demos, and integration work; they are not real model results.
