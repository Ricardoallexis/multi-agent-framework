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

## Saved runs, baselines and regressions

Every execution of a set is saved in the system database, Mock runs included
(their temporary runs are still deleted; the report is kept). The report
carries the saved ID in `meta.evaluation_run_id`. A saved run holds the set
name and version, `set_sha256`, whether it was live, the full fingerprint, the
report, its score, the actor if known, and its creation time. `POST
/api/v1/evaluations/run` accepts an optional top-level `actor`.

Each set name has at most one baseline: a saved run of that same set. Setting
it again replaces the previous one. A run of another set is refused with
`evaluation_set_mismatch` (409).

`compare` checks a candidate run against a base run of the same set; the base
defaults to the baseline. Each case in both runs gets one status:

| Status | Meaning |
| --- | --- |
| `regressed` | It passed and no longer does, or its score fell by more than the threshold |
| `improved` | It failed and now passes, or its score rose by more than the threshold |
| `changed` | Its check results or its fingerprint differ, within the threshold |
| `unchanged` | Nothing differs |

Cases found in only one run are listed in `only_in_base` and `only_in_candidate`
and do not count as regressions. `fingerprint` says what changed: `agent_or_prompt`,
`workflow`, `models` (models and providers), and `set_changed`. `regression` is
`true` when any case regressed. The threshold is the setting
`eval_regression_threshold` (0 to 1, default 0, so any drop counts); a request can
override it. A comparison of two different sets returns `evaluation_set_mismatch`
(409); an unknown run, or a set without a baseline, returns `not_found` (404).

```sh
multiagent eval runs --set example
multiagent eval show evr-0123456789ab
multiagent eval baseline set evr-0123456789ab        # --set NAME, --actor ACTOR
multiagent eval baseline show example
multiagent eval compare evr-ba9876543210 --threshold 0.1   # --base ID
```

These commands read the system database in the CLI process. `compare` prints the
comparison and exits with 3 on a regression, 0 without one, and 1 on an error, so
CI can tell them apart.

| Route | Purpose |
| --- | --- |
| `GET /api/v1/evaluations/runs?set_name=` | List saved runs, newest first (`limit`, default 100) |
| `GET /api/v1/evaluations/runs/{id}` | One saved run with its report and fingerprint |
| `PUT /api/v1/evaluations/baselines/{set_name}` | Body `{run_id, actor}` sets the baseline |
| `GET /api/v1/evaluations/baselines/{set_name}` | Read the baseline |
| `POST /api/v1/evaluations/compare` | Body `{candidate_id, base_id?, threshold?}` |

A regression is not an HTTP error: the comparison answers 200 with `regression: true`.
