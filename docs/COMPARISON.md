# Scope, status, and comparison

> **About this document.** It describes the **target scope and architectural direction** of Multi-Agent Framework and compares it with other agent frameworks. Some capabilities already work; others are part of the [roadmap](../ROADMAP.md). A status marker on every capability separates what you can use today from planned work. Nothing marked as planned is implemented yet.

Status legend:

| Marker | Meaning |
| --- | --- |
| ✅ | Available now in this repository |
| 🟡 | Partial or evolving: a foundation exists, the full capability does not yet |
| 🧭 | Planned in the roadmap, not implemented |
| 🔌 | Planned through an optional backend, adapter, or integration, not implemented |
| 🔬 | Under evaluation: no decision yet |

Some ✅ items are in the repository but not yet in a tagged release; [`CHANGELOG.md`](../CHANGELOG.md) lists them under `[Unreleased]`.

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
| **Current** | What works today | ✅ rows below, [`README.md`](../README.md), [`CHANGELOG.md`](../CHANGELOG.md) |
| **Roadmap** | Planned, not implemented | 🧭/🔌/🔬 rows below, [`ROADMAP.md`](../ROADMAP.md), [`PROGRESS.md`](../PROGRESS.md) |
| **Direction** | The long-term architecture | [Architectural direction](#architectural-direction) and the roadmap's autonomy principle |

## Architectural direction

- **From declared workflows to governed autonomy.** Today every step, its order, and its main conditions are declared before a run starts, and models produce each step's content. The framework will keep supporting that mode. Over time, a planner or orchestrator will decompose objectives, assign and delegate tasks, consult specialists, request reviews, replan, and choose routes dynamically. In short, *autonomous execution of declared workflows* evolves into *autonomous orchestration within declared organizational constraints*.
- **The declarative structure stays, with a new role.** Instead of declaring every transition, it declares the limits: organization, roles, capabilities, permissions, contracts, budgets, gates, scoped workspaces, and policies, with escalation to a human. The goal is an autonomous and traceable organization, not agents conversing without restrictions.
- **Reuse before rebuilding.** Mature components such as graph execution, MCP, A2A, AG-UI, and OpenTelemetry are consumed through optional backends and adapters instead of being rewritten. The framework's own configuration, contracts, runs, Human Bridge, artifacts, and policies stay independent of any backend.
- **One public layer for every interface.** The CLI, the HTTP API, and future UI, MCP server, and notebooks use the same application services.

## Capabilities and status

| Area | Capability | Status | Notes |
| --- | --- | --- | --- |
| Execution | Deterministic declared workflows (YAML) | ✅ | Steps, order, conditions, checkpoints, preferred and fallback model |
| Execution | Validation before execution | ✅ | Structure and references checked before a run exists, with stable issue codes |
| Execution | Structured contracts between steps | ✅ | Pydantic output contracts for model and human output |
| Execution | Durable, resumable runs (SQLite) | ✅ | Resume after review, restarts, idempotent requests, call and time budgets |
| Execution | Traceability | ✅ | Prompt id, version, and hash; model or human; telemetry; artifacts |
| Execution | Execution backend abstraction (native, mock, optional backends) | 🧭 | Next after the minimal UI in the development order |
| Execution | Microsoft Agent Framework as optional backend | 🔌 | Never a required dependency of the core |
| Execution | Parallel steps and several workers | 🧭 | Today one local worker processes a queue |
| Humans | Human-in-the-loop review at any step | ✅ | Approve the exact attempt, request changes, regenerate, reject, cancel |
| Humans | Human Bridge (externally executed steps) | ✅ | The framework hands out the prompt and validates the pasted answer |
| Humans | Impact-based permissions and policy-driven gates | 🧭 | On top of the existing human-in-the-loop |
| Definitions | Definition bundles outside the package | ✅ | Workflows, agents, prompts, contracts, and sample outputs in a folder |
| Definitions | Team and organization definitions (members, relationships) | 🧭 | Teams and relationships as data |
| Definitions | Capabilities on agents | 🟡 | Declared in the agent catalog; not yet used to select agents |
| Definitions | Presets (teams, workflows, agents, prompts, checks) | 🟡 | Bundles are the foundation; team presets are planned |
| Definitions | Community catalog of presets | 🧭 | Packaging, install, validation, and trust rules planned |
| Interfaces | Python API (application services) | ✅ | `multiagent.services`: one facade with structured errors |
| Interfaces | HTTP API with parity | ✅ | Generic and bundle runs, definitions, artifacts, structured errors |
| Interfaces | CLI | ✅ | Uses the same HTTP API |
| Interfaces | Mock mode without API keys | ✅ | Synthetic fixtures through the same engine and contracts |
| Interfaces | Graphical UI | 🧭 | Stage 0 (operator UI over the API) is next |
| Models | Local models (Ollama) | ✅ | Text generation; sensitive runs can stay local |
| Models | Cloud providers (Gemini, OpenAI) | ✅ | Optional |
| Models | Routing by preferred model and fallback | ✅ | Static per step and execution mode |
| Models | Intelligent routing (capability, cost, hardware, availability) | 🧭 | Resource-aware scheduling included |
| Models | More local models: research, embeddings, image, video | 🔬 | Evaluation of models and providers |
| Orchestration | Structured context between agents | 🟡 | Steps receive earlier outputs as validated context; free agent-to-agent messaging is not implemented |
| Orchestration | Manual, semi-automatic, and automatic planning | 🧭 | Plans as data, executed by the existing engine |
| Orchestration | Delegation and capability-based assignment | 🧭 | Deterministic rules first |
| Orchestration | Replanning and dynamic or temporary teams | 🧭 | After planning is stable |
| Tools | Generic tool contracts and deterministic function steps | 🧭 | Separate from model-provider adapters |
| Tools | MCP server (framework exposed as tools) | 🧭 | Over the same application services |
| Tools | MCP client (use external MCP tools) | 🔌 | Through Microsoft Agent Framework or the official SDK |
| Tools | A2A (remote agents) | 🔌 | When a concrete need appears |
| Tools | Shared-folder channel, desktop bridge, application gateway | 🧭 | For applications without an API |
| Tools | Integrations with external applications (design, CAD/BIM, office, media, development) | 🔌 | Independent adapters or plugins |
| Observability | OpenTelemetry | 🔌 | Through the optional backend or an adapter |
| Observability | AG-UI as the event transport for the UI | 🔬 | To be evaluated with the UI |

## Comparison with other frameworks

This compares **scope and architecture, not quality**. Descriptions of other projects are deliberately general and may be out of date; check their own documentation before choosing.

| Framework | Main focus | Overlap with this project | Architectural difference | What we could reuse | What stays in our core and configuration |
| --- | --- | --- | --- | --- | --- |
| **Microsoft Agent Framework** (successor of AutoGen and Semantic Kernel agents) | Agents plus graph workflows with checkpoints, MCP, A2A, AG-UI, and OpenTelemetry | Workflows, human-in-the-loop, persistence | A general agent runtime; our project centers on user-declared teams, contracts, and policies | Graph execution, MCP, A2A, AG-UI, and OpenTelemetry, as an **optional backend** | Definitions, validation, contracts, runs, Human Bridge, artifacts, and policies |
| **AutoGen** (historical) | Conversational multi-agent programming | Multi-agent coordination | Conversation-first; its ideas continue in Microsoft Agent Framework | Through Microsoft Agent Framework | Same as above |
| **LangGraph** | Stateful graphs of LLM steps with checkpointing | Durable execution, human interrupts | A graph library in a larger ecosystem; our graph execution is planned through an optional backend | Ideas; possibly an adapter | Contracts, team definitions, Human Bridge |
| **CrewAI** | Role-based crews and flows with low setup | Roles, tasks, delegation | Roles and processes defined in code; we target validated declarative teams with governance | Ideas for role and process presets | Validation, contracts, persistence, policies |
| **AgentScope** | Multi-agent applications with message-based coordination | Multi-agent teams, distribution | Message-driven agents; our messages will be structured and contract-validated | Possibly an adapter for distributed execution | Contracts, human control, traceability |
| **OpenAI Agents SDK** | Lightweight agents with handoffs, guardrails, and tracing | Handoffs, guardrails, tracing | Designed around OpenAI's platform and tracing, though other providers can be plugged in; our core is provider-neutral and local-first | Handoff and guardrail patterns | Provider neutrality, local models, contracts |
| **MetaGPT** | Simulated software company with fixed roles and standard procedures | Role-based teams and procedures | A fixed software-team template; we keep domain teams as presets | The idea of standard procedures as presets | A domain-neutral core |

### Relationship with Microsoft Agent Framework

Microsoft Agent Framework is a mature, MIT-licensed runtime. Instead of reimplementing its pieces, this project plans to use it as an **optional execution backend**:

- it is not a fork and not a mandatory dependency;
- workflow definitions, configuration, validation, contracts, runs, the Human Bridge, artifacts, and policies remain this project's own;
- everything keeps working with the default backend when it is not installed;
- only its stable packages are considered for the backend, and beta or experimental features stay behind explicit adapters.

## Not implemented in the current alpha

These are roadmap items, not design limits. Each will arrive when its turn in the development order comes:

- a graphical interface (stage 0 is next);
- the execution backend abstraction and the optional Microsoft Agent Framework backend;
- team and organization definitions, relationships, and capability-based assignment;
- planning (manual, semi-automatic, automatic), delegation, replanning, and dynamic teams;
- parallel steps, graphs with cycles, and several workers;
- generic tool contracts, the MCP server and client, A2A, and integrations with external applications;
- intelligent routing, more local models, and retrieval over large knowledge bases;
- presets as installable packages and a community catalog.

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
