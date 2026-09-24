# Python quickstart — same Core, no server

The current factory is `multiagent.bootstrap.build_system`. It returns the same engine, store, services, contracts, and adapters used by CLI/API. Python callers do not need to start FastAPI or a background worker.

This example exercises the existing **social-content** preset: strategy, create, and design. Research is skipped because web input is not requested. A fully generic three-agent demo and Colab notebook remain roadmap items.

## Install from the checkout

Run these commands from the repository root. Dependencies must currently be installed separately: the baseline wheel does not bundle the required runtime resources or declare runtime dependency metadata. Use the editable checkout until that distribution gap is fixed.

Windows PowerShell:

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
& .\.venv\Scripts\python.exe -m pip install -e . --no-deps --no-build-isolation
& .\.venv\Scripts\python.exe .\examples\python_quickstart.py
```

Linux/macOS:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -e . --no-deps --no-build-isolation
.venv/bin/python examples/python_quickstart.py
```

Python 3.12 and 3.13 are the supported versions. The example was verified on Linux/Python 3.12; the commands above are not a claim that native Windows/macOS or Colab were run in this review.

## Expected result

The JSON summary contains a generated run ID and:

```json
{
  "status": "waiting_human",
  "waiting_reason": "review",
  "steps": ["strategy", "create", "design"],
  "artifact_count": 3,
  "providers": ["fake"],
  "llm_calls": 3,
  "artifacts_retained": false
}
```

FakeAdapter returns deterministic fixture content, not model-generated responses to the topic. The existing LLM budget counter counts these simulated calls; they are not billable API calls. All outputs still pass through the engine's contracts, validation, artifact writer, and persistence. Final human approval is not automatic.

By default, the workspace is temporary and deleted after the summary is produced. Retain artifacts and the database by choosing a private location:

```powershell
& .\.venv\Scripts\python.exe .\examples\python_quickstart.py --local-dir "C:\work\multi-agent-private\public-demo"
```

The example sets its data paths explicitly, ignores `.env` files, clears cloud credentials in its Settings, disables the background worker, and executes only the run it just created. Existing unrelated queued runs are not processed.

## Verify imports outside the checkout

From the repository root in PowerShell:

```powershell
$frameworkRoot = (Get-Location).Path
Push-Location $env:TEMP
try {
    & "$frameworkRoot\.venv\Scripts\python.exe" "$frameworkRoot\examples\python_quickstart.py"
} finally {
    Pop-Location
}
```

The example contains no `sys.path` modification. This verifies an **editable installation**, not a standalone wheel. The source checkout must remain available.

## Use the underlying Python interfaces

The [example source](../examples/python_quickstart.py) shows the complete code. The essential flow is:

```python
from multiagent.bootstrap import build_system
from multiagent.config import Settings
from multiagent.contracts import PipelineMode, SocialPostRequest

system = build_system(Settings(worker_enabled=False), dry_run=True)
run = system.run_service.create_social_post(SocialPostRequest(
    project_name="Example project",
    objective="Create an educational publication",
    topic="Explicit contracts",
    pipeline_mode=PipelineMode.FULL,
    use_brand_context=False,
))
state = system.engine.process_run(run["id"])
# Review state["artifacts"] before explicitly calling:
# system.run_service.approve(run["id"])
```

This shorter snippet uses the normal Settings/environment and persists to the configured workspace. The executable example provides isolated defaults. For private prompts/data and Human Bridge continuation, use the [private implementation recipe](PRIVATE_PRESETS.md).

## Regression gate

```powershell
& .\.venv\Scripts\python.exe -m pytest -q
& .\.venv\Scripts\python.exe .\examples\python_quickstart.py
```

No new runtime abstraction, provider, database migration, contract, or release version is introduced by this example. Standalone packaging is the next implementation slice; the existing public baseline remains `M1-B01-F00-alpha`.
