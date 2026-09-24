# Changelog

Notable changes to the public project are documented here. The project uses `M-B-F` product versions and a separate PEP 440 version for the Python package.

## [Unreleased]

### Added

- Reserved for changes after the first public alpha release.

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
