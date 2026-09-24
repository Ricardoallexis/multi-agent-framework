# Architecture

## Purpose

Multi-Agent Framework coordinates workflows composed of specialized agents and combines local execution, cloud providers, and human participation without coupling workflow logic to a specific model provider.

## Main components

```text
main.py
  -> FastAPI app
      -> RunService
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

### `multiagent/api.py`

Exposes the HTTP API for creating and querying runs, approving or rejecting artifacts, requesting revisions, cancelling runs, and completing externally executed human-guided steps.

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

Runtime data is not part of the source tree. By default it is stored under `.local/data/`. This separation keeps public checkouts clean and allows private development data to coexist with the repository without being versioned.

## Frontend

The current `F00` release does not include a UI. A future frontend should consume the public API rather than access SQLite or internal engine implementations directly.
