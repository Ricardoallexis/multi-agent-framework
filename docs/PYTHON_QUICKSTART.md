# Direct Python quickstart

`multiagent.bootstrap.build_system` creates the same Core used by the CLI and
API. The current example exercises the three-step social-content workflow with
FakeAdapter. It does not need a server, API key, or provider network request.

## Run from a checkout

After running the repository setup, use:

```powershell
& .\.venv\Scripts\python.exe .\examples\python_quickstart.py
```

On Linux/macOS:

```bash
.venv/bin/python examples/python_quickstart.py
```

The result contains `waiting_human`, `waiting_reason: review`, steps `strategy`,
`create`, and `design`, three artifacts, provider `fake`, and three simulated
LLM calls. Human approval is left to the caller. Without `--local-dir`, the
example deletes its temporary data when it finishes. To retain the run, pass
`--local-dir` with a private directory outside the repository.

## Test a wheel independently

Build both the wheel and source archive from this checkout:

```bash
python -m pip install build
python -m build
```

Install the resulting `dist/*.whl` into a fresh virtual environment. Then
change to a directory outside the checkout and run the example by its absolute
path with the Python executable from that virtual environment. The package
contains the catalog, workflows, prompts, skills, and migrations; this check
must not import `multiagent` from the checkout.

On Windows PowerShell, one local verification sequence is:

```powershell
$sourceRoot = (Get-Location).Path
$probe = Join-Path $env:TEMP 'multiagent-wheel-check'
py -3.12 -m venv $probe
$wheel = (Get-ChildItem .\dist\*.whl | Select-Object -First 1).FullName
& "$probe\Scripts\python.exe" -m pip install $wheel
Push-Location $env:TEMP
try {
    & "$probe\Scripts\python.exe" -m pip check
    & "$probe\Scripts\python.exe" "$sourceRoot\examples\python_quickstart.py"
} finally {
    Pop-Location
}
```

A regular wheel install writes default runtime data under
`~/.multi-agent-framework/`. An editable checkout uses its ignored `.local/`
folder. `LOCAL_DIR` or an explicit `Settings(local_dir=...)` selects a private
writable location. Installed resource files remain read-only definitions.

The base package installs core dependencies. Cloud SDKs are separate extras:
`pip install '.[gemini]'` or `pip install '.[openai]'` when working from the
checkout. The FakeAdapter example needs neither extra.

This example verifies the existing workflow, not a generic AgentDefinition,
Team, or arbitrary workflow API. Configuration and workflow validation remain
separate development tasks.
