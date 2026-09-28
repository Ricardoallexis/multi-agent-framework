# Changelog

Notable changes to the public project are documented here. The project uses `M-B-F` product versions and a separate PEP 440 version for the Python package. Because the roadmap is a living document, each release also lists the roadmap changes it publishes under **Roadmap**.

## [Unreleased]

### Added

- Add application services (`multiagent.services`): one Python facade, `ApplicationServices`. It covers definitions (workflows, agents, and bundles) and runs, and the HTTP API and CLI use it. Failures raise `ServiceError` with a stable `code`, a `message`, an HTTP-like `status`, and JSON-ready `details`.
- Run definition bundles through the facade, each with its own store and worker. Bundles are registered by name in Python and are never exposed by path.
- Add HTTP routes for workflows, agents, bundles, bundle validation, and run artifacts. `POST /api/v1/runs` accepts a generic `RunRequest` (optionally with `?bundle=`) as well as the social-post request.
- Add CLI commands `workflows`, `agents`, `bundle-validate`, and `artifacts`, and generic runs with `run --workflow ... --input name=value [--bundle ...]`.
- Add Mock mode: with `MOCK_MODE=true`, the server and CLI run with synthetic fixtures and no model API keys. `/api/v1/health` reports `dry_run`.
- Add `docs/API.md` (facade, endpoints, error format, and Mock mode) and a README quick start with Mock.
- Add the Stage 0 run console: a local browser UI at `/ui/`, started with `multiagent ui` (or `run_ui_windows.bat`) in Mock mode by default, with `--real` for configured providers. It selects a workflow or bundle, creates a run, follows its status, current step, pending action, and events, handles human review (approve, request changes, regenerate, reject, cancel), runs the Human Bridge (copy the prompt, paste an external response, see validation errors), and shows artifacts. It is static HTML and JavaScript modules served by FastAPI, with no build step, and uses only the public HTTP API. See `docs/UI0.md`.
- Add Mock acceptance tests for the Stage 0 console and a manual checklist (`tests/ui0/CHECKLIST.md`).
- Number each run's events with `seq` (1, 2, 3… without gaps) and read only newer events with `GET /api/v1/runs/{run_id}/events?after=N`, `services.runs.events(run_id, after=N)`, or `multiagent events <run_id> --after N`. The Stage 0 run view detects gaps, repeats, and out-of-order copies and reloads the run instead of guessing its state.
- Add parity tests across the Python facade, HTTP, and the CLI.
- Record provider-reported token usage locally per run, agent, step, and attempt, including elapsed duration and failed attempts. Read records and totals through Python, HTTP, and CLI (including JSONL export); missing provider usage stays null, and telemetry write failures do not stop execution. See `docs/TELEMETRY.md`.
- Add `scripts/check_version_consistency.py`. It checks that every current version declaration (README header, newest changelog release, latest milestone, `pyproject.toml`, and the tag on tag builds) matches `multiagent/version.py`, and that the product stage and the Python pre-release agree. CI and `run_tests_windows.bat` run it before the tests.

### Changed

- Protect the local API from other web pages in the same browser. Requests whose `Host` is not in `API_ALLOWED_HOSTS` (default `127.0.0.1`, `localhost`) answer 400 `invalid_host`, which blocks DNS rebinding. State-changing requests with an `Origin` from another site, or a cross-site `Sec-Fetch-Site`, answer 403 `forbidden_origin`. The CLI and other clients that send no `Origin` keep working. This is not authentication: do not expose the server to a network.
- Rewrite `docs/COMPARISON.md` around the target scope, with a status marker per capability (available, partial, planned, optional backend or adapter, under evaluation). It also separates what is not implemented in the current alpha from what stays intentionally outside the core, and compares scope with other frameworks.
- Describe the project as a domain-neutral, configurable runtime for teams of AI agents, humans, models, and tools. `README.md` and `ROADMAP.md` separate what works today, what is planned, and the long-term direction.
- Rename the roadmap section for `M1-B01-F00-alpha` to "First public baseline", so it is not mistaken for the current release.
- Mark the current version lines in `README.md` so they are checked, and document the release procedure in `docs/VERSIONING.md`.
- `multiagent/version.py` omits the stage suffix for stable releases instead of producing a trailing `-`.

- API errors now answer `{"detail": {"code", "message", "details"}}` with the same HTTP statuses as before, and invalid request bodies use the same format. Creating a run for an invalid workflow answers 422 instead of 500. Clients that read `detail` as text must read `detail.message`.
- Clarify the autonomy model in `docs/COMPARISON.md`, `README.md`, `ROADMAP.md`, and `PROGRESS.md`. The runtime currently executes declared workflows. The roadmap target is governed autonomy: planning, delegation, and dynamic routing within declared roles, permissions, contracts, budgets, gates, and policies, with escalation to a human. It is not unrestricted agent conversation.

### Roadmap

- Mark application services and HTTP parity as done.
- Mark the minimal operator UI (stage 0) as done; the execution backend abstraction is next.
- Add the autonomy principle (autonomous orchestration within declared organizational constraints) and state the limits planners work within.
- Add **Team design from a prompt**: generate or formalize a team (roles, rules, organization chart, and recommended but never mandatory model bindings) as a validated, human-approved team definition. Roles are not tied to models, and one model can switch between roles.
- Add **Reference tool stacks for team presets**: research well-known open-source tools as examples for the five most common team types (for example Tailwind CSS for web design). They are examples in presets, never core dependencies.
- Record the external-delivery importer (`tools/seguimiento/`) as a design reference for externally produced output, the Stage 0 review step, and the future shared-folder channel. It is not runtime code.
- Add an optional evaluation of external structured-decision providers (for example Jev, commercial) behind a provider-neutral interface. It requires explicit opt-in, the native implementation remains the default, and it is not an adoption decision.
- Add **Advanced operator interface**: live topology and high-volume telemetry for large or long-running teams (background event ingestion, client state machines that mirror the core, macro and meso views with semantic zoom, virtualization, OpenTelemetry and MCP views, and an accessible design system). It is placed after OpenTelemetry, MCP, and planning, and its technologies are candidates for an architecture decision.
- Add near-term UI foundations: sequence-numbered run events with resynchronization, untrusted rendering of model output with sanitization and a Content Security Policy, and status that never depends on color alone.
- Mark provider-reported token-usage telemetry as delivered; retry counts, routing decisions, and token estimates remain planned.

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
