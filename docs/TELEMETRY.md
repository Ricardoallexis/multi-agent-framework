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
- context size, elapsed duration, model digest, and estimated cost;
- prompt identity metadata (ID, version, and SHA-256 hash);
- success or failure and the error type.

Token counts are nullable by design. A human-guided step has
`provider: "human"` and no provider token counts. A provider failure is
recorded when possible, including the attempt number. Missing usage is not
converted to zero.

Telemetry writes are best-effort. If a write fails, the engine emits a
`telemetry_failed` run event and continues the workflow step. This prevents
observability from changing workflow availability.

## Reading a run

The Python facade returns records and totals for the complete run, grouped by
agent and step:

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
run, each `agent_id`, and each `step_id`; they include record count, failed
records, records without usage, token sums, duration, and estimated cost.
`tokens_total` is `null` when either token count is unavailable. Costs are
estimates from the configured model catalog, not provider billing data.

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
      "tokens_total": 516
    }
  }
}
```

The actual response also contains the full per-agent and per-step totals.
Failed attempts remain visible in `records` and contribute to `failed`.

## OpenTelemetry GenAI mapping

The current implementation stores local SQLite records rather than emitting
OpenTelemetry spans. If a future exporter is added, these fields map naturally
to the OpenTelemetry GenAI semantic conventions:

| Local field | Future span/event attribute |
| --- | --- |
| `run_id` | workflow or trace correlation ID |
| `step_id`, `attempt` | operation name and attempt attribute |
| `agent_id` | agent identifier |
| `provider`, `model` | system/provider and request model |
| `tokens_in`, `tokens_out` | input and output token usage |
| `duration_ms` | span duration |
| `success`, `error_type` | status and error attributes |

This is a mapping guide, not an active exporter. Any exporter must preserve
the local-only default, avoid prompt or response capture, and follow the
version of the OpenTelemetry GenAI conventions adopted by the project.

## Dashboard guidance

A future local dashboard can use the usage response without reading the
database directly. Useful views include total input/output tokens by day,
duration and failure rate by provider and model, usage by workflow agent and
step, and the percentage of records without provider usage. Keep nullable
values distinct from zero, label costs as estimates, and filter sensitive
identifiers before displaying or exporting them.
