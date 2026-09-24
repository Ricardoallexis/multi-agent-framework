# Architecture decisions — configurable capability runtime

Status: **proposed architectural direction for review**, grounded in the [baseline audit](ARCHITECTURE_REVIEW.md). These records distinguish current behavior from future design. The only implemented slice in this review is a Python example using the existing runtime; the records do not introduce new Core APIs.

## 01 — Core mechanisms and domain configuration

**Decision:** target a configurable runtime for teams of intelligent capabilities. Core owns execution mechanisms, runs, steps, state, persistence, contracts, events, errors, retries, interruption, and general execution policy. Configuration/extensions own roles, purposes, capabilities, prompts, models, tools, relationships, workflows, and organization-specific rules.

Keeping domain branches in Core is initially convenient but makes every new use case a runtime change. Extract them incrementally behind explicit contracts/context bindings. Researcher, strategist, reviewer, legal analyst, and similar roles remain user-defined examples, never mandatory Core classes. The current marketing-specific paths remain compatibility behavior until their replacements are verified.

## 02 — Agent does not mean LLM

**Decision:** an agent definition describes intended work, not its implementation technology. Future execution strategies may include API, local model, deterministic Python, tool, human, Human Bridge, remote agent, and mock executors.

Today automatic steps use `LLMAdapter`; preserve that tested boundary. Introduce a broader executor protocol only with a second concrete execution type and shared input/output validation. Renaming `LLMAdapter` now would not remove model-specific fields or semantics.

The target boundary is input contract → execution → raw result → validation → output contract, regardless of executor. Production and verification may use separate configured agents or deterministic validators without imposing those roles on every workflow.

## 03 — Capability is independent of role

**Decision:** distinguish role (purpose/identity), capability (what can be done), executor (how), tool (available action/resource), and knowledge/context (accessible information).

Current YAML capabilities are descriptive metadata. First add a validated definition and reference contract; routing by capability comes later. Preserve current labels while a migration is designed. Do not use role names as capability IDs or require one provider/model for an agent's lifetime.

## 04 — The runtime owns Run state

**Decision:** clients request transitions; the runtime validates and persists them. A Run should own its status, current work, waits, results, execution history, and eventually resolved definition/checkpoint identity.

Keep SQLite and the current sequential scope. Strengthen consistent step transitions and snapshot/version semantics before adding replay, fork, retry-from-point, or changing executors on a suspended run. The existing cursor and events are not an event-sourced runtime or an exactly-once guarantee.

## 05 — Human Bridge is user-controlled execution

**Decision:** describe Human Bridge as **human-mediated interoperability / user-controlled execution**. The runtime prepares a prompt, pauses, exposes the expected contract, and validates the returned output. The user chooses an interface/model, may edit the prompt, and reports what was used. Provider/model provenance remains self-reported.

The future common mechanism is external-input interruption and resume, independent of CLI, Python, HTTP, Colab, or UI. Preserve `human_next`, `human_submit`, persisted human-request rows, and existing status values while defining compatible generic request types. Do not frame this feature as API evasion or silently rename stored state.

## 06 — Observability is UI-agnostic

**Decision:** runtime observability must be UI-agnostic. CLI, API, Colab, and future UIs consume the same runtime facts. The Core must not contain graph positions, colors, layouts, animations, or visual edge definitions.

A proposed versioned event envelope should support:

| Field | Meaning |
| --- | --- |
| `event_id`, `run_id`, `schema_version` | Stable event identity, owning execution, compatible schema evolution. |
| `step_id`, `attempt`, `correlation_id` | Logical step/attempt and a related interaction's identity. |
| `source_type`, `source_id`, `target_type`, `target_id` | Explicit endpoints where an interaction exists. |
| `interaction_type`, `status`, timestamps | Task, delegation, question, response, review, handoff, artifact share, and lifecycle. |
| Optional references | Worker/team, provider/tool, artifact, parent event, and external request identities when available. |

Endpoints may eventually be agents, workers, teams, tools, humans, remote agents, or providers. Do not invent missing endpoints for old events. Ordinary run events need not be forced into a communication shape. Define a compatibility projection from existing `event_type/payload` data.

`interaction.started`, `interaction.completed`, and `interaction.failed` must share correlation identity; later define cancellation/interruption closure so a terminated interaction does not remain falsely in flight. Hook candidates include `before_step`, `after_step`, provider-call boundaries, validation, and errors. Start with persisted facts; define hook failure/ordering rules before exposing mutation hooks.

**Live Topology** is a projection of active/waiting workers, teams, tools, communications, and external waits. **Historical Trace** retains completed/failed/cancelled activity and its artifacts. Removing a worker from a live view must never delete its history. A future Communication Graph uses these structured facts for counts, durations, costs/tokens where available, loops, and bottlenecks; it must not parse log text to guess relationships.

## 07 — Teams are optional organization boundaries

**Decision:** allow a future workspace/project to use more than one Team without requiring a leader or supervisor. A team entrypoint may name an agent **or** a workflow. Team-to-team calls go through declared entrypoints and contracts rather than unrestricted all-to-all agent access.

Plan scoped references while retaining legacy unqualified IDs. Team status is a derived view of owned work: running if relevant work is running, waiting if all active work waits, idle when none remains. Precise mixed/failure semantics need a consumer before implementation. Teams, nested organizations, and UI zoom levels are not prerequisites for today's Python runtime.

## 08 — AgentDefinition and WorkerInstance are different identities

**Decision:** one reusable AgentDefinition may create several temporary WorkerInstances. A future worker needs its own ID, run/task, definition reference, scoped context, state, timestamps, and output/artifact references. Never reuse `LocalWorker.worker_id` as an agent-instance ID.

Future states: created, ready, running, waiting, completed, failed, cancelled. Waiting reasons may identify another worker/team, human, tool, provider, external input, retry, or resource. Concurrency/timeouts/retries/fallback/cost/privacy/availability belong in execution policies, with definition and run overrides, not a rigid one-agent/one-worker rule.

First establish claiming, identities, cancellation, and persistence guarantees. Dynamic allocation and large worker populations remain experimental.

## 09 — Presets compose in explicit layers

**Decision:** target `framework defaults < public preset < private/local overlay < runtime overrides`. This is a design direction, not an existing loader.

Proposed initial merge rules: address definitions by stable IDs; recursively merge mappings; replace lists as whole values; apply explicit scalar overrides; interpret null only when the field contract permits it. Reject ambiguous deletion and unknown references until dedicated operations are specified. Validate the resolved configuration and record a version/hash with the Run before making resume depend on composition. Policy enforcement must not be mistaken for ordinary last-value-wins configuration.

A full merge engine is unnecessary until a concrete pilot needs one. Avoid implicit directory scanning, guessed precedence, and duplicated public/private team trees.

## 10 — Private implementation does not require copying public definitions

**Decision:** keep private prompts, rules, credentials, knowledge, paths, and customer data outside the public repository when practical. Public presets contain shareable structure, generic prompts, contracts, tool requirements, and safe defaults. A private layer should add/override only the private differences.

Use the current request `instructions` field for a limited pilot; it does not implement per-agent prompt composition. A later prompt extension should have explicit append/replace semantics and preserve public prompt identity plus private version/hash. Full private prompt replacement must be an intentional override, not a second manually synchronized copy of the public team.

`LOCAL_DIR` relocates runtime data, not the default environment-file lookup or public definitions. Explicit external `_env_file` loading is already available in Python. A future `preset validate-public` check is defense in depth, never a sanitizer relied upon to publish private content automatically.

## 11 — RAVC is a private reference implementation

**Decision:** RAVC validates the framework as a private dogfooding case. It must not determine Core role names, provider choices, contracts, or business rules. The same public-base/private-implementation pattern must work for any user's team.

Maintain two complementary checks: a small public, deterministic, zero-key demo and a confidential RAVC workload on the same Core. Public fixtures never contain real RAVC prompts or data. Record private check results without exporting their contents. The current review validates only the public side; the [RAVC pilot plan](PRIVATE_PRESETS.md) describes the private handoff.

## 12 — Python is a first-class entrypoint

**Decision:** importing and running the same Core must remain practical without starting a server. Reuse `multiagent.bootstrap.build_system`, Settings, contracts, and services for Python/CLI/Colab/API/UI. A separate Core Lite would create divergent behavior and regression burden.

Today the supported reproducible route is a checkout, requirements installation, and editable package. Fix wheel dependency/resource packaging next; then build the Colab Run All path on the same factory and a small generic mock workflow. Do not create a misleading generic `run(...)` facade while requests are still restricted to social posts. Optional provider SDKs should remain lazy at actual provider use, as the cloud adapters currently do.

## 13 — Choose the smallest useful verified slice

**Decision:** a roadmap item is not authorization to implement all of its dependencies at once. Preserve compatibility, use focused commits, and require an executable acceptance condition for each slice.

| Candidate | Benefit | Cost / limitation | Recommendation |
| --- | --- | --- | --- |
| Python same-Core quickstart | Immediate reusable Python entrypoint, zero-key verification, basis for Colab and private pilots. | Does not generalize the domain or fix standalone distribution. | **Selected for this review.** One example plus documentation; no runtime/schema changes. |
| Packaging/resources | Removes a verified blocker to standalone Python installation. | Needs a resource layout decision and isolated wheel/sdist verification. | First following implementation slice. |
| Typed capabilities | Gives definitions a validated boundary. | Does not itself remove domain-specific requests, context, or executors. | After distribution; coordinate with definition validation. |
| Executor abstraction | Admits deterministic tools and other execution strategies. | Touches step requirements, router, response metadata, and budgets. | Defer until generic contract/context boundaries are defined. |
| Structured interaction events | Enables trustworthy provenance and future projections. | Needs identity and correlation semantics, including terminal outcomes. | Early backbone work; no UI implementation. |
| Complete preset merge engine | Enables broad reusable teams with private implementations. | Premature schema/merge commitments; current pilot can use request instructions. | Start only with a demonstrated private-composition need. |

The selected example must import through the installed package from another working directory, produce three artifacts using FakeAdapter, and stop at human review without API keys or a background server. Existing tests remain the compatibility gate. Native Windows, Colab, real providers, and private RAVC execution remain separately reported checks.
