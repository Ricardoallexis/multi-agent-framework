# Software development team (example bundle)

A copyable example of a small software development team as a **definition bundle**: seven
roles that work one after another on a feature request, with a structured output contract per
step, two points where a human reviews the work, and sample outputs so it runs in Mock mode
with no API keys and no model calls.

The example is generic. It is not the team that builds this project, and it uses no private
files, local paths, or provider credentials.

## What this example shows today

- A declared, deterministic workflow: the steps, their order, and their agents live in YAML.
- One agent per role, each with its own prompt and a Pydantic output contract.
- Each step reads the outputs of the earlier steps through generic placeholders.
- Human review after the design and after the final review (approve, or ask for changes).
- Validation before anything runs, and a full run in Mock mode with the bundled sample outputs.

## What it does not show yet

Roadmap items such as teams defined as data, delegation between agents, planning, and generated
teams are **not** part of this example. Here the order of the steps is fixed by the workflow,
and no agent hands work to another on its own. When those capabilities arrive, this bundle stays
valid as a simple sequential team.

## Files

```text
software_development/
  workflows/software_development.yaml   the seven steps, their agents, prompts and contracts
  catalog/agents.yaml                   the seven roles
  catalog/models.yaml                   one local model entry (unused in Mock mode)
  prompts/dev/<step>.v1.md              one prompt per step
  contracts.py                          output contracts and SAMPLE_OUTPUTS for Mock runs
```

Only `workflows/` is required by the bundle format. The others are included so the folder is
self-contained.

## The team and the flow

| # | Step | Role (agent) | Output contract | Human review after |
| --- | --- | --- | --- | --- |
| 1 | `define_scope` | Product manager (`product_manager`) | `ProductBrief` | no |
| 2 | `design_solution` | Architect (`architect`) | `TechnicalDesign` | **yes** |
| 3 | `plan_backend` | Backend engineer (`backend_engineer`) | `BackendPlan` | no |
| 4 | `plan_frontend` | Frontend engineer (`frontend_engineer`) | `FrontendPlan` | no |
| 5 | `plan_tests` | QA engineer (`qa_engineer`) | `TestStrategy` | no |
| 6 | `draft_documentation` | Technical writer (`technical_writer`) | `DocumentationPlan` | no |
| 7 | `review_delivery` | Reviewer (`reviewer`) | `ReviewReport` | **yes** |

The only input is `feature_request`, the text of the feature to build. Every later prompt
receives the outputs of the earlier steps it needs (for example `{define_scope_output}`).

## Configuration: raise the model-call limit

The workflow makes one model call per step, so it needs **at least 7 calls**. The default limit
is `MAX_LLM_CALLS=4`. With the default, the run stops after four steps with a
`budget_exhausted` event and can still end as `completed`, so the last three steps (tests,
documentation, and the final review) never run. Set the limit before you start:

```powershell
$env:MAX_LLM_CALLS = "7"
```

```bash
export MAX_LLM_CALLS=7
```

You can also set `MAX_LLM_CALLS=7` in your local `.env` file. Requesting changes at a review
runs steps again and uses more calls, so a higher value such as `10` leaves room for that.

## Run it in Mock mode

Requirements: Python 3.12 to 3.14 and this repository installed
(`python -m pip install -e .` inside a virtual environment).

### With the browser console

From the repository root, with `MAX_LLM_CALLS` set as above:

```text
multiagent ui --bundle team=examples/teams/software_development
```

Mock mode is the default for `multiagent ui`. In the console choose **Bundle: team**, workflow
`software_development`, give the project a name, and enter a `feature_request` such as
"Let users reset a forgotten password by email". Approve the run at the first review
(after `design_solution`) and again at the second (after `review_delivery`).

### With Python

```python
from pathlib import Path

from multiagent.config import Settings
from multiagent.run_request import RunRequest
from multiagent.services import ApplicationServices

settings = Settings(_env_file=None, local_dir=Path("demo_workspace"), worker_enabled=False, max_llm_calls=7)
services = ApplicationServices.create(
    settings, bundles={"team": Path("examples/teams/software_development")}, dry_run=True,
)
run = services.runs.create(RunRequest(
    project_name="Password reset",
    workflow_id="software_development",
    inputs={"feature_request": "Let users reset a forgotten password by email."},
), bundle="team")

worker = services.runs._bundle_runtime("team").worker
worker.process_once()                        # runs steps 1-2, then waits for review
print(services.runs.get(run["id"])["status"])   # waiting_human
services.runs.approve(run["id"])
worker.process_once()                        # runs steps 3-7, then waits for review
services.runs.approve(run["id"])
print(services.runs.get(run["id"])["status"])   # completed
services.runs.close()
```

`worker.process_once()` is used here because the example turns the background worker off; a
server (`multiagent ui`) processes runs on its own.

## Expected result

- The run pauses twice: with status `waiting_human` after `design_solution` and after
  `review_delivery`.
- Seven `step_completed` events and two `human_approved` events, no `budget_exhausted` event,
  and the final status `completed`.
- One artifact per step. In Mock mode the content comes from `SAMPLE_OUTPUTS` in
  `contracts.py`: a password-reset feature with two stories, a small design, backend and frontend
  task lists, two acceptance tests, a documentation plan, and an approving review.

## Copy it to your own project

1. Copy the `software_development/` folder anywhere you like. It has no dependency on this
   repository's other files.
2. Rename the folder and edit `workflows/software_development.yaml` (the workflow `id`),
   `catalog/agents.yaml`, `prompts/dev/*.md`, and `contracts.py` for your own roles and outputs.
   Keep every step's `contract` in `contracts.py` and give each contract a sample in
   `SAMPLE_OUTPUTS` so Mock runs keep working.
3. Register your copy with `multiagent ui --bundle NAME=YOUR_FOLDER`, or pass it to
   `ApplicationServices.create(..., bundles={"NAME": Path("YOUR_FOLDER")})`.
4. To use real models, choose entries from your model catalog in each step's
   `preferred_model` and start the console with `--real`. Provider calls may cost money.

`contracts.py` is imported as code: load only bundles you trust. See
[the bundle contract](../../../docs/API.md) for the loading rules and error codes.
