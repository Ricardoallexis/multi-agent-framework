# Roadmap

This roadmap captures the capabilities and directions discussed for Multi-Agent Framework. It is intentionally not tied to delivery dates, milestones, or commitments. Priorities may change as the architecture, model ecosystem, hardware requirements, and contributor feedback evolve.

This roadmap is a **living document**: it is updated whenever priorities, findings, or architecture decisions change, and each release lists the roadmap changes it publishes in the [changelog](CHANGELOG.md).

**How to read it:** checked items (`[x]`) work today; unchecked items are planned and not implemented. The long-term direction is described by the principles below. [`docs/COMPARISON.md`](docs/COMPARISON.md) gives the status of each capability in one table, and the current release is shown at the top of [`README.md`](README.md).

**Domain neutrality:** the core stays domain-neutral. Software development, architecture and engineering, research, design, finance, operations, or media production are implemented through configuration, presets, plugins, adapters, workflows, or projects built on the framework, without domain-specific logic in the core.

**Guiding principle:** reuse mature, stable components behind adapters instead of rebuilding them. [Microsoft Agent Framework](https://github.com/microsoft/agent-framework) (the successor of AutoGen) is the main candidate for graph execution, MCP, A2A, AG-UI, and OpenTelemetry. It is planned as an **optional** execution backend: the framework's configuration, runs, contracts, Human Bridge, and artifacts stay independent of any backend, and the default backend keeps working without it.

**Autonomy principle:** today the runtime executes declared workflows. Steps, order, and main conditions are defined before a run starts, and models produce each step's content.

The target is governed autonomy, reached through the team, planning, and routing areas below. A planner or orchestrator will decompose objectives, assign and delegate tasks, consult specialists, request reviews, replan, and choose routes dynamically. It will act within the organization, roles, capabilities, permissions, contracts, budgets, gates, scoped workspaces, and policies the user declares, and escalate to a human when needed.

The declarative structure stays, but it defines limits and relationships instead of every transition. The goal is autonomous orchestration within declared organizational constraints, not unrestricted conversation between agents.

## Development order

The order follows technical dependencies and changes as the project learns (see the changelog):

1. Stable core ✅: validation before execution, review after any step, and definition bundles.
2. Application services and HTTP parity ✅: one public Python layer used by the CLI, the HTTP API, a UI, and an MCP server, with generic run requests and structured errors over HTTP.
3. A minimal operator UI (stage 0) over that API, working with the mock backend ✅.
4. The execution backend abstraction (native, mock, and optional Microsoft Agent Framework).
5. Agent, tool, and capability contracts, impact-based permissions on the existing human-in-the-loop, and deterministic function steps.
6. Team relationships as data, then team design from a prompt: roles, rules, an organization chart, and recommended (never mandatory) model bindings generated as a validated team definition.
7. Planning and delegation: manual first, then semi-automatic and automatic.
8. Interoperability: MCP server, shared-folder channel, MCP client, desktop bridge, application gateway, and external application adapters.
9. Replanning, dynamic teams, preset sharing, a plugin ecosystem, and optional UI automation.
10. Advanced operator interface: live topology and high-volume telemetry, once OpenTelemetry events, MCP tool access, and planning with concurrent agents exist, because it visualizes them. Its low-cost foundations (ordered events with resynchronization, strict rendering of generated content, and accessibility baselines) are added earlier, while the stage 0 UI grows.
11. Reference tool stacks: well-known open-source tools as examples for the most common team presets. The research can start earlier because it does not block the core.

## First public baseline — `M1-B01-F00-alpha`

**Progress:** `██████████` 15/15 · 100% <!-- progress -->

The first public baseline is backend-first and provides the foundation for the rest of the project:

- [x] Deterministic multi-agent workflow engine.
- [x] Declarative YAML workflows.
- [x] Specialized agents for research, strategy, content creation, branding, and visual pre-production.
- [x] Model routing with preferred models and fallbacks.
- [x] Ollama support for local inference.
- [x] Gemini and OpenAI adapters for optional cloud execution.
- [x] `auto`, `local`, `cloud`, and `human_guided` execution modes.
- [x] Human-in-the-loop review, approval, revision, rejection, and externally executed steps.
- [x] Structured Pydantic contracts between workflow steps.
- [x] SQLite persistence for projects, runs, artifacts, assets, publications, telemetry, and brand profiles.
- [x] CLI and FastAPI API.
- [x] Cancellation, checkpoints, worker recovery, and execution budgets.
- [x] Private `.local/` workspace for secrets and runtime data.
- [x] Public code, documentation, prompts, examples, fixtures, and identifiers standardized in English.
- [x] Automated tests and GitHub Actions CI.

## Orchestration and agent runtime

**Progress:** `███░░░░░░░` 3/11 · 27% <!-- progress -->

- [ ] Expand the workflow engine so new agents and workflows can be added with minimal changes to the core runtime.
  - [x] Load definition bundles (workflows, agents, prompts, and contracts) from outside the package, validate them, and run them through a generic request from Python, the HTTP API, and the CLI.
- [x] Provide one public application-services layer in Python, used by the CLI, the HTTP API, a future UI, and an MCP server, including generic run requests and structured validation errors over HTTP.
- [ ] Introduce an execution backend abstraction: native (default), mock, and an optional Microsoft Agent Framework backend.
- [ ] Support richer conditional routing, branching, retries, dependencies, and reusable workflow fragments by compiling workflow definitions to the optional Microsoft Agent Framework backend instead of building a separate graph engine.
- [ ] Improve agent-to-agent context management while preserving explicit contracts and traceability.
- [x] Add stronger validation for workflow definitions before execution.
- [ ] Improve cancellation, pause/resume, recovery, idempotency, and long-running execution behavior.
- [ ] Support multiple workers and controlled concurrency, bounded by a resource-aware scheduler (CPU, RAM, GPU/VRAM, and provider limits) that works with any backend.
- [ ] Formalize compatibility rules for workflows, contracts, prompts, migrations, and provider adapters.
- [ ] Provide a clearer plugin/extension model for custom agents, tools, skills, providers, and workflows.

## Intelligent model routing

**Progress:** `░░░░░░░░░░` 0/7 · 0% <!-- progress -->

The framework is intended to use the orchestrator to choose the most appropriate model for each task rather than relying on one model for every capability.

- [ ] Expand routing based on task type, model strengths, latency, context size, privacy, availability, and cost.
- [ ] Allow explicit priority rules so text, research, image, and video tasks are routed only to suitable models.
- [ ] Add model health checks, capability discovery, and automatic fallback policies.
- [ ] Track per-model quality, latency, failures, and usage to inform future routing decisions.
- [ ] Support configurable local-first, cloud-first, offline-only, and hybrid execution policies.
- [ ] Evaluate optional external structured-decision providers (for example Jev, a commercial service) behind a provider-neutral interface. The native implementation stays the default, any external provider requires an explicit opt-in, and no provider becomes a required dependency. This is a possible evaluation, not an adoption decision.
- [ ] Improve token/context budgeting and prevent avoidable context overflow.

## Local and offline AI

**Progress:** `░░░░░░░░░░` 0/9 · 0% <!-- progress -->

Reducing dependence on paid cloud inference is a core direction of the project.

- [ ] Evaluate and integrate additional downloadable local language models.
- [ ] Start with a small set of complementary local models and expand gradually as their roles become clear.
- [ ] Assign local models according to their strengths instead of using one model indiscriminately.
- [ ] Support local research/synthesis models where practical.
- [ ] Evaluate local image-generation models and providers.
- [ ] Evaluate local video-generation models and providers as the ecosystem matures.
- [ ] Add local embedding and retrieval options for private knowledge workflows.
- [ ] Improve GPU/VRAM-aware model selection and runtime diagnostics.
- [ ] Make offline workflows usable without cloud credentials when all required capabilities are available locally.

## Research and knowledge workflows

**Progress:** `░░░░░░░░░░` 0/6 · 0% <!-- progress -->

- [ ] Expand research workflows beyond social-content use cases.
- [ ] Add stronger source tracking, evidence normalization, contradiction handling, and freshness metadata.
- [ ] Support human-supplied research as a first-class input when automated web access is unavailable or undesirable.
- [ ] Add retrieval over project files and private knowledge sources.
- [ ] Build reusable research outputs that can feed multiple downstream agents without repeating the same work.
- [ ] Improve separation between verified facts, inference, uncertainty, and editorial interpretation.

## Content, branding, and production

**Progress:** `░░░░░░░░░░` 0/7 · 0% <!-- progress -->

- [ ] Expand content workflows for additional formats and channels.
- [ ] Improve reusable brand profiles, brand assets, tone constraints, and project-level context.
- [ ] Add richer strategy, editorial planning, SEO, and campaign workflows.
- [ ] Connect visual briefs to image-generation providers while preserving human approval points.
- [ ] Add video pre-production workflows such as concepts, scripts, shot lists, storyboards, and generation prompts.
- [ ] Support iterative review loops between strategy, copy, design, and human reviewers.
- [ ] Keep factual research, brand guidance, and generated creative content clearly separated in the data model.

## Human-in-the-loop

**Progress:** `█░░░░░░░░░` 1/8 · 12% <!-- progress -->

Human control is intended to remain a first-class part of the architecture rather than an exception path.

- [x] Allow a human review after any step and resume from the next step without repeating approved work.
- [ ] Improve review queues and actionable human-step requests.
- [ ] Improve the human-guided external model flow (copy the prompt, use any external chat or application, import the response, and validate it against the contract) across the CLI, the API, and the UI, as a feature for human control, interoperability, provider independence, and transparency.
- [ ] Support approval policies per workflow, step, risk level, or project.
- [ ] Allow humans to replace an agent step with externally produced output while preserving validation and traceability. Reuse the pattern of the external-delivery importer in `tools/seguimiento/importar-paquete-nube.ps1` (manifest, integrity hashes, allowlisted paths, preview, explicit human confirmation, and an auditable receipt) as a design reference only; that tool is specific to this project's workflow and is not part of the runtime. A hash proves integrity, not authorship: a human confirms who produced the output.
- [ ] Add clearer revision history and comparisons between attempts.
- [ ] Add comments, reviewer notes, and structured feedback that can be passed safely into subsequent attempts.
- [ ] Improve audit trails showing what was produced by a model, a deterministic tool, or a human.

## Collaborative agent teams

**Progress:** `░░░░░░░░░░` 0/14 · 0% <!-- progress -->

A domain-neutral core for coordinating two or more agents, and the humans who supervise them, on shared work. Software development is one application; the same primitives should serve design, finance, research, operations, or any other domain. Domain-specific behavior lives in team presets and examples that users can extend, and agents can help a user design their own team setup. The protocol is the framework's own and runs on its runs, events, and Human Bridge; A2A or Microsoft Agent Framework may carry messages, but they do not define it.

- [ ] Teams: roles, capabilities, members (framework agents, external agents, or humans), and an entrypoint (an agent or a workflow).
- [ ] Scoped workspaces: each member changes only the resources assigned to it (for example folders, documents, datasets, or records); shared resources need a proposal and an approval.
- [ ] A team log and member inboxes: an append-only log plus a per-member inbox where reading a message acknowledges it, so members process only unread messages and never poll.
- [ ] Structured messages and handoffs (delivery, proposal, question, answer, decision, result) that state the baseline, what changed, what was not verified, and what is requested from whom.
- [ ] Change proposals: members deliver reviewable change sets in an ordered queue, with dependencies and held drafts, and a supervisor applies them rather than their authors.
- [ ] Review and gates: cross-review between members, and stages that close only when shared checks pass and a supervisor approves.
- [ ] Check results with visibility rules: a member's own checks stay private to it, shared checks are visible to the team, and one designated reader summarizes each report.
- [ ] Workspace synchronization after each approved stage, only with each member's confirmation and without losing unfinished work.
- [ ] Operating modes: supervised (a human approves applying, checking, approving, and publishing; members act only when asked) and autonomous (policy-driven gates, budgets, and sandboxing, escalating to a human on failures, shared resources, or external actions).
- [ ] Per-member token and cost budgets and minimal-reading rules.
- [ ] A resumable status snapshot (stage, milestones, and next action for each member) and human-readable views of communication, progress, and pending decisions.
- [ ] Team presets as examples: a software development team (resources are files, change sets are patches, checks are test suites, and stages end in a commit), plus examples for other domains such as design reviews or financial reporting.
- [ ] Let users extend presets or create their own (roles, resources, message types, checks, gates, and modes) without changing the core.
- [ ] A guided setup in which agents help a user design their team, scopes, rules, and mode, and adjust them as the work evolves (built on [team design from a prompt](#team-design-from-a-prompt)).

## Planning, delegation, and team orchestration

**Progress:** `░░░░░░░░░░` 0/12 · 0% <!-- progress -->

Turn a high-level objective into an executable plan for a team the user has defined: understand the objective, decompose the work, find the required capabilities, assign specialists, coordinate execution, request revisions, and integrate the final result. Planners and orchestrators act only within the limits the user declares (roles, capabilities, permissions, contracts, budgets, gates, and policies) and escalate to a human when needed; every decision stays traceable. The core stays domain-neutral; industries appear only in presets and examples.

- [ ] Represent a plan as data (tasks, subtasks, dependencies, required capabilities, expected outputs and contracts, assignments, parallel groups, review and integration steps) and execute it through the existing workflow engine and backends instead of a second engine.
- [ ] Make planners pluggable: manual, deterministic, LLM-based, a custom plugin, or an external planner.
- [ ] Support manual, semi-automatic, and automatic planning on the same core; semi-automatic plans wait for approval through the existing human-in-the-loop mechanism, where users can edit, reassign, or reject them.
- [ ] Assign tasks by capabilities, roles, tools, provider or model, permissions, availability, relationships, and budget, starting with deterministic rules and reusing the model router.
- [ ] Describe organizations as data (members, teams, and relationships such as reporting, supervision, delegation, review, consultation, and approval), supporting hierarchical, flat, matrix, peer-to-peer, and temporary structures.
- [ ] Express sequential, parallel, dependent, optional, conditional, and iterative work.
- [ ] Coordinate specialists through delegation, consultation, review, revision, approval, and handoff, and integrate partial results through merging, synthesis, conflict detection, cross-review, and final assembly.
- [ ] Record delegation traceability (who created and assigned each task and why, required capabilities, status, attempts, reviews, and results) without storing private model reasoning.
- [ ] Allow editing a plan before and during execution (add, remove, split, or merge tasks; change assignees, priorities, reviewers, or dependencies; pause, resume, or cancel).
- [ ] Replan when a task fails, an agent or tool is unavailable, validation or a human rejects an output, or new information appears.
- [ ] Propose temporary teams from a catalog of available agents for the user to approve or modify, once assignment on user-defined teams is stable.
- [ ] Honor plan constraints: call, cost, and time budgets, local-only or offline execution, allowed or prohibited providers, and required approvals.

## Team design from a prompt

**Progress:** `░░░░░░░░░░` 0/7 · 0% <!-- progress -->

Describe what you need in one prompt and get a working team: the agents it recommends, their instructions and responsibilities, and an organization chart. If the prompt already defines the team, the generator formalizes it instead of redesigning it. The result is data — a team definition that is validated, reviewed by a human, and versioned like any bundle or preset — never code in the core.

Roles are not tied to models. A **role** holds rules, skills, contracts, capabilities, and permissions. A **member** is whoever executes: a model, a provider's agent, an external agent, or a human. An **assignment** links them. Recommended bindings (for example a designer on one provider, a researcher on another) are only suggestions from the capability catalog. A single model can play several roles and switch between them, loading each role's rules when a step needs that role.

- [ ] Define the team definition as data: roles (purpose, rules, skills, contracts, capabilities, permissions), members, assignments, relationships (the organization chart), and working rules, reusing the relationship model of the planning area.
- [ ] Generate a team definition from a prompt with a meta-workflow that runs on the existing engine: (a) from an objective alone, recommending roles with a short rationale; (b) from a team the user already described, formalizing it and asking about gaps instead of redesigning it.
- [ ] Validate generated teams with the same rules as bundles (references, contracts, capabilities, least-privilege permissions) and require human approval before any team is created or changed, reusing the human-in-the-loop.
- [ ] Recommend model or provider bindings per role from the capability catalog and the model router, as editable suggestions; never hard-code vendors in the core, and allow any available member, or a human, to take any role.
- [ ] Let one member switch roles at runtime: each step declares its role, the runtime loads that role's rules and context, keeps role contexts separated, and records the role and the member in the trace.
- [ ] Render the organization chart from the relationships (for example as a Mermaid diagram) in docs and, later, in the UI (stage 2).
- [ ] Regenerate a team as a reviewable diff against the current definition, and save accepted teams as presets.

## Preset sharing and community catalog

**Progress:** `░░░░░░░░░░` 0/6 · 0% <!-- progress -->

Presets (teams, workflows, agents, prompts, contracts, and checks) should be easy to share, so that one user's setup for design reviews, financial reporting, or software development can be downloaded and used by others. This builds on the existing definition bundles and stays domain-neutral.

- [ ] Package presets in a portable, versioned format with a manifest: name, version, author, license, description, compatible framework versions, and required capabilities, providers, and permissions.
- [ ] Publish and install presets from a file, a Git repository, or a community catalog with a single command, including updates and removal.
- [ ] Validate an installed preset before its first use: structure, references, compatibility with the installed framework, and declared permissions.
- [ ] Treat presets that include executable code (such as contract models or tools) as untrusted until the user approves them: show what will run, verify checksums or signatures, prefer data-only presets, and allow sandboxing.
- [ ] Customize a shared preset with a private overlay instead of copying it (defaults < public preset < private overlay < runtime overrides), so updates to the shared preset can still be applied.
- [ ] Offer a searchable catalog with descriptions, examples, domains, and compatibility information, and keep private presets out of it unless the user publishes them.

## Reference tool stacks for team presets

**Progress:** `░░░░░░░░░░` 0/5 · 0% <!-- progress -->

Research which well-known open-source tools make good examples for the most common teams, so presets show realistic setups. The tools are examples inside presets and adapters, never dependencies of the core. Candidates to evaluate, not decisions: a web design team with Tailwind CSS and Vite; a software development team with Git, pytest, and Ruff; a data and research team with Jupyter, pandas, and DuckDB; a documentation team with Pandoc and MkDocs; a design and media team with Inkscape, GIMP, and Blender.

- [ ] Choose the top five team types by expected use, with a short justification for each.
- [ ] Define the evaluation criteria: license compatible with Apache-2.0 use, maturity and maintenance, community size, a stable integration path (CLI, API, SDK, or MCP), Windows/Linux/macOS support, and offline use.
- [ ] For each team type, list three to five reference tools with the role they serve and how an agent would use them.
- [ ] Publish the result as a reference page and link it from the matching team presets.
- [ ] Review the list periodically and record changes in the changelog.

## Frontend / UI

**Progress:** `█░░░░░░░░░` 1/18 · 6% <!-- progress -->

`F00` ships without a graphical interface. A minimal UI arrives early as a development, testing, and operator interface rather than as the final product. It is one more client of the public API, like the CLI, with no business logic, persistence, or orchestration of its own, and it works with the mock backend so no API keys are needed. Its technology is chosen at that milestone, and each later area of the roadmap adds UI only where it clearly improves validation, operation, or experience.

- [x] Stage 0, right after the application services and HTTP parity: start a run from an available workflow (mock by default), follow its status and events, handle the pending human action (approve, request changes, regenerate, reject, cancel, or paste an external model's response and see validation errors), and inspect artifacts. Its review and confirmation step for externally produced output can follow the same human-confirmation pattern as the external-delivery importer (`tools/seguimiento/`).
- [ ] Stage 1: agent and workflow configuration, once agent, tool, and capability contracts are stable.
- [ ] Stage 2: teams, plans, and tasks, to inspect and approve generated plans and follow task execution.
- [ ] Stage 3: tools and interoperability, including pending permissions, channels, MCP, and artifacts.
- [ ] Stage 4: a more complete experience, including live topology over domain events (see [Advanced operator interface](#advanced-operator-interface)).
- [ ] Define the frontend architecture and stable API boundary.
- [ ] Evaluate AG-UI as the event transport between the framework's domain events and a future UI.
- [ ] Dashboard for projects, workflows, active runs, queued work, and system health.
- [ ] Visual workflow execution and status timeline.
- [ ] Project, workflow, provider, model, and routing configuration.
- [ ] Brand-profile and asset management.
- [ ] Artifact previews and revision comparisons.
- [ ] Usage, latency, cost, routing, and error visualization.
- [ ] Local-model availability and hardware status views.
- [ ] Administrative views for workers, migrations, diagnostics, and recovery.
- [ ] Give run events a monotonic sequence number so clients detect gaps, duplicates, and out-of-order delivery, and resynchronize from a run snapshot instead of guessing state.
- [ ] Treat all model output, artifacts, and raw payloads as untrusted in every UI: render them as text by default, sanitize any rich rendering (Markdown or HTML) with a strict allowlist sanitizer, and serve the UI with a restrictive Content Security Policy.
- [ ] Status and alerts never depend on color alone (text, icon, or shape as well), with WCAG 2.2 AA contrast as the baseline.

## Advanced operator interface

**Progress:** `░░░░░░░░░░` 0/15 · 0% <!-- progress -->

A later evolution of the operator UI for large or long-running teams: many agents, tools, and nested tasks running at once, followed live without freezing the browser. It stays a client of the public API and of standard event streams, with no inference, prompts, provider calls, or orchestration in the browser, and it keeps only the current session's state in memory.

It depends on OpenTelemetry events, MCP tool access, and planning with concurrent agents, and is built after them (see the development order). The technologies named below are candidates to evaluate in an architecture decision, not commitments.

- [ ] Record an architecture decision for the advanced UI: rendering stack, build toolchain, state management, and how it coexists with the stage 0 UI that needs no build step.
- [ ] Measurable performance targets before building: events per second ingested, frames per second while panning and zooming, main-thread load, and memory over multi-day runs.
- [ ] Ingest events in a background worker (Web Workers over WebSocket or SSE) so the main thread only renders. Zero-copy transfer (`SharedArrayBuffer`) is optional, because it requires cross-origin isolation headers.
- [ ] Client-side state machines (for example XState) for agents, tools, and teams that mirror the lifecycle published by the core and reject impossible transitions. The core stays the source of truth: the UI resynchronizes from a snapshot and never decides transitions itself.
- [ ] Tolerate out-of-order and late events, such as a completion arriving before the last streamed tokens, with buffering by sequence number.
- [ ] Macro view: a force-directed graph of agents and clusters rendered with WebGL or Canvas (optionally in `OffscreenCanvas`).
- [ ] Meso view: the run's task graph over time (a DAG library such as React Flow), reached by zooming in.
- [ ] Semantic zoom: a smooth transition from the macro view to structured, interactive nodes.
- [ ] A detail panel per agent or step with progressive disclosure: summary, trace (reasoning only where the provider exposes it and logging policies allow it), metrics, and raw payload.
- [ ] Lightweight live indicators (sparklines) instead of spinners.
- [ ] Virtualize long logs, timelines, and graphs so runs lasting days do not exhaust browser memory.
- [ ] Map OpenTelemetry GenAI traces (parent-child spans, tokens, cost, latency) into the views with provider-neutral schemas; no view assumes a specific provider.
- [ ] Show MCP tool access: which agent used which tool, with what permission and result.
- [ ] Render pending human actions from a standard payload that tells the UI which fields and actions to offer (approve, reject, request changes with feedback, or replace the output).
- [ ] An accessible, mostly achromatic design system with design tokens, evaluated with APCA in addition to the WCAG 2.2 AA baseline, with muted red and amber reserved for alerts.

## Memory, context, and project knowledge

**Progress:** `░░░░░░░░░░` 0/5 · 0% <!-- progress -->

- [ ] Add explicit project-scoped memory/context that does not depend on hidden model memory.
- [ ] Support reusable knowledge packs and structured project context.
- [ ] Add retrieval and summarization strategies for large project histories.
- [ ] Define retention, privacy, provenance, and invalidation rules for stored context.
- [ ] Allow workflows to request only the context they need instead of loading all available history (context propagation policies such as none, selected, summary, artifacts, and full).

## Tools, interoperability, and external applications

**Progress:** `░░░░░░░░░░` 0/13 · 0% <!-- progress -->

The core works with generic concepts (tool, capability, provider, channel, permission, external application, request, result, and artifact); anything specific to a model, protocol, or application lives in adapters, plugins, bridges, or providers.

- [ ] Define generic tool contracts (tool request, result, error, and permission request) separate from model-provider adapters; adapters translate into them.
- [ ] Classify tool actions by impact (read, write, destructive, and external side effect) and route approvals through the existing human-in-the-loop mechanism.
- [ ] Select tools and agents by namespaced capabilities (for example `file.read`, `image.edit`, or `planning.decompose`), starting with deterministic, configuration-based selection.
- [ ] Allow agents to call approved deterministic tools and external services through controlled contracts.
- [ ] Expose the framework as an MCP server through the same application services as the API (list agents and workflows, create and inspect runs, handle pending human steps, and read artifacts) without exposing internals.
- [ ] Connect MCP tools as a client, through Microsoft Agent Framework's MCP support or the official MCP SDK, behind the framework's tool abstraction; do not implement a custom MCP protocol.
- [ ] Add a shared-folder channel: structured requests, responses, and artifacts exchanged through files, with correlation to runs and steps, atomic writes, timeouts, duplicate handling, permissions, and an audit trail, for applications that cannot or should not be automated otherwise. Its request/response envelope can follow the external-delivery importer's pattern (`tools/seguimiento/importar-paquete-nube.ps1`): a structured manifest, integrity hashes, file and path limits, deduplication, and an auditable receipt.
- [ ] Add an assisted desktop bridge that prepares the prompt, uses the clipboard, opens or activates an application, and imports the response, with the user in control.
- [ ] Add an application gateway and launcher that keep launching or opening an application separate from controlling it, each with its own permission.
- [ ] Build integrations with external applications (design, CAD/BIM, 3D, media, office, and development tools) as independent adapters or plugins, preferring official APIs or SDKs, then MCP, CLI or IPC, official plugin systems, shared files, and UI automation last.
- [ ] Consume and expose remote agents through A2A, behind an adapter, when a concrete need appears.
- [ ] Support project-specific integrations without coupling them to the core framework.
- [ ] Record tool calls and outputs in the same traceability model used for agents and human steps.

## Observability, quality, and evaluation

**Progress:** `░░░░░░░░░░` 0/7 · 0% <!-- progress -->

- [ ] Expand telemetry for execution time, retries, routing decisions, token estimates, and provider usage.
- [ ] Emit OpenTelemetry traces and metrics (GenAI semantic conventions) alongside the framework's own domain events.
- [ ] Add structured evaluation datasets for agents, prompts, and workflows.
- [ ] Compare local and cloud model performance on the same contracts and tasks.
- [ ] Track regressions when prompts, models, workflows, or providers change.
- [ ] Add richer diagnostics for model availability, context limits, GPU resources, and dependency health.
- [ ] Provide exportable run reports for debugging and audit purposes.

## Security and privacy

**Progress:** `░░░░░░░░░░` 0/6 · 0% <!-- progress -->

- [ ] Continue enforcing the separation between public source code and private runtime data.
- [ ] Improve secret-management options beyond local `.env` files.
- [ ] Add configurable policies for sensitive workloads and local-only execution.
- [ ] Review uploaded assets, external tool inputs, and generated artifacts for safe handling boundaries.
- [ ] Add dependency, secret, and static-analysis checks to CI where appropriate.
- [ ] Define responsible defaults for logging and telemetry so confidential content is not exposed unintentionally.

## Developer experience and distribution

**Progress:** `░░░░░░░░░░` 0/9 · 0% <!-- progress -->

- [ ] Make clean installation reproducible on supported environments.
- [ ] Improve cross-platform support beyond the current Windows-first helper scripts.
- [ ] Evaluate containerized deployment for the backend and supporting services.
- [ ] Provide clearer examples and starter workflows for contributors.
- [ ] Document concept equivalences for developers coming from Microsoft Agent Framework.
- [ ] Publish a stable public API reference and developer documentation.
- [ ] Improve migration tooling for databases, configuration, prompts, and workflows.
- [ ] Establish contribution conventions for agents, adapters, workflows, skills, and tests.
- [ ] Prepare package/release automation when the project reaches an appropriate level of stability.

## Open source, licensing, and sustainability

**Progress:** `███░░░░░░░` 2/6 · 33% <!-- progress -->

- [x] Keep the core open source under Apache-2.0, with no additional restrictions on commercial use.
- [x] Ship no hidden telemetry, call-home, or installation identifiers; public reuse is observed only through transparent means such as forks, dependents, and code search.
- [ ] Add SPDX license headers progressively, starting with new files.
- [ ] Review the license and governance model before 1.0, or earlier for a concrete reason (Apache-2.0, MPL-2.0, AGPL-3.0, or dual licensing), together with contribution terms (inbound = outbound, DCO, or CLA) before any relicensing.
- [ ] Open GitHub Sponsors or other community funding once there is a stable quickstart, documentation, external users, and recurring use.
- [ ] Keep optional services (managed hosting, cloud execution, observability, collaboration, enterprise tooling, support, and custom integrations) outside the core, so the core stays complete and usable on its own.

## Long-term architecture

A future `M2` generation is reserved for changes that are intentionally incompatible with `M1`, such as a major redesign of the runtime, public contracts, persistence model, distributed execution architecture, or backend/frontend boundary. A new generation should be created only when compatibility cannot reasonably be preserved through normal `B` and `F` revisions.
