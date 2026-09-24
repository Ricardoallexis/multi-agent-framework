# Contributing

Multi-Agent Framework is currently in alpha. Contributions are welcome as long as they preserve the separation between public source code and private local runtime data.

## Before you start

- Search existing issues before opening a duplicate bug report or feature request.
- Use an issue to discuss substantial behavioral or architectural changes before investing in a large implementation.
- Keep public code, prompts, fixtures, examples, documentation, issue discussions, and pull requests in English.
- Never include credentials, real customer data, private model outputs, production databases, or other confidential material.

## Development setup

### Windows

```powershell
setup_windows.bat
```

### Linux / macOS

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

Ubuntu is covered by GitHub Actions. macOS currently uses the same standard Python setup but is not part of the automated CI matrix.

## Local data

Keep secrets and real runtime data inside `.local/` or an external `LOCAL_DIR`. Never commit real SQLite databases, production runs, API keys, backups, customer assets, or other confidential material.

## Branches and pull requests

Create a focused branch from the latest `main`, keep commits scoped to the proposed change, and open a pull request rather than pushing unrelated work together.

A pull request should describe:

- the problem or objective;
- affected files and components;
- tests executed;
- backend/frontend impact;
- configuration or migration changes, if any;
- compatibility implications for workflows, prompts, contracts, or persisted data.

Before submitting, run:

```bash
python -m pytest -q
```

The pull request should not knowingly reduce test coverage for changed behavior without explaining why.

## Language

English is the canonical language for public code identifiers, documentation, prompts, examples, issue discussions, and pull requests. User-generated content may target any language supported by the selected model.

## Version changes

- Backend/runtime/API changes increment `B`.
- Frontend/UI changes increment `F`.
- Changes to both increment both values.
- Incompatible architectural changes may require a new `M` generation.

Documentation-only or maintenance changes that do not alter a distributable release do not require a version increment. Record relevant changes under `Unreleased` until the next release.

## Security reports

Do not disclose exploitable security details or secrets in a public issue. Follow [`SECURITY.md`](SECURITY.md) for responsible reporting.

## Licensing of contributions

By submitting a contribution for inclusion in this project, you represent that you have the right to submit it and agree that it may be distributed under the Apache License, Version 2.0, unless you explicitly state otherwise in writing before the contribution is accepted.

AI-assisted contributions are allowed, but the contributor is responsible for reviewing, testing, and validating the submitted material and for ensuring that no confidential or third-party restricted content is included.

## Community expectations

Participation in project spaces is subject to [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md).
