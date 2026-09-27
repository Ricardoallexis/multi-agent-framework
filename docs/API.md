# Python and HTTP API

Multi-Agent Framework exposes one application-services facade for Python and an
HTTP API built on that same facade. Both interfaces share workflow validation,
run operations, and structured errors.

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
| `services.runs` | `create`, `get`, `list`, `approve`, `request_changes`, `regenerate`, `reject`, `cancel`, `human_next`, `human_submit`, `artifacts`, `events` |

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
| `POST` | `/api/v1/runs/{run_id}/approve` | Approve the current human review. |
| `POST` | `/api/v1/runs/{run_id}/changes` | Request changes with `{"feedback": "..."}`. |
| `POST` | `/api/v1/runs/{run_id}/regenerate` | Regenerate the item under review. |
| `POST` | `/api/v1/runs/{run_id}/reject` | Reject the run with `{"feedback": "..."}`. |
| `POST` | `/api/v1/runs/{run_id}/cancel` | Cancel or request cancellation; optional `?force=true`. |
| `GET` | `/api/v1/runs/{run_id}/human-next` | Get the next pending human-guided step. |
| `POST` | `/api/v1/runs/{run_id}/human-submit` | Submit a human response. |

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
