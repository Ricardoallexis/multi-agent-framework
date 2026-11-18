# Architecture

## Purpose

Multi-Agent Framework is a domain-neutral runtime for teams of AI agents, humans, models, and tools. Today it coordinates declared workflows composed of specialized agents, and combines local execution, cloud providers, and human participation without coupling workflow logic to a specific model provider. The planned evolution (teams, planning, delegation, optional backends) is described in [`COMPARISON.md`](COMPARISON.md) and the [roadmap](../ROADMAP.md). This page describes the current code.

## Main components

```text
main.py / CLI / Python callers
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

### `multiagent/model_router.py`

Selects the preferred model and optional fallback according to workflow bindings and execution mode.

### `multiagent/adapters/`

Isolates provider-specific behavior. Workflows depend on capabilities and structured contracts rather than provider SDK details.

### `workflows/`

Contains declarative definitions for agent order, prompts, contracts, preferred models, fallbacks, conditions, and checkpoints. A step may instead name a registered deterministic function that calls no provider; see [Function steps](FUNCTION_STEPS.md).

### `prompts/` and `skills/`

Contain versioned prompts and reusable instruction fragments assembled by the runtime.

### `multiagent/contracts.py`

Defines the Pydantic input and output contracts exchanged between workflow steps.

### `multiagent/db/`

Provides SQLite persistence, migrations, and access to run state, artifacts, projects, brand profiles, assets, publications, telemetry, and tasks (`task_store.py`: task groups, tasks, dependencies, and the team event log, where a state change and its event are committed together).

## Local persistence

Runtime data is not part of the versioned source tree. Editable checkouts use
`.local/data/` by default; installed distributions use a directory beneath
`~/.multi-agent-framework/`. Public definitions resolve from the checkout in
editable mode and from bundled package resources in an installed wheel.

## Frontend

The current `F00` release does not include a UI. A future frontend should consume the public API rather than access SQLite or internal engine implementations directly.
