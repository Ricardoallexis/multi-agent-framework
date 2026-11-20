# Bundle scan

A registered bundle can contain a `contracts.py`, which the framework imports and runs as code.
Before it does, the framework scans a bundle that lives outside its own folder with SkillSpector,
if you have it installed. The scan shows you what it found and checks the bundle before any of its
code loads. It is not a sandbox: a bundle you decide to load still runs with your permissions.

## What is scanned

- Only **external** bundles: folders registered with `--bundle NAME=FOLDER` that are not inside the
  framework's own checkout. The bundles shipped with the repository are not scanned
  (`state: "exempt"`).
- Each result is tied to a **fingerprint**: a SHA-256 over the bundle's relative paths, sizes and
  contents (`__pycache__` is left out). Any edit gives a new fingerprint and the bundle is scanned
  again. Results are cached in `.local/bundle-scans/` under the fingerprint.
- The check runs before anything imports the bundle: `bundle-validate`, listing its workflows or
  agents, and queuing a run all go through it.

## SkillSpector is optional

- The framework **detects** SkillSpector. It never installs it and never updates it. Installing it
  is your decision.
- Without SkillSpector, or with `bundle_scan = off`, the bundle loads and its status carries a
  warning («Sin escanear: SkillSpector no está»). Nothing is blocked.
- The framework looks for `%APPDATA%\uv\tools\skillspector\Scripts\python.exe`, or for the
  interpreter named in the `skillspector_python` setting.
- On the maintainer's Windows PC, installing the tool with uv needed `--system-certs`, because uv
  did not trust the system's certificates otherwise. That is a note about one machine, not a step
  the framework performs. Follow SkillSpector's own instructions.

### Why the signed `python.exe` and never uv's `.exe`

- The scan runs the tool environment's own `python.exe` with
  `-I -c "from skillspector.cli_entry import app; app()"`. It never launches `skillspector.exe` or
  any other uv launcher.
- An antivirus can hold or freeze an unsigned executable the first time it runs. The tool's
  `python.exe` is a normal, signed Python interpreter.
- Before every scan the framework checks that file. It must be a regular file (not a link) with a
  valid Authenticode signature whose signer is the Python Software Foundation. If it is not, or the
  check cannot run, nothing is launched.
- The command is `scan FOLDER --no-llm --format json --recursive --max-skills N`, started without a
  shell, with a scrubbed environment, in a temporary working folder and with a time limit
  (`bundle_scan_timeout_seconds`). The whole process tree is stopped on timeout.

## The OSV.dev query

A local SkillSpector scan looks up the bundle's dependencies on `api.osv.dev`, and the tool cannot
be told not to.

- **What leaves the computer:** package names and versions found in the bundle's manifests
  (`requirements*.txt`, `pyproject.toml`, `package.json`). Not your files and not the bundle's code.
- **Your permission:** the scan does not start until you approve the query with one click in the
  GUI («Aprobaciones», card «Consulta a api.osv.dev»: «Permitir la consulta» or «No permitir»). The
  card lists the manifests. The approval belongs to that bundle and that fingerprint, is used once
  and is never remembered. Only the GUI can grant it: the API, the CLI and agents cannot.
- If you decline, the bundle is `not_analyzed` with the reason `osv_rejected`.
- No framework test, worker or CI job runs SkillSpector or contacts OSV.dev. They use a fake runner
  and synthetic reports.

## What the result means

| State | Meaning | Loading |
| --- | --- | --- |
| `safe` | SkillSpector's verdict is `SAFE` and it covered 100 % of every folder. | Loads. |
| `caution` | Verdict `CAUTION` with full coverage. Review the findings. | Loads. |
| `do_not_install` | Verdict `DO_NOT_INSTALL` with full coverage. | Needs your click on «Cargar este bundle». |
| `not_analyzed` | The tool gave no usable full-coverage verdict. See `reason`. | Needs your click on «Cargar este bundle». |
| `not_installed` | SkillSpector or its interpreter was not found. | Loads, with a warning. |
| `off` | `bundle_scan = off`. | Loads, with a warning. |

The status route also reports `unscanned` (not scanned yet), `pending` (waiting for your OSV
approval) and `running`.

An interpreter without a valid signature never runs; the bundle shows as `not_analyzed` with the reason
`signature_not_valid` (the scan module itself calls that case `unsigned_interpreter`).

`not_analyzed` comes with one of these reasons: `osv_rejected`, `signature_not_valid`, `osv_not_approved`,
`coverage_below_100`, `coverage_unknown`, `verdict_not_understood`, `report_not_json`, `timeout`,
`output_truncated`, `exit_code_N`, `launch_failed`, `invalid_folder` or `scan_failed`.

### Coverage

A verdict only counts when every folder was fully analyzed. If any folder is below 100 %, or its
coverage is unknown, the whole result becomes `not_analyzed`, whatever verdict the tool gave. The GUI
lists each folder with its percentage and marks the ones left unanalyzed.

### Findings and false positives

The scan runs with `--no-llm`: SkillSpector looks for words and patterns, not for meaning. Many
high findings are false positives, for example a prompt that mentions «password» or a helper that
calls `subprocess`. The status keeps the 20 most severe findings, and the GUI shows the first 10 as
plain text with level, rule, file and line. Read the top ones before you decide. A clean report is
not proof of safety, and it does not replace reading code you did not write.

## Loading after the scan

When the state needs your click, a request that would load the bundle answers `409`:

| Code | Meaning | `details` |
| --- | --- | --- |
| `bundle_scan_pending` | The OSV query waits for your approval. | `approval_id`, `location` (`/gui/`) |
| `bundle_scan_running` | A scan is in progress (one at a time). Retry shortly. | `bundle` |
| `bundle_scan_blocked` | The verdict needs your approval to load. | `approval_id`, `location`, `verdict`, `findings`, `coverage` |

Open «Aprobaciones» in the GUI, decide, and repeat the request. The load approval is bound to that
fingerprint and is used once; an edited bundle is scanned again. Nothing imports the bundle until
then, and its `contracts.py` always runs from source, never from `__pycache__`.

## Checking the status

Reading the status never starts a scan and never asks for an approval:

- `GET /api/v1/bundles/{name}/scan` and `GET /gui/api/bundles/{name}/scan`
- `multiagent bundle-scan NAME`

See [`API.md`](../API.md#bundle-scan-status) for the fields and
[`GUI.md`](../GUI.md#bundle-scans-in-approvals) for the cards.

## Settings

| Setting (environment name) | Default | Purpose |
| --- | --- | --- |
| `bundle_scan` (`BUNDLE_SCAN`) | `auto` | `auto` scans when the signed interpreter is found; `off` never scans and never asks. |
| `skillspector_python` (`SKILLSPECTOR_PYTHON`) | empty | Absolute path of another installation's `python.exe`. Empty means the uv default. It must still be signed by the Python Software Foundation. |
| `bundle_scan_timeout_seconds` (`BUNDLE_SCAN_TIMEOUT_SECONDS`) | `300` | Time limit of one scan, from 10 to 3600 seconds. |

A Mock server does not look for your installed SkillSpector unless `skillspector_python` is set.

## Limits

- The scan shows and checks a bundle before it loads. It does not sandbox it, and a bundle you load
  runs with your permissions.
- There are no private overlays, and no install, update or removal of bundles.
- The scan reads files. It does not watch what the bundle does at run time.
- The test with the real SkillSpector is done by the user at the end. No agent runs it.
