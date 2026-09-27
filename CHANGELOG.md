# Changelog

Notable changes to the public project are documented here. The project uses `M-B-F` product versions and a separate PEP 440 version for the Python package.

## [Unreleased]

### Added

- Declare core runtime dependencies and optional Gemini/OpenAI extras in package metadata.
- Bundle public catalog, workflow, prompt, skill, and migration resources in the wheel and sdist.
- Add a direct Python Mock example and an isolated-wheel smoke check for CI.
- Validate workflow definitions before a run is created or executed: structure, identifiers, conditions, and references to agents, contracts, models, prompts, and skills, reported together with stable issue codes.
- Resume a run after an intermediate human review: approvals target the exact reviewed attempt, and changes to the definition while a run waits are detected before continuing.
- Load definition bundles (workflows, agents, prompts, contracts, and sample outputs) from outside the package and run them through `RunRequest` from Python.
- Document how the project differs from other multi-agent frameworks (`docs/COMPARISON.md`).

### Changed

- Checkpoints are no longer limited to the final workflow step.
- Resolve read-only definitions from the installed package and writable data from a user-owned directory when installed from a wheel.

## [M1-B01-F00-alpha] - 2026-09-23

### Added

- First public alpha baseline.
- Multi-agent orchestrator with CLI and FastAPI API.
- YAML workflows and specialized researcher, strategist, creator, designer, and branding agents.
- Ollama, Gemini, and OpenAI adapters.
- Human-in-the-loop checkpoints and `human_guided` execution.
- SQLite persistence for runs, artifacts, assets, publications, and brand profiles.
- Private `.local/` workspace for secrets and runtime data.
- Product versioning based on `M<generation>-B<backend>-F<frontend>-<stage>`.
- 55-test automated suite.
- Apache License 2.0 and project `NOTICE`.
- AI-assisted-development disclosure.
- Cross-platform manual setup guidance in addition to Windows helper scripts.
- Public issue templates, pull-request checks, and community code of conduct.
- Least-privilege GitHub Actions permissions for the public CI workflow.
- Dedicated Ollama local-inference setup, diagnostics, and troubleshooting guide.

### Changed

- Public identity standardized as `multiagent` / `multi-agent-framework`.
- Public documentation, prompts, skills, CLI messages, runtime errors, fixtures, examples, and tests normalized to English.
- Public agent identifiers and prompt namespaces standardized as `researcher`, `strategist`, `creator`, `designer`, `branding`, and `analyst`.
- Local configuration and runtime data redirected outside the versioned source tree.

### Security

- `.env`, databases, runs, assets, backups, and the local workspace are excluded from version control by default.
- Public history starts from a clean baseline rather than reusing private development history.
