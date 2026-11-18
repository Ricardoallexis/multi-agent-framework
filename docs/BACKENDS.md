# Execution backends

An execution backend is whatever turns a queued run into executed steps. The run service and the local worker talk to it only through the `ExecutionBackend` interface in `multiagent/backends/`, so the same workflow definitions, contracts, runs and human steps work with any backend that meets the contract below.

Status (roadmap [S4](ROADMAP_STAGES.md#s4--execution-backend-abstraction-)): the interface, its contract and the registry exist (S4.A1). The native engine behind the interface (S4.A2), a mock backend (S4.A3) and a conformance suite (S4.B1) are in progress. An optional Microsoft Agent Framework backend (S4.C) and several workers (S4.D) are planned.

## The contract

The `Store` is the one durable record of a run. Every backend reads and writes it, and keeps no run state of its own between calls.

| Part | What a backend does |
| --- | --- |
| Execute | `process_run(run_id)` takes a `queued` run, executes steps from `current_step` and returns the stored run once it is `completed`, `failed`, `cancelled`, `rejected` or `waiting_human`. A run that is not queued comes back as it is, after its approval deadlines are checked. Each attempt is recorded with `Store.record_step` and each output with `Store.add_artifact`. A failure of the run is stored, not raised; what `process_run` raises is a fault of the backend, and the worker fails the run with it. |
| Events | Only through the `Store`: `Store.db.log_event`, or a `Store` operation that writes its own event in the same transaction. Every backend emits `run_started`, `step_started`, `step_completed`, `workflow_completed`, `workflow_failed` and `workflow_cancelled`. When a budget (`max_llm_calls`, `max_run_seconds`) stops a run, the backend sets `budget_exhausted` on it. A run with an artifact to show waits as `waiting_human` / `budget_exhausted` and gets the `budget_exhausted` event. A run without one fails with `workflow_failed`. |
| Usage | Only through the `UsageRecorder` the backend exposes as `usage_recorder`: one record per model call. A backend without real calls records zero tokens. |
| Pause for a person | A step that needs a person sets `status=waiting_human` with a `WaitingReason` and returns. A backend never blocks a thread waiting for a person. |
| Resume | The person's decision goes through the run service (approve, reject and the held-call decisions) or through `submit_human_step`. A run that goes back to `queued` is picked up again and continues from `current_step`. |
| Cancel | `cancel` sets `cancel_requested`. The backend checks it before each step and before each model call, and ends the run in `cancelled`. |

The interface has five methods (`preflight`, `process_run`, `submit_human_step`, `review_point`, `check_approval_deadlines`) and three attributes (`name`, `capabilities`, `usage_recorder`).

## Capabilities

A backend declares what it really does in `capabilities`. Every backend has `usage`, `budgets` and `cancellation`. The others are optional, and the conformance suite checks each one only against the backends that declare it:

| Capability | Meaning |
| --- | --- |
| `human_steps` | Pauses `human_guided` steps until `submit_human_step` validates a submission. |
| `review` | Pauses at checkpoints for approve or reject, and reruns a rejected step with the revision feedback. |
| `recovery` | Continues a run the worker recovered after an interruption. |
| `approval_deadlines` | Expires checkpoint approvals after `approval_timeout_seconds`. |
| `routing` | Selects models with the router, routing profiles and bounded escalation. |
| `tools` | Runs the tools a step declares, with per-call approval. |
| `step_cache`, `context_compaction`, `document_barrier` | The step result cache, context compaction and the untrusted-content barrier. |
| `budget_guard` | Asks `budgets_source` before each paid call. The backend has a writable `budgets_source` attribute. |
| `inbox` | Reads `availability_source` for the person's messages at step boundaries. The backend has a writable `availability_source` attribute. |

## The registry

`build_backend(name, context)` builds a backend from a `BackendContext` (the store, settings, definitions and artifact writer every backend shares, plus the router, tools and policies for the backends that use them). The built-in backends are declared as `module:function` and imported only when they are built. A host registers its own with `register_backend(name, factory)`. A name nobody registered raises `UnknownBackend` with the list of available names, and an object that does not meet the contract raises `InvalidBackend` with what is missing.
