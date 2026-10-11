# Model routing and task assignment

Model routing chooses a model for a workflow step. Task assignment chooses a person or executor surface for a task. A selected route or assignment is a plan; it does not prove that a provider or executor was called, that the requested settings took effect, or that the resulting work passed review.

## Defaults and opt-in profiles

Workflows keep their configured preferred model and optional fallback unless a routing profile is explicitly assigned to a workflow step in the catalog. An absent profile preserves this legacy behavior. An explicitly requested profile that is unknown, disabled, invalid, or has no eligible candidate returns a blocked decision; it does not silently use the legacy binding.

Profiles and assignments live in the routing_profiles section of catalog/models.yaml. A workflow assignment is keyed by workflow ID and step ID. The bundled profile below is the current valid declaration; it is disabled and no workflow step is bound to it by default.

~~~yaml
routing_profiles:
  profiles:
    local_non_sensitive_supervised:
      version: 1
      enabled: false
      candidate_model_ids: [local_default]
      max_candidates: 1
      required_capabilities: [structured_output]
      required_roles: []
      required_permissions: []
      allowed_permissions: []
      allowed_data_classes: [non_sensitive]
      execution_modes: [auto, local]
      require_health: false
      economic_selection: false
      max_retries_per_candidate: 0
      max_provider_invocations: 1
  assignments: {}
~~~

To bind a deliberately enabled profile, assignments use workflow and step IDs, for example the existing research step:

~~~yaml
routing_profiles:
  assignments:
    social_post_full:
      research: local_non_sensitive_supervised
~~~

This binding fragment does not enable the profile. Review the candidate list, permissions, privacy classes, evidence requirements, and budget before setting enabled to true. The local/cloud tier describes where a model runs; it is not a quality or context-size ranking. Model IDs refer to entries in the same catalog, not provider model names.

## Step kind and reasoning effort

Every workflow step may declare two optional fields. A workflow without them behaves exactly as before.

~~~yaml
steps:
  - id: review_plan
    # ...the usual step fields...
    kind: review      # orchestration | planning | review | work (default: work)
    effort: medium    # none | low | medium | high | max (default: unset, provider default)
~~~

Unknown values are rejected when the workflow loads (`invalid_value`; a wrong type is `invalid_type`). In YAML, `none` is the level "no reasoning"; leave the field out (or `null`) to keep the provider default. `kind` is generic and has no effect by itself; it is carried in `RoutingRequest.step_kind` together with `effort` and the 1-based `attempt` of the step. Only a profile with `levels` (see [Economic profile](#economic-profile)) reads `step_kind`; every other profile selects as before.

The step's `effort` is passed to the provider adapter, which translates it only where the provider can express it:

| Adapter | Setting sent | Mapping |
| --- | --- | --- |
| Ollama | `think` | `none` = false; `low`, `medium`, `high`, `max` = true |
| Gemini | `generation_config.thinking_level` (Interactions API) | `none` = minimal (Gemini 3 cannot turn thinking off), `low` = low, `medium` = medium, `high` = high, `max` = high |
| OpenAI | `reasoning.effort` (Responses API) | `low`, `medium`, `high` as is; `max` = high; `none` has no portable value and is ignored |
| Fake (tests) | simulated `effort` | applied unless built with `supports_effort=False` |

An explicit effort overrides the model's catalog `thinking` value for that call; without an effort the adapters send exactly what they sent before. An adapter that cannot express the effort ignores it and the call proceeds. Each `model_attempt` event of a step with an effort records `effort_requested`, `effort_applied` (true only when the provider call carried the setting), `effective_config_effort` (the provider setting sent, or null) and `effort_note` (why it was not applied). The adapter also copies these into the response metadata. The record says what was requested and sent, not that the provider honored it or what it cost. The adapters do not know which models support the setting: a model that rejects it fails the call as a provider error.

## Economic profile

The bundled `economic` profile in `catalog/models.yaml` chooses a model by step kind, model level and price evidence. It is optional and reversible: it ships with `enabled: false` and is assigned to no workflow step, so nothing changes until you turn it on. It is a routing profile like any other; a decision it makes is a plan, not proof of a call, of the price paid, or of quality.

~~~yaml
routing_profiles:
  profiles:
    economic:
      version: 1
      enabled: false
      mode: supervised            # supervised | automatic | single
      levels:
        orchestration: {min: T3}
        planning:      {min: T3}
        review:        {min: T3}
        work:          {max: T2}
      effort: {work: low, review: medium}
      economic_selection: true
      escalation: {max_escalations_per_step: 1, ceiling: T3}
      # plus the usual profile fields: required_capabilities, allowed_permissions, allowed_data_classes, ...
~~~

The economic fields are all optional; a profile that declares none of them behaves exactly as before. They are validated when the catalog loads, and a malformed one is an error naming the profile.

| Field | Meaning |
| --- | --- |
| `mode` | `supervised` (default), `automatic` or `single`. Selection is the same in `supervised` and `automatic`; they differ only in how an [escalation](#bounded-escalation) is approved. `single` uses only `model_id` and never escalates. |
| `levels` | Per step kind (`orchestration`, `planning`, `review`, `work`) a `min` and/or `max` model level (`T1` to `T4`). A step kind that is not listed is blocked (`step_kind_levels_missing`). |
| `effort` | Per step kind, one of `none`, `low`, `medium`, `high`, `max`. It is used only when the step declares no effort of its own; a step's own effort always wins. |
| `model_id` | The catalog model of `mode: single`; required there, ignored in the other modes. |
| `escalation` | `max_escalations_per_step` (0 or 1) and `ceiling` (`T1` to `T3`; T4 is never automatic). Without this block, or with a limit of 0, a step fails as it always did. See [Bounded escalation](#bounded-escalation). |
| `candidate_model_ids` | Optional allowlist. Without it every model in the catalog is a candidate (the 2-candidate limit of the other profiles does not apply). |
| `economic_selection` | For `work`, `true` (the default here) takes the cheapest candidate and `false` takes the first eligible one in the order of `candidate_model_ids`. Orchestration, planning and review always take the cheapest. |

How a step is routed:

1. A candidate is eligible when it passes the same rules as any profile (capabilities, role, permissions and the profile's permission ceiling, data class, local or cloud mode, health) and, in addition, it is `classification: classified`, its level is inside the bounds of the step's kind, and it has current price evidence (source and validity window). A model that is unclassified, outside the bounds or without a current price is left out with a reason; an unknown price is never read as 0.
2. Among the eligible candidates the cheapest is chosen: by estimated cost when the request carries input and output token estimates, otherwise only when one candidate's input and output rates are both lowest. Candidates with crossing rates and no estimates block (`common_token_quantities_or_price_dominance_missing`). The workflow engine currently supplies the input estimate only, so a choice between several models needs one whose rates are both lowest.
3. If no candidate is eligible the decision is `blocked` with `no_eligible_candidate` plus the level, classification or price reasons seen (`model_unclassified`, `level_below_minimum`, `level_above_maximum`, `price_evidence_missing_or_expired`). The run does not fall back to the static binding.
4. `mode: single` skips levels, classification and price: only `model_id` is considered, still under the permission, privacy, mode and health rules, with no cheaper or dearer alternative and no fallback model.

Permissions and privacy are never relaxed. Sensitive data is still sent only to a candidate whose declared data classes include it, within the profile's `allowed_data_classes` and `allowed_permissions` and the caller's grants; cloud models still need `provider:cloud` granted by the caller and inside the profile ceiling.

The decision records what it was based on (`RoutingDecision`, also in the `routing_decision` run event): `step_kind`, `profile_mode`, `selected_level`, `price_evidence` (the input and output rates with source, validity window and provenance, whether they were current, the ranking used and the estimated cost, or null when it could not be computed), the `effort` to use with `effort_source` (`step` or `profile`), and every candidate with its `level`, `classification`, `eligible` flag and `reason_codes`; the rejected candidates are those with `eligible: false`.

To enable it:

1. Classify the models you want to use: give each a `level`, `classification: classified` and a `pricing` block with a source and validity window (the bundled local Ollama models are T1 with a declared no-charge price; the bundled cloud models are unclassified and have no pricing, so they stay out until you measure and price them). Prices in the catalog are your declaration, not live provider data.
2. If cloud models will take part, add `provider:cloud` to the profile's `allowed_permissions` (it is empty, so only local models qualify) and make sure the caller grants it and authorizes the data class.
3. Set `enabled: true` and review `levels`, `effort` and `mode`.
4. Mark the steps with `kind` (steps default to `work`) and assign the profile to the steps in `assignments`, for example `assignments: {social_post: {create: economic}}`.

With the bundled catalog nothing is T3, so orchestration, planning and review steps block until a T3 or T4 model is classified and priced; that is the intended behavior and not a fault.

To turn it off, remove the assignment (steps return to their static binding) or set `enabled: false` (a step still assigned to a disabled profile blocks). No migration is needed either way.

## Bounded escalation

A step whose cheaper model fails on the **quality of its output** can move up one model level, once. It applies only to an economic profile in `supervised` or `automatic` mode that declares `escalation` with `max_escalations_per_step: 1`; every other profile, `single` mode, a limit of 0 and a dry run behave as before.

- **Trigger.** The step exhausts its per-candidate retries (and its same-level fallback) with a `StructuredOutputError` or a failed semantic validation. Provider errors (network, authentication, quota, rate limit, a model that does not fit the request) never escalate: they keep the fallback to a peer. If the last failure of the call is a provider error, nothing escalates.
- **Action.** The step is routed again with the same profile, role, permissions, data class and execution mode, restricted to the models of the **next level** after the one that failed (T1 to T2, T2 to T3), never above `ceiling` and never to T4. Those models must be classified, have current price evidence and pass the profile's normal eligibility; sensitive data is still sent only to a model that is allowed to receive it. If none qualifies, or the ceiling is reached, the step fails with a message that asks for human intervention. A level is never skipped. While escalation is enabled, a model's fallback is a peer of its own level; a stronger model is reached only by escalating.
- **Budgets.** Checked before escalating, with no escalation if one is exhausted: `max_llm_calls`, the profile's `max_provider_invocations`, `max_run_seconds`, and the profile's planning budgets (`planning_max_cost`, `planning_max_input_tokens`, `planning_max_output_tokens`) applied to the candidate of the next level. The calls of the failed tries already count.
- **Limit.** One escalation per attempt of a step. A human "request changes" revision is not an escalation trigger: it repeats the step at the profile's normal level, and the ORQ-2 gate only records its result.
- **Modes.** `automatic` escalates up to the ceiling without asking. `supervised` (the default) escalates automatically below T3 and, to reach T3 or above, **pauses the step**: the run waits as `waiting_human` with `waiting_reason: escalation_approval` and an `escalation_requested` event (reason, error, the model proposed with its price evidence, and the estimated cost so far). `POST /api/v1/runs/{id}/approve` resumes the same step with the escalated model, selected again so a changed catalog, grant or budget still applies; `reject` fails the step and the run with the reason. A pending escalation takes no other review action (`changes`, `regenerate`). `single` never escalates.
- **Record.** Run events `model_escalated` (step, attempt, escalation number, mode, from and to level and model, `reason` = `structured_output_failed` or `semantic_validation_failed`, the error, `estimated_cost_so_far` for the run and for the step with unknown costs apart, and `approved_by_human`), `escalation_requested`, `escalation_approved`, `escalation_rejected` and `escalation_blocked` (with a `code` such as `ceiling_reached`, `no_eligible_candidate`, `max_llm_calls_reached` or `max_escalations_reached`). The `routing_decision` of the escalation carries `escalation` (number, attempt, from and to level, from model, reason, `ceiling`, `max_escalations_per_step`) and the request's `RoutingRequest.escalation`; later `model_attempt` events of the step carry `escalation: 1`, and `model_selected` lists the attempts of both levels. The estimated cost is an estimate from catalog prices, not a bill, and it is `null` for a call whose tokens or price are unknown.

## Measuring the economic profile

A routing decision is a plan, not proof that the profile saves money or keeps quality. Whether the economic profile should become anyone's default is decided from measured runs, by the user, using three offline tools in `tools/evals/routing/`. They read records and a database; they call no model, change no catalog, and **activate nothing**: the profile stays disabled and unassigned unless someone assigns it.

| Tool | Does |
| --- | --- |
| `export_runs.py` | Turns a labelled set of finished runs in a framework database into comparator records (the `compare.py` format of RUT-T08). Reads only. |
| `compare.py` | Compares two routes case by case: acceptance, calls, tokens, cost, escalations, rework. |
| `adoption.py` | Applies the adoption criterion below to those records, per corpus category and overall, and writes a verdict as JSON or Markdown. |

### The criterion

The criterion is the one approved in the ECO-T01 contract (section 8). The economic profile (the candidate route) is adopted for a group of cases only when, on the cases that both routes ran with the same frozen input, rubric, configuration and surface:

| # | Requirement | Rule |
| --- | --- | --- |
| 1 | Enough cases | At least 20 paired cases in each category of the corpus (`--min-cases`). |
| 2 | Cost | Cost **per accepted run** strictly lower than the current route's, counting the failed attempts and escalations. A tie is not a saving. |
| 3 | Quality | Acceptance rate not worse by more than 2 percentage points (`--max-acceptance-drop-points`). |
| 4 | Safety | No new critical errors: no case where only the candidate has a critical error. |

Each category gets a verdict, and so do all cases together:

- `adopt`: every requirement holds.
- `not_adopt`: a requirement measurably fails (cost not lower, quality drop beyond the margin, a new critical error).
- `insufficient_data`: it cannot be decided: fewer than 20 paired cases, or a cost that is unknown (a call without price evidence or without reported tokens), undefined (no accepted run) or in a different unit than the other route. An unknown cost is never read as 0. A requirement that already fails on too few cases is listed but does not decide `not_adopt`.

The overall verdict is `not_adopt` if any group fails, `insufficient_data` if any category is short, uncategorized cases exist, or a corpus category named with `--category` has no case, and `adopt` only when every category and the pooled cases adopt. The verdict is evidence for the user; enabling the profile by default stays a user decision. The same measurement serves to classify a new model: run the cases with the model as the candidate and read the verdict per category before giving it a `level` and `classification: classified` in the catalog.

### Running a measurement

A measurement with paid models spends money and quota. Run one only after the user has authorized it and fixed a budget (a cap on `max_llm_calls` per run, the number of cases, and the models involved), and keep real inputs and the exported files outside version control. Everything below also runs offline: `tests/test_economic_measurement.py` does it end to end with a fake provider and fake priced catalog entries.

1. **Prepare.** Freeze the corpus (at least 20 cases per category) and its rubric, and give each case a stable id, an input version and a rubric version and hash. Classify and price the T1 to T3 models in the catalog with a `source` and validity window, and define the economic profile as in [Economic profile](#economic-profile).
2. **Run both routes.** Run every case twice with the same request, workflow, settings and budgets: once as it runs today (no profile assigned for the step: route `legacy`) and once with the profile assigned (route = the profile id, for example `economic`). Switch routes by adding and removing the entry in `routing_profiles.assignments`; nothing else changes. Keep the run ids.
3. **Review.** A human approves or rejects each result at the review checkpoint (an approved final result is an accepted run), and a reviewer adjudicates critical errors per the corpus rubric.
4. **Label the runs.** One JSON object per run, in a JSON or JSONL file:

   ~~~json
   {"run_id": "...", "case_id": "case-007", "route": "economic", "category": "extraction",
    "input_version": "v1", "rubric_version": "r1", "rubric_hash": "...", "critical_error": false}
   ~~~

   `run_id`, `case_id`, `route`, `input_version`, `rubric_version` and `rubric_hash` are required, and so is `critical_error` (the framework cannot judge it; `--assume-no-critical-errors` records `false` for labels without it, only when no adjudication exists). Optional: `category`, `input_hash` and `config_hash` (default: hashes of the run request and of the workflow and budget settings), `surface`, and `accepted: false` for a run an evaluator rejected although it completed. Only these keys are read; a corpus row with its input text can be passed as a label without leaking it.
5. **Export, compare, judge.**

   ~~~sh
   python tools/evals/routing/export_runs.py --db data/multiagent.db --labels labels.jsonl --output records.jsonl
   python tools/evals/routing/compare.py records.jsonl --output report.json
   python tools/evals/routing/adoption.py report.json --candidate economic --category extraction --category classification --output verdict.md
   ~~~

   `adoption.py` takes the baseline route `legacy` by default, writes Markdown or JSON by the extension of `--output`, and exits 0 whatever the verdict (the verdict is data). Use `--category` once per corpus category so that a category with no run cannot go unnoticed.

How the export fills a record, so the numbers can be audited:

- `accepted`: the run is `completed`, did not end because its budget ran out, and has no critical error. The export refuses runs that are still queued, running or waiting, and a route label that disagrees with the routing profile in the run's events (`legacy` means none).
- `calls`, `input_tokens`, `output_tokens`, `duration_ms`: the provider calls in the usage telemetry, failed attempts included and human steps not. A value any call did not report is unknown (null), and so is everything when a telemetry write failed.
- `cost` and `cost_unit`: the sum of the calls' estimated costs from catalog price evidence (see [`TELEMETRY.md`](TELEMETRY.md#estimated-cost)). It is unknown if any call has no cost or the currencies differ, and `adoption.py` then reports `insufficient_data`. A run with no model call has a measured 0.
- `rework`: human requests for changes. `escalations`: `model_escalated` events (see [Bounded escalation](#bounded-escalation)). `requested` and `effective`: the catalog model ids routed to and called.
- **Privacy:** records hold identifiers, hashes, counters, tokens, costs and model ids. Prompts, requests, artifacts, event payloads and error messages are never exported.

An illustrative verdict, from the synthetic test corpus (fake provider, fake prices, 6 cases per category and `--min-cases 6`; with the real 20-case minimum the same evidence is `insufficient_data`):

~~~text
Verdict: adopt - candidate `economic` against baseline `legacy`, 12 paired case(s).

| Category       | Cases | Verdict | Acceptance (baseline -> candidate) | Cost per accepted run  | New critical | Candidate escalations |
| classification | 6     | adopt   | 100.0% -> 100.0%                   | 0.03 -> 0.00373333 USD | 0            | 1                     |
| extraction     | 6     | adopt   | 100.0% -> 100.0%                   | 0.03 -> 0.00506667 USD | 0            | 2                     |
| (all cases)    | 12    | adopt   | 100.0% -> 100.0%                   | 0.03 -> 0.0044 USD     | 0            | 3                     |
~~~

The synthetic numbers show the mechanics only. They say nothing about any real model, price or task.

## What a decision tells you

The selector is a pure in-process operation. It consumes a RoutingRequest and optional RoutingEvidence; it does not probe a provider or call a model. The result status is selected, manual, or blocked. Reason codes and provenance explain why a candidate was kept or rejected.

Requested, planned, configured, and observed values have separate meanings:

| Field | Meaning |
| --- | --- |
| requested_model | The original preference supplied by a workflow or caller. |
| selected_model / planned_model | The eligible catalog ID and configured provider model chosen by the selector. |
| effective_config_model | The model name resolved from the active catalog at dispatch. It is empty before a provider invocation. |
| effective_model | The model associated with an actual attempted invocation. It is empty when there was no invocation. |
| observed_model | A model named by provider response metadata with provider-response provenance. Missing evidence stays unknown. |

For task assignment, the parallel fields are requested_executor, planned_executor, effective_executor, and observed_executor. An assignment preview does not launch an executor. Current bundled surfaces are manual declarations with null capabilities and no adapter IDs, so they do not establish API availability. A future adapter must report a descriptor and pass its conformance/evaluation before it can be treated as automatic.

Capabilities, roles, and permissions are distinct. Required permissions must be present in the candidate’s available permissions, in trusted caller grants, and within the profile’s allowed-permission ceiling. Model declarations do not grant authority to the caller. For task assignment, trusted operator, alias, request, and profile scopes are intersected; unknown grants or permission ceilings cannot satisfy a required permission. Data classes are also explicit: cloud selection requires an authorized class accepted by both the profile and candidate. The catalog does not establish a provider’s retention or training terms beyond fields explicitly configured by the operator.

## Evidence, privacy, and external surfaces

RoutingEvidence is supplied by the caller and scoped to a run. Health is considered only when the profile requires it and the snapshot has the expected adapter-health provenance, source, scope, and freshness. Selection itself never calls health endpoints. A missing, stale, expired, or unverified health snapshot does not count as healthy.

Task assignment profiles and executor declarations live under executor_routing in catalog/models.yaml. The bundled declarations for Codex app subagents, Claude Code, Copilot CLI, Claude app, and ChatGPT app are manual, have no adapter ID, and leave supported capabilities, permissions, and context isolation unknown. These are configuration labels, not claims that an API is installed, reachable, authorized, or supported in a particular account. The Claude, Codex, and Copilot CLI declarations (claude_cli, codex_cli, copilot_cli) carry `enabled: false` and extra evidence (non-interactive launch, structured output, session store, resume, blocks when open, open in app, quota signal, terms) from the P-384 manual tests; unknown capabilities stay unknown, and an executor with `enabled` other than `true` is never selected (reason code executor_disabled).

The RUT-T02 capability inventory distinguishes supported, unsupported, and unknown evidence and requires a manual/Human Bridge path where a surface lacks a verified API. Context isolation is separately represented as fresh, inherited, or unknown; unknown is not equivalent to inherited. Requested model and effort are not evidence that a surface applied or observed them.

For assignment, the public task-creation requirements remain plain labels. executor_routing.requirement_map maps known labels to capabilities, roles, permissions, or a data class. Unmapped labels remain unknown and block automatic selection. Nonempty task-envelope constraints are currently unmapped and block with canonical_constraints_unmapped; unknown request constraints also block. Preserve those constraints and handle the task manually until a typed mapping exists. Do not remove constraints to force an automatic match.

## Budgets, price data, and quality limits

A profile’s planning_max_input_tokens, planning_max_output_tokens, and planning_max_cost are pre-dispatch planning filters. They depend on estimates and comparable, current catalog pricing; they do not cap actual provider usage or guarantee a final bill. Missing token estimates, missing prices, different currencies/units, or stale price evidence leave cost unknown and can block a profile that requires that estimate.

The model catalog’s legacy input_cost_per_million and output_cost_per_million values, including 0.0, are not by themselves price evidence and do not prove that a model is free. Cost comparison needs input and output pricing with matching currency, unit, and denominator plus source, provenance, and validity dates. A profile with economic_selection enabled selects only when the configured prices can be compared; it does not prove lower real-world cost or higher quality. The units `token` and `tokens` are the same unit when prices are compared (case and surrounding spaces are ignored); any other unit, or a different currency or denominator, is incomparable and blocks.

A model may also declare an optional level (T1 economic, T2 standard, T3 advanced, T4 maximum) and a classification (classified or unclassified, the default). Classified requires a level, and a model with no classification is unclassified: it has not been measured. Only a profile with `levels` reads level and classification (see [Economic profile](#economic-profile)); for every other profile they only record the declaration. The pricing block is validated when the catalog is loaded (input and output each with amount, an integer denominator, unit, currency, source, valid_from, valid_until and provenance; the two must match in currency, unit, and denominator), and a malformed block is an error that names the model. A block outside its validity window loads, but the router ignores it as if it were absent. A local model with no per-token charge may declare an amount of 0 with provenance local_no_charge and a source that says why; the bundled local Ollama models do this and are T1 classified, while bundled cloud models stay unclassified with no pricing. Setting a non-zero legacy input_cost_per_million or output_cost_per_million without pricing logs a warning at load and changes nothing else.

Strict budgets currently support provider_invocations only. max_provider_invocations is enforced at each dispatch for the run/profile, together with the run-wide max_llm_calls limit. Each dispatched attempt, including retries, fallback attempts, and failed calls, consumes an invocation. The profile’s max_candidates and retry settings constrain the planned cascade as well. Requesting strict token or cost limits is unsupported and blocks the route; planning estimates are not substitutes for strict enforcement. Run duration and workflow limits remain separate.

Routing and usage summaries are run-scoped. Attempt events count dispatches; usage records contain reported token and cost fields. Unknown values remain null/unknown rather than zero, and the framework does not infer quality, savings, or model effectiveness from a route decision alone. The estimated cost in a usage record is computed from the same price evidence (tokens times the price in force at the call, unknown when either is missing); see [`TELEMETRY.md`](TELEMETRY.md#estimated-cost).

## Offline examples

A local ModelRouter selection can be inspected without constructing provider adapters or making a model call. With the bundled catalog, this reports the profile as blocked because it is intentionally disabled:

~~~python
from multiagent.catalog import Catalog
from multiagent.config import Settings
from multiagent.model_router import ModelRouter
from multiagent.routing_policy import RoutingRequest

catalog = Catalog(Settings())
router = ModelRouter(catalog, adapters={})
decision = router.select(RoutingRequest(
    profile_id="local_non_sensitive_supervised",
    execution_mode="local",
    data_class="non_sensitive",
    authorized_data_classes=frozenset({"non_sensitive"}),
))
print(decision.status, decision.reason_codes)
~~~

Task assignment preview is also local API work and does not call a model or launch an executor. It requires an existing ready task in an authorized task group and the local API at the configured API base URL:

~~~sh
multiagent task assignment preview TASK_ID
~~~

The CLI accepts --profile-id, --executor, --model, --effort, --context-isolation, --requirement, --permission, and --data-class. The HTTP preview endpoint is POST /api/v1/tasks/{task_id}/assignment/preview and accepts profile_id, requested_executor, requested_model, requested_effort, required_context_isolation, additional_requirements, permission_scope, and data_class. Request bodies are strict. Role, capability, and permission requirements for these public surfaces should come from trusted profile and requirement-map configuration; do not assume the in-process service’s broader request type is exposed by HTTP or CLI.

An explicit accept is a separate operation. The CLI command is:

~~~sh
multiagent task assignment accept TASK_ID PREVIEW_TOKEN --acknowledge
~~~

Accept re-reads task, envelope, profile, descriptor, and routing state before claiming. It can reject a stale preview. By default, the HTTP application and CLI backend do not inject a verified operator context, so preview remains manual/unverified and accept cannot claim an executor. An embedded host may provide a trusted per-request operator_context_provider; this is an injection point, not a production authentication system. The local API host/origin checks are not authentication for network exposure.

## Rollback

For workflow model routing, remove the workflow-step entry from routing_profiles.assignments to restore the static preferred/fallback binding. Do not leave an explicit reference to a profile that has been disabled: an explicit disabled profile blocks instead of falling back.

For task assignment, omit the assignment profile to retain the manual/preferred path. Disabling a profile that was explicitly requested does not enable legacy fallback. No rollback requires a database migration: these settings and decisions are configuration/data contracts, while the default executor declarations remain manual.

See also the [API reference](API.md), [architecture overview](ARCHITECTURE.md), and [stage plan](ROADMAP_STAGES.md).
