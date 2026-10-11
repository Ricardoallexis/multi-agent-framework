# Executor applications

The framework can run tasks through the Claude, Codex, and Copilot command-line adapters. These adapters share the
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

## CI coverage and validation boundary

`tests/test_executor_battery.py` is a synthetic CI battery. It runs each adapter only with a disposable Python fake or
controls the framework's process boundary to return timeout, cancellation, and quota outcomes. The cases compare the
shared service behavior across all three apps for result handling, assignment-profile model/effort inheritance, caller
model/effort overrides, absent usage, session provenance, recent app opens, and failure handling. They also check the
app-specific model and effort flags, including omission of Claude effort while its support remains unknown. The
adapter-specific test modules cover additional command construction and parsing details.

This battery does not start or probe an installed Claude, Codex, or Copilot CLI. It does not validate live account
authentication, subscription quota, real session files, desktop-app visibility, or cross-process locking in those
applications. No real-CLI battery is included in this CI scope. Treat application-specific behavior outside the
synthetic tests as unverified until separately authorized and tested.
