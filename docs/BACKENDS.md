# Execution backends

An execution backend is whatever turns a queued run into executed steps. The run service and the local worker talk to it only through the `ExecutionBackend` interface in `multiagent/backends/`, so the same workflow definitions, contracts, runs and human steps work with any backend that meets the contract below.

Function steps call no provider and execute identically in every backend.

Status (roadmap [S4](ROADMAP_STAGES.md#s4--execution-backend-abstraction-)): the interface, its contract and the registry (S4.A1), the native engine behind the interface (S4.A2), the mock backend (S4.A3), the conformance suite (S4.B1), which runs against both in CI (S4.B2), and several workers with controlled concurrency (S4.D1) are done. An optional Microsoft Agent Framework backend (S4.C) and a resource-aware scheduler (S4.D2) are planned.

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

The interface has five required methods (`preflight`, `process_run`, `submit_human_step`, `review_point`, `check_approval_deadlines`) and three attributes (`name`, `capabilities`, `usage_recorder`). A backend declaring `external_output` also provides the optional `external_output(run_id, output, commit=False)` operation. Validation returns normalized output without changing the run; commit records the output on a waiting step or a new review attempt.

## Capabilities

A backend declares what it really does in `capabilities`. Every backend has `usage`, `budgets` and `cancellation`. The others are optional, and the conformance suite checks each one only against the backends that declare it:

| Capability | Meaning |
| --- | --- |
| `human_steps` | Pauses `human_guided` steps until `submit_human_step` validates a submission. A backend may also offer read-only `check_human_step(run_id, raw_response)`, returning `ok`, `contract`, and field-level `problems`; callers receive `invalid_state` with `details.reason=unsupported` when it is absent. |
| `external_output` | Validates externally produced structured output and records it on a waiting step or a new attempt at a review checkpoint. |
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

When enabled, OpenTelemetry attaches at backend construction and observes its store events, usage records and queued run passes (see [Telemetry](TELEMETRY.md#opentelemetry-traces-and-metrics)).

## Choosing a backend

`EXECUTION_BACKEND` (the `execution_backend` setting, `native` by default) names the backend that `build_system` builds and gives to the run service and the local worker. `multiagent doctor` shows the active backend and the available ones. With a backend other than `native`, `System.engine` is still a native engine, kept only for the reads that use it (workflow metadata, the router of Nueva sesión); it runs nothing and has no telemetry.

| Backend | Use | Capabilities |
| --- | --- | --- |
| `native` | The workflow engine: real providers, routing, tools and every policy. | All 15. |
| `mock` | Deterministic runs without providers, routing or tools, for tests and demos. Model steps take `sample_outputs` or a minimal valid example of the step's output schema, and each one records zero tokens. | `usage`, `budgets`, `cancellation`, `human_steps`, `review`, `recovery`, `external_output`. |

Runs of a registered bundle do not go through the setting: each bundle builds its native engine with `WorkflowEngine.from_bundle`.

## Several workers

Several local worker processes can share one SQLite database. Each claims a queued run atomically in a transaction and holds the claim with a lease (`worker_id` and `heartbeat_at`), renewed while the run executes. The claim transaction also checks the active-run limits, so the total and per-project limits apply across workers sharing that database. With leases being renewed, a run is not claimed by two workers at once.

The worker renews its active leases before recovering expired leases and claiming more work. A running run is recovered only after its lease expires: it emits `run_interrupted` and is requeued with `run_recovered`, unless cancellation was requested, in which case it becomes `cancelled`. A queued run with a stale claim and no heartbeat loses that claim. When stopping, a worker waits up to `WORKER_SHUTDOWN_WAIT_SECONDS` for its runs; if they do not finish, it leaves their leases for another worker to recover after expiry.

| Setting | Default | Purpose |
| --- | --- | --- |
| `WORKER_CONCURRENCY` | `1` | Maximum runs executed at once by one worker process. |
| `WORKER_MAX_ACTIVE_TOTAL` | unset (unlimited) | Maximum active runs across worker processes sharing the database. |
| `WORKER_MAX_ACTIVE_PER_PROJECT` | unset (unlimited) | Maximum active runs per project across worker processes sharing the database. |
| `WORKER_LEASE_SECONDS` | `30` | Lease duration before an unrenewed claim can be recovered. |
| `WORKER_SHUTDOWN_WAIT_SECONDS` | `30` | How long a stopping worker waits for its active runs. |

`multiagent worker [--concurrency N]` runs a worker against the configured database. The option overrides `WORKER_CONCURRENCY` for that process; `N` must be from 1 to 256.

The concurrency service reports an effective limit and reason for the GUI. Its source order is CLI, then explicit `WORKER_*` settings, then `gui-settings.json`. A per-app or per-model cap can only lower the selected total cap. `WORKER_MAX_ACTIVE_PER_APP` and `WORKER_MAX_ACTIVE_PER_MODEL` are reserved settings for the later worker integration; this service does not apply them to running work yet. Auto uses the tightest quota window reported by `usage.plans()`: it subtracts the selected margin (3, 5, 10, 15, 20, 30 or 40 percent), divides by the declared cost per step, and in Eco or Normal spreads the remaining steps over 15-minute blocks until reset. Performance and Maximum use the available steps immediately. The manual cap and the machine cap bound every mode, Eco and Normal included. When no step is left once the margin is subtracted, the limit is 0 in every mode with the reason `quota_margin_reached`, so the reserve is not spent; a limit of at least 1 is only the floor of the Eco and Normal spreading when steps remain. Missing quota data permits one slot (`no_quota_data`). The default step-cost estimate is 5 percentage points per step until a caller supplies measured costs. Reading quota does not dispatch a model call.

This coordination does not cover workers using different databases, external queues, or resource-aware scheduling (S4.D2).

## Provider certificates

On a machine with an antivirus or a proxy that inspects TLS, a provider fails with `CERTIFICATE_VERIFY_FAILED`. `PROVIDER_TLS_TRUST` chooses which certificates the HTTP clients of Gemini, OpenAI and Ollama trust:

| Value | Meaning |
| --- | --- |
| `certifi` | The default and today's behaviour: each client keeps its own configuration. |
| `system` | The certificate store of the operating system (`ssl.create_default_context()`). |
| `file:<path>` | A PEM file of your own. Only through `.local/.env`; the GUI never writes it. |

The value in force is, in this order: the `provider_tls_trust` key of `gui-settings.json` (written by the GUI; it accepts only `certifi` and `system`), then `PROVIDER_TLS_TRUST` from the environment or `.local/.env`, then `certifi`. A value outside the three is an error at startup.

The setting applies to the clients of the providers, not to the executors. OpenAI gets an `httpx` client with the context, Ollama's `httpx.Client` gets `verify=<context>`, and Gemini gets it through `http_options.client_args` (supported by the pinned `google-genai` 2.13.0). Changing the value drops the cached clients, so it takes effect without a restart.

A certificate failure is recognised by walking the `__cause__` and `__context__` chain of the error (up to 10 links), because the SDKs wrap the `ssl` error. Every provider reports it as `tls_certificate`: in the connection card, in **Probar conexión** (`POST /gui/api/connections/{id}/probe`, which asks the provider for its model list or health and only runs when pressed) and in `multiagent doctor --deep`, whose report has `tls.<provider> = {"trust", "state"}` with `state` one of `ok`, `tls_certificate`, `skipped` or `error`. Gemini no longer counts a certificate failure as `no_access`.

On a `tls_certificate` card the GUI offers **Usar los certificados de Windows** (**Usar los certificados del sistema** outside Windows). Pressing it writes `provider_tls_trust = "system"`, records the event `provider.tls_trust_changed` (`connection_id`, `from`, `to`) and probes again. It is a decision of the person and nothing else presses it. **Volver a los certificados de siempre** writes `certifi`.

