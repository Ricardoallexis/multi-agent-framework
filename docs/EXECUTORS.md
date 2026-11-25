# Executor applications

The framework can run tasks through the Claude, Codex, and Copilot command-line adapters, and through the Antigravity
CLI (agy) under supervision (see [Antigravity (agy), supervised](#antigravity-agy-supervised)). These adapters share the
executor service and process runner, but each adapter builds its own command and reads that app's structured output.
They are declared **disabled by default**. A person turns one on with **Activar** in the GUI
(see [Choosing and launching from the GUI](#choosing-and-launching-from-the-gui)), or by hand in the advanced mode
(see [Turning an executor on by hand](#turning-an-executor-on-by-hand)). The catalog keeps every executor off.

## Configuration and launch

`ExecutorServices.run_task` accepts an explicit model and effort, or can take them from the selected assignment
profile's routing decision. Explicit caller values take precedence. A profile value is sent only when the executor's
catalog evidence declares support for it. Unknown support does not become an assertion of support: Claude effort is
currently unknown and is not sent from a profile. A value explicitly supplied by a caller remains visible in the
attempt record, while an adapter may report that it did not use that value.

**Copilot needs a model.** A Copilot CLI launch or resume without a model is refused with `model_required` before any
command line or prompt file is built, so Copilot never falls back to its own default model (which can be a Claude
model). A resume uses the model recorded for the session, and is refused when none is recorded. The other refusals
stay: a Claude model is `claude_model_rejected`, a model that is not a plain id is `invalid_model`, and the privacy
check still runs. Pass `model`, or let the assignment profile's routing decision supply one. The framework sets no
spending cap on a Copilot run (`--max-ai-credits` is not used).

The service uses read permission by default. A task with a file scope may receive write permission inside its task
work folder. The folder comes from the task/executor configuration or the framework's task work-folder root; invalid
folders are refused. The adapters use a fixed prompt template and pass the canonical task envelope through stdin or a
framework-created prompt file.

The process runner uses an argument list with `shell=False`, resolves a program from absolute `PATH` entries, removes
Claude/Anthropic variables from the child environment, sets `NoDefaultCurrentDirectoryInExePath=1`, and enforces a
timeout and bounded output capture. Free-form prompt text is kept out of arguments and command records. A timeout or
explicit cancellation stops the process tree.

`CliTransport` is a common session interface around these headless adapters. It accepts a validated
`ExecutorLaunchRequest` with a task envelope; its `prompt` must match the adapter's rendering of that envelope.
The first attempt calls `launch`, and another attempt calls `resume` only with the session id returned by the
framework launch. Antigravity still refuses resume. The wrapper returns the adapter's original run result,
including its command record, usage, limit and failure details. It emits bounded usage and end-of-turn status
after the adapter finishes. The current CLI adapters do not provide live events or interactive permission
requests; permissions remain fixed before launch. This interface does not change the CLI commands or offer
free-form chat.

## Choosing and launching from the GUI

«Nueva sesión» in the [operator GUI](GUI.md#views) shows which executors are connected and can launch one, always
one launch at a time.

- **Connected.** The picker reads the connection status (`multiagent connections`, `GET /api/v1/connections`,
  `GET /gui/api/connections`), which calls no model. For each executor it gives whether the app is installed, its
  version, its session, the enabled state, the quota the usage plans report, and the one step that is missing:
  `install`, `session` or `enable`. An executor is selectable only when it is enabled and has no reason. The status is
  cached and checked one row at a time.
- **Copilot is selectable by its cache.** Its session cannot be checked by calling a model. When its local quota cache
  of the current month is valid, the row is `verification: verified`, the session is signed in and the executor is
  eligible; otherwise the row keeps `session_unverifiable`. A balance of zero or less is «límite alcanzado», even when
  paid overage is allowed. Only an allow-list of quota fields is read from the cache and nothing else is read or stored
  (see [Limits in the GUI](GUI.md#limits-sin-comprobar-and-copilots-cache)). agy has no session check other than its
  `/usage` quota reading.
- **Confirmation.** The GUI shows the executor, model, effort, remaining quota and, for agy, its notice. Only
  **Aprobar y lanzar** sends `POST /gui/api/executors/launch` with `confirmed: true`; without it the route answers
  `409 confirmation_required`. The route creates a task and calls `ExecutorServices.run_task`, so the checks above
  apply: `executor_disabled`, `model_required` for Copilot, and for agy the switches, quota floors and single running
  launch below. A refused launch cancels the task the route created. `GET /gui/api/executors/launch/{task_id}` follows the
  task.

### Only a person turns an executor on

Turning an executor on is the person's decision, made in the GUI, one executor at a time. The rules:

- **Only the person, one by one.** «Apps y agentes» shows **Activar** on a card that is installed and signed in
  (`ready`). There is no «Activar todos». The command line, the HTTP API (`/api/v1`), MCP, the plans and the agents
  cannot activate: the routes are browser-only, and anything else answers that a person is needed
  (`409`, `details.reason: person_required`).
- **A fixed note first.** **Activar** opens a note the server writes. It says which application and which account will
  be used, that every launch spends the person's own subscription and quota, that the agent reads and writes in the
  working folder of each task, and that the terms of the provider apply. For agy it adds the third-party notice
  (`agy-third-party-notice/1`) and says that AI credits are never used. The button of the note, for example
  **Activar Codex**, is the one-use approval (the code lasts 120 s; if it expires the note says so and the person tries
  again). **Cancelar** closes the note without deciding.
- **A local setting.** The activation is saved in `executors-activados.json` in the local folder (`.local/`, outside the
  repository), with who, when, the executor, its version and the account when the CLI reports it. The record is an event
  of the team (`executor.activated`, `executor.deactivated`). `catalog/models.yaml` is not edited and stays all off.
  Accepting agy's notice goes with its activation; the GUI does not read or change agy's own settings or the Google
  account.
- **When it asks again.** Only after a change of substance: another path of the application, another major version of
  the CLI, or another account. A new minor version, or an account the CLI stops reporting, changes nothing. Until the
  person reviews it, the executor is `paused` and shows what changed (**Revisar y activar**). A provider that changes
  where it keeps data cannot be detected.
- **Turning off is immediate.** **Desactivar** turns one executor off at once, without a note. **Apagar todos**, above the
  list, asks for confirmation («Detiene lo que esté corriendo y pone todos en pausa»), stops what is running, pauses
  every activation and, for agy, writes its kill switch file. A kill switch file that a person made by hand is never
  touched: the card says agy is off because of a file the app did not write, and the person removes it.
- **The limits do not change.** The quotas, the permissions, the privacy classes and the secret gate apply as before.

The routes are `GET /gui/api/executors/activations`, `POST /gui/api/executors/{id}/activation`, `.../activate`,
`.../deactivate` and `POST /gui/api/executors/stop-all` (see [Operator GUI](GUI.md#views)).

## The secret gate before a launch

Every launch and every resume of a task passes the [secret gate](security/secret-gate.md) first, whatever the
entry (the GUI, `multiagent executors run`, `POST /api/v1/executors/run`, the worker, `resume-due`, `resume-met`).
It cannot be turned off.

- **What it reads.** What the framework would send to the app: the rendered prompt and the task's objective,
  constraints, context references and file scope. The ids the framework made (task, group, dependencies, session,
  folder) are taken out first, so they are not read as secrets. It looks for private keys, secret keys, bearer
  tokens and long high-entropy tokens.
- **What happens when it finds one.** The app is not started. The attempt ends `secret_review_required`, the task
  is held on an `approval` condition and a request appears in the GUI under «Aprobaciones». The result carries
  `secret_review` (`approval_id`, `secret_types`, `sha256`, `destination`). Without an approvals service the launch is
  refused (`launch_refused: secret_review_unavailable`).
- **Who approves.** Only a click in the GUI. `record_approval` answers `409` `user_click_required` for a secret
  approval, and `approval_lookup_failed` when it cannot read the approvals, so the tray, the CLI and the API cannot
  decide it. After the click the GUI checks the held task at once (`resume_met`); if the approval is valid and the
  text is the same, the app launches once and the approval is spent. A rejection never launches it. Automatic
  resumes, the worker and hand-offs never approve: they find the hold and wait.
- **Bound to the text.** The digest covers the envelope without what changes between attempts (attempt number,
  revision, status, assignee, event cursor, data class and dependencies) and the secret values found. A changed
  text, or another secret in the same text, asks again.
- **What is kept.** The type, a masked sample and the digest. Never the value.

**What it does not see.** An executor application can read files on disk by itself, so a secret in a file that
the app opens is not in the prompt and the gate cannot see it. The permission templates in
[`docs/permissions/`](permissions/) (see [Permission profiles](PERMISSIONS.md)) remain the defense against that.
On a PC where the agents run as the same user as you, the approval is not a barrier against a program built to
imitate your click; the guide to the secret gate states that risk in full.

## Installing and signing in to an executor

`multiagent setup` measures, without spending any model quota, which of Claude, Codex and Copilot is installed (version) and,
for Claude and Codex, whether it is signed in, and keeps the evidence under `.local/setup-evidence/`. agy is only
informational. To fix what is missing, the person opens a visible console, either with
`multiagent setup install <app>` and `multiagent setup login <app>` (`claude`, `codex`, `copilot`; `agy` has `login`
only) or with **Abrir consola** in «Apps y agentes» of the [operator GUI](GUI.md#views), which calls
`POST /gui/api/setup/console`.

- The console shows a fixed, dated official command (winget for Claude and Copilot, npm for Codex; never a remote
  script) and its source, and waits for Enter before running it here. Closing the window cancels.
- Only one console is open at a time. After Claude's install the console checks its publisher signature, and when the
  console closes the connection status is measured again. A new `PATH` shows only in consoles opened afterwards.
- It needs Windows. On another system, or with `GUI_ALLOW_LAUNCH=false`, the GUI shows the command to copy and run
  yourself.
- agy has no `install` step, and its `login` has no command: the person signs in from their own terminal.

The framework installs nothing in the background and never types credentials. When an executor is installed and
signed in, «Apps y agentes» offers **Activar**
([Only a person turns an executor on](#only-a-person-turns-an-executor-on)); it no longer shows lines to copy.

Codex ACP is a separate `codex-acp` executable; finding `codex` does not mean that `codex-acp` is installed. A person
may explicitly probe an installed adapter with one ACP `initialize` request. The probe creates no session, sends no
prompt, does not authenticate or activate the executor, and does not install an adapter. Any `agentInfo`,
capabilities, and authentication methods in its result are claims announced by that process, not identity,
authentication, permission, quota, or task-compatibility checks. Evidence is kept in memory and can be refreshed by
another explicit probe.

### Turning an executor on by hand

The manual way stays in the advanced mode, for another system or when the GUI is not at hand. It is the person's step,
as before: the declaration `enabled: true` in `catalog/models.yaml`
(`executor_routing: executors: <id>: enabled: true`) and, for agy, the notice line
`ANTIGRAVITY_NOTICE_ACCEPTED=agy-third-party-notice/1` in `.local/.env`. The card says only «También puedes encenderlo a
mano en `catalog/models.yaml`». An executor enabled this way is not paused by a change of version or account, because it
has no activation record; **Apagar todos** does not turn it off either.

## Results, sessions, and limits

The service submits only a parsed task-result envelope through the normal result submission path; the task gate still
decides acceptance. A missing, malformed, or rejected envelope leaves the attempt failed with bounded evidence. The
service does not automatically retry failures.

Only a session recorded from a framework launch can be resumed. A session opened through the framework's app-opening
path is blocked from resume for 12 hours. This record cannot detect a user opening a session directly in an app or
following a URL outside the framework. Codex can also reject a write to a session it currently has open. The adapters
do not bypass that application behavior.

Recognized quota or usage-limit failures pause the affected executor for the configured interval. They do not trigger
an automatic retry. Usage is recorded when the app supplies it; absent or unusable usage stays unknown (`null`). The
framework does not infer token counts or cost from output that lacks usage fields.

## Antigravity (agy), supervised

`antigravity_cli` runs a task through `agy -p`, with the subscription of the person who enables it. It is off by
default and stays supervised.

### Third-party notice

> Antigravity (agy) runs with the subscription of the person who enables it, through its official CLI. Review your
> plan's terms before you enable it. The framework spends no AI credits, but only if you keep them turned off in your
> account. Respect the quota limits that `agy /usage` shows. Do not use it with shared or third-party accounts.

To accept this notice, set the following line in `.local/.env` (created from `.env.example`):

```dotenv
ANTIGRAVITY_NOTICE_ACCEPTED=agy-third-party-notice/1
```

### Controls and launch

- **Two switches.** The catalog declaration (`catalog/models.yaml`) must say `enabled: true`, and
  `ANTIGRAVITY_NOTICE_ACCEPTED` must equal `agy-third-party-notice/1` (see [Third-party notice](#third-party-notice)).
  Until both hold, the executor stays disabled (`executor_disabled`; `details.code` is `notice_not_accepted` when the
  notice is missing).
- **Kill switch.** While the file `ANTIGRAVITY_KILL_SWITCH` (default `<data_dir>/antigravity-off`) exists, nothing is
  launched, not even the quota check, and a running agy is stopped within half a second. The service refuses before the
  task changes (`executor_unavailable`, `details.code` `kill_switch_on`).
- **One run at a time** per installation: a lock in the process plus an operating-system lock on the file
  `ANTIGRAVITY_LOCK_FILE` (default `<data_dir>/antigravity-run.lock`), shared by every process (`run_in_progress`).
  The system frees it when the holding process ends, so a run that died never leaves it taken; the file stays.
- **Quota floors.** Before each launch the adapter reads `agy -p /usage --output-format text`, which spends no model. The
  lines that apply are the model's group (`Gemini Models`, or `Claude and GPT models`) in both windows and the model's
  own weekly line; with no model, or one the adapter cannot place, both windows of both groups must be readable and
  every line applies. Without a person's acceptance, under 15% left the launch is
  refused (`quota_below_launch_floor`) and under 10% nothing starts and the attempt ends `quota_exhausted` with a
  `plan_limit` at the reset `/usage` gave, so the executor pauses until then. A `/usage` that fails or cannot be read
  refuses the launch (`usage_unavailable`): the floors are never skipped.
- **The floors are a warning a person can accept.** Between 15% and 0% left, the connection row stays eligible and
  carries `warning: {reason, remaining_percent, resets_at}` (`reason` is `quota_below_launch_floor` or
  `quota_below_pause_floor`). One person's confirmation for one launch crosses either floor: `accept_quota_warning: true`
  in `POST /api/v1/executors/run` and in the GUI launch route (the **Aprobar y lanzar** click on a row with a warning),
  or `--accept-quota-warning` in `multiagent executors run`. It is not stored: a resume, an automatic launch and the
  next launch never inherit it, and it only applies to agy. The acceptance is a bool (`StrictBool`); anything else is
  `422 invalid_request`. At 0% the plan limit always stops the run: the result is `quota_exhausted` with the plan limit
  and the reset, shown as «límite alcanzado», and the framework never spends agy's AI credits.
- **No AI credits from the framework.** The adapter changes no agy flag, setting or file, and never reads `~/.gemini`
  (credentials, `settings.json`, `brain/`). `GEMINI_API_KEY` and `GOOGLE_API_KEY` reach agy empty, so a key in the
  environment cannot turn a run into API billing. Keeping the account option that spends AI credits after the quota
  turned off is up to the person.
- **Approval and visibility.** Each launch goes through the same `launch_executor` effect and pin guard as the other CLI
  executors. The conversation is recorded as an `antigravity:<id>` session, which the session viewer lists (metadata
  only).

The command is `agy -p "Read <prompt file> in the current folder and do exactly what it says." [--model M] --mode
plan|auto --output-format stream-json --print-timeout <N>s [--add-dir <dir>]...`. Read permission uses `--mode plan`, the
only mode that leaves the folder unchanged: the folder is listed before and after the run, and a change fails the attempt
(`read_only_folder_changed`). Write permission uses `--mode auto`, whose sandbox sees only the folder and the
active virtual environment and Python installation; `ANTIGRAVITY_ADD_DIRS` adds further folders. If
agy reports that a mode is not available for the account, a read run fails (`read_only_mode_unavailable`) and a write
run notes it. `--dangerously-skip-permissions` is never passed. The effort is part of agy's model id
(`gemini-4-argon-high`), so the catalog declares no separate effort.

The result envelope comes from the final response. A write run whose response is empty may leave it in
`maf-result-<task>-<attempt>.json`, which the adapter reads once and removes. Existing prompt or result files make the
launch fail without overwriting them. The usage of a run is the sum of its
`step_update` usage: `input_tokens` excludes the cache, `output_tokens` includes reasoning, and `total_tokens` is
input + cache_read + output (agy's own total is kept as `agy_total_tokens`). Limits are read from stderr, the result
status and its error with agy's own quota pattern, never from the answer. They are never `rate_limited`, because the usage
contract forbids a retry in the same round. A cost or credit limit is `credits_exhausted`, which pauses the executor
until a person lifts the pause. A denied action is `permission_denied`, even when a result came back. Continuing an agy conversation
was not verified, so resuming is refused and a held task is relaunched from its checkpoint.

## Pinned versions

`catalog/executor-pins.yaml` records, for each executor application, the version and the SHA-256 digest a person
accepted. The list is closed: `claude_cli`, `codex_cli`, `copilot_cli`, `antigravity_cli` and `hermes_cli` (the id is
provisional until the Hermes adapter fixes it). The file ships with no versions or digests; the operator loads the real values in an
authorized maintenance window, never from an example. `multiagent/executor_pins.py` reads and compares the manifest.
Pinning is evidence for the autonomy policy: a match enables no executor and grants no autonomy, and a drift only blocks.

Each record has a `pin` (what a person accepted) and a `candidate` (a newer observation waiting for a person), both
`null` until loaded. A pin holds `version`, `sha256` (64 lowercase hex characters), `artifact_kind`, `first_observed_at`,
`accepted_at` and `accepted_by`; Hermes also holds `tag` and `commit` (40 lowercase hex characters). Quote text values.
`artifact_kind` says what the digest covers, and the operator hashes the same kind of object every time:

- `package`: the installed package as published.
- `launcher`: only the launcher or shim file that resolves on the path. It does not identify the package behind it.
- `source_archive` (Hermes only): the reproducible archive of the commit a release tag points at. Hermes ships no
  binary, so nothing is claimed about one; the tag, the commit and the digest are what is pinned.

The installed version is never read by running the application: no `--version`, no network, no install. The evidence
comes from the host (local package metadata) or from an observed registry the operator writes by hand after checking
the installation, in the same window. That registry is only as current as the last time it was written. It has the form
`version: 1` and `executors: {<id>: {version, sha256}}` (plus `tag` and `commit` for Hermes).

**Guard.** `ExecutorServices.pin_guard` is a `PinGuard` that a host sets when it turns autonomy on; while it is `None`
the check is off, as the other barriers are. With a guard, before an executor is launched or resumed (and before a
relaunch retires a held session) the service compares the evidence with the pin. Anything but an exact match refuses the
call with `invalid_state`, reason `executor_pin_blocked`, and `details.status` says why: `drift` (a version, digest, tag
or commit differs; `details.changed` names them), `missing_evidence`, `unpinned` (no pin accepted, or not in the closed
list) or `manifest_invalid`. Nothing is started and the task is not changed. A drift writes one `executor.version_changed`
team event per observed change: the executor, the pinned and observed values, `changed`, the candidate's window,
a fingerprint and the time. It has no local path, argument or task data (`task_id` and `group_id` are empty; `ref` is the
executor). The same observation again writes nothing, even from two launches at once (the latest event is read and the
new one written in one write transaction); a different one, including a return to an earlier one, writes a new event.
If the event cannot be written the call is refused all the same
(`details.event_recorded` is `false`). The autonomy policy (AXI-T18) is to read this event to put the executor back at
level 2; the pin check itself keeps no autonomy state.

**Accepting a new version.** A new version is never accepted by itself, by waiting or by an agent. The operator records
the observation as a `candidate` (`first_observed_at` is the time of the `executor.version_changed` event), and a person
accepts it in a maintenance window: the candidate becomes the `pin` with `accepted_at` and `accepted_by` (a person, not a
`system:`, `executor:` or `agent:` identity). The window is 7 days from `first_observed_at`: it is open from that moment
up to, not including, 7 days later. After it the previous pin stays in force and the candidate stays blocked; a new
observation starts its own window. The checker also rejects a pin whose `accepted_at` lies outside that window.
`accept_candidate` in `executor_pins.py` applies the rule to a manifest value; installing or updating the application
is a separate manual act that nothing in the service or the checker does.

**Checker.** `scripts/check_executor_pins.py` is offline and read-only. Without `--observed` it validates the manifest
and notes the executors without a pin (`--require-pins` makes that an error) and any candidate with the state of its
window. With `--observed` it compares each executor with its pin and exits 1 on a drift, missing or malformed evidence,
or an executor with no pin. Example on a throwaway copy:

```text
python scripts/check_executor_pins.py --manifest pins-copy.yaml --observed observed.yaml --only claude_cli
```

## CI coverage and validation boundary

`tests/test_executor_battery.py` is a synthetic CI battery. It runs each adapter only with a disposable Python fake or
controls the framework's process boundary to return timeout, cancellation, and quota outcomes. The cases compare the
shared service behavior across all three apps for result handling, assignment-profile model/effort inheritance, caller
model/effort overrides, absent usage, session provenance, recent app opens, and failure handling. They also check the
app-specific model and effort flags, including omission of Claude effort while its support remains unknown. The
adapter-specific test modules cover additional command construction and parsing details.
`tests/test_executor_antigravity.py` drives the Antigravity adapter with a fake `agy` in the same way: notice, kill
switch, quota floors, one run at a time, `stream-json` usage, empty answers, timeouts and credentials.

This battery does not start or probe an installed Claude, Codex, Copilot or Antigravity CLI. It does not validate live account
authentication, subscription quota, real session files, desktop-app visibility, or cross-process locking in those
applications. No real-CLI battery is included in this CI scope. Treat application-specific behavior outside the
synthetic tests as unverified until separately authorized and tested.
