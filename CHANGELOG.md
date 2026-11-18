# Changelog

Notable changes to the public project are documented here. The project uses `M-B-F` product versions and a separate PEP 440 version for the Python package. Because the roadmap is a living document, each release also lists the roadmap changes it publishes under **Roadmap**.

## [Unreleased]

### Added

- Project memory (S12.A1): facts, decisions and conventions that belong to one project, kept in SQLite (migration `014_project_memory.sql`) with their author as provenance, a trust level computed from it (only a `user` actor is trusted; an untrusted author cannot claim `high` confidence) and a confidence. Manage them with `multiagent memory add|list|show|delete`, `POST/GET /api/v1/projects/{project}/memory`, `GET/DELETE /api/v1/memory/{id}` or `services.memory`; only a `user` actor may delete. A workflow, or a single step, that sets `project_memory` (`true` or `{max_chars, kinds}`) gets the entries in its context as a `## Project memory` section, each one quoted as untrusted data with its provenance and screened by the document barrier when it is on, up to a size cap and recorded in a `project_memory_included` event. It is off by default, so existing workflows are unchanged. Entries live in the database of the framework that runs the workflow; a bundle run reads its own database.
- OpenTelemetry traces and metrics of the native engine (S10.B1, partial), off by default and behind the optional `otel` extra: spans for the run, each step, each provider call and each tool call with `gen_ai.*` attributes and the ids of the run events, plus token-usage and duration histograms, exported over OTLP through the standard `OTEL_*` variables. Prompts and responses are not attached unless `TELEMETRY_OTEL_CAPTURE_CONTENT` is on. See `docs/TELEMETRY.md`.
- Read-only reader of Antigravity (agy) sessions in `multiagent/sessions/antigravity.py`: it lists conversations from `conversation_summaries.db` (id, title, last change, working folder, run status, parent) without reading any message, and a pure parser of the text of `agy -p /usage` (`parse_agy_usage`) that returns the remaining percent and reset of each limit. The source is listed by `multiagent sessions` and the session API (`GUI_SESSION_SOURCES`, root `GUI_ANTIGRAVITY_HOME`), and `multiagent usage-report plans` includes an `antigravity` app (five-hour and weekly windows per model group and per model, from `agy -p /usage`, which spends no model).

- The GUI's Nueva sesión chat reads free text with the configured provider (`services.new_session`, `POST /gui/api/new-session/interpret`): a model proposes a workflow and its inputs as JSON, checked against the declared workflows and inputs, and the operator still confirms with Lanzar. The text enters the prompt as untrusted data and the egress policy applies. With Mock the proposal is deterministic; with no model, an error or an invalid answer the chat compares words as before and says which one it used. Settings `GUI_NEW_SESSION_INTERPRET` (turn it off) and `GUI_NEW_SESSION_MODEL`.

### Removed

- Remove the Stage 0 run-console prototype, its route, CLI command, package, and guide.

## [M1-B03-F01] - 2026-11-18

Python package: `1.0.0`.

Release 1.0 (MVP 1.0, scope in [`docs/SCOPE.md`](docs/SCOPE.md)). It is `M1-B03-F01-rc1` plus the fixes below, found by installing the release candidate from a clean clone.

### Changed

- Bump the product version to `M1-B03-F01` and the Python package version to `1.0.0`, and mark the package as stable.

### Fixed

- `multiagent doctor`, the last step of `setup_windows.bat`, no longer hangs when Ollama is installed but stopped: it runs `ollama ps` only when the Ollama server answers (otherwise the CLI starts the Ollama app on Windows), and its diagnostic commands stop at their timeout even when they leave a process holding their output.
- `multiagent doctor` accepts every Python version that `pyproject.toml` declares (3.12 to 3.14); it reported Python 3.14 as unsupported, which failed the setup.

## [M1-B03-F01-rc1] - 2026-11-17

Python package: `1.0.0rc1`.

First release candidate of 1.0 (MVP 1.0, scope in [`docs/SCOPE.md`](docs/SCOPE.md)): clean start on `main`, the real three-step flow on Ollama, workflow-step tools through `ToolInvoker` with per-call approval, `/gui/` as the one local interface, and docs in sync.

### Added

- Run the tools a workflow step declares (`tools:` with a tool id or a capability resolved by `select_tool`, literal arguments or `${input}` values) through `ToolInvoker` in the real engine; results reach the step prompt as untrusted text, and a write tool pauses the run in `tool_approval` until a person approves that call (MVP 1.0).
- Approve a pending tool call from the operator GUI: the run screen shows the step, tool, summary, effects and actions (never raw arguments) and «Aprobar esta llamada» sends the `permission_request_id` it was shown; rejecting fails the run without running the tool (MVP 1.0).
- Add an ordered call-guard chain for inbox, context, content review, call limits, and budget checks before model calls.
- Add opt-in context compaction that replaces older step outputs with validated summaries while preserving inputs, contracts, and recent outputs.
- Close stage CMP: document context-profile activation, workflow summaries and task snapshots, event visibility, and offline quality comparison. See [`docs/CONTEXT_COMPACTION.md`](docs/CONTEXT_COMPACTION.md).
- Treat untrusted content as data: detect instruction-like or hidden text, quarantine it or pause for review, and never accept it as effect authorization.
- Add context-window estimates and configurable usage budgets, with budget cutoffs and a human approval pause before a call that would exceed a budget.
- Add provider health and availability evidence for opt-in routing, plus a user inbox with explicit acknowledgement.
- Add task holds and condition-based waits that resume the same session and record each hold and resumption.
- Add project templates and services for validating plan data and creating projects atomically against a confirmed plan hash.
- Add read-only setup diagnostics for installed agent applications and requirements.
- Add reviewed executor version pins and diagnostics that report when installed versions differ.
- Add GUI usage summaries and a task work map for following project work.
- Fill the right-panel sections «Cambios», «Tokens» and «Fuentes» of the GUI (GUI2-T05) for the session or run open in the centre: files changed (with +/- lines only where the source counts them), orchestrator against workers with the estimated cost and its price evidence («desconocido», never 0, when unknown), and the references used (files, web pages without query or fragment, searches, attachments; for a run, workflow, prompts, contracts and input names). New read-only route `GET /gui/api/runs/{id}/usage` (token-protected, same data as `/api/v1/runs/{id}/usage`). Only paths and names are shown, never message text. See [`docs/GUI.md`](docs/GUI.md).
- Complete the ORQ-6 task execution path for Claude, Codex, and Copilot: shared process handling, adapters, and task launch/resume/open operations are available through the executor service. All three adapters remain disabled by default. CI uses synthetic executables only; no installed CLI, live authentication, subscription quota, real session files, app visibility, or cross-process locking was verified. See [`docs/EXECUTORS.md`](docs/EXECUTORS.md).
- Extend task coordination with exact source-aware snapshot preview/import, durable receipts and guarded rollback; keep native dependencies and legacy release milestones/facts separate; expose the capability-tagged release context through API v2 with fingerprint/unchanged support. These operations do not claim a real migration or bulk snapshot import facility. See [`docs/TASKS.md`](docs/TASKS.md) and [`docs/API.md`](docs/API.md).
- Add the three-zone GUI shell (GUI2-T02): a left sidebar with "Nueva sesión" (for now the create-run form at `#new`), a search box and the sessions of Claude, Codex and Copilot plus the framework runs grouped by project, with a status dot and a count per app; the view of the current route in the centre, where consecutive tool calls of a session fold into one "Trabajó N min · K herramientas" block; and a collapsible right panel (Cambios, Subagentes, Tokens, Fuentes) whose state the browser remembers. Below about 900 px the sidebar is a drawer and the panel stacks under the content. It uses only `/gui/api/sessions` and `/gui/api/runs`; the old list of children in the session screen is replaced by the subagents panel. See [`docs/GUI.md`](docs/GUI.md).
- Add the GUI subagents panel (GUI2-T04): `GET /gui/api/sessions/{id}/children` (token-protected, optional `?status=active|done`) lists the subagents, spawned threads and workers of one session in a single model (Claude `subagents/`, Codex child threads, Copilot `subagent.*` events and the steps of a framework run) with model, level when the catalog maps it, status (`running`, `testing`, `idle`, `done`, `stopped`, `unknown`) from recent activity, tokens (`null` when unknown) and a link to its conversation, never message text. `/gui/api/stream?session=<id>&only=children` sends a lightweight `children` event when a child appears or changes status. The panel (`subagents-panel.js`, mounted by `subagents-mount.js` inside the «Subagentes» section of the right panel of the GUI shell) has Activos/Terminados tabs and opens a child through the existing `#sessions/<id>` route.
- Make the session viewer cheap on large histories (GUI2-T10): the readers keep a bounded per-file index (absolute path + `mtime_ns` + size, at most 5,000 entries, least recently used first) of session rows and children; a transcript that only grew is not read again, only its appended bytes are folded into the cached summary (a rewrite, a smaller file or a changed head is read whole); the listing of each source is reused for 2 seconds; and no file is read while holding the service lock, so a slow listing never delays the live stream. Adds `GET /gui/api/sessions/counts` (token-protected): sessions per source (`claude`, `codex`, `copilot`, `framework`) and per status (`active`, `idle`, `archived`, `unknown`), also available as `services.sessions.counts()`. Response shapes of the existing routes do not change.
- Add the guided «Nueva sesión» workflow chat (GUI2-T03): starter options and slash commands, free-text workflow suggestions by deterministic matching of names and descriptions (no model reads the text), one input question at a time, and a summary with prompt preview, edit and form options and an explicit launch. See [`docs/GUI.md`](docs/GUI.md).
- Add routing controls to the guided chat confirmation (GUI2-T06): pick an available mode, profile, level, model and effort per workflow step where the preview offers choices; the preview validates preferences without creating a run, and launch still needs explicit confirmation. The chat launches framework workflows only.
- Add the read-only agent tree at `#tree/<session-id>` (GUI2-T08) with status, model, level, elapsed time and token data, an active-only filter, node details, and child changes over SSE with a polling fallback.
- Add the `#usage` view (GUI2-T09) with plan measurements, token totals by application, model, level and role, and estimated framework costs with price evidence (`/gui/api/usage/plans`, `/usage/tokens`, `/usage/costs`). No budget or alert/cutoff controls.
- Add shared GUI components in `multiagent/gui/static/js/ui/` (GUI2-T11): status chips, progress bars, accessible tabs and level badges.
- Add the token-protected `GET /gui/api/map` route and the `#map` work-map view (GUI2-T12, GUI2-T13): projects, stages, tasks and linked sessions in tree or list form, with filters and folding; it refreshes incrementally from child-session notices with deduplication and a polling fallback (GUI2-T14) and meets keyboard, reduced-motion and contrast checks (GUI2-T15).
- Give the executor applications the model and effort of the routing profile (ECO-T08, economic profile): when `run_task` is given a `profile_id` and no explicit `model`/`effort`, the launch request takes them from the model routing decision of the assignment profile's `model_routing_profile_id` (economic or not) for the task's `step_kind` (`orchestration`, `planning`, `review`, `work`; default `work`). The router is asked once per executor with the catalog models that executor cannot receive excluded (`RoutingRequest.excluded_models`), so each executor gets the cheapest model it can take; Copilot never gets a Claude model (`CopilotAdapter.refuses_model`). A value from the profile is sent only if the executor declares it (`model_support` / `effort_support` values; Claude's effort is unknown, so it is never sent) and otherwise dropped with a reason; the caller's values always win (`source: user`) and are still refused when the executor declares it does not take them. The assignment preview shows the model and effort each executor would be asked to use, with the routing profile, mode, level, price source and effort source (`decision.evidence.executor_requests`), computed by the same function as the launch. The evidence is recorded on every attempt in the new team event `executor.routing_selected` (and `model_source`/`effort_source` in `executor.session_recorded` and `executor.attempt_finished`). `step_kind` is also accepted by the preview and run requests (HTTP and `--step-kind`). See [`docs/model-routing.md`](docs/model-routing.md).
- Run an ORQ task in an executor application (`services.executors`, ORQ-T51): `run_task` launches a ready task in Codex, Claude or Copilot through its adapter (the task envelope in, a work folder the framework owns, read or write permission from the task's file scope) and submits the result envelope with `submit_result`, so the gate decides; the lifecycle changes only through `tasks.transition` (`claim`, `start`, `resume`, `pause`). The session id and the attempt are recorded as team events (`executor.session_recorded`, `executor.attempt_finished`, no migration). An attempt without a valid result envelope, a timeout or a block by an open session leaves the task `paused` with trimmed evidence; a quota or limit error also pauses the executor until a configured time (`EXECUTOR_QUOTA_PAUSE_MINUTES` or `quota_pause_minutes` in the catalog); nothing is retried automatically. `resume_task` continues only a session the framework created and refuses one opened in an app in the last 12 hours (`session.opened_in_app`, written when the GUI launches Claude or `open_target` hands out a target; links the browser follows for Codex and Copilot leave no record). `open_target` returns the adapter's target for the recorded session. Model and effort come from the caller or from the assignment decision (see the ECO-T08 entry below). The catalog now names the adapter of `codex_cli`, `claude_cli` and `copilot_cli`, which stay `enabled: false`. HTTP: `GET /api/v1/executors/status`, `POST /api/v1/executors/run|resume|open`; CLI: `multiagent executors run|resume|open|status`, with parity tests that use fake programs only (no CLI is ever launched by the tests).
- Add the local operator GUI at `/gui/`, started with `multiagent gui` or `python -m multiagent.gui`. It provides a read-only session viewer for configured Claude, Codex, Copilot, and framework sources, plus framework-run creation, prompt preview, run details, human actions, and live session follow. It uses a local bearer token and loopback-only server; see `docs/GUI.md`.
- Add read-only local session commands: `multiagent sessions list`, `multiagent sessions show`, and `multiagent sessions items`, backed by the same session service as the GUI.
- Add confirmation-gated "Open in app" actions for Codex and Copilot session links and Claude Desktop resume in a visible console, configurable with `GUI_ALLOW_LAUNCH`.
- Add declared workflow inputs to the operator GUI, including field descriptions and examples; workflows without declarations continue to derive inputs from prompt placeholders (GUI-T11).
- Add contextual help across GUI controls, with keyboard-accessible «?» explanations and Esc dismissal (GUI-T12).
- Add repeatable `--bundle NAME=FOLDER` support to `multiagent gui` and `python -m multiagent.gui`, plus the general example bundle with three Mock-compatible workflows (GUI-T13).
- Stop GUI and Stage 0 UI servers cleanly on Ctrl+C, printing «GUI detenida.» and «UI detenida.» respectively (GUI-T14).
- Add GUI access hardening: a random local token, Host allow-list and Origin checks, JSON-only state-changing API requests, and a strict Content Security Policy with Trusted Types. The private GUI routes are under `/gui/api/*`, outside the public `/api/v1` contract.
- Estimate the cost of each provider call in the usage telemetry (migration `010_telemetry_cost_basis.sql`): `tokens_in x input price + tokens_out x output price` from the model's `pricing` evidence in force at the call, stored in `estimated_cost_usd` when the evidence is in USD (other currencies keep `estimated_cost_usd` null and record the cost and currency in the new `cost_basis`, which also keeps the model, source, validity window and provenance). A missing price, an expired one, an unsupported unit, or an unreported token count leaves the cost unknown (`null`, never 0); a `local_no_charge` model produces a real 0. Run, agent, step and new per-model totals keep unknown costs apart (`cost_known_records`, `cost_unknown_records`, `cost_other_currencies`, `cost_complete`) and `estimated_cost_usd` stays the known subtotal. The price validity check is now shared between the router and the telemetry (`catalog.current_price`). `docs/TELEMETRY.md` describes the calculation and when the cost is unknown.
- Add optional `kind` (`orchestration`, `planning`, `review`, `work`; default `work`) and `effort` (`none`, `low`, `medium`, `high`, `max`; default unset) to workflow steps. Existing workflows load and behave unchanged; unknown values are rejected when the workflow loads. `RoutingRequest` carries `step_kind`, `effort` and the step `attempt`. Adapters translate `effort` where the provider supports it (Ollama `think`, Gemini `thinking_level`, OpenAI `reasoning.effort`) and ignore it elsewhere; each model attempt records `effort_requested` and `effort_applied`. See [`docs/model-routing.md`](docs/model-routing.md).
- Add opt-in model-routing profiles and policy-constrained task-assignment previews. T14 filters routes using declared capabilities, permissions, privacy, modes, budgets, and supplied health/pricing evidence; T15 previews and revalidates task assignments against executor descriptors before a guarded claim. Current bundled executor surfaces remain manual or unknown, default clients do not launch executors, and nonempty unmapped task-envelope constraints block. Provider invocation budgets are enforced separately from token/cost estimates; actual served models and costs are not measured. See [`docs/model-routing.md`](docs/model-routing.md).
- Add catalog model levels (`T1` economy through `T4` maximum), classified/unclassified status, and validity-bounded pricing evidence for economic routing. Legacy cost-per-million fields are not price evidence; bundled local Ollama models declare T1 and a zero `local_no_charge` price.
- Add the disabled-by-default `economic` routing profile: per-step-kind level bounds and cheapest capable model selection, with `supervised`, `automatic`, and `single` modes. Bounded escalation moves at most one level per attempt after structured-output or semantic-quality failures; provider errors do not escalate, budgets are checked first, and T4 is never an automatic target. Supervised escalation to T3+ pauses for human approval.
- Add offline routing measurement tools (`export_runs.py`, `compare.py`, and `adoption.py`) to compare paired accepted runs against the approved cost, quality, safety, and sample-size criteria. Real measurements require user authorization and a budget; the profile remains disabled unless explicitly enabled and assigned.
- Add task envelopes and results by reference (`services.envelopes`, migration `006_envelopes.sql`): `get_envelope` computes what an agent needs to start a task (objective, effective status, dependencies with references to their latest results, patch blobs, file scope, gate, base commit, context references, constraints) and answers `unchanged` for a known hash; `submit_result` stores a bounded result and submits the task in one transaction, idempotently per task, attempt, and patch; patches and reports are blobs stored once by sha256 and read by ranges; each consumer has an event cursor that moves only with `ack_events`. Validation is a pure module (`multiagent.tasks.envelopes`); HTTP routes and CLI commands (`task context|envelope|result`, `blob put`, `artifact get`, `cursor`) mirror the facade, with parity tests. `scripts/benchmark_envelopes.py` compares the bytes an agent reads to start or resume a task against a handoff-document protocol on a synthetic fixture: about 3% and 5% of the raw handoff size, or about 20% and 24% when the agent profile and minimum context are counted in both modes (`*_with_floor` in the report); the handoff side uses a worst-case context package, so the ratio is reported, not promised. The envelope hash covers the task context and referenced data, not the event cursor.
- Add task gates (`services.gates`, migration `005_gates.sql`): an ordered list of checks declared on a task or as a group default, which a submitted task must pass to be accepted. Checks run project commands defined only in a local, trusted file (`project_commands.toml`, or `PROJECT_COMMANDS_FILE`) without a shell, with a timeout that stops the whole process tree, or require a review by an actor other than the assignee. A passing gate accepts the task and releases its dependents; a failing check asks for changes and stops the branch; a configuration error returns the task to `submitted`. Results are stored per attempt with the last 4000 characters of output. Available through Python, the HTTP API, and the CLI (`task gate`, `task review`, `task-group gate`, `project-commands`). See `docs/TASKS.md`.
- Coordinate work as tasks with dependencies (`services.tasks`, migration `004_tasks.sql`): task groups with authorization, tasks whose `blocked` or `ready` status is computed from their dependencies, one transition function (claim, start, pause, resume, submit, accept, request changes, fail, cancel) that records the actor and cause, file locks on claim, optimistic revisions, and a team event log read with `after`. Available through Python, the HTTP API (`/api/v1/task-groups`, `/api/v1/tasks`, `/api/v1/team-events`), and the CLI (`task-groups`, `task-group`, `tasks`, `task`, `team-events`) with the same results and errors. See `docs/TASKS.md`.
- Add application services (`multiagent.services`): one Python facade, `ApplicationServices`. It covers definitions (workflows, agents, and bundles) and runs, and the HTTP API and CLI use it. Failures raise `ServiceError` with a stable `code`, a `message`, an HTTP-like `status`, and JSON-ready `details`.
- Run definition bundles through the facade, each with its own store and worker. Bundles are registered by name in Python and are never exposed by path.
- Add HTTP routes for workflows, agents, bundles, bundle validation, and run artifacts. `POST /api/v1/runs` accepts a generic `RunRequest` (optionally with `?bundle=`) as well as the social-post request.
- Add CLI commands `workflows`, `agents`, `bundle-validate`, and `artifacts`, and generic runs with `run --workflow ... --input name=value [--bundle ...]`.
- Add Mock mode: with `MOCK_MODE=true`, the server and CLI run with synthetic fixtures and no model API keys. `/api/v1/health` reports `dry_run`.
- Add `docs/API.md` (facade, endpoints, error format, and Mock mode) and a README quick start with Mock.
- Add the Stage 0 run console: a local browser UI at `/ui/`, started with `multiagent ui` (or `run_ui_windows.bat`) in Mock mode by default, with `--real` for configured providers. It selects a workflow or bundle, creates a run, follows its status, current step, pending action, and events, handles human review (approve, request changes, regenerate, reject, cancel), runs the Human Bridge (copy the prompt, paste an external response, see validation errors), and shows artifacts. It is static HTML and JavaScript modules served by FastAPI, with no build step, and uses only the public HTTP API. See `docs/UI0.md`.
- Add Mock acceptance tests for the Stage 0 console and a manual checklist (`tests/ui0/CHECKLIST.md`).
- Number each run's events with `seq` (1, 2, 3… without gaps) and read only newer events with `GET /api/v1/runs/{run_id}/events?after=N`, `services.runs.events(run_id, after=N)`, or `multiagent events <run_id> --after N`. The Stage 0 run view detects gaps, repeats, and out-of-order copies and reloads the run instead of guessing its state.
- Add parity tests across the Python facade, HTTP, and the CLI.
- Record provider-reported token usage locally per run, agent, step, and attempt, including elapsed duration and failed attempts. Read records and totals through Python, HTTP, and CLI (including JSONL export); missing provider usage stays null, and telemetry write failures do not stop execution. See `docs/TELEMETRY.md`.
- Add `scripts/check_version_consistency.py`. It checks that every current version declaration (README header, newest changelog release, latest milestone, `pyproject.toml`, and the tag on tag builds) matches `multiagent/version.py`, and that the product stage and the Python pre-release agree. CI and `run_tests_windows.bat` run it before the tests.
- Add a reproducible example team, `examples/teams/software_development/`: a definition bundle with seven roles (product manager, architect, backend, frontend, QA, technical writer, reviewer), one output contract per step, two human reviews, and sample outputs that run in Mock mode. It has its own README and an automated test that loads it from an empty folder, runs it end to end, and checks that it carries no private or local references. The workflow makes one model call per step, so run it with `MAX_LLM_CALLS=7` or more.

### Changed

- Bump the product version to `M1-B03-F01-rc1` and the Python package version to `1.0.0rc1`.
- Make `/gui/` the one supported local interface (MVP 1.0): the server root redirects to `/gui/`, `run_ui_windows.bat` starts `multiagent gui`, and the Stage 0 prototype (`multiagent ui`, `/ui/`) prints a deprecation notice and links to `/gui/`; it is removed after 1.0.
- Raise the default Ollama context window (`num_ctx`, `OLLAMA_NUM_CTX`) to 8192 tokens so the three-step `social_post_full` flow with `qwen3:8b` completes without overflowing (MVP 1.0).
- Tracker (`tools/seguimiento`): agent cards, board status, and token parsing come from the channel's agent registry (`AGENTES.md`) and accept Application-Model names such as `Claude-Fable`; retired names are listed folded. Without a registry the three founding names are used.
- Protect the local API from other web pages in the same browser. Requests whose `Host` is not in `API_ALLOWED_HOSTS` (default `127.0.0.1`, `localhost`) answer 400 `invalid_host`, which blocks DNS rebinding. State-changing requests with an `Origin` from another site, or a cross-site `Sec-Fetch-Site`, answer 403 `forbidden_origin`. The CLI and other clients that send no `Origin` keep working. This is not authentication: do not expose the server to a network.
- Rewrite `docs/COMPARISON.md` around the target scope, with a status marker per capability (available, partial, planned, optional backend or adapter, under evaluation). It also separates what is not implemented in the current alpha from what stays intentionally outside the core, and compares scope with other frameworks.
- Describe the project as a domain-neutral, configurable runtime for teams of AI agents, humans, models, and tools. `README.md` and `ROADMAP.md` separate what works today, what is planned, and the long-term direction.
- Rename the roadmap section for `M1-B01-F00-alpha` to "First public baseline", so it is not mistaken for the current release.
- Mark the current version lines in `README.md` so they are checked, and document the release procedure in `docs/VERSIONING.md`.
- `multiagent/version.py` omits the stage suffix for stable releases instead of producing a trailing `-`.

- API errors now answer `{"detail": {"code", "message", "details"}}` with the same HTTP statuses as before, and invalid request bodies use the same format. Creating a run for an invalid workflow answers 422 instead of 500. Clients that read `detail` as text must read `detail.message`.
- Clarify the autonomy model in `docs/COMPARISON.md`, `README.md`, `ROADMAP.md`, and `PROGRESS.md`. The runtime currently executes declared workflows. The roadmap target is governed autonomy: planning, delegation, and dynamic routing within declared roles, permissions, contracts, budgets, gates, and policies, with escalation to a human. It is not unrestricted agent conversation.
- `setup_windows.bat` picks a supported Python (3.12, then 3.13) instead of falling back to the default `py -3`, which can be an unsupported version. When none is found it stops before creating `.venv` and says which versions are needed; an existing `.venv` with an unsupported Python is reported and left untouched. The supported versions are listed in one line at the top of the script.
- Support Python 3.14: `requires-python` is now `>=3.12,<3.15`, the 3.14 classifier is declared, CI runs on 3.12, 3.13, and 3.14, and `setup_windows.bat` accepts 3.14. The test suite passes unchanged on 3.14.
- Tracker (`tools/seguimiento`): a task whose card says `disponible`, `liberada` or `reabierta` but that waits for a dependency is shown as blocked, naming the dependency and its state, and the stage card counts available and blocked tasks separately. The decision is the one `tomar-tarea.ps1` applies (rule 27, including the stage's release mode), so the page no longer offers a task that cannot be taken. Dependency ranges such as `T02-T08` are now expanded in the dependency column.

### Roadmap

- Update the public roadmap and stage plan to reflect audited delivered work, partial implementations, and proposed guardrail and caching tasks.
- Record the local operator GUI as a partial delivery of S14; UI stages 1–3 and the advanced interface remain planned.
- Reflect the S14 operator GUI work (guided workflow chat, session panels and agent tree, usage view, work map) and `/gui/` as the one local interface; UI stages 1–3 remain planned.
- Mark task gates as done and stage gates with supervisor approval as partial; check visibility rules and the dependency-aware integration gate for changes remain planned.
- Mark tasks, groups, and dependencies as data, task traceability in the team event log, and task reservation with declared resources as done; plan schemas, planners, and delegation traceability for reviews and results remain planned.
- Add an organization chart model (S6.B2: role, holder, reports, and status through the application services) and an accessible organization chart view in the UI (S14.9), inspired by the organization chart in Paperclip; the idea is reused, not its code.
- Add a stage plan (`docs/ROADMAP_STAGES.md`): stages S0–S18 with sub-stages, tasks, dependencies, and parallel lanes, classified as Core, Extension, or Example, with objectives, acceptance criteria, and examples. The development order now follows these stages.
- Mark the stage 0 UI's untrusted rendering, Content Security Policy, and color-independent status as done, keeping rich-rendering sanitization and accessibility in later UI stages as planned.
- Add proposed items from the project inventory: task reservation and a dependency-aware integration gate for teams, per-task context packages, a register of proposals and decisions, moving the branding domain to a preset, and a library of requests.
- Mark application services and HTTP parity as done.
- Mark the minimal operator UI (stage 0) as done; the execution backend abstraction is next.
- Add the autonomy principle (autonomous orchestration within declared organizational constraints) and state the limits planners work within.
- Add **Team design from a prompt**: generate or formalize a team (roles, rules, organization chart, and recommended but never mandatory model bindings) as a validated, human-approved team definition. Roles are not tied to models, and one model can switch between roles.
- Add **Reference tool stacks for team presets**: research well-known open-source tools as examples for the five most common team types (for example Tailwind CSS for web design). They are examples in presets, never core dependencies.
- Record the external-delivery importer (`tools/seguimiento/`) as a design reference for externally produced output, the Stage 0 review step, and the future shared-folder channel. It is not runtime code.
- Add an optional evaluation of external structured-decision providers (for example Jev, commercial) behind a provider-neutral interface. It requires explicit opt-in, the native implementation remains the default, and it is not an adoption decision.
- Add **Advanced operator interface**: live topology and high-volume telemetry for large or long-running teams (background event ingestion, client state machines that mirror the core, macro and meso views with semantic zoom, virtualization, OpenTelemetry and MCP views, and an accessible design system). It is placed after OpenTelemetry, MCP, and planning, and its technologies are candidates for an architecture decision.
- Add near-term UI foundations: sequence-numbered run events with resynchronization, untrusted rendering of model output with sanitization and a Content Security Policy, and status that never depends on color alone.
- Mark provider-reported token-usage telemetry as delivered; retry counts, routing decisions, and token estimates remain planned.

## [M1-B02-F00-alpha] - 2026-09-27

Python package: `0.2.0a1`.

### Added

- Declare core runtime dependencies and optional Gemini/OpenAI extras in package metadata.
- Bundle public catalog, workflow, prompt, skill, and migration resources in the wheel and sdist.
- Add a direct Python Mock example and an isolated-wheel smoke check for CI.
- Validate workflow definitions before a run is created or executed: structure, identifiers, conditions, and references to agents, contracts, models, prompts, and skills, reported together with stable issue codes.
- Resume a run after an intermediate human review: approvals target the exact reviewed attempt, and changes to the definition while a run waits are detected before continuing.
- Load definition bundles (workflows, agents, prompts, contracts, and sample outputs) from outside the package and run them through `RunRequest` from Python.
- Document how the project differs from other multi-agent frameworks (`docs/COMPARISON.md`).
- Show progress by area in `ROADMAP.md` and `PROGRESS.md`, generated from the roadmap checkboxes by `scripts/update_progress.py` and checked in CI.
- Add `docs/LICENSING.md` and `TRADEMARKS.md`: Apache-2.0 without additional commercial restrictions, attribution, no telemetry, source headers, and use of the project name.

### Changed

- Checkpoints are no longer limited to the final workflow step.
- Resolve read-only definitions from the installed package and writable data from a user-owned directory when installed from a wheel.
- Bump the product version to `M1-B02-F00-alpha` and the Python package version to `0.2.0a1`.

### Roadmap

- The roadmap is now a living document, and roadmap changes are listed with each release.
- Reuse mature components behind adapters: Microsoft Agent Framework is planned as an optional execution backend (graph workflows, MCP, A2A, AG-UI, OpenTelemetry) while the core stays independent.
- Mark validation before execution and review after any step as done; add the execution backend abstraction, a resource-aware scheduler, MCP and A2A adapters, AG-UI as a UI transport, OpenTelemetry, and concept equivalences for Microsoft Agent Framework users.
- Add **Collaborative agent teams**: a domain-neutral core for two or more agents (scoped workspaces, inboxes with read receipts, reviewed change proposals, gates, supervised and autonomous modes), with software development as the first preset.
- Add **Preset sharing and community catalog**: portable presets with a manifest, installation from a file, Git, or a catalog, validation before use, trust rules for executable content, and private overlays.
- Show progress by area in the roadmap and in `PROGRESS.md`.
- Add a development order: application services with HTTP parity, then a minimal operator UI with the mock backend, the execution backend abstraction, tool and capability contracts, team relationships, planning, and interoperability.
- Add **Planning, delegation, and team orchestration**: plans as data executed by the existing engine, pluggable planners, manual, semi-automatic, and automatic modes, capability-based assignment, organizations as data, traceability, and replanning.
- Extend tools into **Tools, interoperability, and external applications**: generic tool contracts, impact-based permissions on the human-in-the-loop, capability-based selection, an MCP server and client, a shared-folder channel, an assisted desktop bridge, an application gateway and launcher, and external application adapters.
- Plan an early minimal UI as an operator and testing interface built on the public API, growing in stages.
- Improve the human-guided external model flow as a feature for human control, interoperability, and provider independence.
- Add **Open source, licensing, and sustainability**: Apache-2.0 without additional restrictions, no telemetry, progressive SPDX headers, a license and governance review before 1.0, and future community funding.

## [M1-B01-F00-alpha] - 2026-09-23

### Added

- First public alpha baseline.
- Multi-agent orchestrator with CLI and FastAPI API.
- YAML workflows and specialized researcher, strategist, creator, designer, and branding agents.
- Ollama, Gemini, and OpenAI adapters.
- Human-in-the-loop checkpoints and `human_guided` execution.
- SQLite persistence for runs, artifacts, assets, publications, and brand profiles.
- Private `.local/` workspace for secrets and runtime data.
- Product versioning based on `M<generation>-B<backend>-F<frontend>-<stage>`.
- 55-test automated suite.
- Apache License 2.0 and project `NOTICE`.
- AI-assisted-development disclosure.
- Cross-platform manual setup guidance in addition to Windows helper scripts.
- Public issue templates, pull-request checks, and community code of conduct.
- Least-privilege GitHub Actions permissions for the public CI workflow.
- Dedicated Ollama local-inference setup, diagnostics, and troubleshooting guide.

### Changed

- Public identity standardized as `multiagent` / `multi-agent-framework`.
- Public documentation, prompts, skills, CLI messages, runtime errors, fixtures, examples, and tests normalized to English.
- Public agent identifiers and prompt namespaces standardized as `researcher`, `strategist`, `creator`, `designer`, `branding`, and `analyst`.
- Local configuration and runtime data redirected outside the versioned source tree.

### Security

- `.env`, databases, runs, assets, backups, and the local workspace are excluded from version control by default.
- Public history starts from a clean baseline rather than reusing private development history.
