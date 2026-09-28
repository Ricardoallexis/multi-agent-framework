# Stage 0 run console

Stage 0 is a local operator interface for declared workflows. It uses the public
HTTP API at `/api/v1` for every operation. Runs, artifacts, and workflow state
belong to the backend; the UI adds no persistence or orchestration layer.

This guide describes the implementation in this checkout. It does not imply
that Stage 0 has been published in the release named in the README.

## Install and start

Use the [repository installation instructions](../README.md#installation)
with Python 3.12 or 3.13. The UI uses static HTML and JavaScript modules; there is
no separate frontend build or Node installation.

From the repository root on Windows:

```powershell
.\run_ui_windows.bat
```

With the project environment activated on Linux/macOS (or Windows):

```bash
multiagent ui
```

The launcher starts the API and its worker (unless `WORKER_ENABLED=false`),
prints the URL, and attempts to open your browser. Defaults are
`http://127.0.0.1:8000/ui/`; `/ui` redirects to `/ui/`. Keep the terminal open.
If the browser opens before the server is ready, reload the printed URL.

The launcher forces `MOCK_MODE=true` unless you pass `--real`. Mock uses
synthetic contract fixtures and needs no model API keys or model inference.
The health request can still probe configured provider availability, including
Ollama; Mock does not mean every network check is disabled. Runs and artifacts
are saved even in Mock mode.

| Option | Behavior |
| --- | --- |
| `--no-browser` | Print the URL and start the server without opening a browser. |
| `--port 8010` | Use another port; otherwise use `APP_PORT` (default 8000). |
| `--bundle NAME=FOLDER` | Register a trusted definition bundle; repeat for multiple bundles. |
| `--real` | Leave `MOCK_MODE` to your settings instead of forcing it on. |

For example, on Windows:

```powershell
.\run_ui_windows.bat --port 8010 --no-browser
```

If a server already occupies the port, stop it or choose another port. Do not
start `run_backend.bat` and the UI launcher on the same port. An existing API
server from this checkout also serves `/ui/`, but its own settings determine
Mock mode; opening that URL does not enable Mock.

### Provider execution

Configure the desired providers as described in [API.md](API.md) and
[OLLAMA.md](OLLAMA.md). Set `MOCK_MODE=false` and launch with `--real`:

```powershell
$env:MOCK_MODE = "false"
.\run_ui_windows.bat --real
```

```bash
MOCK_MODE=false multiagent ui --real
```

The launcher prints a cost warning for `--real`, but that option alone does
not override an existing `MOCK_MODE=true`. Check the browser's server-mode
banner, which reads the API's actual `dry_run` value. A non-Mock server requires
the confirmation checkbox before creating a Run. Execution mode (`auto`,
`local`, `cloud`, or `human_guided`) is a separate per-Run choice.

Keep the default loopback host (`APP_HOST=127.0.0.1`) for local operation.
Host and browser-origin checks protect the local API; they are not user
login or authorization for network deployment. Open the UI from the server's
printed URL, not by opening `index.html` from disk or another web server.

## Create a Run

1. Wait for the mode banner and workflow catalog to load.
2. Enter a project name (2–120 characters).
3. Choose **Workflow source** and **Workflow**; wait for its details to load.
4. Use **Add input** to supply the workflow's text values as key/value pairs.
   Input names begin with an ASCII letter and contain letters, digits, or
   underscores. Names must be unique. `previous_output`, `revision_feedback`,
   and names ending in `_output` are reserved.
5. Choose an execution mode. Confirm provider use if the server is non-Mock.
6. Click **Create run**. The returned Run is selected for monitoring and human
   actions. Save its ID if you need to inspect it later through the API or CLI.

The UI sends a generic Run request, not the specialized social-post form. It
does not generate typed fields or discover required inputs from a schema; use
the selected workflow's input contract and prompts. Values are strings, not
JSON objects or booleans. Server validation errors appear in the form.

Submission is disabled while the request is pending. An unchanged retry after
an error reuses its idempotency key within this page. After a successful
creation, clicking **Create run** again creates another Run. Reloading the page
also loses the key, so inspect the server before repeating an uncertain request.

### Checkout example with a registered bundle

The repository includes a trusted synthetic fixture with `SAMPLE_OUTPUTS`:

```powershell
.\run_ui_windows.bat --bundle spec=tests/fixtures/bundles/spec_review
```

Or, from the repository root in an activated environment:

```bash
multiagent ui --bundle spec=tests/fixtures/bundles/spec_review
```

Select **Bundle: spec**, workflow **spec_review**, project **UI demo**, and
input `spec_text` with value `The service must export a report.` Keep execution
mode `auto` for the Mock path. The declared workflow has review checkpoints
after risk assessment and the test plan; inspect each result before approving.
Choose `human_guided` on a new Run to use the Human Bridge instead.

This fixture is part of the source checkout, not the installed wheel. Custom
bundles need their own Mock sample outputs. Bundle folders are server-side
trusted code/configuration; register only folders you trust. The browser can
select registered names but cannot upload or load a folder. See
[API.md](API.md) for the bundle contract.

## Monitor state and artifacts

The Run panel shows the API state, step cursor, waiting reason/attempt,
cancellation request, events, and artifacts. A cursor is a position in the
workflow, not a completion percentage. `running` is a stored Run state, not
proof that a worker process is currently alive.

The view polls approximately every two seconds while visible. Polling pauses
when the tab is hidden and refreshes on return. It stops for `completed`,
`failed`, `cancelled`, `rejected`, and `interrupted`. Use **Refresh run** or
**Refresh artifacts** for a manual update.

Artifacts appear as formatted data with step/attempt metadata. Recognized
local paths are omitted from this view. There is no original-file download.
Events and timestamps are those returned by the API.

## Human actions

Controls follow the selected Run's current API state:

| State / waiting reason | Available actions |
| --- | --- |
| `waiting_human` / `review` | Approve, request changes with non-empty feedback, regenerate, reject, cancel. |
| `waiting_human` / `budget_exhausted` | Approve the existing result, reject, or cancel. |
| `waiting_human` / `execute_step` or `research` | Human Bridge and cancel. |
| `queued`, `running`, `paused`, `interrupted` | Cancel when cancellation has not already been requested. |
| `completed`, `failed`, `cancelled`, `rejected` | Inspect results; no mutation controls. |
| Other states | Refresh and inspect; no generic resume action. |

Review the artifact before approving. An intermediate approval lets execution
continue; the final review can finish the Run. **Request changes** requires
feedback; **Regenerate** requests another attempt. Rejecting or cancelling
requires a confirmation step. For a running Run, cancellation may first set
`cancel_requested`; wait for the API to report `cancelled`. Closing the tab is
not cancellation.

### Human Bridge

1. Read the pending step, attempt, and expected output contract.
2. Use **Copy prompt**, then execute it in your chosen external model or tool.
   If clipboard access fails, the prompt is selected for manual copying.
3. Paste the complete answer into **Response**. Fill in **Provider** and,
   optionally, **Model**, then click **Submit response**.
4. The backend validates the answer. On `human_submission_invalid`, correct
   the retained response and submit again. Successful submission updates the Run.

The browser does not contact that external model for you. Response drafts live
only in this page's memory; they survive ordinary unchanged-state refreshes and
validation failures, but not a page reload or browser close.

## Errors, recovery, and stopping

- **API unavailable:** check the server terminal and printed URL, then reload.
  Creation remains disabled if initialization cannot load the API/catalog.
- **HTTP 400 `invalid_host` / 403 `forbidden_origin`:** use the local server URL
  and the same origin for UI and API. Check the configured allowed host if it
  was changed; do not disable the guards to work around a different-origin UI.
- **Validation errors:** read the code and available details, correct the
  inputs/response, and retry. Invalid workflow definitions need a definition fix.
- **HTTP 409 during human actions:** the panel refreshes the current Run. Review
  the updated state before acting again.
- **Network error or 5xx after an action:** the result may be unknown. Refresh
  and inspect the Run before retrying; human actions are not automatically retried.
- **A Run remains queued:** check whether the server worker is enabled. The UI
  cannot start a worker or recover execution itself.
- **Missing UI/module:** confirm this checkout or installation includes the UI
  assets. An installation without `index.html` can still serve the API.

Stop the server with **Ctrl+C** in its terminal. Closing the browser leaves the
server running. Stopping the server is not a request to cancel all Runs. Runtime
data remains in the configured [private workspace](LOCAL_WORKSPACE.md).

There is no Run history picker or reopen-by-ID control in Stage 0. Reloading
loses the selection. To inspect a saved Run, use the API or CLI, for example
`multiagent status RUN_ID` and `multiagent artifacts RUN_ID` while its server is
running. With a custom port, set the CLI's `API_BASE_URL` accordingly.

## Untrusted content and browser policy

Model responses, artifacts, event payloads, errors, and user input are untrusted
content. The UI displays them using text nodes or form values; it does not
interpret their HTML, execute scripts, or turn their URLs into active links.
Any future rich Markdown or HTML renderer must sanitize its output with a
strict allowlist before inserting it into the document. Keep script execution,
event-handler attributes, and unsafe URL schemes disabled.

The server sends a Content Security Policy on `/ui` and `/ui/` resources,
including redirects and ordinary error responses. Scripts, styles, API requests,
images, and fonts are restricted to the same origin. Inline scripts/styles and
`eval` are not allowed. Object embeds, frames, framing the UI, and document base
URLs are disabled. The UI also sends `X-Content-Type-Options: nosniff` and
`Referrer-Policy: no-referrer`. Its bootstrap is an external JavaScript module.

This policy supplements safe rendering; it does not sanitize content or provide
authentication. API and interactive API documentation routes retain their own
behavior. New UI modules must preserve these restrictions rather than relaxing
the policy with `unsafe-inline` or `unsafe-eval`.

## Scope and verification

Stage 0 supports one operator's workflow loop. It has no cross-tab coordination,
generic resume button, bundle uploads, agent/plan/tool editors, typed workflow
forms, or event streaming. UI state never replaces backend state.

Commands and controls in this guide were checked against source. This
documentation change did not execute the framework, browser tests, builds, or
pytest. Acceptance testing and integration are separate from documenting the UI.
