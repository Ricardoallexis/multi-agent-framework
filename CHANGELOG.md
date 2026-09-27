# Changelog

Notable changes to the public project are documented here. The project uses `M-B-F` product versions and a separate PEP 440 version for the Python package. Because the roadmap is a living document, each release also lists the roadmap changes it publishes under **Roadmap**.

## [Unreleased]

## [M1-B02-F00-alpha] - 2026-09-27

Python package: `0.2.0a1`.

### Added

- Declare core runtime dependencies and optional Gemini/OpenAI extras in package metadata.
- Bundle public catalog, workflow, prompt, skill, and migration resources in the wheel and sdist.
- Add a direct Python Mock example and an isolated-wheel smoke check for CI.
- Validate workflow definitions before a run is created or executed: structure, identifiers, conditions, and references to agents, contracts, models, prompts, and skills, reported together with stable issue codes.
- Resume a run after an intermediate human review: approvals target the exact reviewed attempt, and changes to the definition while a run waits are detected before continuing.
- Load definition bundles (workflows, agents, prompts, contracts, and sample outputs) from outside the package and run them through `RunRequest` from Python.
- Document how the project differs from other multi-agent frameworks (`docs/COMPARISON.md`).
- Show progress by area in `ROADMAP.md` and `PROGRESS.md`, generated from the roadmap checkboxes by `scripts/update_progress.py` and checked in CI.
- Add `docs/LICENSING.md` and `TRADEMARKS.md`: Apache-2.0 without additional commercial restrictions, attribution, no telemetry, source headers, and use of the project name.

### Changed

- Checkpoints are no longer limited to the final workflow step.
- Resolve read-only definitions from the installed package and writable data from a user-owned directory when installed from a wheel.
- Bump the product version to `M1-B02-F00-alpha` and the Python package version to `0.2.0a1`.

### Roadmap

- The roadmap is now a living document, and roadmap changes are listed with each release.
- Reuse mature components behind adapters: Microsoft Agent Framework is planned as an optional execution backend (graph workflows, MCP, A2A, AG-UI, OpenTelemetry) while the core stays independent.
- Mark validation before execution and review after any step as done; add the execution backend abstraction, a resource-aware scheduler, MCP and A2A adapters, AG-UI as a UI transport, OpenTelemetry, and concept equivalences for Microsoft Agent Framework users.
- Add **Collaborative agent teams**: a domain-neutral core for two or more agents (scoped workspaces, inboxes with read receipts, reviewed change proposals, gates, supervised and autonomous modes), with software development as the first preset.
- Add **Preset sharing and community catalog**: portable presets with a manifest, installation from a file, Git, or a catalog, validation before use, trust rules for executable content, and private overlays.
- Show progress by area in the roadmap and in `PROGRESS.md`.
- Add a development order: application services with HTTP parity, then a minimal operator UI with the mock backend, the execution backend abstraction, tool and capability contracts, team relationships, planning, and interoperability.
- Add **Planning, delegation, and team orchestration**: plans as data executed by the existing engine, pluggable planners, manual, semi-automatic, and automatic modes, capability-based assignment, organizations as data, traceability, and replanning.
- Extend tools into **Tools, interoperability, and external applications**: generic tool contracts, impact-based permissions on the human-in-the-loop, capability-based selection, an MCP server and client, a shared-folder channel, an assisted desktop bridge, an application gateway and launcher, and external application adapters.
- Plan an early minimal UI as an operator and testing interface built on the public API, growing in stages.
- Improve the human-guided external model flow as a feature for human control, interoperability, and provider independence.
- Add **Open source, licensing, and sustainability**: Apache-2.0 without additional restrictions, no telemetry, progressive SPDX headers, a license and governance review before 1.0, and future community funding.

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
