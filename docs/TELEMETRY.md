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

## Dashboard guidance

A future local dashboard can use the usage response without reading the
database directly. Useful views include total input/output tokens by day,
duration and failure rate by provider and model, usage by workflow agent and
step, and the percentage of records without provider usage. Keep nullable
values distinct from zero, label costs as estimates, show the number of calls
with an unknown cost next to any cost total, and filter sensitive identifiers
before displaying or exporting them.
