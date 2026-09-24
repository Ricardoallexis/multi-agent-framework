# Public presets and private reference implementations

## What works now

RAVC can already exercise the existing social-content workflow using private instructions, a private brand profile, configured providers, and Human Bridge. This is a **limited private reference implementation**, not a completed generic Team/preset system.

Use one checkout of the public framework and a separate private workspace. For example:

| Location | Contents |
| --- | --- |
| `C:\work\multi-agent-framework` | Public source, generic example prompts, workflows, contracts, tests. |
| `C:\work\multi-agent-private\ravc` | Private `.env`, instructions, brand/knowledge inputs, database, runs, and artifacts. |

No real RAVC prompts, credentials, or customer data are included in this repository or review. The public social-content configuration can serve as the initial shared base; a named, reusable RAVC public preset has not yet been extracted.

## Start with existing Python interfaces

Install using the [editable Python quickstart](PYTHON_QUICKSTART.md). Create private inputs outside the checkout. The following is an integration recipe, not an automatic import or publication of private files:

```python
from pathlib import Path

from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import ExecutionMode, PipelineMode, SocialPostRequest

private_root = Path(r"C:\work\multi-agent-private\ravc")
settings = Settings(
    _env_file=private_root / ".env",
    local_dir=private_root,
    data_dir=private_root / "data",
    db_path=private_root / "data" / "multiagent.db",
    runs_dir=private_root / "data" / "runs",
    assets_dir=private_root / "data" / "assets",
    worker_enabled=False,
)
system = build_system(settings)
request = SocialPostRequest(
    project_name="Private reference project",
    objective="Prepare an educational publication for human review",
    topic="A topic selected by the project owner",
    instructions=(private_root / "instructions.md").read_text(encoding="utf-8"),
    execution_mode=ExecutionMode.HUMAN_GUIDED,
    pipeline_mode=PipelineMode.FULL,
    use_brand_context=False,
)
run = system.run_service.create_social_post(request)
state = system.engine.process_run(run["id"])
pending = system.run_service.human_next(run["id"])
# pending["prompt"] is private. The user chooses where to execute it.
```

The public prompts already include `instructions`. The current field is limited to 4,000 characters and applies at request scope. This is enough to try private differences without cloning a team or editing a public prompt; it is not a knowledge loader or per-agent overlay. `use_brand_context=False` makes this recipe independent of an active brand profile. Enable it only after intentionally saving/activating the appropriate private profile through the existing BrandService/Store interfaces.

Continue a Human Bridge step using the same persisted run and workspace:

```python
from multiagent.contracts import HumanStepSubmission

state = system.run_service.human_submit(
    run["id"],
    HumanStepSubmission(
        raw_response=(private_root / "response.json").read_text(encoding="utf-8"),
        provider="user_selected_interface",
        model="user_reported_model",
        # Optional: prompt_used records the user's edited prompt.
    ),
)
if state["status"] == "queued":
    state = system.engine.process_run(run["id"])
```

An invalid response keeps the pending request for correction. Intermediate external execution requeues the next step; the bundled final review remains `waiting_human/review` until an explicit approve/revise/reject action. Review the returned status before acting. Reuse the same public definitions while a run is suspended: the current runtime does not snapshot the YAML.

For a zero-key automated check, use `build_system(settings, dry_run=True)` with automatic execution. For actual local/API execution, configure the private environment and execution mode deliberately; no real provider calls were tested in this review.

`LOCAL_DIR` changes data placement only. The default `.env` lookup still points inside the checkout; the explicit `_env_file` argument above selects the external file. Keep distinct private implementations in distinct databases for now, because active brand selection is global within a database.

## Target composition — not implemented

Precedence is intended to be:

`framework defaults < public preset < private/local overlay < runtime overrides`

| Layer | Shareable or private content |
| --- | --- |
| Framework defaults | Generic runtime defaults, contract/extension mechanisms, safe execution defaults. |
| Public preset | Definition IDs, roles/purposes, capabilities, relationships, workflow/entrypoint, contract references, tool requirements, generic prompts/policies, mock fixtures, README. |
| Private overlay | Private prompt fragments, brand/knowledge references, organization rules, provider selection, private file references. Credentials remain runtime secrets, not values exported with the preset. |
| Runtime overrides | Task inputs and explicit execution choices within enforced policy boundaries. |

A shared `finance-team/` or `research-team/` should work the same way. The runtime must not contain an RAVC-specific loader or namespace exception.

Before implementing composition, specify stable IDs, deterministic merges, list replacement, permitted nulls, explicit deletions, reference resolution, policy constraints, and validation of the final result. Snapshot or hash the resolved configuration for reproducible resume. The [decision record](ARCHITECTURE_DECISIONS.md) contains the proposed rules; they are not current Settings behavior.

For prompts, prefer a generic base plus an explicitly appended private fragment when sufficient. Permit an intentional replacement when necessary without maintaining a copied public team. Do not rely on a sanitizer to transform a confidential tree into a publishable preset. A later public-preset validator may detect suspicious paths, secret references, and missing inputs as an additional check.

## Incremental private validation

1. Run the public Mock example and the existing tests against the selected Core commit.
2. In an external workspace, prepare a small private task using existing request instructions and Human Bridge; confirm invalid/valid responses, final review, and artifact provenance.
3. Repeat with a deliberately selected real/local adapter if needed. Record the commit and pass/fail observations privately; do not upload prompts, responses, or credentials with a public regression report.
4. Identify the first private customization that cannot be expressed through existing inputs. Use that concrete case to choose a minimal prompt/config composition slice.
5. After meaningful Core changes, rerun both checks. A green public Mock test does not imply a successful private workload or equivalent real-model quality.

The private side requires owner-supplied configuration and execution evidence. This review completes the public verification and documents the private path; it does not claim that the actual RAVC workflow has been validated.
