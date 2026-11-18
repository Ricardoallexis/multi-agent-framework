# Executor applications

The framework can run tasks through the Claude, Codex, and Copilot command-line adapters, and through the Antigravity
CLI (agy) under supervision (see [Antigravity (agy), supervised](#antigravity-agy-supervised)). These adapters share the
executor service and process runner, but each adapter builds its own command and reads that app's structured output.
They are declared **disabled by default**. An operator must enable an executor in the catalog before the service can
launch it.

## Configuration and launch

`ExecutorServices.run_task` accepts an explicit model and effort, or can take them from the selected assignment
profile's routing decision. Explicit caller values take precedence. A profile value is sent only when the executor's
catalog evidence declares support for it. Unknown support does not become an assertion of support: Claude effort is
currently unknown and is not sent from a profile. A value explicitly supplied by a caller remains visible in the
attempt record, while an adapter may report that it did not use that value.

The service uses read permission by default. A task with a file scope may receive write permission inside its task
work folder. The folder comes from the task/executor configuration or the framework's task work-folder root; invalid
folders are refused. The adapters use a fixed prompt template and pass the canonical task envelope through stdin or a
framework-created prompt file.

The process runner uses an argument list with `shell=False`, resolves a program from absolute `PATH` entries, removes
Claude/Anthropic variables from the child environment, sets `NoDefaultCurrentDirectoryInExePath=1`, and enforces a
timeout and bounded output capture. Free-form prompt text is kept out of arguments and command records. A timeout or
explicit cancellation stops the process tree.

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
default and stays supervised:

- **Two switches.** The catalog declaration must say `enabled: true`, and the person must accept the third-party notice
  by setting `ANTIGRAVITY_NOTICE_ACCEPTED=agy-third-party-notice/1`. Until both hold, the executor is disabled
  (`executor_disabled`; `details.code` is `notice_not_accepted` when the notice is missing). The notice says that agy runs
  with the subscription of whoever enables it, through its official CLI; that the plan's terms apply; that the framework
  spends no AI credits only if they stay off in the account; that the quota shown by `agy /usage` is respected; and that
  it is not for shared or third-party accounts. Where the notice is shown is a separate task (GEM-T05).
- **Kill switch.** While the file `ANTIGRAVITY_KILL_SWITCH` (default `<data_dir>/antigravity-off`) exists, nothing is
  launched, not even the quota check, and a running agy is stopped within half a second. The service refuses before the
  task changes (`executor_unavailable`, `details.code` `kill_switch_on`).
- **One run at a time** per installation: a lock in the process plus an operating-system lock on the file
  `ANTIGRAVITY_LOCK_FILE` (default `<data_dir>/antigravity-run.lock`), shared by every process (`run_in_progress`).
  The system frees it when the holding process ends, so a run that died never leaves it taken; the file stays.
- **Quota floors.** Before each launch the adapter reads `agy -p /usage --output-format text`, which spends no model. The
  lines that apply are the model's group (`Gemini Models`, or `Claude and GPT models`) in both windows and the model's
  own weekly line; with no model, or one the adapter cannot place, both windows of both groups must be readable and
  every line applies. Under 15% left the launch is
  refused (`quota_below_launch_floor`). Under 10% nothing starts and the attempt ends `quota_exhausted` with a
  `plan_limit` at the reset `/usage` gave, so the executor pauses until then. A `/usage` that fails or cannot be read
  refuses the launch (`usage_unavailable`): the floors are never skipped.
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
