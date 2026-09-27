<!-- Copyright 2026 Ricardoallexis and contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Stage 0 manual acceptance checklist

Run from the repository root with Python dependencies installed:

```powershell
python -m multiagent ui --no-browser --bundle team=tests/fixtures/bundles/spec_review
```

The launcher should report Mock mode and a local `/ui/` URL. Open that URL in a browser. The fixture bundle provides synthetic outputs, so model credentials and model-generation calls are not needed. The initial `/health` request may still check configured adapter availability. All visible run status, timestamps, events, and artifacts come from the API, not process-presence telemetry.

## Workflow selection and run monitoring

- [ ] The page reports **MOCK** mode and loads the `team` bundle and its `spec_review` workflow.
- [ ] Keyboard users can move through labeled controls, select the workflow, and review its detail.
- [ ] Create a run with a project name and `spec_text` input. The run appears in the monitoring and human-action panels without a duplicate submission.
- [ ] Refresh the run and artifacts. The view reports API state, current/waiting step, API-provided times, events, and artifact data; it does not claim live agent/process presence.
- [ ] Invalid or unavailable API requests show a useful error without clearing unrelated form entries or rendering response content as HTML.

## Human review actions

Create a Mock run and wait for the API to report a review:

- [ ] Approve the first review and observe the next step through the API.
- [ ] On a separate run, request non-empty changes, observe it resume, then regenerate at a review and observe it resume.
- [ ] On separate runs, reject and cancel after using each confirmation panel. Confirm that the API reports `rejected` and `cancelled`.
- [ ] A queued or running run can be cancelled only when the API allows it; the page distinguishes a cancellation request from a completed cancellation.

## Human Bridge

Create a run with execution mode `human_guided`:

- [ ] When a step is waiting for external input, inspect and copy the prompt, then paste a deliberately invalid response.
- [ ] The API validation error is shown, the response text remains available to correct, and resubmitting the fixture's valid JSON advances the run.
- [ ] Repeat through all three steps, approving any review waits shown by the API, until the run completes and its artifacts are visible.

## Non-Mock confirmation

Only perform this check if a separately configured non-Mock server is available; do not switch the default launcher to real providers:

- [ ] The page labels the server as non-Mock and requires explicit confirmation before creating a run.
- [ ] Without confirmation, submission is blocked; with confirmation, the UI sends only the selected workflow inputs to the API.
