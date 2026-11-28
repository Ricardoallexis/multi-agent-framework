# Architecture

## Purpose

Multi-Agent Framework is a domain-neutral runtime for teams of AI agents, humans, models, and tools. Today it coordinates declared workflows composed of specialized agents, and combines local execution, cloud providers, and human participation without coupling workflow logic to a specific model provider. The current code also includes task and team-definition foundations; runtime role use is partial, while fuller planning, delegation, and optional backends remain on the [roadmap](../ROADMAP.md). See [`COMPARISON.md`](COMPARISON.md) for the release, checkout, and roadmap distinctions. This page describes the current code, which may include changes not yet published in a release.

## Main components

```text
main.py / CLI / Python callers / local GUI (/gui/)
  -> FastAPI app (HTTP API)
  -> ApplicationServices (multiagent/services/)
      -> definitions (workflows, agents, bundles)
      -> tasks -> TaskStore + pure dependency graph (multiagent/tasks/)
      -> runs -> RunService (one per bundle runtime)
          -> ExecutionBackend (multiagent/backends/, chosen by EXECUTION_BACKEND)
            - mock (no providers)
            - native = WorkflowEngine
              -> ModelRouter
                  -> adapters/
                     - Ollama
                     - Gemini
                     - OpenAI
              -> Prompt loader
              -> Contracts
              -> Store / Database
              -> Artifact & Asset services
              -> Human checkpoints
```

### `multiagent/services/`

The public Python layer (`ApplicationServices`) used by the HTTP API and, through it, the CLI. It lists and validates definitions, creates and steers runs (including definition bundles, each with its own store and worker), coordinates tasks with dependencies through one transition function, and reports failures as `ServiceError` with stable codes. See [`API.md`](API.md) and [`TASKS.md`](TASKS.md).

### `multiagent/api.py`

Exposes the HTTP API over the application services: definitions, runs, reviews, cancellation, externally executed human-guided steps, artifacts, task groups, tasks, and team events, with structured errors.

### `multiagent/cli.py`

Provides development and operational commands for diagnostics, runs, Human-in-the-Loop flows, tasks and team events, assets, publications, metrics, and branding.

### `multiagent/tasks/`

The pure dependency graph of tasks: cycle detection, `blocked` and `ready` status, blockers, released dependents, and parallel layers. It has no database or service imports, so the rules can be tested and reused on their own.

### `multiagent/backends/`

The seam between the run service and whatever executes a run: the `ExecutionBackend` interface, its contract (events and usage only through the `Store`, declared capabilities) and a registry by name. `build_system` builds the backend named by `EXECUTION_BACKEND`: `native` (the default, the workflow engine below) or `mock`, which keeps the same durable state but calls no provider. Optional OpenTelemetry attaches when a backend is built. One conformance suite runs against both. Runs of a registered bundle build the native engine directly (`WorkflowEngine.from_bundle`). See [`BACKENDS.md`](BACKENDS.md).

### `multiagent/worker.py`

The local worker has one dispatcher and up to `WORKER_CONCURRENCY` run threads. Worker processes that use the same SQLite database coordinate through atomic claims and renewable leases; total and per-project active-run limits are checked in the claim transaction. The dispatcher renews active leases before recovering expired ones, so it does not requeue its own live runs. When a worker stops, it waits up to `WORKER_SHUTDOWN_WAIT_SECONDS` for active runs and leaves unfinished leases for recovery after they expire. This does not coordinate workers on different databases or external queues. See [Several workers](BACKENDS.md#several-workers) for settings and recovery behavior.

### `multiagent/workflow_engine.py`

The native backend. Executes workflow steps, builds context, applies routing, validates contracts, persists artifacts, and pauses execution at human checkpoints.

A step may declare `tools:` that run before its model call, through `ToolInvoker` and the tool registry that `build_system` creates (`System.tool_registry`, with the built-in synthetic `mock.*` tools; a host may register its own or MCP tools before running). Each entry names a tool (`tool: mock.echo`, optional `version`) or a capability resolved by `select_tool` (`capability: text.upper`), with literal `arguments`; a string argument written `${name}` takes the run input `name`. Every call leaves `tool_call_*` events in the run log, and its result reaches the prompt as untrusted data. A call with a write or destructive effect pauses the run in `waiting_human` / `tool_approval`; approving it with the shown `permission_request_id` (GUI, CLI, or `RunService.approve(..., permission_request_id=..., person=True)`) queues the run and the call runs once; rejecting it fails the run.

```yaml
steps:
  - id: create
    # ...
    tools:
      - tool: mock.note_write
        arguments: {target: notes/post.txt, text: "${topic}"}
```

### Availability (opt-in)

The availability policy keeps the orchestrator from blocking its caller. It is off by default (`AVAILABILITY_ENABLED`, read by `multiagent/services/availability.py`); with it off, every path below behaves as before and the inbox refuses writes (`availability_disabled`). Nothing here turns it on for you.

- **Background and heartbeat.** With the policy on, `services.executors.run_task` starts an executor attempt in a background thread and waits at most `AVAILABILITY_MAX_WAIT_SECONDS` (30 by default). A short attempt answers as before; a longer one answers `running`, and its end is still recorded as `executor.attempt_finished`. While it runs, its process watch writes `executor.heartbeat` every `AVAILABILITY_HEARTBEAT_SECONDS` (60 by default) with only the elapsed time, idle time and attention flag, never any content; the 15-minute stall check reuses the same watch. A resume stays synchronous.
- **The person's inbox.** `services.inbox` appends `user.message` events to the log that already exists (`run_events` for a run, `team_events` for a task; no table of its own): `cancel`, `interrupt`, `adjust` or `note`, numbered by `seq`, idempotent by `message_id`, and never written by a program or by the assignee. Nothing reads the inbox when a message is posted. A run reads it in its worker thread at the boundary of each step (the `UserInboxGuard`, before a model call); a background task attempt reads it at each tick of its process watch. In both, the messages are read in the policy's order (`cancel` first), each is marked `user.message_consumed` before anything is done, and a `note` only goes on the record. `cancel` ends the run or task as cancelled; `interrupt` and `adjust` stop it and hold it on an approval that only the person who wrote the message can record. The inbox never approves, accepts or decides anything.
- **Hand-off at the boundary.** An attempt is never moved in the middle of a step. When the next attempt of a task starts (`resume_task`) and the context measure of the CMP profile is over its threshold, the framework saves a checkpoint, retires the old session once (`session.retired`) and launches a new one with the checkpoint and the context snapshot; the package keeps `inbox_cursor`, and the new session reads `messages(after=cursor)`. It first reads the inbox again: a `cancel` nobody has acted on stops the hand-off before anything is claimed.
- **Surfaces.** `POST /api/v1/runs/{id}/messages` and the task routes, `multiagent/gui/inbox_api.py` for the GUI (see [Operator GUI](GUI.md#views)), and the integration test `tests/test_availability_integration.py`, which runs a long fake attempt through all of it. The sessions of Claude, Codex and Copilot as apps are outside this path, and the orchestrator threshold stays reserved.

### `multiagent/model_router.py`

Selects the preferred model and optional fallback according to workflow bindings and execution mode.

### `multiagent/adapters/`

Isolates provider-specific behavior. Workflows depend on capabilities and structured contracts rather than provider SDK details.

### `workflows/`

Contains declarative definitions for agent order, prompts, contracts, preferred models, fallbacks, conditions, and checkpoints. A step may instead name a registered deterministic function that calls no provider; see [Function steps](FUNCTION_STEPS.md).

### `prompts/` and `skills/`

Contain versioned prompts and reusable instruction fragments assembled by the runtime.

### `multiagent/contracts.py`

Defines the Pydantic input and output contracts exchanged between workflow steps, including the two neutral ones any office can use (`FreeTextAnswer`, `DocumentSummary`). A bundle can declare its own as data in `contracts.yaml` (`multiagent/contracts_data.py`).

### `multiagent/db/`

Provides SQLite persistence, migrations, and access to run state, artifacts, projects, brand profiles, assets, publications, telemetry, and tasks (`task_store.py`: task groups, tasks, dependencies, and the team event log, where a state change and its event are committed together).

`Database.connect()` reuses SQLite handles by database and thread. A nested lease
gets a separate handle, so reads inside a write transaction see only committed
data. Returning a handle closes its cursors and rolls back uncommitted work.
`Database.tx()` serializes in-process writers by database file while SQLite's
timeout still handles other processes. `Database.close()` releases idle handles
and retires active ones when returned; a later call can reopen the database.
`RunServices.close()` and `release_bundle()` close their respective databases.
Set `DB_REUSE_CONNECTIONS=false` to use a fresh handle per call.

## Local persistence

Runtime data is not part of the versioned source tree. Editable checkouts use
`.local/data/` by default; installed distributions use a directory beneath
`~/.multi-agent-framework/`. Public definitions resolve from the checkout in
editable mode and from bundled package resources in an installed wheel.

## Frontend

The local operator GUI at `/gui/` is the supported local interface for 1.0. It is served by the local API and calls the same application services as the HTTP API and the CLI (the CLI reaches them through the HTTP API); it is an operator view, not a separate orchestration layer. The GUI defaults to Mock mode and the server binds to loopback. See [`GUI.md`](GUI.md) for its views and controls.

The current checkout may contain additional GUI work under `[Unreleased]` in the changelog. That work is not part of the published 1.0 surface until released.
