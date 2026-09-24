# Roadmap

Multi-Agent Framework is evolving into a **configurable runtime for teams of intelligent capabilities**. RAVC is a private reference implementation built on the framework. Domain-specific roles, marketing workflows, and provider choices belong in configuration or extensions.

This roadmap has no promised delivery dates. **NOW is an ordered queue, not a request to implement every item together.** Each slice needs a concrete acceptance condition and compatibility evidence.

Reviewed baseline: `M1-B01-F00-alpha` / `cdc52b7`, verified on 2026-09-24. See the [code audit](docs/ARCHITECTURE_REVIEW.md), [architectural decisions](docs/ARCHITECTURE_DECISIONS.md), and [private implementation strategy](docs/PRIVATE_PRESETS.md).

## Current baseline — verified, with limits

| Available | Current boundary |
| --- | --- |
| Declarative workflows, contracts, artifacts, SQLite state | Sequential social-content workflows; hardcoded context/validation and quick/full selection remain. |
| Agent roles and capabilities in YAML | Raw metadata; no typed AgentDefinition, capability dispatch, or agent lifecycle. |
| Ollama, Gemini, OpenAI, primary/fallback bindings | Model-centric automatic execution with static routing; no dynamic model routing. |
| Human Bridge, review/revision/rejection, external submissions | Human-specific waiting/request interfaces; terminal review checkpoints, not generic interrupt/resume. |
| Cancellation, budgets, recovery, events, telemetry | Single local queue consumer; no atomic claim, definition snapshot, or complete interaction lineage. |
| FakeAdapter and CLI dry-run | Same Core and existing domain contracts; no generic mock registry or Colab notebook. |
| Python factory, CLI, FastAPI | Editable checkout works; standalone wheel lacks runtime resources and dependency metadata. |
| Private external runtime storage | No public/private preset composition loader; default environment-file lookup stays checkout-relative. |
| 55 tests and Ubuntu Python 3.12/3.13 CI | Public synthetic cases; actual RAVC, native Windows, Colab, and live inference are separate checks. |

## NOW — establish and verify the foundation

### 1. Python usability and reproducible distribution

- [x] Select and document a same-Core Python quickstart using the existing three-step Mock flow, without a server or API key.
- [ ] Package runtime dependencies and catalog/workflow/prompt/skill/migration resources correctly.
- [ ] Make default resource discovery work from an installed wheel, independently of the current directory; keep writable runtime data separate.
- [ ] Verify a clean wheel/sdist install outside the checkout and preserve the editable Windows/Linux path.

The [quickstart](docs/PYTHON_QUICKSTART.md) added with this review deliberately uses the existing social-content example. A domain-neutral demo remains work to do.

### 2. Validate definitions and preserve sequential correctness

- [ ] Validate agent/contract references, unique step IDs, conditions, prompt references, and supported checkpoint placement before execution.
- [ ] Handle workflow-load failures without leaving a run falsely running.
- [ ] Specify intermediate review approval as continuation where appropriate, preserving existing final review behavior.
- [ ] Record the resolved workflow/configuration identity before generalizing resume.
- [ ] Define consistent step/output/cursor persistence and document recovery guarantees. Keep one queue consumer until claiming and lease semantics are implemented.

### 3. Separate domain rules from generic execution

- [ ] Introduce a minimal generic run input and explicit contract registration while retaining `SocialPostRequest` compatibility.
- [ ] Move social-content context bindings, validation, workflow selection, and brand/provider assumptions behind a preset or extension boundary.
- [ ] Formalize AgentDefinition fields and capabilities independently of role, prompt, model, and tools; validate references before adding capability-based selection.
- [ ] Keep Python imports and the existing public dry-run green at every extraction.

### 4. Establish observability facts

- [ ] Define a versioned, backward-compatible event envelope with run/event/step/attempt identity, source/target references where meaningful, correlation, timestamps, and status.
- [ ] Record enough executor/agent provenance to answer who executed what without consulting mutable YAML.
- [ ] Introduce interaction started/completed/failed records with shared correlation and defined cancellation/interruption handling.
- [ ] Specify event/hook ordering and failure semantics before exposing middleware. No visualization implementation is needed.

### 5. Start private dogfooding

- [ ] Run a small RAVC workload with external runtime storage and current private request instructions/brand data.
- [ ] Record where existing inputs are insufficient before selecting the first external prompt/config composition change.
- [ ] Maintain two explicit regression gates: public synthetic demo and confidential private reference workload. The private gate is pending owner-side evidence.

No RAVC-specific roles, secrets, business rules, or real outputs should enter Core or public fixtures.

## NEXT — generalize one boundary at a time

| Slice | Scope and exit condition |
| --- | --- |
| Execution strategy boundary | Wrap existing LLM adapters and add one deterministic/mock executor. All paths use the same input/output contracts, validation, state, and provenance. Keep LLM metrics specific to LLM calls. |
| Generic external input | Define request/run/step/correlation/expected-contract semantics; adapt Human Bridge without breaking existing human APIs, submissions, or stored status values. |
| Context propagation | Start with explicit `none` and `selected` inputs/artifact references; prepare `summary`, `artifacts`, and `full`. Do not automatically give every worker the full history. |
| Artifact lineage | Preserve current IDs/files, add producer identity, parent/source references and metadata sufficient to trace a final output through intermediate results. No knowledge graph. |
| Minimal preset composition | Use `defaults < public < private < runtime`, stable IDs and explicit merge rules, validation, and resolved-config identity. Begin with a real private customization; avoid duplicated team trees. |
| Public Mock + Colab | Add a small domain-neutral workflow with about three configured agents and a Run All notebook using the same packaged Core. Zero-key execution first; optional providers via Colab secrets later. |
| Early execution policies | Extract explicit timeout/retry/fallback/privacy/budget choices from step-name branches. Keep defaults simple; dynamic optimization is deferred. |
| Workflow evolution | Preserve sequential behavior, then add controlled parallel work and generic conditions once state/claiming/budget/cancellation tests exist. |

A provider, agent, or tool is not required to use an API or an LLM. Supervisor/leader roles stay optional. Execution strategy and model selection can vary across tasks without changing an agent's identity.

## LATER — preserve compatibility, implement when needed

These are design boundaries to keep open, not current APIs.

| Direction | Prepared concept | Trigger for implementation |
| --- | --- | --- |
| AgentDefinition / WorkerInstance | Reusable definition versus temporary task execution; one definition can have several workers. Separate IDs, scoped context, timestamps, results. | A concrete bounded parallel workflow needs multiple executions of one definition. |
| Worker lifecycle and policies | Created, ready, running, waiting, completed, failed, cancelled; waiting reason and bounded concurrency separate from role. | Scheduler and durable transitions can support those states reliably. |
| Multi-Team organization | Workspace/project with multiple optional teams, scoped references and controlled interfaces. | More than one reusable team must be composed in a real use case. |
| Team entrypoint / Team-to-Team | Expose an agent or workflow; no mandatory leader and no unrestricted all-to-all communication. Derived team status. | Contracted cross-team work exists. |
| Workflow patterns | Fan-out/fan-in, subflows, event-driven work, optional map/reduce. Executor/verifier, hierarchical, and consensus are presets. | Simple sequential/parallel/conditional mechanisms no longer suffice. |
| Communication Graph | Counts, durations, requests/responses, shared artifacts, costs/tokens, loops, bottlenecks from Core events. | Event identities/lifecycles are stable and complete enough. |
| Live Execution Topology | Active/waiting workers, teams, communications, tools and external waits; grouped views as needed. | A UI consumer exists. Rendering choices remain outside Core. |
| Historical Trace | Preserve completed workers, transitions, interactions and artifacts even after live-view removal. | Always preserve recorded history; add richer projections with richer runtime entities. |
| Tools and permissions | Generic deterministic tool interface; read/write/external/destructive/requires_approval metadata. | The first real tool adapter needs policy and audit boundaries. |
| Local models and knowledge | A small complementary set for text/reasoning/research, then images, embeddings/retrieval and multimodal workloads as justified. | Hardware, privacy, capability, and quality requirements are measured. |
| Evaluation and diagnostics | Contract-based datasets, prompt/model regressions, quality/latency/cost comparisons, hardware/context checks. | Specific runtime/model changes need measured comparisons. |
| Shareable presets | Generic public base plus private implementation, optional public-validation command. | An actual second user's preset confirms the composition rules. |

Domain work such as editorial planning, SEO, campaigns, brand assets, image/video pre-production, publication metrics, and iterative content review remains valuable **as presets/extensions**, not as mandatory Core behavior.

## FUTURE / EXPERIMENTAL — not implementation work now

- Dynamic model/executor routing by capability, quality, latency, cost, privacy, availability, and local preference.
- Dynamic exploration: divide, explore independently, produce artifacts, synthesize, verify, and promote promising branches. The lesson is controlled exploration, not a target worker count.
- Dynamic resource allocation, large fan-out, and extensive consensus/research loops.
- Replay, fork, retry from a checkpoint, and executor changes during suspended work, after definition snapshots and transition guarantees.
- MCP for external tools/services and A2A for remote agents/systems, as optional adapters.
- More advanced local image/video/research/multimodal execution, training, and substantial retrieval infrastructure.
- Full visual workflow editors, administrative dashboards, complex organizations, multi-tenancy, and sophisticated permission engines.
- Distributed runtime, Redis, Kubernetes, enterprise observability, and complex vector databases only when a measured deployment need justifies them.

## Delivery and compatibility gates

1. Pick one bounded slice and explain alternatives, tradeoffs, and acceptance evidence.
2. Preserve or explicitly migrate Python/CLI/API contracts, YAML, prompts, IDs, events, and persisted state.
3. Run the public test suite and same-Core demo. Run the private reference check when affected; report when it was unavailable.
4. Keep public material in English and private content outside Git. Never rewrite a published tag.
5. Record unreleased work in the changelog. Increment backend/frontend versions when their distributable behavior changes; examples and architecture documentation alone do not change the release version.

An incompatible M2 generation is an option only when compatibility cannot reasonably be maintained. It is not a prerequisite for the incremental backbone above.
