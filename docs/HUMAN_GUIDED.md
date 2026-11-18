# Human-guided execution

Human-guided steps let you copy a prompt into any external chat or tool, then bring its response back for validation. The framework does not call that external model. Use synthetic data and Mock mode when trying this flow.

## CLI

Create a run with `--mode human_guided` (or set a step's execution mode), then wait until its status is `waiting_human` with reason `execute_step` or `research`.

```sh
multiagent run --project "Guided demo" --objective "Draft a sample post" --topic "Synthetic topic" --mode human_guided
multiagent human-next RUN_ID --out prompt.txt
multiagent human-next RUN_ID --prompt-only
multiagent human-check RUN_ID --file response.json
multiagent human-submit RUN_ID --file response.json --provider external_chat --model example-model --prompt-used-file prompt.txt --notes "Reviewed locally"
```

`human-check` reports `ok`, the expected `contract`, and `problems` with a `path` and `message` for each invalid field. It does not submit or persist the response. Use `--file -` with `human-check`, `human-submit`, or `research-submit` to read standard input. For external research, `multiagent research-submit RUN_ID --file -` defaults to provider `human_web`.

## HTTP API

`GET /api/v1/runs/{run_id}/human-next` returns the exact `prompt`, `expected_contract`, its separate `expected_schema` JSON Schema, and `request_path` relative to the runtime's data directory. The path identifies the saved request Markdown file. It is never an absolute local path.

```sh
curl http://localhost:8000/api/v1/runs/RUN_ID/human-next
curl -X POST http://localhost:8000/api/v1/runs/RUN_ID/human-check -H 'Content-Type: application/json' -d '{"raw_response":"{\"field\":\"value\"}"}'
curl -X POST http://localhost:8000/api/v1/runs/RUN_ID/human-submit -H 'Content-Type: application/json' -d '{"raw_response":"{\"field\":\"value\"}","provider":"external_chat","model":"example-model","prompt_used":"The prompt sent","notes":"Reviewed locally"}'
```

`POST /api/v1/runs/{run_id}/research-submit` accepts the same body and defaults `provider` to `human_web`. Both submission routes use the same validation and persistence. A stale run or attempt returns HTTP 409 with `code=invalid_state` and `details.reason=not_waiting`.

## Operator GUI

Open `/gui/`, select the waiting run, copy its prompt, and paste the external response. The screen reads `GET /gui/api/runs/{run_id}/human-next`, checks with `POST /gui/api/runs/{run_id}/human-check`, and submits with `POST /gui/api/runs/{run_id}/human-submit`; these routes require the GUI's local token. Enter the provider and optional model. The optional **Prompt used** field records edits to the prompt; when empty, the prompt shown for the step is saved. **Notes** record any context about the response. **Validate without submitting** shows field-level problems and leaves the run untouched. **Submit response** validates and records the answer, then advances the run.

An accepted submission stores the raw response, normalized output, provider, model, `prompt_used`, and `notes` with the human step. An invalid submission records the raw response and a rejection event so it can be diagnosed. A validation-only check writes neither.

## External step output

Use external output when a run waits for a `human_guided` step (`execute_step` or `research`) or at a review checkpoint. Supply a JSON object matching the step contract. Preview validates it and any evidence without changing run events, artifacts, or state. The preview expires after `EXTERNAL_PREVIEW_TTL_SECONDS` (900 by default). Confirmation requires the preview ID and an actor beginning with `user:`. A review confirmation creates a new attempt and leaves it waiting for the normal review decision.

```sh
multiagent external-output preview RUN_ID --output output.json --evidence screenshot.png
multiagent external-output confirm RUN_ID PREVIEW_ID --actor user:operator
```

The API uses `POST /api/v1/runs/{run_id}/external-output/preview` with `{"output": {...}, "evidence": [{"name": "screenshot.png", "content_base64": "..."}], "actor": "user:operator", "channel": "api"}`. Confirm with `POST /api/v1/runs/{run_id}/external-output/confirm` and `{"preview_id": "...", "actor": "user:operator", "channel": "api"}`. Evidence is stored as untrusted data; it is never interpreted or included in a prompt. Allowed types are PNG, JPEG, WebP, MP4, WebM, UTF-8 text, JSON, and JSON Lines. `EXTERNAL_EVIDENCE_MAX_FILE_BYTES` and `EXTERNAL_EVIDENCE_MAX_TOTAL_BYTES` bound uploads. Confirmation adds each evidence file, a SHA-256 manifest, and a receipt to the run's artifacts.

The GUI has the same flow in the run detail («Salida externa»): preview, confirm, and receipt (see [GUI](GUI.md)).
