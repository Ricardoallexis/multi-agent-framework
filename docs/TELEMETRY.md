# Local usage telemetry

Multi-Agent Framework records token usage for each model or human-guided step
that runs through the workflow engine. The data is local operational metadata:
it is stored in the SQLite database under the configured local data directory,
is not sent to a telemetry service, and never contains prompts, model
responses, or customer content.

## What is recorded

Each record identifies the run, workflow step, workflow agent, attempt,
provider, model, and creation time. It may also include:

- input and output token counts, when the provider reports them;
- context size, elapsed duration, model digest, and estimated cost with the
  price evidence behind it (`cost_basis`, see [Estimated cost](#estimated-cost));
- prompt identity metadata (ID, version, and SHA-256 hash);
- success or failure and the error type.

Gemini reports usage under the Interactions API names `total_input_tokens`,
`total_output_tokens`, `total_thought_tokens` and `total_cached_tokens`. The
adapter reads those, with the older `input_tokens` and `prompt_tokens` style
names as a fallback. Thinking tokens are added to the output count and also
noted in the attempt metadata as `thought_tokens`. If a call reports no usage,
the record keeps the tokens unknown (`null`), not 0.

Token counts are nullable by design. A human-guided step has
`provider: "human"` and no provider token counts. A provider failure is
recorded when possible, including the attempt number. Missing usage is not
converted to zero.

Telemetry writes are best-effort. If a write fails, the engine emits a
`telemetry_failed` run event and continues the workflow step. This prevents
observability from changing workflow availability.

## Reading a run

The Python facade returns records and totals for the complete run, grouped by
agent, step, and model:

```python
from multiagent.services import ApplicationServices

services = ApplicationServices.create()
usage = services.runs.usage(run_id)
print(usage["records"])
print(usage["totals"]["by_agent"])
```

The HTTP equivalent is:

```text
GET /api/v1/runs/{run_id}/usage
```

The CLI prints the same structured result:

```powershell
python -m multiagent usage RUN_ID
```

Use `--jsonl` for one JSON object per usage record, suitable for local
pipelines:

```powershell
python -m multiagent usage RUN_ID --jsonl > usage.jsonl
```

Every response includes `records` and `totals`. Totals are available for the
run (`run`), each `agent_id` (`by_agent`), each `step_id` (`by_step`), and each
`model` (`by_model`); they include record count, failed records, records
without usage, token sums, duration, and the cost fields described below.
`tokens_total` is `null` when either token count is unavailable. Costs are
estimates (`cost_is_estimate: true`), not provider billing data.

## Estimated cost

The cost of one provider call is computed when the call is recorded, from the
price evidence (`pricing`) of the catalog model that served it:

```text
tokens_in x input.amount / input.denominator + tokens_out x output.amount / output.denominator
```

The evidence must be in force at the time of the call (`valid_from <= now <
valid_until`), complete, not `unknown`, and the same currency, unit and
denominator for input and output; this is the same check the model router
applies when it selects by price. The unit must be per token: `token` is the
documented value and `tokens` is accepted as the same unit.

The cost is **unknown (`null`, never 0)** when any of these holds:

- the model is not in the catalog (for example the `fixture` model of a dry
  run) or declares no `pricing`;
- the pricing is incomplete, malformed, expired, or not yet valid at the call
  time (the legacy `input_cost_per_million` and `output_cost_per_million`
  fields are not price evidence and are never used);
- the input or the output token count was not reported (a count of 0 is treated
  as not reported, like the stored token fields), which includes every
  human-guided step.

A model that declares a price of 0 with evidence, such as a local model with
`provenance: local_no_charge`, produces a real 0 when its tokens are reported.
That 0 only means "no per-token charge by the framework"; hardware and energy
costs are out of scope.

`estimated_cost_usd` holds the cost only when the evidence is in USD. When the
price is in another currency, `estimated_cost_usd` stays `null` and the cost
and its currency are kept in `cost_basis`. Whenever a cost is computed (USD or
not), `cost_basis` records the evidence used:

```json
{
  "model_id": "cloud_fast", "currency": "USD", "cost": 0.0123,
  "tokens_in": 420, "tokens_out": 96, "priced_at": "2026-10-11T08:03:23+00:00",
  "input":  {"amount": 0.3, "denominator": 1000000, "unit": "token",
             "source": "...", "valid_from": "...", "valid_until": "...", "provenance": "..."},
  "output": {"amount": 2.5, "denominator": 1000000, "unit": "token",
             "source": "...", "valid_from": "...", "valid_until": "...", "provenance": "..."}
}
```

Rows recorded before cost estimation existed keep `cost_basis: null` and an
unknown cost.

Every totals object (`run`, `by_agent`, `by_step`, `by_model`) keeps unknown
costs apart instead of adding them as 0:

| Field | Meaning |
| --- | --- |
| `estimated_cost_usd` | Sum of the USD costs that are known (`null` when none is). It is the whole cost only when `cost_complete` is true. |
| `cost_known_records` | Records with a USD cost. |
| `cost_unknown_records` | Records with no cost at all (see the list above). |
| `cost_other_currencies` | Subtotal per currency for records priced in a currency other than USD (`{}` when none). |
| `cost_complete` | True when there is at least one record and every record has a USD cost. |

Show `estimated_cost_usd` together with `cost_unknown_records`, for example
"at least 0.42 USD, 2 calls without a price", whenever `cost_complete` is false.

For a generic workflow or a registered bundle, use the same run endpoint or
command. The run ID is the stable lookup key; telemetry does not create a
second workflow-specific storage system.

## Example

An abbreviated response for a run with one provider step and one human step
looks like this:

```json
{
  "run_id": "run-123",
  "cost_is_estimate": true,
  "records": [
    {
      "step_id": "create",
      "agent_id": "creator",
      "attempt": 1,
      "provider": "ollama",
      "tokens_in": 420,
      "tokens_out": 96,
      "tokens_total": 516,
      "duration_ms": 812,
      "estimated_cost_usd": 0.0,
      "cost_basis": {"model_id": "local_default", "currency": "USD", "cost": 0.0, "...": "..."},
      "success": true
    },
    {
      "step_id": "review",
      "agent_id": "editor",
      "attempt": 1,
      "provider": "human",
      "tokens_in": null,
      "tokens_out": null,
      "tokens_total": null,
      "duration_ms": null,
      "estimated_cost_usd": null,
      "cost_basis": null,
      "success": true
    }
  ],
  "totals": {
    "run": {
      "records": 2,
      "failed": 0,
      "records_without_usage": 1,
      "tokens_in": 420,
      "tokens_out": 96,
      "tokens_total": 516,
      "estimated_cost_usd": 0.0,
      "cost_known_records": 1,
      "cost_unknown_records": 1,
      "cost_other_currencies": {},
      "cost_complete": false
    }
  }
}
```

The actual response also contains the full per-agent, per-step, and per-model totals.
Failed attempts remain visible in `records` and contribute to `failed`.

## OpenTelemetry traces and metrics

Execution backends can also emit OpenTelemetry traces and metrics next to the
local records. Telemetry attaches when a backend is built. It is off by default
and does not replace the run events or the SQLite records.

Install the extra and turn it on:

```bash
pip install '.[otel]'
```

```dotenv
TELEMETRY_OTEL_ENABLED=true
# Standard OTLP variables configure the exporter:
OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
OTEL_SERVICE_NAME=multi-agent-framework
```

The development requirements (`requirements-dev.txt`) pin the API and the SDK,
so the trace tests run in CI; the OTLP exporter is only in the extra.

With the setting off, or without `opentelemetry-api` installed, a backend
builds no telemetry object and imports no OpenTelemetry module. A tracer or
meter provider of the SDK that the host application already set as global is
used as it is. Otherwise, with the SDK and the OTLP exporter installed, one
private pair of providers is built per process and shared by every backend (the
system's and each bundle's); without them, the global providers are used (a
no-op unless the host set one).

The native backend emits `invoke_workflow`, `invoke_agent`, `chat` and
`execute_tool` spans. The Mock backend emits `invoke_workflow` and
`invoke_agent` spans, plus a `chat` span for each zero-token mock usage record;
it does not execute tools. Bundle engines use the same telemetry attachment.

| Span | Opened by | `gen_ai.operation.name` |
| --- | --- | --- |
| `invoke_workflow <workflow>` | each `process_run` pass of a run | `invoke_workflow` |
| `invoke_agent <step>` | the step's first tool event or `step_started`, closed by `step_completed` | `invoke_agent` |
| `chat <model>` | each provider call, from its usage record | `chat` |
| `execute_tool <tool>` | `tool_call_requested`, closed by its outcome event; an early denial or failure has only its outcome event | `execute_tool` |

| Local field | Span attribute |
| --- | --- |
| `provider` | `gen_ai.provider.name` |
| `model` | `gen_ai.request.model`, `gen_ai.response.model` |
| `agent_id` | `gen_ai.agent.id` |
| `tokens_in`, `tokens_out` | `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` (absent when not reported) |
| `cache_read_tokens` | `gen_ai.usage.cache_read.input_tokens` |
| `duration_ms` | span duration |
| `success`, `error_type` | span status and `error.type` |
| `run_id`, `step_id`, `attempt` | `multiagent.run_id`, `multiagent.step_id`, `multiagent.attempt` |

Step and tool spans also carry `multiagent.start_event_id` and
`multiagent.end_event_id`, the ids of the `run_events` rows that opened and
closed them, so a trace can be joined to the domain events. Two histograms are
recorded: `gen_ai.client.token.usage` (tokens, split by `gen_ai.token.type`)
and `gen_ai.client.operation.duration` (seconds).

Prompts and responses are never attached to spans. `TELEMETRY_OTEL_CAPTURE_CONTENT=true`
adds `gen_ai.input.messages` and `gen_ai.output.messages` to the `chat` span of a
successful call, truncated to 32,000 characters each. The prompt is the one sent
to the provider, after the egress policy redacted it. It is off by default and
should stay off for sensitive runs. The GenAI conventions are still evolving, so
attribute names may follow later versions.

## Secret gate events

The [secret gate](security/secret-gate.md) records what it held and what a person decided. None of these events,
or any log line of the gate, holds the value of a secret: only the type, a masked sample and a SHA-256 digest of the
text.

Run events (`GET /api/v1/runs/{run_id}/events`):

- `call_paused` with `guard: "secret"` and `waiting_reason: "secret_review"`. Its `request` carries `approval_id`,
  `step_id`, `attempt`, `model_id`, `ordinal`, `retry`, `sha256`, the `destination` (`model_id` and `tier`),
  `secret_types` and `masked_sample` (the finding's type and three characters at each end of a long value, or `***`).
  The run report keeps only `step_id`, `attempt`, `guard`, `waiting_reason`, `model_id` and `ordinal` of this event.
- `secret_approved` and `secret_rejected`, with the same identity fields and no sample. A rejection also adds
  `workflow_failed` of type `SecretReviewRejected`.
- `secret_released`, written when the one approved call goes out, with the `approval_id` it spent.

Team events (`GET /api/v1/team-events`): `user_approval.requested` (kind, destination, origin, secret types, masked
sample and `sha256`), `user_approval.decided` (`approved` or `rejected`, with the actor `user:gui`) and
`user_approval.consumed`. A request is made by `system:approval`.

The usage records of a call that the gate held do not exist, because the provider was not called. Nothing here goes
to a telemetry service, and the OpenTelemetry export does not carry these events.

## Usage settings in the GUI

The token-protected GUI API exposes `GET` and `PUT /gui/api/settings/budget`.
The budget request is a complete `providers` map. Each provider has an `amount`
in USD per UTC calendar month (`null` removes its cap; `0` with `cut` holds paid calls for approval)
and a `mode` of `warn` or `cut`. The response includes `source` (`gui`,
`environment`, or `default`) and the effective budgets. A saved GUI map takes
precedence over all environment budgets; with no saved map the existing budgets
remain in force. Only calls to the named provider count against a GUI budget.
A provider must be one the model catalog knows (the `provider` of its models, which is what the guard compares);
the response lists them as `known_providers`, and any other name is a 422.
`cut` pauses a call for human approval before it exceeds the cap. Changing a
budget does not approve any paused call or allow paid overage.

`GET` and `PUT /gui/api/settings/continuity` expose `mode` (`manual` or
`semi_auto`), `wait_threshold_hours` (1, 3, 5, or 24), and candidate policies
for the three subscription CLIs (`no_ask`, `ask`, or `never`). The response
includes the effective source, the fixed hand-off limit, `automatic`,
`automatic_since` and `saved_mode`. Task policy can still restrict the GUI
policy.

`automatic` is the switch "Keep going by itself with another model or app when
a limit is reached". It is off until a person turns it on in Usage; only the
GUI user (`user:gui`) can set it, and no command, MCP tool or environment value
can. `PUT` accepts an optional `automatic` next to the three keys (left out, the
switch stays as it is); the server stamps `automatic_since` (UTC) when it is
turned on. While it is on, `mode` reads `auto` and `saved_mode` keeps the
`manual` or `semi_auto` choice that returns when it is turned off. With `auto`
the policy hands a task held by a usage limit to the best app marked `no_ask`
at once, without waiting for the threshold, under the same conditions as
`semi_auto`; it never applies to a limit held before `automatic_since`. Each
automatic hand-off records `continuity.handoff` with `by` `policy:auto`, the
limit and the origin and destination apps and models. The route does not change
the integration guard, payment approval, or the hand-off limit.

## Project cost

The project view shows what one project has cost so far, per stage and per task, with
`GET /gui/api/projects/{project_id}/costs` (token-protected, read only; Python:
`services.usage.project_costs(project_id)`). It adds up the `telemetry` rows that
already exist: a row belongs to a project through its run, and to a task through its own
`task_id` or, when it has none, the `task_id` of its run. Nothing new is stored, no model is
called and nothing is estimated here; each call keeps the cost it was given when it was recorded.

```json
{
  "project_id": "snt",
  "cost_is_estimate": true,
  "totals": {
    "records": 4, "failed": 0, "records_without_usage": 1,
    "tokens_in": 3010, "tokens_out": 620, "tokens_total": 3630, "duration_ms": 20,
    "estimated_cost_usd": 0.0201, "cost_known_records": 3, "cost_unknown_records": 1,
    "cost_other_currencies": {}, "cost_complete": false,
    "cost_unknown_reasons": {"no_price_evidence": 1},
    "evidence": [{"model_id": "m-priced", "currency": "USD",
                  "input": {"amount": 2.0, "denominator": 1000000, "unit": "token", "source": "...", "valid_from": "...", "valid_until": "...", "provenance": "..."},
                  "output": {"amount": 6.0, "denominator": 1000000, "unit": "token", "source": "...", "valid_from": "...", "valid_until": "...", "provenance": "..."}}],
    "tokens_partial": true, "cost_state": "partial"
  },
  "stages": [
    {"group_id": "snt-INV", "code": "INV", "title": "Research", "...": "same fields as totals"},
    {"group_id": "snt-CON", "code": "CON", "title": "Content", "...": "same fields as totals"}
  ],
  "tasks": [
    {"task_id": "snt-INV-T01", "group_id": "snt-INV", "title": "Audience study", "...": "same fields as totals"}
  ],
  "activities": {"...": "calls of the project's activities (a group of its own)"},
  "unassigned": {"...": "calls with no task, or a task that is not in the project"}
}
```

- Every group (`totals`, each row of `stages` and `tasks`, `activities`, `unassigned`) has the
  fields of a group of `usage-report`: the token sums, `estimated_cost_usd`, `cost_known_records`,
  `cost_unknown_records` with `cost_unknown_reasons`, `cost_other_currencies` and `evidence`.
  It adds `tokens_partial` (some call reported no tokens, so the token sums are only the reported
  part) and `cost_state`: `complete` (every call has a USD cost), `partial` (some do), `unknown`
  (calls, but no known cost) or `none` (no calls).
- `stages` always lists every stage of the project, in order, with `null` sums where it has no
  calls. `tasks` lists only the tasks that have calls. The stages, `activities` and `unassigned`
  add up to `totals`, so the sum of the stages plus the calls with no task is the project's total.
- Money is the API cost of the framework's own provider calls. Each currency is kept apart
  (`estimated_cost_usd` is USD only) and nothing is converted. A call with no price counts as
  unknown, never as 0, and its tokens still count: that is how a model with no known price shows up.
  The quota of an app's own plan has no cost per call and is not in these rows: executor attempts
  are counted by the app's session (see [Estimated cost](#estimated-cost)), never as API money.
- The summary of the project view shows the totals as two figures (tokens and API cost, with
  «partial» and «No known price» when they apply). The table of stages adds a tokens and cost column in advanced
  mode, and a folded timeline lists each stage's planned start and end and its real closing
  (`planned_start`, `planned_end` and `closed_at` of `GET /gui/api/projects/{id}`). The timeline promises no end date.

## Dashboard guidance

A future local dashboard can use the usage response without reading the
database directly. Useful views include total input/output tokens by day,
duration and failure rate by provider and model, usage by workflow agent and
step, and the percentage of records without provider usage. Keep nullable
values distinct from zero, label costs as estimates, show the number of calls
with an unknown cost next to any cost total, and filter sensitive identifiers
before displaying or exporting them.
