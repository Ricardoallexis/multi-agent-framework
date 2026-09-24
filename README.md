# Multi-Agent Framework

**Current release:** `M1-B01-F00-alpha`

**Python package:** `0.1.0a1`

**Status:** Alpha / backend-first

Multi-Agent Framework is a Python orchestration framework for hybrid human/AI workflows, evolving into a configurable runtime for teams of intelligent capabilities. The current baseline executes sequential social-content workflows with local/cloud models, structured contracts, human checkpoints, persistence, and traceability. RAVC is a private reference use case; a generic preset/Team system and graphical interface are not yet implemented.

> **Alpha notice:** public APIs, workflow contracts, prompts, migrations, and configuration may still change while the project evolves toward a stable release.

## What is included

- Deterministic YAML workflow orchestration.
- Example agent definitions for research, strategy, content creation, branding, and visual pre-production; capabilities are currently descriptive metadata.
- Ollama, Gemini, and OpenAI adapters.
- `auto`, `local`, `cloud`, and `human_guided` execution modes.
- Human-in-the-loop review, approval, revision, rejection, and externally executed steps.
- Pydantic contracts and structured output validation.
- SQLite persistence for runs, artifacts, assets, publications, and brand profiles.
- CLI and FastAPI API.
- A private `.local/` workspace for secrets, databases, runs, and assets.
- 55 automated tests in the current baseline.

## Architecture at a glance

```text
CLI / API
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
.local/  (private runtime data; never versioned)
```

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for more detail.

The [architecture review](docs/ARCHITECTURE_REVIEW.md) documents current coupling and limitations; the [roadmap](ROADMAP.md) separates NOW, NEXT, LATER, and FUTURE work.

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

## Direct Python quickstart

After the editable installation above, run the same Core without starting a server:

```powershell
& .\.venv\Scripts\python.exe .\examples\python_quickstart.py
```

On Linux/macOS: `.venv/bin/python examples/python_quickstart.py`.

This uses FakeAdapter and the existing three-step social-content workflow, produces three artifacts, and stops at human review. The default workspace is temporary. See [Python quickstart](docs/PYTHON_QUICKSTART.md) for retained artifacts, direct imports, and verification outside the checkout. The current standalone wheel is incomplete; use the documented requirements plus editable installation until packaging is fixed.

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

Local runtime data belongs in `.local/`, which is excluded by `.gitignore`:

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

The current release is:

```text
M1-B01-F00-alpha
```

`B` changes when the backend/runtime/API changes. `F` changes when the UI changes. The Python package keeps a separate PEP 440 version for packaging compatibility.

See [`docs/VERSIONING.md`](docs/VERSIONING.md).

## Documentation

- [`ROADMAP.md`](ROADMAP.md) — planned project direction.
- [`CHANGELOG.md`](CHANGELOG.md) — public release history.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — components and execution flow.
- [`docs/ARCHITECTURE_REVIEW.md`](docs/ARCHITECTURE_REVIEW.md) — verified baseline and answers to the architecture inspection questions.
- [`docs/ARCHITECTURE_DECISIONS.md`](docs/ARCHITECTURE_DECISIONS.md) — proposed boundaries and incremental implementation tradeoffs.
- [`docs/PYTHON_QUICKSTART.md`](docs/PYTHON_QUICKSTART.md) — same-Core Mock execution from Python.
- [`docs/PRIVATE_PRESETS.md`](docs/PRIVATE_PRESETS.md) — public-base/private-overlay strategy and current private pilot recipe.
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

Licensed under the Apache License, Version 2.0. See [`LICENSE`](LICENSE).
