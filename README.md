# Multi-Agent Framework

**Current release:** `M1-B02-F00-alpha` <!-- version:release -->

**Python package:** `0.2.0a1` <!-- version:package -->

**Status:** Alpha / backend-first

Multi-Agent Framework is a domain-neutral, configurable runtime for building and operating teams of AI agents, humans, models, and tools. Today it runs declared workflows with specialized agents, local and cloud models, structured contracts, human checkpoints, persistence, and traceability. Over time, users will define their own agents, capabilities, relationships, workflows, and execution policies, and the same core will support everything from deterministic and human-guided execution to progressively more dynamic planning and delegation. A graphical user interface is not part of the current public baseline yet.

> **Alpha notice:** public APIs, workflow contracts, prompts, migrations, and configuration may still change while the project evolves toward a stable release.

## What is included

- Deterministic YAML workflow orchestration.
- A bundled example workflow with specialized agents for research, strategy, content creation, branding, and visual pre-production.
- Ollama, Gemini, and OpenAI adapters.
- `auto`, `local`, `cloud`, and `human_guided` execution modes.
- Human-in-the-loop review, approval, revision, rejection, and externally executed steps.
- Pydantic contracts and structured output validation.
- Validation of workflow definitions before any run starts, with stable error codes.
- Human review after any step, resuming from the next step without repeating approved work.
- Definition bundles and generic run requests through Python, HTTP, and the CLI for workflows outside the bundled social-content use case.
- SQLite persistence for runs, artifacts, assets, publications, and brand profiles.
- Python application-services facade, CLI, and FastAPI API.
- Server Mock mode for synthetic runs without model API keys.
- A Stage 0 browser console (`multiagent ui`) to create and follow runs, handle human review and the Human Bridge, and inspect artifacts, in Mock mode by default.
- A private writable workspace for secrets, databases, runs, and assets.
- An automated test suite, run in CI on every push and pull request.

## How it differs

**Today:** workflows are declared and deterministic. Each step's output is validated against a strict contract, a human can review or take over any step, runs are persisted and resumable, and local models come first. This is a strength of the current baseline, and the framework will keep supporting this mode.

**Planned:** configurable teams of agents, planning and delegation, and modes with different degrees of autonomy, from manual and human-guided to automatic planning. Autonomy is always governed: agents act within the roles, capabilities, permissions, contracts, budgets, and policies the user declares, and escalate to a human when needed. The project does not aim for unrestricted conversation between agents.

The core stays domain-neutral. Software development, engineering, research, design, finance, operations, or media production are built on it through configuration, presets, plugins, adapters, and workflows.

See [`docs/COMPARISON.md`](docs/COMPARISON.md) for the target scope with a status for each capability, how it compares with other frameworks, and what stays intentionally outside the core.

## Architecture at a glance

```text
CLI / HTTP API / Python
   |
Application services (multiagent.services)
   |
Run Service
   |
Workflow Engine ---- Human-in-the-Loop
   |
Model Router
   |-----------------------------|
 Ollama          Gemini        OpenAI
   |
Contracts / Store / Artifacts / Assets
   |
Private workspace (runtime data; never versioned)
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for more detail.

## Requirements

- Python `>=3.12,<3.14` (Python 3.12 and 3.13 are tested in CI).
- Git for cloning and contributing.
- Ollama is optional for local inference.
- Gemini and OpenAI are optional cloud providers.
- Windows 10/11 helper scripts are included. The core Python test suite also runs on Ubuntu in GitHub Actions.

## Installation

### Windows quick start

```powershell
setup_windows.bat
```

The setup script creates `.venv`, installs dependencies and the editable package, creates `.local/.env` from `.env.example`, initializes the database, and runs diagnostics.

Start the backend:

```powershell
run_backend.bat
```

In another terminal:

```powershell
.venv\Scripts\multiagent.exe --version
.venv\Scripts\multiagent.exe doctor
```

### Linux / macOS manual setup

The core project uses standard Python tooling. Ubuntu is covered by CI; macOS is not currently part of the automated test matrix.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pip install -e . --no-deps --no-build-isolation
mkdir -p .local
cp .env.example .local/.env
multiagent init
multiagent doctor
```

Start the backend with:

```bash
python main.py
```

## API and CLI quick start with Mock

After installation, start the server in Mock mode to try the workflow without
model API keys or a running Ollama model. Mock outputs are synthetic fixtures,
not real model results. Runs and artifacts are saved in the configured workspace.

Windows PowerShell, from the repository root:

```powershell
$env:MOCK_MODE = "true"
.\.venv\Scripts\python.exe main.py
```

Linux / macOS:

```bash
MOCK_MODE=true .venv/bin/python main.py
```

Leave that terminal running. In a second PowerShell terminal, inspect the
definitions and create a social-post run:

```powershell
.\.venv\Scripts\multiagent.exe workflows
.\.venv\Scripts\multiagent.exe workflows social_post
.\.venv\Scripts\multiagent.exe agents
.\.venv\Scripts\multiagent.exe run --project "API demo" --objective "Explain workflow automation" --topic "Human review" --platform LinkedIn --no-brand
```

On Linux/macOS, replace `.\.venv\Scripts\multiagent.exe` with
`.venv/bin/multiagent`. The CLI connects to `http://127.0.0.1:8000` by default;
set `API_BASE_URL` in the client environment if the server uses another address.

Copy the returned run `id` into these commands in place of `RUN_ID`:

```powershell
.\.venv\Scripts\multiagent.exe status RUN_ID
.\.venv\Scripts\multiagent.exe artifacts RUN_ID
```

Creation queues the run; the server worker processes it asynchronously. Check
status again until it reaches `waiting_human`, then approve it:

```powershell
.\.venv\Scripts\multiagent.exe approve RUN_ID
.\.venv\Scripts\multiagent.exe status RUN_ID
```

The built-in quick social-post workflow completes after that approval. Other
workflows can have intermediate reviews that resume processing. HTTP clients
use the same operations under `/api/v1`; the interactive API reference is at
`http://127.0.0.1:8000/docs`.

### Generic workflows and registered bundles

Use `run --workflow WORKFLOW_ID` with repeated `--input name=value` options for
generic workflows. Add `--bundle BUNDLE_NAME` for a bundle registered by the
server. Bundle names select trusted server configuration, not client filesystem
paths. The default server does not register custom bundles automatically.

After your server operator registers a bundle, replace the names and inputs
below with those required by its workflow:

```powershell
.\.venv\Scripts\multiagent.exe workflows --bundle BUNDLE_NAME
.\.venv\Scripts\multiagent.exe bundle-validate BUNDLE_NAME
.\.venv\Scripts\multiagent.exe run --project "Bundle demo" --workflow WORKFLOW_ID --bundle BUNDLE_NAME --input "INPUT_NAME=example value"
```

Generic runs use `--input` for domain data instead of social-post options such
as `--objective` and `--topic`. HTTP errors appear in the CLI with their code,
message, and available details. See [Python and HTTP API](docs/API.md) for the
facade, bundle registration contract, endpoints, and error format.

To return to provider inference, stop the server, set `MOCK_MODE=false`, and
restart it with your provider configuration. This server mode is separate from
the standalone `dry-run` command below, which does not need the HTTP server.

## Stage 0 local run console

This checkout includes a browser UI for creating and following Runs, reviewing
outputs, using Human Bridge, and inspecting artifacts. After installation, run
`multiagent ui` in the activated environment, or `run_ui_windows.bat` on Windows.
The launcher enables Mock by default and opens `http://127.0.0.1:8000/ui/` with
the default settings. Keep its terminal open; use Ctrl+C to stop the server.

See [Stage 0 operation](docs/UI0.md) for bundles, provider mode, human actions,
recovery, and current limits. This describes the checkout implementation;
publication and acceptance validation are separate steps.

## Python quickstart and standalone installation

Run the current three-step Mock workflow through the same Core without a server
or API key. It produces three artifacts and waits for human review:

```powershell
& .\.venv\Scripts\python.exe .\examples\python_quickstart.py
```

On Linux/macOS: `.venv/bin/python examples/python_quickstart.py`. Use
`--local-dir PATH` to retain the run in a private location; otherwise its
workspace is temporary. The wheel includes the public definitions and declares
its core dependencies. Gemini and OpenAI SDKs are optional extras. See the
[Python quickstart](docs/PYTHON_QUICKSTART.md) for a clean wheel installation.

## Local inference with Ollama

Ollama can be used as the local inference provider without configuring a cloud API key. The default `.env.example` expects Ollama at `http://127.0.0.1:11434` with `qwen3:8b`, and both values are configurable.

See [`docs/OLLAMA.md`](docs/OLLAMA.md) for installation, model configuration, diagnostics, local execution, and troubleshooting.

## First dry run

Windows PowerShell:

```powershell
.venv\Scripts\multiagent.exe dry-run `
  --project "Example Project" `
  --objective "Create an educational post" `
  --topic "multi-agent architecture" `
  --platform LinkedIn
```

Linux / macOS:

```bash
multiagent dry-run \
  --project "Example Project" \
  --objective "Create an educational post" \
  --topic "multi-agent architecture" \
  --platform LinkedIn
```

A `dry-run` does not need to consume real provider APIs. It is intended to validate contracts, routing, artifacts, and human checkpoints.

## Private data and secrets

Editable checkouts store runtime data in `.local/`, which is excluded by
`.gitignore`:

```text
.local/
├── .env
└── data/
    ├── multiagent.db
    ├── runs/
    └── assets/
```

Never store API keys, production databases, private model outputs, customer logos, backups, or other confidential material in versioned paths. To keep runtime data outside the repository entirely, set `LOCAL_DIR` in `.local/.env`.

See [`docs/LOCAL_WORKSPACE.md`](docs/LOCAL_WORKSPACE.md).

The framework sends no telemetry and contacts only the model providers and services that you configure.

## Tests

Windows:

```powershell
run_tests_windows.bat
```

Cross-platform:

```bash
python -m pytest -q
```

## Contributing

Contributions, bug reports, and feature proposals are welcome. Please read [`CONTRIBUTING.md`](CONTRIBUTING.md) before opening a pull request and follow [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) when participating in the project community.

Do not include credentials, private runtime data, customer material, or other confidential content in issues or pull requests. Security-sensitive reports should follow [`SECURITY.md`](SECURITY.md).

## Versioning

Public releases use a component-aware version:

```text
M<generation>-B<backend>-F<frontend>-<stage>
```

The current release and Python package version are shown at the top of this file. Both come from `multiagent/version.py`, the single source of truth, and CI checks that every current declaration matches it (`scripts/check_version_consistency.py`).

`B` changes when the backend/runtime/API changes. `F` changes when the UI changes. The Python package keeps a separate PEP 440 version for packaging compatibility.

See [`docs/VERSIONING.md`](docs/VERSIONING.md), including the release procedure.

## Documentation

- [`ROADMAP.md`](ROADMAP.md) — planned project direction.
- [`PROGRESS.md`](PROGRESS.md) — progress by area and what each finished feature enables.
- [`CHANGELOG.md`](CHANGELOG.md) — public release history.
- [`docs/API.md`](docs/API.md) — Python facade, HTTP endpoints, structured errors, and Mock mode.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — components and execution flow.
- [`docs/COMPARISON.md`](docs/COMPARISON.md) — how this project differs from other multi-agent frameworks.
- [`docs/LICENSING.md`](docs/LICENSING.md) — license, attribution, and no-telemetry policy.
- [`TRADEMARKS.md`](TRADEMARKS.md) — use of the project name.
- [`docs/VERSIONING.md`](docs/VERSIONING.md) — versioning rules.
- [`docs/LOCAL_WORKSPACE.md`](docs/LOCAL_WORKSPACE.md) — public-code/private-data separation.
- [`docs/OLLAMA.md`](docs/OLLAMA.md) — local Ollama setup, diagnostics, and execution.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — contribution guidelines.
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — community participation expectations.
- [`SECURITY.md`](SECURITY.md) — responsible vulnerability reporting.

## AI-assisted development

This project has been developed with the assistance of generative AI tools for activities such as code drafting, refactoring, documentation, test preparation, and technical analysis. Human contributors remain responsible for architecture decisions, integration, review, validation, testing, and publication.

See [`NOTICE`](NOTICE) for the project notice.

## License

Copyright 2026 Ricardoallexis and contributors. Licensed under the Apache License, Version 2.0, with no additional restrictions on commercial use. See [`LICENSE`](LICENSE), [`NOTICE`](NOTICE), [`docs/LICENSING.md`](docs/LICENSING.md), and [`TRADEMARKS.md`](TRADEMARKS.md).
