# Architecture

## Purpose

Multi-Agent Framework is a domain-neutral runtime for teams of AI agents, humans, models, and tools. Today it coordinates declared workflows composed of specialized agents, and combines local execution, cloud providers, and human participation without coupling workflow logic to a specific model provider. The planned evolution (teams, planning, delegation, optional backends) is described in [`COMPARISON.md`](COMPARISON.md) and the [roadmap](../ROADMAP.md). This page describes the current code.

## Main components

```text
main.py / CLI / Python callers
  -> FastAPI app (HTTP API)
  -> ApplicationServices (multiagent/services/)
      -> definitions (workflows, agents, bundles)
      -> runs -> RunService (one per bundle runtime)
          -> WorkflowEngine
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

The public Python layer (`ApplicationServices`) used by the HTTP API and, through it, the CLI. It lists and validates definitions, creates and steers runs (including definition bundles, each with its own store and worker), and reports failures as `ServiceError` with stable codes. See [`API.md`](API.md).

### `multiagent/api.py`

Exposes the HTTP API over the application services: definitions, runs, reviews, cancellation, externally executed human-guided steps, and artifacts, with structured errors.

### `multiagent/cli.py`

Provides development and operational commands for diagnostics, runs, Human-in-the-Loop flows, assets, publications, metrics, and branding.

### `multiagent/workflow_engine.py`

Executes workflow steps, builds context, applies routing, validates contracts, persists artifacts, and pauses execution at human checkpoints.

### `multiagent/model_router.py`

Selects the preferred model and optional fallback according to workflow bindings and execution mode.

### `multiagent/adapters/`

Isolates provider-specific behavior. Workflows depend on capabilities and structured contracts rather than provider SDK details.

### `workflows/`

Contains declarative definitions for agent order, prompts, contracts, preferred models, fallbacks, conditions, and checkpoints.

### `prompts/` and `skills/`

Contain versioned prompts and reusable instruction fragments assembled by the runtime.

### `multiagent/contracts.py`

Defines the Pydantic input and output contracts exchanged between workflow steps.

### `multiagent/db/`

Provides SQLite persistence, migrations, and access to run state, artifacts, projects, brand profiles, assets, publications, and telemetry.

## Local persistence

Runtime data is not part of the versioned source tree. Editable checkouts use
`.local/data/` by default; installed distributions use a directory beneath
`~/.multi-agent-framework/`. Public definitions resolve from the checkout in
editable mode and from bundled package resources in an installed wheel.

## Frontend

The current `F00` release does not include a UI. A future frontend should consume the public API rather than access SQLite or internal engine implementations directly.
