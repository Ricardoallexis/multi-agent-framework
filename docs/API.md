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
| `services.plans` | `status`, `advance`: run a task group from a `multiagent.plan/1` document by creating ordinary runs linked by `task_id` and moving each task from its run's result; no second engine. See [planning](PLANNING.md). |
| `services.planning` | `planners`, `propose`: named planners (`manual`, `deterministic`, `model`, and any registered by trusted code) return a `multiagent.plan_proposal/1` whose `plan` is the `multiagent.plan/1` document `services.plans` runs; every proposal passes the same validation (ids, dependencies without cycles, existing workflows, text inputs). Proposing never creates tasks or runs. See [planning](PLANNING.md). |
| `services.plan_proposals` | `save`, `get`, `list`, `edit`, `reassign`, `approve`, `reject`, `advance`: a saved proposal waits until a `user:` actor approves it; edits and reassignments raise its revision and keep who, when, channel and what changed; approving creates the group's tasks and advances the plan once through `services.plans`; rejecting closes it; `advance` runs one more `services.plans.advance` from the plan an approved proposal saved. Each decision also goes to the decision register. See [planning](PLANNING.md). |
| `services.teams` | `propose`, `submit`, `approve`, `reject`, `get`, `list`, `approved`, `diff`, `org_chart`: versions of a bundle's `team.yaml` with human approval (`user:` actors only), structural diffs against the approved version, and decision-register records; see [`TEAMS.md`](TEAMS.md) and the `/api/v1/teams` routes. |
| `services.gates` | `set_task_gate`, `set_group_gate`, `get_gate`, `review`, `run_gate`, `abort_gate`, `results`, `project_commands` |
| `services.envelopes` | `set_task_context`, `get_envelope`, `put_blob`, `get_artifact`, `submit_result`, `get_result`, `list_results`, `get_cursor`, `read_events`, `ack_events` |
| `services.executors` | `run_task`, `resume_task`, `open_target`, `status` (and `record_app_open`, `session_guard`): run an ORQ task in Codex, Claude or Copilot, continue or open its session |
| `services.memory` | `create`, `list`, `get`, `delete`: facts, decisions and conventions of a project, each with its author as provenance and a computed trust level. A `user:` author is only a claim, so a `user:` entry is stored `untrusted` until a person approves it in the GUI (`memory_trust`, one-use code), and a deletion waits for a person too (`memory_delete`). Entries live in the system database; a bundle run reads the memory of the project with the same name there. A workflow or step receives them, quoted as data, only when it sets `project_memory`. An `untrusted` entry that contradicts one a person confirmed (same project and kind, same title once normalized by NFKD without marks, `casefold` and single spaces, a different body) is never quoted: `select_entries` leaves it out and the note says so apart from the size cap. Two untrusted entries that clash are both quoted. |
| `services.decisions` | `propose`, `get`, `list`, `accept`, `reject`, `supersede` (plus `record_review`, accepting optional `team_id`): durable proposals with a human decision, effective expiry and active filtering. Successful run reviews and team version approvals/rejections create records automatically. |
| `services.offices` | `list`, `get`, `import_folder`, `import_zip`, `reimport_folder`, `reimport_zip`, `remove`, `remove_area`, `office_of`, `requirements`, `requirements_for`, `area_class`, `privacy`, `cloud_permission_for`, `raise_level`, `request_cloud_permission`, `grant_cloud_permission`, `request_lowering`, `lower_level`: offices of level 1 (data only), installed from a folder or a `.zip`; each area is an installed package named `<office>__<area>` and its team is only proposed. See [Offices](#offices). |

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
`services.runs.close()` when shutting the facade down. It stops the worker of every
package runtime and waits for its run threads (each bounded by
`WORKER_SHUTDOWN_WAIT_SECONDS`), so nothing of the framework writes to the local
folder afterwards and a temporary folder can be deleted. After `close()` the
services build no new package runtime (a call that needs one fails with
`invalid_state`, `details.reason` `services_closed`); the API calls `reopen()` when
an app starts on services that were closed before. `close()` does not stop the
system worker: its owner does (the API's lifespan, `multiagent worker`, or the host
that called `services.system.worker.start()`).

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
| `GET` | `/api/v1/bundles/{name}/scan` | Report the scan state of a registered bundle. Read-only: it never starts a scan or asks for an approval. See [Bundle scan status](#bundle-scan-status). |
| `POST` | `/api/v1/runs` | Queue a run; optional `?bundle=<name>`. Returns `202 Accepted`. A request that chooses cloud (`execution_mode: cloud` or a cloud model preference) needs `confirm_cloud: true` (CLI `--confirm-cloud`), otherwise `422 cloud_confirmation_required`; it is never accepted for a `sensitive` run. Without a saved office/area permission, an automatic local-preferred step with a cloud fallback stays local, while a cloud-preferred automatic step fails with `422 cloud_permission_required`. |
| `GET` | `/api/v1/runs` | List runs; optional `limit` (1–500), `status`, and `project` query parameters. |
| `GET` | `/api/v1/runs/{run_id}` | Get a run with its events and artifacts. A role-bound step's `step_started` event includes `role`, `member`, `team_id`, `team_version_id`, and `team_version`. |
| `GET` | `/api/v1/runs/{run_id}/artifacts` | List the run's artifacts. |
| `GET` | `/api/v1/runs/{run_id}/events` | List the run's events; optional `after` (integer ≥ 0) returns only events with a greater `seq`. |
| `GET` | `/api/v1/runs/{run_id}/usage` | List local token-usage records and totals by run, agent, and step; costs are estimates. |
| `GET` | `/api/v1/runs/{run_id}/report` | JSON run report (or `?format=markdown`); `steps` entries for role-bound `step_started` events include the role and member fields. |
| `GET` | `/api/v1/runs/{run_id}/document.md` | The result of a completed run as Markdown (`text/markdown; charset=utf-8`, `Content-Disposition: attachment; filename="<name>.md"`). `409 invalid_state` with `details.reason` `run_not_completed` or `no_result` when there is no document yet; `404 not_found` for an unknown run. The same document as JSON, `{schema: "maf.run-document/1", run_id, title, step_id, filename, markdown}`, is `GET /gui/api/runs/{run_id}/document` (token). CLI: `multiagent run-document RUN_ID [--output FILE.md]`. See [Result document](GUI.md#result-document). |
| `GET` | `/api/v1/runs/{run_id}/steps/{step_id}/attempts` | List completed attempts of one step with producer, revision input, JSON/Markdown output, review status, human notes/prompt, tokens, and role/member fields when present. |
| `GET` | `/api/v1/runs/{run_id}/steps/{step_id}/attempts/compare` | Compare two completed attempts (`?a=1&b=2`) field by field, with unified line diffs for long or multi-line strings; `a` and `b` retain their role/member fields. |
| `POST` | `/api/v1/runs/{run_id}/approve` | Approve the current human review (`tool_approval` requires `POST /gui/api/runs/{run_id}/approve` with `permission_request_id`; see [Built-in file tools](FILE_TOOLS.md#approvals)). |
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
| `POST` | `/api/v1/projects/{project}/memory` | Write a memory entry `{kind, title, body, actor, confidence?, source_ref?}`; `kind` is `fact`, `decision` or `convention`. Returns `201` with the entry, its `trust` and `source`. Every author starts `untrusted`. For a `user:` actor the answer adds `pending_approval` (the id of the `memory_trust` approval, listed in «Pending» and decided in the GUI with the one-use code) and `details.reason = "user_confirmation_pending"`; the entry becomes `trusted` only when a person approves it, and a requested `high` confidence is stored as `medium` until then. When the new entry contradicts a `trusted` one (any author), the answer also carries `conflicts_with` (the id of the confirmed entry) and a `memory_trust` approval naming it; for an `agent:` or `executor:` author the `details.reason` is `contradicts_confirmed_entry`. Approving it makes the new entry `trusted` and opens a `memory_delete` approval for the old one; nothing is deleted until a person decides that one, and until then both are quoted. «Pending» shows the pair as text, read by id and cut to a fixed size (`conflict: {new, existing}` on `GET /gui/api/approvals`). |
| `GET` | `/api/v1/projects/{project}/memory` | List a project's memory entries, oldest first; optional `kind`. |
| `GET` | `/api/v1/memory/{entry_id}` | Get one memory entry. |
| `DELETE` | `/api/v1/memory/{entry_id}` | Ask to delete an entry; `?actor=` must be a `user:` actor (`409`, `confirmation_required`, otherwise). Nothing is deleted: it answers `{deleted: false, id, pending_approval, details: {reason: "user_confirmation_pending"}}` and the entry goes only when a person approves the `memory_delete` approval in the GUI. An open request for the same entry is reused. |
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
| `POST` | `/api/v1/plans/status` | Read a plan with `{"document"}`: its status and, per task, state, latest run and blockers. See [planning](PLANNING.md). |
| `POST` | `/api/v1/plans/advance` | Advance a plan once with `{"document", "actor"}`: finished runs move their tasks, ready tasks get a run. See [planning](PLANNING.md). |
| `GET` | `/api/v1/planners` | List the planners by name and description. See [planning](PLANNING.md). |
| `POST` | `/api/v1/plans/propose` | Propose a plan with `{"planner", "request", "confirm_cloud"?}`; the `model` planner goes through the same router, egress policy and usage records as a new session, and a cloud model needs `confirm_cloud`. See [planning](PLANNING.md). |
| `POST` | `/api/v1/plan-proposals` | Save a proposal with `{"proposal", "actor", "channel"?, "note"?}`; it creates no task or run. See [planning](PLANNING.md). |
| `GET` | `/api/v1/plan-proposals` | List saved proposals (`?group_id=&status=`), each with its history. See [planning](PLANNING.md). |
| `GET` | `/api/v1/plan-proposals/{id}` | Read one proposal, its revision and its history. |
| `POST` | `/api/v1/plan-proposals/{id}/edit` | Replace its tasks; needs `expected_revision`. |
| `POST` | `/api/v1/plan-proposals/{id}/reassign` | Change one task's `preferred`, `workflow` or `bundle`; needs `expected_revision`. |
| `POST` | `/api/v1/plan-proposals/{id}/approve` | A `user:` actor approves the current revision: tasks are created and ready ones get runs. If a bundle with `role:` steps has no approved team, it answers `409 team_not_approved` with `blockers` (`[{bundle, roles}]`) and creates nothing; `get` and `list` carry the same `blockers`. |
| `POST` | `/api/v1/plan-proposals/{id}/reject` | A `user:` actor rejects it with a note; it takes no more changes. |
| `POST` | `/api/v1/plan-proposals/{id}/advance` | Advance the plan of an approved proposal once with `{"actor", "channel"?}`. A proposal that is not approved is a conflict (`not_approved`); with nothing to move it answers `nothing_to_advance: true`, and `advanced` lists the tasks that moved. The CLI is `multiagent plan advance --proposal <id> --actor <actor>`. |
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
| `GET` | `/api/v1/connections` | Connection facts for `gemini`, `openai`, `ollama`, `claude_cli`, `codex_cli`, `copilot_cli` and `antigravity_cli`, in that order: `installed`, `version`, `session`, `key_configured` and `key_setting` (never the key), `reachable`, `enabled`, `quota`, `available`, `reason`, `missing_step` (`install`, `session`, `key` or `enable`) and `checked_at`. It calls no model and is cached per process. Gemini is eligible with its key set and carries `verification` (`unverified`, the default, or the outcome of the last real request: `verified`, and `key_invalid`, `limit_reached` or `no_access`, which are also its `reason`) with `verified_at` and `resets_at`; the outcome lives in process memory. Copilot is `verification: verified` with `session: signed_in` only while its local quota cache of the current month is valid, and otherwise keeps `reason: session_unverifiable`. `limit` is `null` or `{code: "limit_reached", resets_at, target, suggestions}` (up to three eligible alternatives; `resets_at` only from observed evidence). `warning` is `null` or, for agy between 15 % and 0 % of plan quota, `{reason, remaining_percent, resets_at}` while the row stays eligible; at 0 % the `reason` is set instead. CLI: `multiagent connections`. |
| `POST` | `/api/v1/connections/{connection_id}/check` | Check one connection again (`404 not_found` for an unknown id); CLI: `multiagent connections --check ID`. |
| `GET` | `/api/v1/executors/status` | Executors that have an adapter (enabled, `paused_until`); with `?task=` also that task's recorded sessions and attempts. |
| `POST` | `/api/v1/executors/run` | Launch a task in an executor application with `{"task_id", "executor_id"?, "profile_id"?, "step_kind"?, "model"?, "effort"?, "actor"?, "timeout_seconds"?, "accept_quota_warning"?}` (without `model`/`effort`, the profile's routing decision supplies them; the answer's `selection` says where each came from); waits for the attempt. The result envelope goes in through `submit_result` and the gate decides; a failed attempt, a timeout or a quota stop leaves the task `paused` (a quota stop also pauses the executor). `accept_quota_warning` (a strict boolean, default `false`; CLI `--accept-quota-warning`) lets a person cross agy's 15 % and 10 % plan-quota floors for this one new launch; resumes and automatic launches never inherit it, and 0 % always stops the run. |
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

A `RunRequest` also accepts an optional `"operating_mode"` (`"supervised"` or
`"autonomous"`). When omitted, the run inherits `mode` from the bundle's bound
approved `team.yaml` if one is set. When either sets a mode, creation emits an
`operating_mode_set` event (`{"mode", "source"}` with `source` `"run"` or
`"team"`), and `GET /api/v1/runs/{run_id}` returns `"operating_mode"`
(`"supervised"`, `"autonomous"`, or `null`); when neither is set, no
`operating_mode_set` event is emitted and `"operating_mode"` is `null`. See
[Run operating modes](PERMISSIONS.md#run-operating-modes).

See [`TELEMETRY.md`](TELEMETRY.md) for the local-only usage model, CLI
export, nullable token semantics, and the future OpenTelemetry mapping.

### Workflow `result_step` and result document

A workflow definition accepts the optional root key `result_step` naming the
`id` of the step whose completed output provides the run's result document
(validation fails with `unknown_result_step` at `result_step` when the id is
not in `steps`). When `result_step` is set and that step did not complete (for
example, it was skipped by `when`), the run has no result document; when
`result_step` is omitted, the result defaults to the last completed step in
workflow order.

The document title is the first line of the workflow's `description` (up to 120
characters), or `id` when `description` is absent. `multiagent.documents`
renders the step output deterministically to Markdown from its Pydantic JSON
Schema in `properties` order (resolving local `$defs`/`$ref` up to 6 levels,
omitting empty or `null` fields, using field `description`, `title`, or name as
headings, and escaping `&`, `<`, and `>` in headings and values). When a schema
property sets `"x-maf-document": true`, that field's escaped text is used as the
document body directly under the title and all other fields are omitted.

### Step attempts and comparison

When a model step declares a role, its `step_started` event payload, each
completed attempt object, and its `step_started` run-report row include `role`,
`member`, `team_id`, `team_version_id`, and `team_version`. The attempt list
returns these fields inside `attempts[]`; comparison returns them inside `a`
and `b`. A step without a role omits the fields. The run's
`team_version_bound` event records the approved team version used to resolve
the role. See [Roles in workflow steps](TEAMS.md#roles-in-workflow-steps) for
declaration, member selection, and error codes.

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
`human_approved` records `by` and `channel` as `human_revision` does (and as a
tool approval does): the GUI route sends `user:operator` and `gui`, and the API
route, including the CLI, sends `api`. The values are what the caller declared;
they are not verified. Events written before this change have no `by` or
`channel`.
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

### Bundle scan status

A bundle registered from outside the framework's own folder is scanned with SkillSpector, when it is installed,
before its code is imported (see [Bundle scan](security/bundle-scan.md)). `GET /api/v1/bundles/{name}/scan`
(also `GET /gui/api/bundles/{name}/scan` and `multiagent bundle-scan NAME`) returns:

| Field | Meaning |
| --- | --- |
| `bundle`, `external` | The name, and whether it is outside the framework's folder. A bundled one answers `state: "exempt"`. |
| `state` | `unscanned`, `pending`, `running`, `safe`, `caution`, `do_not_install`, `not_analyzed`, `not_installed`, `off` or `exempt`. |
| `verdict` | The state when it is `safe`, `caution` or `do_not_install`; otherwise `null`. |
| `findings`, `coverage` | The main findings (at most 20, most severe first) and the coverage percent per folder. |
| `fingerprint`, `tool_version`, `reason`, `warning` | The bundle fingerprint, the tool version, why a result is `not_analyzed`, and the warning shown when there was no scan. |
| `approval`, `load_approved` | The pending approval (`id`, `state`, `location: "/gui/"`) or `null`, and whether a person already approved the load. |
| `recommendation`, `risk_ack_required` | `"do_not_install"` when SkillSpector recommends against installing (otherwise `null`), and whether a `bundle_load` decision requires risk acknowledgement. |

Requests that load a bundle's code (`POST /api/v1/bundles/{name}/validate`, `?bundle=<name>` on workflows and
agents, and `POST /api/v1/runs`) answer `409` until a person decides in the GUI:

| Code | When | `details` |
| --- | --- | --- |
| `bundle_scan_pending` | The query to `api.osv.dev` waits for a click. | `approval_id`, `location` |
| `bundle_scan_running` | A scan is running; only one runs at a time. | `bundle` |
| `bundle_scan_blocked` | The verdict is `do_not_install`, the bundle was not fully analyzed, or the interpreter is unsigned; loading needs a click. | `approval_id`, `location`, `verdict`, `findings`, `coverage`, `risk_ack_required` |

Only the GUI can grant these approvals. The GUI decision body accepts optional `risk_ack` (default
`false`). Approving `bundle_load` requires `risk_ack: true`, or it returns 422 `risk_ack_required`
without spending the one-use code; rejecting it and deciding `secret_send` or `osv_query` do not require
the field. A prior load approval or cache entry without acknowledgement does not authorize loading.
The CLI and the read-only scan API cannot approve a load. Settings: `bundle_scan`, `skillspector_python` and
`bundle_scan_timeout_seconds`.

### Installed packages

An installed package is a bundle copied under `<data_dir>/installed/NAME/v<n>/` and registered at every start
(see [Installed packages](GUI.md#installed-packages)). The same operations exist as `multiagent bundle install|list|remove|reinstall`.

| Endpoint | Body | Result |
| --- | --- | --- |
| `GET /api/v1/installed` | | The entries, sorted by name. |
| `GET /api/v1/installed/{name}` | | One entry; `409 bundle_not_installed` if the name is not in the registry. |
| `POST /api/v1/installed` | `{name, folder, actor, source_label?}` | `201` with the new entry (`v1`, or the next version of a name that was removed before). |
| `POST /api/v1/installed/{name}/reinstall` | `{folder, actor}` | The entry of the new version. The old one stays if the new one fails. |
| `POST /api/v1/installed/{name}/remove` | `{actor}` | `{name, removed: true, kept_data: true, ...}`. The copy and the run data are kept. |

An entry has `name`, `fingerprint`, `level` (`data` or `code`), `trust_functions`, `source_label`, `version`,
`installed_at`, `installed_by`, `channel`, `source` (`copy` or `office`), `office` (the office id of an area, else `null`), `state` (`ok`, `changed` or `missing`) and `reason`
(`null` or `installed_bundle_changed`). It never has a path. The body is strict: an unknown field, such as
`trust_functions`, gives `422 invalid_request`. `actor` must be a `user:` actor.

Errors: `409 invalid_state` with `details.reason` `bundle_name_in_use` (the name is a `--bundle` or an installed package),
`bundle_busy` (a run of the package is `queued`, `running`, `waiting_human` or `paused`), `installed_bundle_changed`
or `bundle_not_installed`; `copy_fingerprint_mismatch` when the copy does not match the folder. An invalid name, a
missing folder or a folder with symbolic links is `422 invalid_request`; a data package that does not validate is
`422 invalid_definition`. An installed copy is always scanned (see [Bundle scan](security/bundle-scan.md)).

### Offices

An office of level 1 is an `office.yaml` and its areas, with data files only (see [Install, remove and reimport](OFFICES.md#install-remove-and-reimport)). Installing registers every area as an installed package named `<office>__<area>` (`source: "office"`, `office: <id>`) in one all-or-nothing change, proposes the team of each area (it never approves one) and records the office in `<data_dir>/installed/offices.json`. The same operations exist as `multiagent office import|list|show|remove|reimport`.

| Endpoint | Body | Result |
| --- | --- | --- |
| `GET /api/v1/offices` | | `{offices: [...]}`, sorted by id. |
| `GET /api/v1/offices/{id}` | | One office; `409 office_not_installed` if it is not in the registry. |
| `POST /api/v1/offices` | `{actor, folder}` or `{actor, zip_base64, filename?}`, and `into?` | `201` with `{office, teams, warnings, changes}`. With `into`, the folder or `.zip` holds an `office.yaml` with one area, which joins that installed office. |
| `GET /api/v1/offices/{id}/requirements` | | `{office, areas: [{id, requires, covered, by, missing, text, note, action}]}`, one entry per area; `409 office_not_installed` if it is not in the registry. See below. |
| `POST /api/v1/offices/{id}/export` | `{area?, include_code?}` | `{filename, zip_base64, manifest, warnings}`; exports an office or one area as an allow-listed `.zip`. |
| `POST /api/v1/offices/{id}/plan-draft` | `{text, confirm_cloud: false, approval_id: null}` | An unsaved plan draft scoped to the office's areas; it never creates or approves tasks. |
| `GET /api/v1/offices/{id}/scene` | | The current `maf.office-scene/1` snapshot. |
| `POST /api/v1/offices/{id}/reimport` | `{actor, folder}` or `{actor, zip_base64, filename?}` | `{office, teams, warnings, changes}`; only what differs changes. |
| `POST /api/v1/offices/{id}/remove` | `{actor}` | `{id, removed: true, kept_data: true, areas, ...}`. Copies and run data are kept. |
| `POST /api/v1/offices/{id}/areas/{area}/remove` | `{actor}` | `{office, removed: true, kept_data: true, area, warnings, ...}`. |
| `GET /api/v1/offices/{id}/privacy` | | `{office, local_only, cloud: {allowed, set_by, set_at, source}, areas: [{id, sensitivity, local_only, data_class, cloud, cloud_effective, ...}], note}`. |
| `POST /api/v1/offices/{id}/privacy/raise` | `{actor, area?, sensitivity?, local_only?, cloud_allowed?: false}` | The same answer, plus `{area, from, to, changed}`; `cloud_allowed: false` revokes without an approval. |
| `POST /api/v1/offices/{id}/privacy/cloud-request` | `{area?}` | Returns `{approval_id, sha256}` for the hidden one-use approval to grant cloud permission; only the GUI completes the grant. |

An office is `{id, name, description, version, fingerprint, installed_at, installed_by, channel, level, local_only, state, issues, warnings, areas, orchestrator, relations}` and never has a path. `level` is `data`. `state` is `ok`, or `degraded` when an area is missing or its package is not `ok`; `issues` then lists `{code: "area_missing" | "area_changed", area, message}`. Each area is `{id, name, bundle, sensitivity, local_only, requires, package_state, team: {status, version_id}}`: `team` is the approved version if there is one, else the latest, and `{status: null, version_id: null}` for an area without `team.yaml`.

`teams` is `[{bundle, version_id, status}]`, one per area with a `team.yaml`. A new team is `proposed`; a team equal to the approved one keeps that version (`approved`), which is not an error. `changes` is `{added, changed, removed, unchanged}`, lists of area ids: a first install has every area in `added`; a reimport compares each area by the fingerprint of its package. `warnings` is `[{code, location, message}]`: a relation to an area the office does not have (`office_relation_unknown_area`), and when an area is removed, a plan item or relation that named it (`office_plan_item_removed`, `office_plan_after_removed`, `office_relation_removed`).

**Requirements (OFI-T06).** `GET /api/v1/offices/{id}/requirements` crosses what each area `requires` with who could cover it: the catalog models of a connected provider (from the saved connection state), the active executors and, in Mock, `mock`. It calls no model and no provider, and `requires` only informs: the router does not read it. Per area: `covered` (bool), `by` (`[{id, kind, label}]`, `kind` is `model`, `executor` or `mock`), `missing` (the ids the closest candidate lacks), `text` («Lo puedes cubrir con A o con B» or «Te falta un agente que pueda X»), `note` (the line of a «Sensible» or «Solo local» area, which counts only local agents, else `null`) and `action` (`"connect"` when something is missing, else `null`). The table of equivalences is in [Requirements](OFFICES.md#requirements). `services.offices.requirements(id)` and `requirements_for(definition)` (an office in preparation) return the same shape.

The body is strict. Any other field, such as `approve_teams`, `trust_functions` or `risk_ack`, gives `422 invalid_request`: an office of level 1 has no code to trust and nothing to acknowledge, and a team is approved only by a person in the GUI. Send exactly one of `folder` and `zip_base64`. `actor` must be a `user:` actor.

Errors. `409 invalid_state` with `details.reason`:

| `reason` | When |
| --- | --- |
| `office_has_code` | A file has a suffix outside `.yaml .yml .md .txt .json .csv`. `details.files` lists up to 20 relative paths, `details.count` the total and `details.fix` the advice. Nothing of the office is imported or run. |
| `office_exists` | An office with that id is installed (`details.office`). |
| `office_area_exists` | With `into`, the office already has that area (`details.area`). |
| `office_team_mismatch` | The area declares `team_sha256` and its `team.yaml` has another digest (`details.area`). |
| `office_not_installed` | No installed office has that id. |
| `office_area_not_installed`, `office_last_area` | Removing an area the office does not have, or its only one. |
| `office_id_mismatch` | A reimport whose `office.yaml` is another office. |
| `bundle_name_in_use` | `<office>__<area>` is a `--bundle` or an installed package. |
| `bundle_busy` | A run of an area that would change or leave is `queued`, `running`, `waiting_human` or `paused`. Nothing changes. |
| `bundle_in_office` | `POST /api/v1/installed/{name}/reinstall` or `/remove` on an area: it changes with its office. |

**Privacy of the areas** (see [Sensitivity](OFFICES.md#sensitivity)). Each area has a level (`normal`, `sensitive` or none) and `local_only`, which also exists for the whole office. The runs of an area carry `data_class` (`non_sensitive`, `sensitive` or `restricted`) in their answer, and the stored request is `sensitive: true` for the last two. `raise` only goes up: from none to any level, `normal` to `sensitive`, `local_only` false to true. `sensitivity` needs an `area`, and the body is strict. In an area, `source` is `person` or `office`. Lowering a level has no route in `/api/v1`: it is done in the GUI with a one-use approval (`office_privacy_change`, hidden like `executor_activate`).

| Status and `reason` | When |
| --- | --- |
| `409 office_level_lowering_needs_gui` | `raise` asked for a lower level (`details.from`, `details.to`). |
| `409 office_level_required` | A run of an area with no level asked for the cloud (`confirm_cloud`); `details.office`, `details.area`. Local runs do not ask. |
| `409 office_privacy_change_not_approved` | `lower_level` without an approved, unspent `office_privacy_change` for that exact change (services only). |
| `422 area_local_only` | A run of a «Solo local» area asked for `confirm_cloud`, `execution_mode: human_guided` or a `human_guided` step. |
| `422 office_privacy_area_required`, `422 office_privacy_nothing_to_change` | `sensitivity` without an area, or no level named. |

`422 invalid_request` with `details.reason`: `office_too_large` (`details.field` is `file`, `files`, `bytes` or `areas`: a file of more than 1 MiB, 2000 files, 50 MiB, or 20 areas), `office_single_area_required` (with `into`), `office_source_required`, and the `.zip` reasons `zip_unsafe_entry`, `zip_invalid` and `zip_too_large`. `422 invalid_definition` carries the problems of `office.yaml` (`details.reason` is the first issue code, `details.issues` all of them: `office_invalid`, `office_path_unsafe`, `office_duplicate_id`) or those of an area (the package validation of [Installed packages](#installed-packages)). No answer names a path of the machine.

### Approvals and `user_click_required`

Some approvals can only be given by a click in the GUI. Today they are a send that carries a secret
(`secret_send`, see the [secret gate](security/secret-gate.md)), the OSV.dev query of a bundle scan
(`osv_query`) and the load of a flagged bundle (`bundle_load`). Each one is a one-use approval stored as team
events (`user_approval.requested`, `user_approval.decided`, `user_approval.consumed`) with its kind, destination,
origin, secret types, a masked sample and the SHA-256 of the exact content, never the content.

A run held by the secret gate has `status: "waiting_human"` and `waiting_reason: "secret_review"`. The
`call_paused` event names the guard (`secret`) and carries the `approval_id`, the secret types, the masked
sample and the digest. These channels cannot approve it and answer `409` `invalid_state` with
`details.reason` `user_click_required` and `details.where` `Aprueba en la GUI → Aprobaciones`:

- `POST /api/v1/runs/{run_id}/approve`, `multiagent approve` and the MCP tool;
- `POST /gui/api/runs/{run_id}/approve`, which has the GUI token but no browser approval session;
- the task approval route (`record_approval`) when the approval it names is a secret approval. If the approvals
  cannot be read, it answers `409` with `details.reason` `approval_lookup_failed` and decides nothing.

Rejecting a held run works from any channel, since it sends nothing.

The model planner (`POST /api/v1/plans/propose`) and the free-text reader of «Nueva sesión»
(`POST /gui/api/new-session/interpret`) answer a held text with `409` `secret_review_required` (planner, in
`details.reason`) or `source: "keywords"` with `reason: "secret_review_required"` (new session), both with
an `approval_id`. After the click, the same request is sent again with `approval_id` and the call goes out once.

An executor task held by the gate returns `outcome: "secret_review_required"` with a `secret_review` object
(`approval_id`, `secret_types`, `sha256`, `destination`). The task stays held until the click.

The decision routes are local, need the GUI token and a browser approval session (cookie), and are not part of
`/api/v1`:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/gui/api/approvals` | The pending approvals as `{items}`. Needs only the GUI token. It lists the requests of runs already held by the gate, but never decides or spends one. |
| `POST` | `/gui/api/approvals/session` | `{code}`: exchanges the startup code, once, for the `HttpOnly` session cookie. `409` `approval_session_unavailable` if another browser already took it or the code is wrong. |
| `POST` | `/gui/api/approvals/{id}/challenge` | With the cookie: a one-use `{code, expires_in: 120, sha256}` tied to that pending approval. |
| `POST` | `/gui/api/approvals/{id}/decision` | With the cookie: `{code, decision: "approve"\|"reject"}`. A wrong, reused or expired code, or no cookie, is `409` `user_click_required`. For a held executor task the answer adds `resume`, the result of the check that follows the click (`checked`, `running` or `error`). |

## GUI routes (local, token-protected)

The operator GUI is served at `/gui/`; its local routes live under
`/gui/api/*` and require the GUI access token. They are not part of the public
`/api/v1` contract. For startup, session access, settings, and the security
model, see the [operator GUI guide](GUI.md).

The chat that follows a run reads three more local routes, all read only: `GET /gui/api/threads/runs/{run_id}` (with `?after=<cursor>`) rebuilds a run's conversation from its stored events (launch, steps, messages, stops with the actions the server accepts, and the final output), `GET /gui/api/threads/teams/{version_id}` returns a team version's card with its one stop, and `GET /gui/api/threads/plans/{proposal_id}` returns a plan proposal's tasks, missing teams and runs. Decisions go through the existing run, team and plan-proposal routes. `GET /gui/api/runs/{run_id}/tool-approval/preview?permission_request_id=<id>` returns the content and diff of a pending `file.write` approval without writing anything (see [Built-in file tools](FILE_TOOLS.md#approvals)). See [Operator GUI](GUI.md#views).

The box at the foot of the thread and «En fondo» in «Pendientes» use four more local routes. `GET` and `POST /gui/api/runs/{run_id}/messages` (and the same for `/gui/api/tasks/{task_id}/messages`) list the person's inbox (`?after=<seq>`, `?limit=`) and append one message `{"message_id", "kind": "note"|"adjust"|"interrupt"|"cancel", "text"}`; the server fixes the actor (`user:operator`), a body with another field is rejected (`422`), a repeated `message_id` answers the first message, and the post only appends: the run or task reads it at its next step boundary, and nothing is approved. While the availability policy is off (the default) a post is `409` with `details.reason` `availability_disabled`. `GET /gui/api/inbox/policy` answers `{"enabled", "reason"}`, and `GET /gui/api/pending/background` lists the runs that are `running` or `queued` in the item format of `GET /gui/api/pending`. See [Operator GUI](GUI.md#views) and [Architecture](ARCHITECTURE.md#availability-opt-in).

«Nueva sesión» adds token-protected routes for its two session modes, also local and outside `/api/v1`: `GET /gui/api/connections` and `POST /gui/api/connections/{id}/check` return the same facts as `/api/v1/connections`; `POST /gui/api/executors/launch` (`{"task", "executor_id", "model"?, "effort"?, "confirmed"}`) launches a task with an executor only when `confirmed` is `true` (`409 confirmation_required` otherwise, `422 model_required` for Copilot without a model), creates its task and group, calls the executor service, and answers `{task_id, launch, status}` (`202` while the attempt runs); a launch the service refuses cancels the task it created; `GET /gui/api/executors/launch/{task_id}` returns that task's status. A single session with a model uses `POST /gui/api/runs` with the `ask_once` workflow of the general bundle and `routing_preferences`. See [Operator GUI](GUI.md#views) and [Executor applications](EXECUTORS.md#choosing-and-launching-from-the-gui).

When the free text of «Nueva sesión» needs more than one workflow, `POST /gui/api/new-session/interpret` answers `reason: "plan_suggested"` with a `plan_draft`, and two more token-protected local routes prepare the plan. `POST /gui/api/new-session/plan` (`{"text", "confirm_cloud": false, "approval_id": null}`) returns the draft `{schema, goal, group_id, planner, tasks, teams, proposal}` and saves nothing; without `proposal` it carries a `reason` (`cloud_confirmation_required` or `secret_review_required` with its `approval_id`, as in `interpret`, `disabled`, `no_workflows` or `no_match`). `POST /gui/api/new-session/plan/save` (`{"draft", "confirmed": true}`) validates the draft again and stores it as a plan proposal in `proposed` status, with a proposed team version for each bundle that lacks an approved team, and answers `{proposal_id, revision, team_versions, team_errors, href}` where `href` is `#new/plan/<id>`. Without `confirmed: true` it is `409 invalid_state` with `details.reason` `confirmation_required`; a draft that does not validate is `422`. Nothing is approved and no task or run is created: approval stays in the plan-proposal routes. See [Planning](PLANNING.md#gui).

«Apps y agentes» adds two more token-protected local routes: `GET /gui/api/setup/status` returns whether launching is allowed (`gui_allow_launch`) and the console is supported, the presence of each permission template, and the fixed, dated install and sign-in plans; `POST /gui/api/setup/console` (`{"id", "step"}`, with `step` `install` or `login`; any other field is `422`) opens the visible Windows console for that step and answers `202` with `{ok, id, app, step, plan, console_id, state}` as soon as the console starts, without waiting for it to close (a step with nothing to open answers `200`), or `403 launch_disabled`, `409 console_already_open`, `409 launch_unsupported` or `409 launch_failed`; `GET /gui/api/setup/console` returns the latest console, `{"state": "none"}`, `open`, `closed` (with `console_exit_code` and `recheck`) or `failed`. See [Operator GUI](GUI.md#views) and [Executor applications](EXECUTORS.md#installing-and-signing-in-to-an-executor).

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
`human_submission_invalid`. A held secret answers `invalid_state` with `details.reason` `user_click_required` when a channel other than the GUI tries to approve it (see [Approvals and `user_click_required`](#approvals-and-user_click_required)). A bundle load can also answer `409` with `bundle_scan_pending`, `bundle_scan_running` or `bundle_scan_blocked` (see [Bundle scan status](#bundle-scan-status)).

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
