# Evaluation sets

Evaluation sets run deterministic checks against workflow runs or one Mock step.
The same JSON report is returned by the Python service, CLI, and HTTP API.

## Set format

```yaml
name: example
version: "1"
target: {kind: workflow, id: social_post}
cases:
  - id: first
    inputs: {topic: Gardens, platform: LinkedIn, audience: Neighbors}
    checks:
      - {type: contract_valid}
      - {type: field_present, field: title}
```

`target.kind` is `workflow`, `agent`, or `prompt`. `target.id` identifies the
workflow, catalog agent, or prompt. A prompt also requires its numeric
`target.version`. A workflow set may omit `target` and give each case a
`workflow` instead; this keeps older sets valid. An explicit workflow target
applies to every case. Case IDs must be unique. Inputs are template variables.

An agent or prompt target uses an existing workflow step that names it. The
step supplies the prompt, skills, output contract, and semantic validation.
The step is chosen from the first workflow ID and step order that match. These
targets use the Mock adapter, with no saved run. A workflow target goes through
the regular run engine and worker.

Examples: [workflow](../tests/evals/workflow.yaml),
[agent](../tests/evals/agent.yaml), and [prompt](../tests/evals/prompt.yaml).
The [legacy set](../tests/evals/example.yaml) has no target.

## Checks

Checks inspect the last artifact by default. Set `step` to inspect a named
workflow step. `field` is a dotted path into the structured output.

| Type | Parameters | Result |
| --- | --- | --- |
| `contract_valid` | — | Output satisfies its declared contract |
| `field_present` | `field` | Field exists |
| `equals` | `field`, `expected` | Values are equal |
| `contains` | `field`, `expected` | Value contains expected value |
| `not_contains` | `field`, `expected` | Value does not contain expected value |
| `number_between` | `field`, `min` and/or `max` | Number is within inclusive bounds |
| `max_length` | `field`, `max_length` | Value length does not exceed limit |

The report gives each check's result, each case's score, and an aggregate
score. `meta.statuses` records run states. Live reports include `meta.run_ids`
to link cases to persistent runs. Mock reports omit run IDs because their
temporary database is deleted after the evaluation. `meta.fingerprint` has the
SHA-256 of the canonical set,
the workflow definitions by case or agent configuration or prompt source
(including prompt ID and version), and the provider and model selected for
each case. Run IDs vary between executions; the scored results and fingerprint
remain comparable.

## Mock and live execution

Mock is the default, even when the application normally uses a configured
provider. It uses fixture outputs and makes no external model call. Every Mock
evaluation uses a new temporary data directory with its own database, runs,
and assets. It leaves the system's queued runs and run history untouched.
`--live` sends the set to the running server, where its worker executes workflow
targets through the regular run engine, with egress, budget, and call limits.
Live runs remain visible in run history. A server without a running worker
returns `eval_live_worker_unavailable` (409) before creating a run. Each case
waits up to `eval_live_timeout_seconds` (default 900); on expiry it reports
`timeout` and leaves the run in its current state. Live agent and prompt targets
return `eval_live_target_unsupported`, with field path `live`.

```sh
multiagent eval run tests/evals/workflow.yaml --format json
multiagent eval run tests/evals/agent.yaml --format markdown
multiagent eval run tests/evals/workflow.yaml --live
```

```python
from multiagent.evaluation import load_eval_set
from multiagent.services import ApplicationServices

services = ApplicationServices.create()
report = services.evaluation.run(load_eval_set("tests/evals/prompt.yaml"))
```

```sh
curl -X POST http://localhost:8000/api/v1/evaluations/run \
  -H 'Content-Type: application/json' \
  -d '{"name":"example","version":"1","target":{"kind":"agent","id":"creator"},"cases":[{"id":"create","checks":[{"type":"contract_valid"}]}]}'
```

Send the set as JSON in the request body, with optional top-level `live`.
The server never reads a path supplied by the client. Invalid sets return a
structured `code`, `message`, and `details.errors[].loc` field path through all
three interfaces.
