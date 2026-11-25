# Scope, status, and comparison

**Evidence date:** 2026-11-25 for the five CLI orchestrators in that section. The older framework descriptions later on this page were not re-checked on that date. Repository status distinguishes the published 1.0 release from this checkout's `[Unreleased]` changes and the roadmap. The external summaries reflect documentation, not installations or tests.

Use only these status terms:

| Status | Meaning |
| --- | --- |
| **available** | The cited source documents the capability in the stated release or checkout. |
| **partial** | A foundation or limited version exists; the full capability is not established. |
| **not documented** | The cited sources do not establish the capability. This does not prove it is absent. |

Quota and cost claims are limited to evidence exposed by each tool or provider. A token count is not a quota measurement, a price estimate is not a bill, and an unknown value is not zero.

## Product for the operator

| Capability | Published 1.0 | Current checkout (`[Unreleased]`) | Roadmap |
| --- | --- | --- | --- |
| Declared workflows, contracts, human review, persistence, CLI and HTTP API | **available** — the supported 1.0 surface ([README](../README.md), [API](API.md)) | **available** | **partial** — governed planning and delegation are still evolving ([roadmap](../ROADMAP.md)) |
| Local operator GUI | **available** at `/gui/`; a local operator view over the API, not a separate runtime ([GUI](GUI.md)) | **available**; further GUI work is listed under `[Unreleased]` ([changelog](../CHANGELOG.md)) | **partial** — later interface stages remain ([roadmap](../ROADMAP.md)) |
| Simple-mode first run and office onboarding | **not documented** as part of published 1.0 | **partial** — onboarding and office flows are in the checkout under `[Unreleased]`, not released ([changelog](../CHANGELOG.md), [offices](OFFICES.md)) | **partial** — planned for a later release ([scope](SCOPE.md)) |
| Task groups, dependencies, gates, and event history | **available** — task groups, dependencies, gates, envelopes, and the team event log are in the 1.0 changelog ([tasks](TASKS.md), [changelog](../CHANGELOG.md)) | **available** as task-coordination foundations ([tasks](TASKS.md)); plan advancement and executor-task flows were added under `[Unreleased]` | **partial** — broader collaboration and integration remain open ([roadmap](../ROADMAP.md#collaborative-agent-teams)) |
| Team definitions and approved versions | **not documented** as part of published 1.0 | **partial** — role-bound workflow steps use the approved team; other team fields are not enforced at runtime ([teams](TEAMS.md)) | **partial** — team entrypoints and broader collaborative primitives remain open ([roadmap](../ROADMAP.md#collaborative-agent-teams)) |
| Plans and planners | **not documented** as part of published 1.0 | **partial** — planners can propose plans and people can review, edit, reassign, approve, or advance proposals; the full plan schema and autonomous replanning remain open ([planning](PLANNING.md), [roadmap](../ROADMAP.md#planning-delegation-and-team-orchestration)) | **partial** — full planning modes, atomic group creation, delegation, and replanning remain open |
| Offices and areas | **not documented** as part of published 1.0 | **partial** — office definitions and installation/privacy flows are in the checkout, under `[Unreleased]`; do not present them as released ([offices](OFFICES.md), [changelog](../CHANGELOG.md)) | **partial** — planned for a later release ([scope](SCOPE.md)) |
| Development-agent CLI executors | **partial** — the 1.0 changelog lists Claude, Codex, and Copilot executor adapters, disabled by default and tested only with synthetic executables ([changelog](../CHANGELOG.md), [executors](EXECUTORS.md)) | **available** as four adapters (Claude, Codex, Copilot, and Antigravity), all disabled by default; live CLI behavior is not verified ([executors](EXECUTORS.md)) | **partial** — real application behavior and broader routing are not established ([roadmap](../ROADMAP.md#intelligent-model-routing)) |
| Multiple workers and concurrent runs | **not documented** as part of published 1.0 | **available** across local worker processes sharing SQLite, with leases and configured limits ([backends](BACKENDS.md#several-workers)) | **partial** — resource-aware scheduling remains open ([roadmap](../ROADMAP.md#orchestration-and-agent-runtime)) |
| General parallel workflow steps | **not documented** | **partial** — task dependencies and concurrent runs exist; they do not establish general parallel workflow-step orchestration ([tasks](TASKS.md), [backends](BACKENDS.md)) | **partial** |
| Ollama local models | **available** ([README](../README.md), [model routing](model-routing.md)) | **available** | **partial** — additional local model types and offline capabilities are roadmap work ([roadmap](../ROADMAP.md#local-and-offline-ai)) |
| Quota and cost reporting | **partial** — the 1.0 changelog lists per-call cost estimates and usage telemetry, with unknown values kept unknown ([changelog](../CHANGELOG.md)) | **partial** — quota is executor-specific; cost is estimated only with comparable token and price evidence. Missing or stale evidence stays unknown ([executors](EXECUTORS.md), [model routing](model-routing.md#budgets-price-data-and-quality-limits)) | **partial** |

The published baseline and the current checkout are different claims. In particular, a GUI being available in 1.0 does not make every newer GUI, simple-mode, plan, or office feature part of that release.

## CLI agent orchestrators

The following projects are compared as developer tools, not as interchangeable end-user office products. Links and observations were checked on **2026-11-25**.

### Goose

Goose is a general-purpose agent with a desktop app and CLI, broad provider support, local models, and MCP. Its CLI-provider guide describes connectors for Claude Code, Codex, Cursor Agent, and Gemini CLI; it marks the Claude Code and Codex connectors as deprecated and recommends ACP for those CLIs. That connector warning does not describe Goose as a whole. Its `approve` and `smart-approve` permission modes route approval to the user, while `auto` runs the connected CLI with `--dangerously-skip-permissions`; the guide does not document quota or cost measurement for the connected CLI accounts.

Sources: [README](https://github.com/aaif-goose/goose/blob/main/README.md) · [CLI provider guide](https://goose-docs.ai/docs/guides/cli-providers/) · [license](https://github.com/aaif-goose/goose/blob/main/LICENSE) · [releases](https://github.com/aaif-goose/goose/releases).

### AI CLI Orchestrator

This repository describes Python scripts and a Windows batch file in which Claude Code directs Codex CLI or Gemini CLI sequentially, or uses a file/watchdog bridge. The README does not document MCP, parallel orchestration, local models, a GUI, quota/cost measurement, or persistent user consent. It describes Codex sandboxing as the default and a `--yolo` option that removes sandboxing and approvals.

Sources: [README](https://github.com/Mhrnqaruni/ai-cli-orchestrator/blob/master/README.md) · [repository](https://github.com/Mhrnqaruni/ai-cli-orchestrator) · [documentation](https://github.com/Mhrnqaruni/ai-cli-orchestrator/blob/master/README.md) (README only; no separate docs directory was found) · [license file](https://github.com/Mhrnqaruni/ai-cli-orchestrator/blob/master/LICENSE) (not found; MIT is declared in the README) · [releases](https://github.com/Mhrnqaruni/ai-cli-orchestrator/releases) (none visible at consultation).

### multiagents

This TypeScript/Bun project documents a local SQLite/HTTP broker, MCP server, and drivers for Claude Code, Codex CLI, and Gemini CLI. It describes shared team state, messages, file ownership, reviews, persistent sessions, recovery, and web/TUI dashboards. Its README reports token usage for Claude/Codex, not native plan-quota or cost measurement. Configurable guardrails are not the same as the per-person activation and consent described for this framework.

Sources: [README](https://github.com/zetbrush/multiagents/blob/main/README.md) · [repository](https://github.com/zetbrush/multiagents) · [documentation](https://github.com/zetbrush/multiagents/blob/main/README.md) (README only; no separate docs directory was found) · [license file](https://github.com/zetbrush/multiagents/blob/main/LICENSE) (not found; MIT is declared in the README) · [releases](https://github.com/zetbrush/multiagents/releases) (none visible at consultation).

### Maestro

Maestro is a development workflow built around specialists and plugins for Antigravity, Claude Code, Codex, and Qwen Code. Its Standard path covers design, plan, execution, and closure, with plan validation, sequential or parallel work, and phase gates; its Express path is for simple tasks and keeps a brief approval. It runs inside the host agent runtime and does not document its own GUI or per-CLI quota/cost measurement.

Sources: [README](https://github.com/josstei/maestro-orchestrate/blob/main/README.md) · [architecture](https://github.com/josstei/maestro-orchestrate/blob/main/docs/architecture.md) · [flow and gates](https://github.com/josstei/maestro-orchestrate/blob/main/docs/flow.md) · [Codex runtime](https://github.com/josstei/maestro-orchestrate/blob/main/docs/runtime-codex.md) · [license](https://github.com/josstei/maestro-orchestrate/blob/main/LICENSE) · [releases](https://github.com/josstei/maestro-orchestrate/releases).

### CLI Agent Orchestrator (CAO, awslabs)

CAO documents a local client/server application with a CLI, API, MCP servers, SQLite state, and CLI processes managed through `tmux` or `herdr`. Its README lists many agent CLIs, parallel sessions, assignment and handoff, workflows, and an isolated-worktree example. It provides web and terminal interfaces, but requires a technical setup. The consulted pages do not establish uniform commercial quota or final-cost measurement.

Sources: [README](https://github.com/awslabs/cli-agent-orchestrator/blob/main/README.md) · [codebase](https://github.com/awslabs/cli-agent-orchestrator/blob/main/CODEBASE.md) · [tmux](https://github.com/awslabs/cli-agent-orchestrator/blob/main/docs/tmux.md) · [working directories](https://github.com/awslabs/cli-agent-orchestrator/blob/main/docs/working-directory.md) · [flows](https://github.com/awslabs/cli-agent-orchestrator/blob/main/docs/flows.md) · [web UI](https://github.com/awslabs/cli-agent-orchestrator/blob/main/docs/web-ui.md) · [tool restrictions](https://github.com/awslabs/cli-agent-orchestrator/blob/main/docs/tool-restrictions.md) · [license](https://github.com/awslabs/cli-agent-orchestrator/blob/main/LICENSE) · [releases](https://github.com/awslabs/cli-agent-orchestrator/releases).

## Capability matrix

Each external-project cell is based on the linked sources in the project profiles above. **Not documented** means the checked sources did not support the claim. It is not a security assessment or hands-on product test. The "This framework" column describes the current checkout and names the release status where it differs from published 1.0.

| Capability | [Goose](#goose) | [AI CLI Orchestrator](#ai-cli-orchestrator) | [multiagents](#multiagents) | [Maestro](#maestro) | [CAO](#cli-agent-orchestrator-cao-awslabs) | This framework |
| --- | --- | --- | --- | --- | --- | --- |
| CLI support | **partial** — connector guide lists four; Claude/Codex connectors are deprecated | **available** — Claude directs Codex or Gemini | **available** — Claude, Codex, Gemini | **available** — four host runtimes | **available** — broad CLI list in README | **partial** — four executor adapters in the checkout (three in published 1.0; Antigravity is under `[Unreleased]`), disabled by default; no ACP adapter is documented |
| Parallel work | **partial** — subagents/workflows; connector-level parallelism not documented | **not documented** — described flows are sequential | **available** — coordinated agent teams | **available** — parallel or sequential phases | **available** — parallel sessions and assignment | **partial** — concurrent runs and task plans, not a general CLI-agent fleet |
| Per-worker worktrees | **not documented** | **not documented** | **not documented** | **not documented** | **partial** — isolated-worktree example | **not documented** as a product capability |
| Handoff and messages | **partial** — agent composition; connector details are limited | **partial** — sequential prompts or file bridge | **available** — messages, steering, and recovery | **partial** — phase-gated workflow handoffs | **available** — assignment and handoff | **partial** — Human Bridge and task envelopes; dynamic delegation remains incomplete |
| Quota and cost evidence | **not documented** for connected CLI plans | **not documented** | **partial** — token use, not native plan quota or cost | **not documented** | **partial** — usage/state, not uniform commercial quota or final cost | **partial** — executor-specific quota evidence; cost is an estimate only when token and price evidence are comparable |
| Local models | **available** — README describes local model support | **not documented** | **not documented** | **partial** — depends on the host runtime | **partial** — depends on the selected CLI/provider | **available** — Ollama |
| Own GUI | **available** — desktop app | **not documented** — terminal/scripts | **available** — web and TUI dashboards | **not documented** — uses the host runtime's interface | **available** — web and terminal interfaces | **available** — local operator GUI at `/gui/` in published 1.0 |
| Non-technical user | **partial** — desktop app, but setup choices remain | **not documented** as non-technical-friendly — the README describes developer scripts and terminal use | **not documented** as non-technical-friendly — the README requires Bun and installed CLIs | **not documented** as non-technical-friendly — the README describes a developer workflow in a host runtime | **not documented** as non-technical-friendly — the README describes technical server/CLI setup | **partial** — 1.0 has a local GUI; expanded simple onboarding is still unreleased |
| Packaged teams or offices | **not documented** as an importable office system | **not documented** | **partial** — teams/roles, not an office package | **partial** — specialist plugins, not an office format | **partial** — profiles, not an office format | **partial** — office/area flows are in `[Unreleased]`, not published 1.0 |
| Consent stored as policy | **partial** — permission modes; no office-level consent documented | **not documented** | **partial** — guardrails, not per-person executor activation | **partial** — workflow gates, not persistent user consent | **partial** — profiles and restrictions; `--yolo` can bypass them | **partial** — persistent per-executor activation is in `[Unreleased]`; it is not blanket approval for every action |
| Deterministic gates and contracts | **partial** — approvals/workflows; typed output contracts not documented in the connector guide | **not documented** — the README describes Codex sandboxing, which is not a workflow gate | **partial** — review cycle, typed contracts not documented | **available** — plan validation and phase gates | **partial** — configurable restrictions and workflows | **available** — workflow contracts, human checkpoints, and task gates; secret-gate additions remain unreleased |
| Cross-CLI switch at quota limit | **not documented** | **not documented** | **not documented** | **not documented** | **not documented** as a quota rule | **partial** — executor quota holds are documented; no general automatic cross-CLI switch is claimed |

## Product capabilities versus development tooling

The framework product is the local operator interface, workflow runtime, and its documented task, team, and executor capabilities. The internal development process is separate: task cards, `tools/seguimiento`, per-task worktrees, and delegated contributors are ways to build and integrate the repository; they are not features of the released product.

| Development need | Most relevant comparison | Boundary |
| --- | --- | --- |
| Track task state, evidence, and handoffs | multiagents (messages and shared state); CAO (sessions and assignments) | These are references for internal tracking, not evidence that the product provides the same developer dashboard. |
| Isolate concurrent changes | CAO (documented worktree example); Maestro (phase gates) | Worktrees isolate development changes; they are not equivalent to importable user offices. |
| Delegate and review work | Maestro (phase approvals); multiagents (messages and recovery) | Internal delegation and product planning/delegation are distinct capabilities with separate release status above. |

## Findings and scenarios

- **A local office for a non-technical operator:** the published 1.0 GUI is available, but office installation, office privacy, and simplified onboarding are not published 1.0 features. Do not select this framework on the assumption those checkout changes have shipped.
- **A parallel CLI development team:** CAO and multiagents are the closest documented comparisons for live sessions, coordination, and parallel CLI work; Maestro is a useful reference for gated phases. None replaces the repository's own task cards, worktrees, and integration process.
- **A local-model workflow:** this framework documents Ollama support; Goose documents a broader local-model surface. These sources establish capability, not comparative model quality or performance.

Goose's connector guide marks its Claude Code and Codex connectors deprecated and recommends ACP. An optional ACP adapter or a compatibility notice is a reasonable **separate design candidate**, not an implementation commitment: protocol support does not guarantee feature, permission, session, or quota parity. Any future work should define those boundaries and avoid silent fallback. This comparison does not commit the project to an adapter or to a release date.

These descriptions are snapshots of public documentation, not tests of installed tools, subscriptions, security behavior, live quota, billed cost, or worktree isolation. AI CLI Orchestrator and multiagents declare MIT in their READMEs, but no `LICENSE` file was found in the repository roots checked; verify licensing before considering code reuse. No code or project text is copied here. See [the project's license](LICENSING.md).

## When another tool may fit better

| If the need is… | Consider… |
| --- | --- |
| A desktop general-purpose agent with local models | Goose |
| A small sequential bridge between already installed CLIs | AI CLI Orchestrator |
| Shared messages and a live dashboard for CLI agents | multiagents |
| Gated development phases inside a host runtime | Maestro |
| A local control plane for many CLIs, sessions, and worktrees | CAO |
| Declared workflows, contracts, human review, local models, and a local operator GUI | This framework |

Corrections are welcome when a cited project's documentation changes.

## What we are building

Multi-Agent Framework is a **domain-neutral, configurable runtime for building and operating teams of AI agents, humans, models, and tools**.

The goal is that any user can progressively define:

- how many agents they need, with their roles, purposes, prompts, models, and providers;
- the tools and capabilities each agent has, and the relationships between agents;
- supervision, rules, workflows, organizational structures, and execution policies.

The same core should support progressively different degrees of autonomy:

- manual and human-guided execution;
- deterministic workflows;
- manual planning, then semi-automatic planning with human approval, then automatic planning;
- dynamic delegation and selection of specialists by capability;
- parallel execution where it makes sense, reviews, replanning, and temporary or dynamic teams.

Whatever the degree of autonomy, the core keeps contracts, validation, persistence, traceability, human control, permissions, budgets, and execution policies.

**The core is domain-neutral.** Software development, architecture and engineering, research, design, finance, operations, media production, or document analysis are built on it through configuration, presets, plugins, adapters, workflows, or projects, never through domain logic in the core.

## Three levels, kept separate

| Level | What it means | Where to look |
| --- | --- | --- |
| **Published release** | What is included in the tagged 1.0 release | [`README.md`](../README.md), [`CHANGELOG.md`](../CHANGELOG.md) |
| **Current checkout** | What works in this repository, including changes not yet released | Capability statuses below, [`CHANGELOG.md`](../CHANGELOG.md) |
| **Roadmap** | Planned work that is not yet complete | Capability statuses below, [`ROADMAP.md`](../ROADMAP.md), [`PROGRESS.md`](../PROGRESS.md) |
| **Direction** | The long-term architecture | [Architectural direction](#architectural-direction) and the roadmap's autonomy principle |

## Architectural direction

- **From declared workflows to governed autonomy.** Today every step, its order, and its main conditions are declared before a run starts, and models produce each step's content. The framework will keep supporting that mode. Over time, a planner or orchestrator will decompose objectives, assign and delegate tasks, consult specialists, request reviews, replan, and choose routes dynamically. In short, *autonomous execution of declared workflows* evolves into *autonomous orchestration within declared organizational constraints*.
- **The declarative structure stays, with a new role.** Instead of declaring every transition, it declares the limits: organization, roles, capabilities, permissions, contracts, budgets, gates, scoped workspaces, and policies, with escalation to a human. The goal is an autonomous and traceable organization, not agents conversing without restrictions.
- **Reuse before rebuilding.** Mature components such as graph execution, MCP, A2A, AG-UI, and OpenTelemetry are consumed through optional backends and adapters instead of being rewritten. The framework's own configuration, contracts, runs, Human Bridge, artifacts, and policies stay independent of any backend.
- **One public layer for every interface.** The CLI, HTTP API, and local operator GUI use the same application services. Future MCP servers and notebooks should use that same layer.

## Capabilities and status

Statuses describe implementation in the current checkout; notes distinguish published 1.0, `[Unreleased]`, and roadmap work where relevant. **Not documented** means a capability is not established by the cited repository documentation, not that it is impossible.

| Area | Capability | Status | Notes |
| --- | --- | --- | --- |
| Execution | Deterministic declared workflows (YAML) | **available** | Steps, order, conditions, checkpoints, preferred and fallback model |
| Execution | Validation before execution | **available** | Structure and references checked before a run exists, with stable issue codes |
| Execution | Structured contracts between steps | **available** | Pydantic output contracts for model and human output |
| Execution | Durable, resumable runs (SQLite) | **available** | Resume after review, restarts, idempotent requests, call and time budgets; tasks can also be held and resumed at usage limits |
| Execution | Traceability | **available** | Prompt id, version, and hash; model or human; telemetry; artifacts |
| Execution | Execution backend abstraction (native, mock, optional backends) | **available** | Native and mock implementations and the backend contract are in the current checkout; the optional Microsoft Agent Framework backend remains planned ([backends](BACKENDS.md)) |
| Execution | Microsoft Agent Framework as optional backend | **not documented** | Planned as optional; no implementation is available ([roadmap](../ROADMAP.md), [backends](BACKENDS.md)) |
| Execution | Parallel steps and several workers | **partial** | Several workers are available in the current checkout; general parallel workflow-step execution remains open ([backends](BACKENDS.md#several-workers), [roadmap](../ROADMAP.md)) |
| Humans | Human-in-the-loop review at any step | **available** | Approve the exact attempt, request changes, regenerate, reject, cancel |
| Humans | Human Bridge (externally executed steps) | **available** | The framework hands out the prompt and validates the pasted answer |
| Humans | Impact-based permissions and policy-driven gates | **partial** | Effect approval, task gates, and budget holds exist; impact classes for tools and per-risk policies do not |
| Definitions | Definition bundles outside the package | **available** | Workflows, agents, prompts, contracts, and sample outputs in a folder |
| Definitions | Team and organization definitions (members, relationships) | **partial** | Team definitions and office data exist in the current checkout; runtime role use and broader organization behavior remain limited ([teams](TEAMS.md), [offices](OFFICES.md)) |
| Definitions | Team design from a prompt (roles, rules, organization chart, recommended model bindings) | **not documented** | Roadmap work; output is intended to be a validated, human-approved team definition, with roles not tied to models ([roadmap](../ROADMAP.md)) |
| Definitions | Capabilities on agents | **partial** | Declared capabilities select models and executor surfaces; agents are not yet selected this way |
| Definitions | Presets (teams, workflows, agents, prompts, checks) | **partial** | Bundles are the foundation; a software development example bundle exists, while team presets are planned |
| Definitions | Community catalog of presets | **not documented** | Packaging, install, validation, and trust rules are planned |
| Definitions | Reference tool stacks for common teams (for example Tailwind CSS for web design) | **not documented** | Under evaluation as examples inside presets, never core dependencies |
| Interfaces | Python API (application services) | **available** | `multiagent.services`: one facade with structured errors |
| Interfaces | HTTP API with parity | **available** | Generic and bundle runs, definitions, artifacts, structured errors |
| Interfaces | CLI | **available** | Uses the same HTTP API |
| Interfaces | Mock mode without API keys | **available** | Synthetic fixtures through the same engine and contracts |
| Interfaces | Graphical UI | **available** | Local operator GUI over the API is part of 1.0; configuration, expanded simple onboarding, teams, and plans are later work ([GUI](GUI.md), [changelog](../CHANGELOG.md)) |
| Models | Local models (Ollama) | **available** | Text generation; sensitive runs can stay local |
| Models | Cloud providers (Gemini, OpenAI) | **available** | Optional |
| Models | Routing by preferred model and fallback | **available** | Static per step and execution mode |
| Models | Intelligent routing (capability, cost, hardware, availability) | **partial** | Opt-in profiles use capabilities, privacy, health evidence, and price; hardware-aware and resource-aware scheduling are not done |
| Models | More local models: research, embeddings, image, video | **not documented** | Evaluation or roadmap direction; not available as supported local model capabilities |
| Orchestration | Structured context between agents | **partial** | Validated summaries and task envelopes are available; free agent-to-agent messaging is not implemented |
| Orchestration | Manual, semi-automatic, and automatic planning | **partial** | Plan proposals can be created and reviewed in the current checkout; the complete range of planning modes is not implemented ([planning](PLANNING.md), [roadmap](../ROADMAP.md)) |
| Orchestration | Delegation and capability-based assignment | **partial** | Assignment preview and guarded claim exist; delegation between agents does not |
| Orchestration | Replanning and dynamic or temporary teams | **not documented** | Planned after planning is stable; not implemented |
| Orchestration | Manual, semi-automatic, and automatic planning | **partial** | Validated plan data and planners exist in part; full planning modes and atomic group creation remain incomplete ([planning](PLANNING.md), [roadmap](../ROADMAP.md)) |
| Tools | Generic tool contracts and deterministic function steps | **partial** | Deterministic function steps exist; generic tool contracts remain planned ([function steps](FUNCTION_STEPS.md), [roadmap](../ROADMAP.md)) |
| Tools | MCP server (framework exposed as tools) | **not documented** | Planned over the same application services |
| Tools | MCP client (use external MCP tools) | **not documented** | Planned through Microsoft Agent Framework or the official SDK |
| Tools | A2A (remote agents) | **not documented** | Optional interoperability when a concrete need appears |
| Tools | Shared-folder channel, desktop bridge, application gateway | **partial** | Applications can be opened with approval and task executors launched; there is no shared-folder channel or prompt clipboard flow |
| Tools | Integrations with external applications (design, CAD/BIM, office, media, development) | **partial** | Three development-agent CLI adapters were included in 1.0; Antigravity was added under `[Unreleased]`; all are disabled by default ([executors](EXECUTORS.md), [changelog](../CHANGELOG.md)) |
| Observability | Local usage telemetry | **available** | Usage records are stored locally |
| Observability | OpenTelemetry | **partial** | Attaches when an execution backend is built; broader integration remains optional ([backends](BACKENDS.md)) |
| Observability | AG-UI as the event transport for the UI | **not documented** | Under evaluation; no adoption decision |

## Comparison with other frameworks

The frameworks below were not re-checked on 2026-11-25. Their descriptions are deliberately general and may be out of date; check each project's own documentation before choosing.

This compares **scope and architecture, not quality**.

| Framework | Main focus | Overlap with this project | Architectural difference | What we could reuse | What stays in our core and configuration |
| --- | --- | --- | --- | --- | --- |
| **[Microsoft Agent Framework](https://github.com/microsoft/agent-framework)** (successor of AutoGen and Semantic Kernel agents) | Agents plus graph workflows with checkpoints, MCP, A2A, AG-UI, and OpenTelemetry | Workflows, human-in-the-loop, persistence | A general agent runtime; our project centers on user-declared teams, contracts, and policies | Graph execution, MCP, A2A, AG-UI, and OpenTelemetry, as an **optional backend** | Definitions, validation, contracts, runs, Human Bridge, artifacts, and policies |
| **[AutoGen](https://github.com/microsoft/autogen)** (historical) | Conversational multi-agent programming | Multi-agent coordination | Conversation-first; its ideas continue in Microsoft Agent Framework | Through Microsoft Agent Framework | Same as above |
| **[LangGraph](https://github.com/langchain-ai/langgraph)** | Stateful graphs of LLM steps with checkpointing | Durable execution, human interrupts | A graph library in a larger ecosystem; our graph execution is planned through an optional backend | Ideas; possibly an adapter | Contracts, team definitions, Human Bridge |
| **[CrewAI](https://github.com/crewAIInc/crewAI)** | Role-based crews and flows with low setup | Roles, tasks, delegation | Roles and processes defined in code; we target validated declarative teams with governance | Ideas for role and process presets | Validation, contracts, persistence, policies |
| **[AgentScope](https://github.com/agentscope-ai/agentscope)** | Multi-agent applications with message-based coordination | Multi-agent teams, distribution | Message-driven agents; our messages will be structured and contract-validated | Possibly an adapter for distributed execution | Contracts, human control, traceability |
| **[OpenAI Agents SDK](https://github.com/openai/openai-agents-python)** | Lightweight agents with handoffs, guardrails, and tracing | Handoffs, guardrails, tracing | Designed around OpenAI's platform and tracing, though other providers can be plugged in; our core is provider-neutral and local-first | Handoff and guardrail patterns | Provider neutrality, local models, contracts |
| **[MetaGPT](https://github.com/geekan/MetaGPT)** | Simulated software company with fixed roles and standard procedures | Role-based teams and procedures | A fixed software-team template; we keep domain teams as presets | The idea of standard procedures as presets | A domain-neutral core |
| **[Paperclip](https://github.com/paperclipai/paperclip)** (MIT) | An operations layer that runs a company of autonomous agents: org chart, goals, budgets, heartbeats, approvals, audit log | Team and organization definitions, budgets, approvals, adapters, audit trail | Operations-first and always-on, in Node.js/TypeScript with PostgreSQL; ours is contract-first, with declared workflows, a Human Bridge, and local-first Python | Ideas: atomic task checkout, budget-driven pause, revisioned configuration, portable organization export with secret scrubbing, and how the org chart shows a role and its holder on one card | Contracts, validation, Human Bridge, and a domain-neutral organization model with no mandatory company roles |
| **OpenAI dots** (closed product on paid ChatGPT plans, announced 2026-09-29) | Always-on personal and organization agents inside ChatGPT, each with its own cloud computer and app connections (specialized dots add their own identity and credentials), plus built-in and custom approval rules | Governed autonomy, approval rules, specialized role holders, human review, background work | A closed, cloud-hosted product tied to ChatGPT plans; ours is an open, provider-neutral, local-first runtime with declared workflows and contracts | Ideas: a three-way action policy (allow, require approval, block) with safety rules that cannot be overridden, a read-only mode for background work, credentials that never reach the model, and a dot as an optional external role holder or Human Bridge target | Definitions, contracts, validation, runs, and policies, with no vendor dependency. No public API or plugin specification is known, so any integration would be an optional adapter and is not planned |
| **[OpenClaw](https://github.com/openclaw/openclaw)** (MIT) | Open-source personal and operations assistant: a local Gateway (Node.js/TypeScript) connecting one agent to 30+ chat channels, companion apps, tools, sessions, cron, and subagents; MCP client and server, A2A as a bundled channel plugin | Subagents and sessions, per-tool approvals, automations, context compaction, control UI, MCP and A2A | Assistant-first and channel-first, with the Gateway as control plane and tool sandboxing off by default; ours is contract-first, run-centric, local-first Python with human review and validated artifacts | Interoperate over MCP, and A2A when a need appears; ideas: tool deny policies, pairing and allow-lists, sandbox profiles, audited compaction summaries, channel adapters for notifications and approvals | Definitions, contracts, runs, Human Bridge, artifacts, policies, and the private workspace; the Gateway, its plugins, and its channels are never core dependencies |
| **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** (Nous Research, MIT) | Open-source autonomous terminal agent in Python: many providers, isolated subagents, persistent curated memory, skills hub, cron, messaging gateway (Telegram, Discord, Slack, WhatsApp, Signal, email), MCP client, three approval modes (`smart` by default, `manual`, `off`) with a hard blocklist, and local, SSH, or container backends | Delegation to subagents, approvals, scheduled work, memory and context compression, sessions, dashboard, MCP | A single agent that does the work itself; ours orchestrates declared teams and external executors under contracts, gates, and budgets, and never lets a model judge its own risk | Candidate external executor (headless CLI with `deny` defaults for unattended runs) behind the existing executor contract; ideas: per-context approval defaults, catastrophic-command blocklist, progressive skill loading, searchable memory | Contracts, validation, gates, Human Bridge, budgets, and policies; its memory, keys, and skills stay outside the repository and the core |

### Relationship with Microsoft Agent Framework

Microsoft Agent Framework is a mature, MIT-licensed runtime. Instead of reimplementing its pieces, this project plans to use it as an **optional execution backend**:

- it is not a fork and not a mandatory dependency;
- workflow definitions, configuration, validation, contracts, runs, the Human Bridge, artifacts, and policies remain this project's own;
- everything keeps working with the default backend when it is not installed;
- only its stable packages are considered for the backend, and beta or experimental features stay behind explicit adapters.

## Not implemented in the current release

These are roadmap items or capabilities that remain incomplete, not design limits. The repository may contain later work under `[Unreleased]`; that work is not part of published 1.0.

- graphical UI beyond the local operator GUI (configuration, teams, tools, and live topology);
- the optional Microsoft Agent Framework backend; the native/mock backend abstraction is available in the current checkout;
- full team and organization behavior; team definitions, approved versions, and office data exist in the current checkout, while runtime role use and wider collaboration remain partial;
- planning (manual, semi-automatic, automatic), delegation, replanning, and dynamic teams; proposals and planners are partial;
- general parallel workflow steps and graphs with cycles; several workers are available in the current checkout;
- generic tool contracts, the MCP server and client, A2A, and most external application integrations; deterministic function steps are available and development-agent CLI adapters are partial;
- intelligent routing beyond its partial, opt-in profiles, more local models, and retrieval over large knowledge bases;
- presets as installable packages and a community catalog;
- team design from a prompt, and reference tool stacks for common teams.

The first public baseline also ships a social-content example workflow and branding services inside the package. They are an example of what can be built, not a direction of the core.

## Intentionally outside the core

These are architectural decisions, not missing features. They can exist through configuration, presets, plugins, adapters, or workflows, but they must not shape the generic framework:

- logic specific to an industry;
- specific marketing or social-media logic;
- CAD/BIM logic;
- logic specific to software development;
- financial logic;
- mandatory roles or organizational structures;
- a mandatory provider, or concrete models hard-coded in the core;
- Microsoft Agent Framework, or any other backend, as a required dependency;
- unrestricted agent conversation without declared limits.

## When another framework may fit better today

| If you need, today… | Consider… |
| --- | --- |
| Agents that plan and talk to each other with little upfront structure | Microsoft Agent Framework orchestrations (group chat, handoff, Magentic) or AgentScope |
| Graph-shaped control flow with branches and cycles | Microsoft Agent Framework workflows or LangGraph |
| Quick role-based "crews" with minimal configuration | CrewAI |
| Simulating a full software team with predefined roles | MetaGPT |
| Declared steps, strict contracts, human review at any point, local-first models, and a persistent audit trail | **This project** |

Corrections are welcome: if a description here is inaccurate for a project you know well, please open an issue.
