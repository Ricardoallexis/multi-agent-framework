# How Multi-Agent Framework differs

There are many open-source frameworks for building multi-agent systems. Before and during development we reviewed several of them, including Microsoft Agent Framework (the successor of AutoGen), MetaGPT, AutoGen, CrewAI, LangGraph, AgentScope, OpenAI's multi-agent work, and smaller community projects. We did not copy code from them. This page explains the design choices that make this project different, so you can decide whether it fits your use case.

It compares **design priorities, not benchmarks**. The descriptions of other projects are deliberately general and may be out of date; please check their own documentation before choosing.

## In one sentence

Multi-Agent Framework is a **deterministic, contract-first runtime for human/AI workflows**. You declare the steps, each step's output is validated against a strict schema, a human can review or take over any step, and every run is persisted and traceable. It runs locally first, and a real LLM is optional. The roadmap extends it towards teams of agents with **governed autonomy**: they will plan, delegate, and route work themselves, within limits the user declares.

## Design choices that set it apart

| Choice | What it means in practice |
| --- | --- |
| **Declared workflows today, governed autonomy as the goal** | Today, steps, their order and their conditions live in YAML files. The model produces each step's content; it does not choose the next agent or reorganize the flow at runtime. Runs are predictable and repeatable. Autonomy is planned, but always within declared limits, never as unrestricted conversation (see [Autonomy](#autonomy-current-state-and-target)). |
| **Validation before execution** | A workflow is checked before a run is created: YAML structure, duplicate steps, unknown agents, contracts, models, prompts and skills, and unsupported conditions. Every problem is reported at once with a stable code and its location, and an invalid definition never leaves a half-started run behind. |
| **Strict contracts between steps** | Every step declares a Pydantic output contract. Model output, and output pasted by a human, must satisfy it before the next step sees it. |
| **Human-in-the-loop as a first-class path** | Any step can pause for review. A reviewer approves the exact attempt they saw, asks for changes, regenerates, or rejects, and approved steps are never repeated. A step can also be executed outside the framework (for example, in any chat tool): the framework hands out the prompt and validates the pasted answer against the same contract. |
| **Durable, resumable runs** | Runs, attempts, artifacts and events are stored in SQLite. A run resumes from the next step after an intermediate review, survives restarts, respects call and time budgets, and supports idempotent requests. If a workflow file changes while a run is waiting, the change is detected instead of executed blindly. |
| **Local-first model use** | Ollama runs models locally, and Gemini and OpenAI are optional. Each step names a preferred model and at most one fallback. Sensitive runs can be forced to stay local. |
| **The same core for tests and production** | Dry runs use a fake adapter through the same engine, store and contracts, so whole workflows are tested without API keys. Definition bundles can ship their own sample outputs for this. |
| **Definitions outside the core** | A *definition bundle* is a folder with its own workflows, agents, prompts and contracts. It is validated and executed with a generic request (`workflow_id` plus inputs) without changing the framework's code, and it can live outside this repository, for example for private configurations. |
| **Traceability** | Each attempt records the prompt id, version and hash, the model or human who produced it, telemetry, and the resulting artifact. |
| **Small operational footprint** | Plain Python, SQLite, a CLI and an optional FastAPI server. There is no separate orchestration server or cloud service to run. |

## Autonomy: current state and target

**Current state.** The runtime executes declared workflows. Steps, their order, and their main conditions are defined before a run starts, and models produce the content of each step. Agents do not yet choose the next agent or reorganize the flow at runtime.

**Target architecture (planned).** The [roadmap](../ROADMAP.md) moves towards agent teams with governed autonomy. A planner or orchestrator will be able to:

- break an objective into tasks;
- assign and delegate those tasks;
- consult specialists and request reviews;
- replan;
- choose routes dynamically.

That autonomy is bounded by what the user declares: the organization and its roles, capabilities, permissions, contracts, budgets, gates, scoped workspaces, and policies, with escalation to a human. The goal is an autonomous and traceable organization working within declared limits, not a group of agents conversing without restrictions.

In short, *autonomous execution of declared workflows* evolves into *autonomous orchestration within declared organizational constraints*. The declarative structure stays but changes its role. Instead of declaring every conversation or exact transition, it defines the limits, relationships, permissions, capabilities, policies, and contracts within which agents make decisions. None of this is implemented yet; see the roadmap sections on collaborative agent teams and on planning and delegation.

## What it deliberately does not do (yet)

These are open directions in the [roadmap](../ROADMAP.md), not current features:

- Dynamic routing, delegation, and replanning at runtime. They are planned as governed autonomy (see above). Agents conversing without declared limits is not a goal.
- Parallel steps, graphs with cycles, or several workers competing for one queue.
- A graphical interface. The API is the intended boundary for a future UI.
- Retrieval over large knowledge bases, tool calling beyond model providers, and MCP/A2A integrations.

## When another framework may fit better

| If you mainly need… | Consider… |
| --- | --- |
| Agents that plan and talk to each other with little upfront structure | Microsoft Agent Framework orchestrations (group chat, handoff, Magentic) or AgentScope |
| Graph-shaped control flow with branches and cycles | Microsoft Agent Framework workflows or LangGraph |
| Quick role-based "crews" with minimal configuration | Role-based frameworks such as CrewAI |
| Simulating a full software team with predefined roles | MetaGPT |
| Predictable steps, strict contracts, human review at any point, local-first models and a persistent audit trail | **This project** |

## Relationship with Microsoft Agent Framework

Microsoft Agent Framework is a mature, MIT-licensed runtime with graph workflows, checkpoints, MCP, A2A, AG-UI, and OpenTelemetry. Instead of reimplementing those pieces, this project plans to use it as an **optional execution backend**. It is not a fork or a rigid wrapper: workflow definitions, validation, contracts, runs, the Human Bridge, and artifacts remain this project's own, and everything keeps working with the default backend when Microsoft Agent Framework is not installed. Only its stable packages are considered for that backend; beta and experimental features stay behind explicit adapters.

Corrections are welcome: if a description here is inaccurate for a project you know well, please open an issue.
