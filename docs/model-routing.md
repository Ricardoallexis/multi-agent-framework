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

Task assignment profiles and executor declarations live under executor_routing in catalog/models.yaml. The bundled declarations for Codex app subagents, Claude Code, Copilot CLI, Claude app, and ChatGPT app are manual, have no adapter ID, and leave supported capabilities, permissions, and context isolation unknown. These are configuration labels, not claims that an API is installed, reachable, authorized, or supported in a particular account.

The RUT-T02 capability inventory distinguishes supported, unsupported, and unknown evidence and requires a manual/Human Bridge path where a surface lacks a verified API. Context isolation is separately represented as fresh, inherited, or unknown; unknown is not equivalent to inherited. Requested model and effort are not evidence that a surface applied or observed them.

For assignment, the public task-creation requirements remain plain labels. executor_routing.requirement_map maps known labels to capabilities, roles, permissions, or a data class. Unmapped labels remain unknown and block automatic selection. Nonempty task-envelope constraints are currently unmapped and block with canonical_constraints_unmapped; unknown request constraints also block. Preserve those constraints and handle the task manually until a typed mapping exists. Do not remove constraints to force an automatic match.

## Budgets, price data, and quality limits

A profile’s planning_max_input_tokens, planning_max_output_tokens, and planning_max_cost are pre-dispatch planning filters. They depend on estimates and comparable, current catalog pricing; they do not cap actual provider usage or guarantee a final bill. Missing token estimates, missing prices, different currencies/units, or stale price evidence leave cost unknown and can block a profile that requires that estimate.

The model catalog’s legacy input_cost_per_million and output_cost_per_million values, including 0.0, are not by themselves price evidence and do not prove that a model is free. Cost comparison needs input and output pricing with matching currency, unit, and denominator plus source, provenance, and validity dates. A profile with economic_selection enabled selects only when the configured prices can be compared; it does not prove lower real-world cost or higher quality.

A model may also declare an optional level (T1 economic, T2 standard, T3 advanced, T4 maximum) and a classification (classified or unclassified, the default). Classified requires a level, and a model with no classification is unclassified: it has not been measured. Routing does not read level or classification yet; they only record the declaration. The pricing block is validated when the catalog is loaded (input and output each with amount, an integer denominator, unit, currency, source, valid_from, valid_until and provenance; the two must match in currency, unit, and denominator), and a malformed block is an error that names the model. A block outside its validity window loads, but the router ignores it as if it were absent. A local model with no per-token charge may declare an amount of 0 with provenance local_no_charge and a source that says why; the bundled local Ollama models do this and are T1 classified, while bundled cloud models stay unclassified with no pricing. Setting a non-zero legacy input_cost_per_million or output_cost_per_million without pricing logs a warning at load and changes nothing else.

Strict budgets currently support provider_invocations only. max_provider_invocations is enforced at each dispatch for the run/profile, together with the run-wide max_llm_calls limit. Each dispatched attempt, including retries, fallback attempts, and failed calls, consumes an invocation. The profile’s max_candidates and retry settings constrain the planned cascade as well. Requesting strict token or cost limits is unsupported and blocks the route; planning estimates are not substitutes for strict enforcement. Run duration and workflow limits remain separate.

Routing and usage summaries are run-scoped. Attempt events count dispatches; usage records contain reported token and cost fields. Unknown values remain null/unknown rather than zero, and the framework does not infer quality, savings, or model effectiveness from a route decision alone.

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
