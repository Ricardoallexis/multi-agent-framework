# How Multi-Agent Framework differs

There are many open-source frameworks for building multi-agent systems. Before and during development we reviewed several of them, including MetaGPT, AutoGen, CrewAI, LangGraph, AgentScope, OpenAI's multi-agent work, and smaller community projects. We did not copy code from them. This page explains the design choices that make this project different, so you can decide whether it fits your use case.

It compares **design priorities, not benchmarks**. The descriptions of other projects are deliberately general and may be out of date; please check their own documentation before choosing.

## In one sentence

Multi-Agent Framework is a **deterministic, contract-first runtime for human/AI workflows**. You declare the steps, each step's output is validated against a strict schema, a human can review or take over any step, and every run is persisted and traceable. It runs locally first, and a real LLM is optional.

## Design choices that set it apart

| Choice | What it means in practice |
| --- | --- |
| **Declared workflows, not autonomous conversations** | Steps, their order and their conditions live in YAML files. The model produces each step's content; it does not decide which agent runs next. Runs are predictable and repeatable. |
| **Validation before execution** | A workflow is checked before a run is created: YAML structure, duplicate steps, unknown agents, contracts, models, prompts and skills, and unsupported conditions. Every problem is reported at once with a stable code and its location, and an invalid definition never leaves a half-started run behind. |
| **Strict contracts between steps** | Every step declares a Pydantic output contract. Model output, and output pasted by a human, must satisfy it before the next step sees it. |
| **Human-in-the-loop as a first-class path** | Any step can pause for review. A reviewer approves the exact attempt they saw, asks for changes, regenerates, or rejects, and approved steps are never repeated. A step can also be executed outside the framework (for example, in any chat tool): the framework hands out the prompt and validates the pasted answer against the same contract. |
| **Durable, resumable runs** | Runs, attempts, artifacts and events are stored in SQLite. A run resumes from the next step after an intermediate review, survives restarts, respects call and time budgets, and supports idempotent requests. If a workflow file changes while a run is waiting, the change is detected instead of executed blindly. |
| **Local-first model use** | Ollama runs models locally, and Gemini and OpenAI are optional. Each step names a preferred model and at most one fallback. Sensitive runs can be forced to stay local. |
| **The same core for tests and production** | Dry runs use a fake adapter through the same engine, store and contracts, so whole workflows are tested without API keys. Definition bundles can ship their own sample outputs for this. |
| **Definitions outside the core** | A *definition bundle* is a folder with its own workflows, agents, prompts and contracts. It is validated and executed with a generic request (`workflow_id` plus inputs) without changing the framework's code, and it can live outside this repository, for example for private configurations. |
| **Traceability** | Each attempt records the prompt id, version and hash, the model or human who produced it, telemetry, and the resulting artifact. |
| **Small operational footprint** | Plain Python, SQLite, a CLI and an optional FastAPI server. There is no separate orchestration server or cloud service to run. |

## What it deliberately does not do (yet)

These are open directions in the [roadmap](../ROADMAP.md), not current features:

- Agents that converse freely or choose the next agent at runtime (dynamic routing).
- Parallel steps, graphs with cycles, or several workers competing for one queue.
- A graphical interface. The API is the intended boundary for a future UI.
- Retrieval over large knowledge bases, tool calling beyond model providers, and MCP/A2A integrations.
- `RunRequest` and definition bundles are available from the Python interface; the CLI and REST API still use the social-content request.

## When another framework may fit better

| If you mainly need… | Consider… |
| --- | --- |
| Agents that plan and talk to each other with little upfront structure | Conversation-oriented frameworks such as AutoGen or AgentScope |
| Graph-shaped control flow with branches and cycles | Graph orchestration libraries such as LangGraph |
| Quick role-based "crews" with minimal configuration | Role-based frameworks such as CrewAI |
| Simulating a full software team with predefined roles | MetaGPT |
| Predictable steps, strict contracts, human review at any point, local-first models and a persistent audit trail | **This project** |

Corrections are welcome: if a description here is inaccurate for a project you know well, please open an issue.
