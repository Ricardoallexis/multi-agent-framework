# Architecture review — public runtime foundation

Review date: **2026-09-24**. Reviewed baseline: [`cdc52b7`](https://github.com/Ricardoallexis/multi-agent-framework/commit/cdc52b77b065fa04a5c0df969a51be15c78190ab), tagged `M1-B01-F00-alpha`.

## Verified scope

- A fresh checkout and `git fetch --prune` confirmed that `main` and the annotated release tag resolve to the same commit. The public history contains one baseline commit; the initial working tree was clean and contained 85 tracked files. No `AGENTS.md` was present.
- The review covers the Python runtime, both YAML workflows, configuration, adapters, contracts, SQLite migrations, CLI/API, artifacts, tests, and existing architecture/roadmap documents.
- The owner's Windows working copy and private RAVC configuration were not available. Nothing here establishes the state of those files or validates a real RAVC production run.
- The supplied research synthesis is design input. This review does not claim to reproduce the earlier investigations of Mangaba AI, Tito Metralleta, Taller Multi-Agentic, Cidadão.AI, MetaGPT, AutoGen, CrewAI, LangGraph, AgentScope, or OpenAI's multi-agent/research work. No external implementation was copied.

**Conclusion:** the baseline is a useful, persistent hybrid social-content runtime. It is not yet a domain-independent runtime for configurable teams of intelligent capabilities. Preserve its contracts, human bridge, adapters, and regression suite while removing domain assumptions incrementally.

## Evidence and gap classification

| Class | Meaning |
| --- | --- |
| A | Exists and is useful within the current supported scope. |
| B | Exists, but needs generalization. |
| C | Missing or incomplete foundation needed soon. |
| D | Preserve architectural room; implement only when a concrete consumer needs it. |
| E | Deferred or experimental. |

The classifications describe the inspected baseline, not completion of the target design.

## Answers to the 20 inspection questions

| # | Question | Finding from the code | Class |
| --- | --- | --- | --- |
| 1 | Is Agent coupled to an LLM provider? | There is no `Agent` runtime class or `LLMProvider` class. `Catalog.agents` is a raw dictionary; `WorkflowStep.agent` is metadata. The automatic execution path is coupled to `ModelRouter`, `ModelSpec`, `LLMAdapter.generate_structured`, and LLM call budgets. Generalize that boundary, not a nonexistent Agent class. [Catalog](../multiagent/catalog.py), [adapter contract](../multiagent/adapters/base.py), [engine](../multiagent/workflow_engine.py). | B |
| 2 | Are capabilities real today? | They are declared separately from roles in `catalog/agents.yaml` and loaded, but are not typed, validated, matched to tasks, or consulted by the engine/router. Model checks only cover web access and structured output. [Agent catalog](../catalog/agents.yaml), `ModelRouter._validate_compat`. | B |
| 3 | What separates Agent from execution? | Agent definitions are configuration records. An execution is currently a `(run_id, step_id, attempt)` row; `LocalWorker` is a queue-processing thread with a process-scoped identity. It is not a temporary instance of an agent definition. [Worker](../multiagent/worker.py), [run_steps schema](../migrations/001_initial.sql). | B |
| 4 | Does Step know too much about its executor? | `WorkflowStep` requires prompt, skills, contract, preferred/fallback model, web requirement, and an agent label. It cannot describe a plain deterministic tool without LLM-oriented fields. Domain behavior also depends on step IDs inside the engine. [Workflow definitions](../multiagent/workflows.py), `WorkflowEngine._routing_for_step`. | B |
| 5 | Can HumanStepRequest evolve toward external input? | Yes, conceptually. The name refers to persisted rows/dictionaries and helper methods, not a typed `HumanStepRequest` class. Requests already store a UUID, run/step/attempt, prompt/hash, expected contract, context, and status; submissions store raw/validated output and self-reported provider/model. Keep this interface until a compatible external-request design exists. [Migration 002](../migrations/002_orchestration_core.sql), `Store.create_human_step_request`, `WorkflowEngine.submit_human_step`. | B |
| 6 | Is Run durable enough for resume/checkpoints? | Run status, current index, waits, attempts, budgets, outputs, and history are persisted. Human submission can requeue the next step, and startup recovery requeues interrupted runs. There is no immutable resolved-workflow snapshot, general checkpoint API, transactional step commit, replay, or fork. The engine reloads YAML on resume. [Store](../multiagent/db/store.py), [engine](../multiagent/workflow_engine.py). | B |
| 7 | Is Workflow sequential? | Yes. It is a list plus an integer cursor. The only supported predicates are `requires_web` and `not_requires_web`; these are skip conditions, not a generic conditional graph. There is no parallel scheduler or dependency graph. [Workflow loader](../multiagent/workflows.py), `WorkflowEngine.process_run`. | B |
| 8 | Are events sufficient for future observability? | There is a useful structured JSON event log and telemetry. Events have a database-local integer ID, run, timestamp, type, and arbitrary payload. No stable envelope, interaction correlation, source/target identities, hook API, or provider-call lifecycle exists. [Database.log_event](../multiagent/db/database.py), [telemetry](../multiagent/db/store.py). | C |
| 9 | Can we know who executed what? | Partly: run/step/attempt, prompt/version, model, API telemetry, and human-reported provider/model are recorded. Agent identity and an agent-instance identity are not persisted on step attempts. Telemetry has no attempt column; the run's worker ID identifies the queue worker. Joining current YAML is not immutable historical provenance. | B |
| 10 | Can we identify source → target interactions? | Not reliably. Some payloads identify a step/model, but there is no source/target/correlation contract and no first-class delegation or handoff record. A communication graph cannot be reconstructed faithfully from the current data. `Database.log_event`, `WorkflowEngine._persist_completed_step`. | C |
| 11 | Can IDs support WorkerInstances? | UUID run/artifact/request IDs provide useful building blocks, but the step-attempt primary key and run-level worker field cannot represent several simultaneous workers for one logical task. Add separate worker/task identities later; do not reinterpret existing IDs. [Migrations](../migrations/002_orchestration_core.sql). | D |
| 12 | Is there a flat agent namespace? | Yes: one `agents.yaml` dictionary per Settings object, flat string agent references, and two selected workflow IDs. There is no workspace/team scope or agent-reference resolver. `Catalog.__init__`, `Store.create_run`. | B |
| 13 | Can Teams be added without rewriting everything? | Persistence, contracts, and explicit workflow references are reusable. A future scope resolver and optional team entrypoint can sit above them, but current step identity, routing, and context assumptions need changes. This is not evidence that Teams will be a drop-in addition. | D |
| 14 | Does Mock use the same Core? | Yes. `build_system(dry_run=True)` builds the same engine, store, contracts, artifact writer, and services. `ModelRouter` selects `FakeAdapter`; it has fixtures for existing domain contracts only. The full pipeline without web research yields strategy/create/design and a review checkpoint. There is no generic mock contract registry or Colab notebook yet. [Bootstrap](../multiagent/bootstrap.py), [FakeAdapter](../multiagent/adapters/fake.py), [CLI dry-run](../multiagent/cli.py). | A/B |
| 15 | Is RAVC logic still in Core? | No literal RAVC identifiers were found in runtime code. Social-content logic remains: `SocialPostRequest`, quick/full workflow selection, brand sections, named research/strategy/create/design context, hashtag rules, and provider choices by step ID. Removing the brand name did not remove domain coupling. [Contracts](../multiagent/contracts.py), `Store.create_run`, `WorkflowEngine._context_for_step`, `_prepare_and_semantic_validate`. | B |
| 16 | Is configuration composable? | Settings supports constructor/environment values, runtime/storage paths, provider values, and per-request step modes; bootstrap accepts adapter overrides. Catalog/workflow/prompt/skill directories are read-only properties bound to the checkout. There is no preset loader, layered merge, or resolved-config snapshot. [Settings](../multiagent/config.py), [Catalog](../multiagent/catalog.py), [PromptManager](../multiagent/prompts.py). | B |
| 17 | Must a private preset duplicate a Team? | There is no Team/preset abstraction yet. A limited private pilot can reuse the existing workflows with private request instructions, brand profiles, modes, and an external data directory. Replacing an entire prompt catalog would currently need code customization or copied files. Neither is a general composition solution. | C |
| 18 | Can roles/capabilities/workflows be separated from private prompts? | Public definitions and prompt files are physically separate already. Runtime `instructions` can hold private additions without copying the public prompt, subject to the current 4,000-character limit. Per-agent private fragments and independent policy/knowledge layers are not implemented. `SocialPostRequest.instructions`, `PromptManager.render`, [public prompts](../prompts/creator/social_post.v2.md). | B |
| 19 | Can private configuration live outside Git? | Runtime data can via `LOCAL_DIR`. Python can explicitly use `Settings(_env_file=external_path, local_dir=external_root)`. Changing `LOCAL_DIR` alone does not change the default `.env` search path and does not relocate public definitions. External public/private preset roots are not supported. [Settings](../multiagent/config.py). | A/B |
| 20 | What minimally enables RAVC before framework completion? | No Core change is required for a limited pilot of the existing social-content workflow: external workspace, private request instructions/brand profile, direct Python factory, and Human Bridge or configured adapters. A configurable RAVC team needs later generic requests/contracts/context and a small explicit composition interface. See [private reference implementation](PRIVATE_PRESETS.md). | A/C |

## Concrete foundation gaps

1. **Python distribution:** editable installation works when requirements are installed separately. A wheel built from this baseline contains no catalog/workflow/prompt/skill/migration resources and its metadata contains no `Requires-Dist` entries. `Settings.PROJECT_ROOT` also assumes checkout-relative resources. Do not advertise standalone `pip install` or a wheel-based Colab quickstart until both packaging and resource discovery are tested outside the checkout.
2. **Definition validation:** agent references, capability declarations, contract references, duplicate step IDs, conditions, and checkpoint placement are not validated as a complete workflow before a run starts. Workflow loading occurs before the engine's execution `try` block, so a loader failure can leave a started run requiring recovery. These are concrete early validation tasks.
3. **Checkpoint semantics:** `RunService.approve` completes the whole run. This supports the bundled terminal review checkpoints, but not a review in the middle of a generic workflow. Define and test intermediate review/resume behavior before exposing it as supported.
4. **Durability limits:** step rows, files, artifacts, telemetry, and cursor updates are separate operations. A crash between them can leave partial progress; startup recovery is not an exactly-once guarantee. Snapshot definitions and establish transition consistency before replay or concurrency.
5. **Concurrency limits:** selecting a queued run does not atomically claim it, and recovery scans all running jobs. The implementation supports a single local queue consumer, not multiple competing workers or distributed leases.
6. **Isolation limits:** the active brand profile is global to a database, and idempotency keys are globally unique there. Separate private workspaces/databases are the current practical isolation boundary; multi-project storage is not multi-tenancy.

## Consolidated gap map

| Category | Keep or change |
| --- | --- |
| A — preserve | Same-Core Mock, strict output contracts, human raw submissions, request fingerprints, sequential persistence, artifact files, local/cloud adapter boundaries, external runtime storage. |
| B — generalize | Domain-bound requests/contracts/context, raw agent metadata, model-centric execution, human-specific waits, flat namespaces, artifact provenance, Settings/config loading. |
| C — soon | Python distribution, definition validation, intermediate checkpoint correctness, generic run/contract boundary, minimum event identity/correlation, documented RAVC pilot and regression gates. |
| D — prepare | AgentDefinition/WorkerInstance split, execution/context policies, optional Teams/entrypoints, generic external input, layered presets, artifact parents, tool permissions, later workflow graph. |
| E — defer | Dynamic model/worker routing, large fan-out, full graph UI, distributed execution, complex RAG, MCP/A2A integrations, multi-tenancy, sophisticated permission engine. |

## Verification evidence

- Local Python **3.12.14**, Linux: **55 tests passed**, with one dependency deprecation warning from Starlette/AnyIO. This environment required `socksio` because its HTTP proxy configuration uses SOCKS; it was installed only in the local validation environment, not added to project requirements.
- The baseline's [GitHub Actions run](https://github.com/Ricardoallexis/multi-agent-framework/actions/runs/35967067867) completed successfully. The CI configuration covers Python 3.12 and 3.13 on Ubuntu; this session did not run native Windows or Colab.
- A direct Python invocation from outside the checkout used the editable install, completed three simulated steps, reached `waiting_human/review`, then reached `completed` through the existing approval service.
- The added executable example was checked from another working directory. With HTTP sends blocked, it still produced three retained artifacts, left a pre-existing unrelated run queued, and did not approve its own outputs. The example is also added to the existing CI matrix as a separate smoke command.
- Wheel archive/metadata inspection confirmed the distribution gaps above; that check is distinct from a successful standalone-wheel runtime test.
- No live provider inference or private RAVC workload was executed.

The selected incremental implementation is the [Python quickstart](PYTHON_QUICKSTART.md). The ordered next work and future compatibility boundaries are recorded in [ROADMAP](../ROADMAP.md) and the [decision record](ARCHITECTURE_DECISIONS.md).
