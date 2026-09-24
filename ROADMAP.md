# Roadmap

This roadmap captures the capabilities and directions discussed for Multi-Agent Framework. It is intentionally not tied to delivery dates, milestones, or commitments. Priorities may change as the architecture, model ecosystem, hardware requirements, and contributor feedback evolve.

## Current baseline — `M1-B01-F00-alpha`

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

- [ ] Expand the workflow engine so new agents and workflows can be added with minimal changes to the core runtime.
- [ ] Support richer conditional routing, branching, retries, dependencies, and reusable workflow fragments.
- [ ] Improve agent-to-agent context management while preserving explicit contracts and traceability.
- [ ] Add stronger validation for workflow definitions before execution.
- [ ] Improve cancellation, pause/resume, recovery, idempotency, and long-running execution behavior.
- [ ] Support multiple workers and controlled concurrency.
- [ ] Formalize compatibility rules for workflows, contracts, prompts, migrations, and provider adapters.
- [ ] Provide a clearer plugin/extension model for custom agents, tools, skills, providers, and workflows.

## Intelligent model routing

The framework is intended to use the orchestrator to choose the most appropriate model for each task rather than relying on one model for every capability.

- [ ] Expand routing based on task type, model strengths, latency, context size, privacy, availability, and cost.
- [ ] Allow explicit priority rules so text, research, image, and video tasks are routed only to suitable models.
- [ ] Add model health checks, capability discovery, and automatic fallback policies.
- [ ] Track per-model quality, latency, failures, and usage to inform future routing decisions.
- [ ] Support configurable local-first, cloud-first, offline-only, and hybrid execution policies.
- [ ] Improve token/context budgeting and prevent avoidable context overflow.

## Local and offline AI

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

- [ ] Expand research workflows beyond social-content use cases.
- [ ] Add stronger source tracking, evidence normalization, contradiction handling, and freshness metadata.
- [ ] Support human-supplied research as a first-class input when automated web access is unavailable or undesirable.
- [ ] Add retrieval over project files and private knowledge sources.
- [ ] Build reusable research outputs that can feed multiple downstream agents without repeating the same work.
- [ ] Improve separation between verified facts, inference, uncertainty, and editorial interpretation.

## Content, branding, and production

- [ ] Expand content workflows for additional formats and channels.
- [ ] Improve reusable brand profiles, brand assets, tone constraints, and project-level context.
- [ ] Add richer strategy, editorial planning, SEO, and campaign workflows.
- [ ] Connect visual briefs to image-generation providers while preserving human approval points.
- [ ] Add video pre-production workflows such as concepts, scripts, shot lists, storyboards, and generation prompts.
- [ ] Support iterative review loops between strategy, copy, design, and human reviewers.
- [ ] Keep factual research, brand guidance, and generated creative content clearly separated in the data model.

## Human-in-the-loop

Human control is intended to remain a first-class part of the architecture rather than an exception path.

- [ ] Improve review queues and actionable human-step requests.
- [ ] Support approval policies per workflow, step, risk level, or project.
- [ ] Allow humans to replace an agent step with externally produced output while preserving validation and traceability.
- [ ] Add clearer revision history and comparisons between attempts.
- [ ] Add comments, reviewer notes, and structured feedback that can be passed safely into subsequent attempts.
- [ ] Improve audit trails showing what was produced by a model, a deterministic tool, or a human.

## Frontend / UI

`F00` intentionally ships without a graphical interface. The future UI should use the public API rather than access the database or engine internals directly.

- [ ] Define the frontend architecture and stable API boundary.
- [ ] Dashboard for projects, workflows, active runs, queued work, and system health.
- [ ] Visual workflow execution and status timeline.
- [ ] Human review, approval, rejection, revision, and manual-step completion from the UI.
- [ ] Project, workflow, provider, model, and routing configuration.
- [ ] Brand-profile and asset management.
- [ ] Artifact previews and revision comparisons.
- [ ] Usage, latency, cost, routing, and error visualization.
- [ ] Local-model availability and hardware status views.
- [ ] Administrative views for workers, migrations, diagnostics, and recovery.

## Memory, context, and project knowledge

- [ ] Add explicit project-scoped memory/context that does not depend on hidden model memory.
- [ ] Support reusable knowledge packs and structured project context.
- [ ] Add retrieval and summarization strategies for large project histories.
- [ ] Define retention, privacy, provenance, and invalidation rules for stored context.
- [ ] Allow workflows to request only the context they need instead of loading all available history.

## Tools and external integrations

- [ ] Define a generic tool interface separate from model-provider adapters.
- [ ] Allow agents to call approved deterministic tools and external services through controlled contracts.
- [ ] Add capability and permission declarations for tools.
- [ ] Support project-specific integrations without coupling them to the core framework.
- [ ] Record tool calls and outputs in the same traceability model used for agents and human steps.

## Observability, quality, and evaluation

- [ ] Expand telemetry for execution time, retries, routing decisions, token estimates, and provider usage.
- [ ] Add structured evaluation datasets for agents, prompts, and workflows.
- [ ] Compare local and cloud model performance on the same contracts and tasks.
- [ ] Track regressions when prompts, models, workflows, or providers change.
- [ ] Add richer diagnostics for model availability, context limits, GPU resources, and dependency health.
- [ ] Provide exportable run reports for debugging and audit purposes.

## Security and privacy

- [ ] Continue enforcing the separation between public source code and private runtime data.
- [ ] Improve secret-management options beyond local `.env` files.
- [ ] Add configurable policies for sensitive workloads and local-only execution.
- [ ] Review uploaded assets, external tool inputs, and generated artifacts for safe handling boundaries.
- [ ] Add dependency, secret, and static-analysis checks to CI where appropriate.
- [ ] Define responsible defaults for logging and telemetry so confidential content is not exposed unintentionally.

## Developer experience and distribution

- [ ] Make clean installation reproducible on supported environments.
- [ ] Improve cross-platform support beyond the current Windows-first helper scripts.
- [ ] Evaluate containerized deployment for the backend and supporting services.
- [ ] Provide clearer examples and starter workflows for contributors.
- [ ] Publish a stable public API reference and developer documentation.
- [ ] Improve migration tooling for databases, configuration, prompts, and workflows.
- [ ] Establish contribution conventions for agents, adapters, workflows, skills, and tests.
- [ ] Prepare package/release automation when the project reaches an appropriate level of stability.

## Long-term architecture

A future `M2` generation is reserved for changes that are intentionally incompatible with `M1`, such as a major redesign of the runtime, public contracts, persistence model, distributed execution architecture, or backend/frontend boundary. A new generation should be created only when compatibility cannot reasonably be preserved through normal `B` and `F` revisions.
