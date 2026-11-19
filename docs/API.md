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
| `services.runs` | `create`, `get`, `list`, `approve`, `request_changes`, `regenerate`, `reject`, `cancel`, `human_next`, `human_check`, `human_submit`, `artifacts`, `events`, `usage`, `step_attempts`, `compare_attempts` |
| `services.tasks` | `create_group`, `update_group`, `get_group`, `list_groups`, `create_task`, `get_task`, `list_tasks`, `ready_tasks`, `add_dependency`, `remove_dependency`, `transition`, `events` |
| `services.teams` | `propose`, `submit`, `approve`, `reject`, `get`, `list`, `approved`, `diff`, `org_chart`: versions of a bundle's `team.yaml` with human approval (`user:` actors only), structural diffs against the approved version, and decision-register records; see [`TEAMS.md`](TEAMS.md) and the `/api/v1/teams` routes. |
| `services.gates` | `set_task_gate`, `set_group_gate`, `get_gate`, `review`, `run_gate`, `abort_gate`, `results`, `project_commands` |
| `services.envelopes` | `set_task_context`, `get_envelope`, `put_blob`, `get_artifact`, `submit_result`, `get_result`, `list_results`, `get_cursor`, `read_events`, `ack_events` |
| `services.executors` | `run_task`, `resume_task`, `open_target`, `status` (and `record_app_open`, `session_guard`): run an ORQ task in Codex, Claude or Copilot, continue or open its session |
| `services.memory` | `create`, `list`, `get`, `delete`: facts, decisions and conventions of a project, each with its author as provenance and a computed trust level. Entries live in the system database; a bundle run reads the memory of the project with the same name there. A workflow or step receives them, quoted as data, only when it sets `project_memory`. |
| `services.decisions` | `propose`, `get`, `list`, `accept`, `reject`, `supersede` (plus `record_review`, accepting optional `team_id`): durable proposals with a human decision, effective expiry and active filtering. Successful run reviews and team version approvals/rejections create records automatically. |
| `services.evaluation` | `run(set, live=False)`: run an evaluation set of workflow, agent or prompt cases and return its report; see [Evaluation sets](EVALUATION.md). |

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

See [Human-guided execution](HUMAN_GUIDED.md) for the complete CLI, API, and GUI flow.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/v1/health` | Version, database schema, provider health, and the effective `dry_run` flag. |
| `GET` | `/api/v1/workflows` | List workflows; optional `?bundle=<name>`. |
| `GET` | `/api/v1/workflows/{workflow_id}` | Get and validate one workflow; optional `?bundle=<name>`. |
| `GET` | `/api/v1/agents` | List agents; optional `?bundle=<name>`. |
| `GET` | `/api/v1/bundles` | List bundles registered by the server. |
| `POST` | `/api/v1/bundles/{name}/validate` | Validate a registered bundle. With a `team.yaml`, the team is checked against the bundle's workflows too: errors return `422` with `details.team` (each finding with `code`, `location`, `message` and `severity`), and warnings alone return `200` with `warnings`. |
| `POST` | `/api/v1/runs` | Queue a run; optional `?bundle=<name>`. Returns `202 Accepted`. |
| `GET` | `/api/v1/runs` | List runs; optional `limit` (1–500), `status`, and `project` query parameters. |
| `GET` | `/api/v1/runs/{run_id}` | Get a run with its events and artifacts. |
| `GET` | `/api/v1/runs/{run_id}/artifacts` | List the run's artifacts. |
| `GET` | `/api/v1/runs/{run_id}/events` | List the run's events; optional `after` (integer ≥ 0) returns only events with a greater `seq`. |
| `GET` | `/api/v1/runs/{run_id}/usage` | List local token-usage records and totals by run, agent, and step; costs are estimates. |
| `GET` | `/api/v1/runs/{run_id}/steps/{step_id}/attempts` | List completed attempts of one step with producer, revision input, JSON/Markdown output, review status, human notes/prompt, and tokens. |
| `GET` | `/api/v1/runs/{run_id}/steps/{step_id}/attempts/compare` | Compare two completed attempts (`?a=1&b=2`) field by field, with unified line diffs for long or multi-line strings. |
| `POST` | `/api/v1/runs/{run_id}/approve` | Approve the current human review. |
| `POST` | `/api/v1/runs/{run_id}/changes` | Request changes with free text, structured comments, or both (see below). |
| `POST` | `/api/v1/runs/{run_id}/regenerate` | Regenerate the item under review. |
| `POST` | `/api/v1/runs/{run_id}/reject` | Reject the run with `{"feedback": "..."}`. |
| `POST` | `/api/v1/runs/{run_id}/cancel` | Cancel or request cancellation; optional `?force=true`. |
| `GET` | `/api/v1/runs/{run_id}/human-next` | Get the next pending human-guided step. |
| `POST` | `/api/v1/runs/{run_id}/human-submit` | Submit a human response. |
| `POST` | `/api/v1/runs/{run_id}/human-check` | Validate a response without submitting or storing it. |
| `POST` | `/api/v1/runs/{run_id}/research-submit` | Submit external research; defaults to provider `human_web`. |
| `POST` | `/api/v1/runs/{run_id}/external-output/preview` | Validate an externally produced step output `{output, evidence: [{name, content_base64}], actor, channel}` against the waiting step's contract and stage it, without changing the run. Returns the preview id, step, attempt, normalized output, files with type, size and SHA-256, and `expires_at`. Rejected evidence fails with `evidence_rejected` (422) and its `reason`. |
| `POST` | `/api/v1/runs/{run_id}/external-output/confirm` | Confirm a preview once `{preview_id, actor, channel}`; `actor` must be `user:`. Completes the waiting step, or adds a new attempt under review, and stores the evidence, a manifest and a receipt as run artifacts. A stale, expired or used preview fails with `invalid_state` (409). See `docs/HUMAN_GUIDED.md`. |
| `POST` | `/api/v1/projects/{project}/memory` | Write a memory entry `{kind, title, body, actor, confidence?, source_ref?}`; `kind` is `fact`, `decision` or `convention`. Returns `201` with the entry, its `trust` (only a `user:` actor is trusted) and `source`. |
| `GET` | `/api/v1/projects/{project}/memory` | List a project's memory entries, oldest first; optional `kind`. |
| `GET` | `/api/v1/memory/{entry_id}` | Get one memory entry. |
| `DELETE` | `/api/v1/memory/{entry_id}` | Delete an entry; `?actor=` must be a `user:` actor (`409` otherwise). |
| `POST` | `/api/v1/decisions` | Propose `{title, body, actor, valid_until?, run_id?, step_id?, attempt?, task_id?, team_id?, project?}`. Returns `201`. |
| `GET` | `/api/v1/decisions` | List decisions; optional `active=true`, `project` and `run_id`. Expired decisions have effective status `expired`; only accepted decisions inside their validity period are active. |
| `GET` | `/api/v1/decisions/{id}` | Get one proposal or decision. |
| `POST` | `/api/v1/decisions/{id}/accept` | Accept a proposal with `{actor, outcome?, valid_from?, valid_until?}`. `actor` must be a `user:` actor. |
| `POST` | `/api/v1/decisions/{id}/reject` | Reject a proposal with `{actor, outcome?}`. `actor` must be a `user:` actor. |
| `POST` | `/api/v1/decisions/{id}/supersede` | Replace an accepted decision with `{actor, replacement_id?}` or `{actor, title, body}`; optional `outcome` and `valid_until`. The replacement becomes accepted and the old decision links to it. |
| `POST` | `/api/v1/teams/{bundle}/versions` | Propose the bundle's `team.yaml` with `{actor, draft?}`. Validates it like `validate_bundle` (`invalid_definition` 422 with the findings); a team equal to the approved version is `invalid_request` (`no_changes`). Returns `201` with the version and its diff. |
| `GET` | `/api/v1/teams/{bundle}/versions` | List the bundle's team versions; optional `status`. |
| `GET` | `/api/v1/teams/versions/{id}` | Get one version with its diff against the approved version. |
| `POST` | `/api/v1/teams/versions/{id}/submit` | Move a draft to `proposed` with `{actor}`. |
| `POST` | `/api/v1/teams/versions/{id}/approve` | Approve a proposed version with `{actor, note?}`. `actor` must be a `user:` actor; another state is `invalid_state` (409). Records the decision in the register and returns `decision_id` (or `decision_id: null` and `decision_record_error` if recording fails, without undoing the approval). See [`TEAMS.md`](TEAMS.md). |
| `POST` | `/api/v1/teams/versions/{id}/reject` | Reject a proposed version with `{actor, note?}`, under the same rules and with the same `decision_id` / `decision_record_error` response fields. |
| `GET` | `/api/v1/teams/{bundle}/approved` | The current approved version, or `null`. |
| `GET` | `/api/v1/teams/{bundle}/org-chart` | The organization chart: positions with holders, manager, reports and status (`occupied`, `vacant`, `pending`, `pending_removal`), `root` or `roots` and `forest`, `links`, and `mermaid`. Optional `version_id` shows that version without the pending overlay. |
| `POST` | `/api/v1/evaluations/run` | Run an evaluation set (the YAML document as JSON). Mock by default, on a temporary database; `"live": true` runs a workflow set on this server's worker and returns `409 eval_live_worker_unavailable` when it is not running. Every report is saved in the system database (the cases of a Mock run still use a temporary one) and `meta.evaluation_run_id` names it; an optional `actor` is stored with it. See [Evaluation sets](EVALUATION.md). |
| `GET` | `/api/v1/evaluations/runs` | List saved evaluation runs, newest first; optional `set_name` and `limit` (1 to 1000, default 100). |
| `GET` | `/api/v1/evaluations/runs/{run_id}` | One saved evaluation run with its fingerprint and report. |
| `PUT` | `/api/v1/evaluations/baselines/{set_name}` | Set the baseline of a set `{run_id, actor?}`; setting it again replaces it. A run of another set fails with `409 evaluation_set_mismatch`. |
| `GET` | `/api/v1/evaluations/baselines/{set_name}` | The baseline of a set (`404 not_found` when there is none). |
| `POST` | `/api/v1/evaluations/compare` | Compare `{candidate_id, base_id?, threshold?}` (the base defaults to the baseline): each case is `regressed`, `improved`, `changed` or `unchanged`, with the cases found in only one run apart and what changed in the fingerprint. A regression answers `200` with `regression: true`. |
| `GET` | `/api/v1/task-groups` | List task groups with task counts per status. |
| `POST` | `/api/v1/task-groups` | Create a task group with `{"id", "title", "actor", "authorized"?, "default_requires"?}`. Returns `201 Created`. |
| `GET` | `/api/v1/task-groups/{group_id}` | Get a task group with task counts per status. |
| `PATCH` | `/api/v1/task-groups/{group_id}` | Change `title`, `authorized`, or `default_requires`; requires `actor`. |
| `PUT` | `/api/v1/task-groups/{group_id}/release-mode` | Set the legacy release policy to `terminada`, `probada`, or `integrada` with `{"mode", "actor"}`. |
| `GET` | `/api/v1/tasks` | List tasks; optional `group`, `status`, and `assignee` query parameters. |
| `POST` | `/api/v1/tasks` | Create a task with native `depends_on`, optional `release_dependencies` (`depends_on_id` plus provenance `source_ref`), context references, and other task fields. Returns `201 Created`. |
| `POST` | `/api/v1/task-snapshots/import` | Preview or atomically import a body containing `snapshot: {groups, tasks, dependencies}`, `source_key`, `source_ref`, `snapshot_sha256`, and `actor`; set `what_if: true` to preview. |
| `GET` | `/api/v1/task-snapshots/{source_key}` | Read the durable import receipt. |
| `POST` | `/api/v1/task-snapshots/{source_key}/rollback` | Roll back an imported batch when baseline and activity checks permit; body `{"actor"}`. |
| `GET` | `/api/v1/tasks/ready` | List ready tasks of authorized groups; optional `group`. |
| `GET` | `/api/v1/tasks/{task_id}` | Get a task with its status, blockers, dependencies, and dependents. |
| `POST` | `/api/v1/tasks/{task_id}/dependencies` | Add a dependency with `{"depends_on_id", "actor", "requires"?}`. |
| `DELETE` | `/api/v1/tasks/{task_id}/dependencies/{depends_on_id}` | Remove a dependency; requires `?actor=`. |
| `POST` / `DELETE` | `/api/v1/tasks/{task_id}/release-dependencies[/{depends_on_id}]` | Add or remove a legacy release edge, kept separate from native dependencies. |
| `GET` | `/api/v1/tasks/{task_id}/release-facts` | Read the current delivery, test, and integration facts with provenance. |
| `POST` | `/api/v1/tasks/{task_id}/release-facts/{delivery\|test\|integration\|reset}` | Record or reset current legacy release evidence. |
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
| `PUT` | `/api/v1/tasks/{task_id}/context` | Set the task's `base_commit`, `context_refs`, and `constraints` with `{"actor", ...}`; only the given fields change. |
| `GET` | `/api/v1/tasks/{task_id}/envelope` | Get the task envelope; optional `known_hash` returns `{"unchanged": true, ...}` when it still matches. |
| `GET` | `/api/v2/tasks/{task_id}/context` | Capability `release_dependencies/v1`: return the v1 task envelope plus `release_context` and a canonical fingerprint; `known_fingerprint` may return `unchanged`; `after` only validates the event-head bound and does not read or acknowledge events. |
| `POST` | `/api/v1/blobs` | Store a blob: the request body is the raw content; `?actor=` is required and `?media_type=` and `?sha256=` (to verify the content) are optional. A blob over 8 MiB answers `413 invalid_request`. |
| `GET` | `/api/v1/artifacts` | Read a range of a blob: `?ref=blob:sha256:…`, optional `offset` and `length` (up to 1 MiB). |
| `POST` | `/api/v1/tasks/{task_id}/results` | Submit the result of the current attempt and submit the task with `{"actor", "result"}`; a repeated identical result returns `"duplicate": true`. |
| `GET` | `/api/v1/results` | Get a result by `?ref=result://<task_id>/<attempt>`. |
| `GET` | `/api/v1/tasks/{task_id}/results` | List the summaries of the task's results, or get the full result of one attempt with `?attempt=`. |
| `GET` | `/api/v1/executors/status` | Executors that have an adapter (enabled, `paused_until`); with `?task=` also that task's recorded sessions and attempts. |
| `POST` | `/api/v1/executors/run` | Launch a task in an executor application with `{"task_id", "executor_id"?, "profile_id"?, "step_kind"?, "model"?, "effort"?, "actor"?, "timeout_seconds"?}` (without `model`/`effort`, the profile's routing decision supplies them; the answer's `selection` says where each came from); waits for the attempt. The result envelope goes in through `submit_result` and the gate decides; a failed attempt, a timeout or a quota stop leaves the task `paused` (a quota stop also pauses the executor). |
| `POST` | `/api/v1/executors/resume` | Continue the session the framework created for the task with `{"task_id", "model"?, "effort"?, "actor"?, "timeout_seconds"?}`; refuses a session opened in an app in the last 12 hours. |
| `POST` | `/api/v1/executors/open` | Get the target that opens the task's recorded session in its app with `{"task_id", "actor"?}`; it records the open. |
| `GET` | `/api/v1/cursors/{consumer}` | Get the consumer's event cursor and the log head; optional `scope`. |
| `GET` | `/api/v1/cursors/{consumer}/events` | List the events after the consumer's cursor; optional `scope`, `type` (repeatable), `mine`, and `limit` (1–1000). Does not move the cursor. |
| `POST` | `/api/v1/cursors/{consumer}/ack` | Move the cursor with `{"seq", "scope"?}`; never backwards, never past the head. |

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

### Step attempts and comparison

Inspect completed attempts of a step or compare two attempts from the CLI with
the same JSON responses returned by the HTTP API:

```console
python -m multiagent attempts <run_id> <step_id>
python -m multiagent attempts <run_id> <step_id> --compare 1 2
```

Run reports (`GET /api/v1/runs/{run_id}/report`, `multiagent run-report`) also
include a `reviews` section listing `checkpoint`, `human_approved`,
`human_revision`, and `human_rejected` events with `step_id`, `attempt`,
`actor`, and `timestamp`.
For `human_revision`, the event also records the original `feedback`, structured
`comments`, `by`, and `channel` (`api` for this unauthenticated route, including
the CLI; `gui` for the GUI route). The report exposes `channel` and
`comment_count`; comment text appears only with `include_raw`.

To request changes, send `{"feedback": "Clarify the conclusion", "comments":
[{"target": "items[2].title", "kind": "rewrite", "text": "Use a shorter title",
"quote": "Old title"}]}`. `feedback` is optional when at least one comment is
present. Up to 20 comments are allowed. Each target is `general` or a field
path; a path absent from the output is retained as entered. Kinds are `fix`,
`add`, `remove`, `rewrite`, and `other`. The next attempt receives the free text
first, followed by numbered comments. Quoted model output is enclosed in a
`trust=untrusted` provenance envelope; the person's comment text remains
literal. Without comments, the next attempt receives the original feedback
unchanged. The CLI accepts `multiagent changes RUN_ID [FEEDBACK]
[--comments-json FILE]`; `--comments-json -` reads a JSON list from stdin.

**Current storage limits.** Attempts that failed before producing a valid
output and the full rendered prompt sent to a model attempt are not persisted
today, so they do not appear in `step_attempts` or `compare_attempts`. Human
submissions record `prompt_used` and `notes` when provided.

See [`TASKS.md`](TASKS.md) for task groups, dependencies, states, transitions,
file locks, gates, project commands, task envelopes and results, blobs, event
cursors, team events, the matching CLI commands, and examples. Task, gate,
and envelope request bodies reject unknown fields with
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
guessing its state. The operator GUI can do this and show a notice while it
reloads. `seq` is additive: existing fields, including the event `id`, keep
their meaning. An `after` below 0 or not an integer returns 422 `invalid_request`.

## GUI routes (local, token-protected)

The operator GUI is served at `/gui/`; its local routes live under
`/gui/api/*` and require the GUI access token. They are not part of the public
`/api/v1` contract. For startup, session access, settings, and the security
model, see the [operator GUI guide](GUI.md).

Start the local GUI with `multiagent gui [--port N] [--real] [--no-browser]`
or `python -m multiagent.gui`. The read-only session service is also available
through `multiagent sessions list`, `multiagent sessions show`, and
`multiagent sessions items`.

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
