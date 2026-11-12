# Context compaction

Context compaction is opt-in. It estimates prompt size, warns before a model call, and can replace older workflow outputs or task history with a validated, traceable summary. Original outputs and task results remain stored.

## Enable a context profile

Profiles live in `catalog/models.yaml`, separate from `routing_profiles`. Assign one to a workflow by workflow ID, or to task envelopes with the `tasks` assignment. Both an assignment and `enabled: true` are required; without them, prompts and task envelopes are unchanged.

```yaml
context_profiles:
  profiles:
    example:
      version: 1
      enabled: true
      warning_at: 0.62
      threshold: 0.70
      unknown_window: skip
      keep_recent: 1
      summary: {first_level: T1, retry_level: T2, effort: low, model_check: true, max_calls: 4, max_ratio: 0.5}
      tasks: {window_tokens: 48000}
  assignments:
    example_workflow: example
    tasks: example
```

The values and names above are illustrative. Defaults are `enabled: false`, `warning_at: 0.62`, `threshold: 0.70`, `unknown_window: skip`, and `keep_recent: 1`. Summary defaults are T1 first, one T2 retry, low effort, model checking enabled, at most four summary-generation calls, and a maximum summary-to-source ratio of 0.5. Thresholds must satisfy `0 < warning_at < threshold < 1`. For tasks, `tasks.window_tokens: null` uses the requested model's catalog window instead.

## Measurement and warning

The framework uses `estimate_tokens_conservative` (prompt characters divided by 3.2) for the token estimate. Workflow calls are measured against the candidate model's window before each call, so fallback and retry candidates can have different ratios. Window evidence comes from `Catalog.context_window` (CMP-T08): local Ollama models use runtime `num_ctx`; cloud models need provider-documentation evidence matching the configured model. An unknown window is never guessed.

- At or above 62% by default, the framework emits `context_warning` with `level: warning`.
- At or above 70%, it emits `context_warning` with `level: threshold` and `compaction_pending: true`. The call still proceeds; workflow compaction is considered after the step succeeds.
- With an unknown window, the default `skip` policy lets a workflow call proceed and warns once per run and model. `block` stops that workflow call with `context_window_unknown`.

Task context is measured at the end of an attempt (also available through task-context refresh), against `tasks.window_tokens` or the specified model's window. A threshold can trigger a task snapshot; it does not interrupt the attempt.

## What stays literal

Workflow compaction summarizes older step outputs only. Inputs, the workflow template, skills, output contract, the latest `keep_recent` outputs (at least one), and pending human revision feedback remain literal. The summary is appended as quoted data; replaced outputs are represented by content-addressed markers, while their originals remain in blob storage. A stored compaction is applied only while its summary and the hashes of the outputs it replaced remain intact.

Task snapshots keep the task contract and criteria (objective, constraints, gate, file scope, dependencies, and base commit), applicable rules, recent results, and unresolved human instructions literal. The snapshot cursor does not pass an unresolved change request. Older eligible results and events before the cursor are summarized; context-machinery events are not fed back as source events.

## Validated summaries and failure behavior

Summaries use the `multiagent.context_summary/1` schema: objective, decisions, pending items, files and artifacts, constraints, and errors seen. Validation checks the schema and limits, rejects invented references, checks required-source coverage, verifies pinned content by hash, and requires a reduction to at most `max_ratio` of the summarized material. With `model_check: true` (the default), a structured model check also looks for omissions or contradictions.

The first summary uses an eligible T1 model. If validation fails, one T2 retry may be made; provider errors, budget stops, or inability to select a suitable model do not trigger that escalation. Chunked summaries are bounded by `max_calls`. Sensitive workflow data must remain within its allowed data classes; if no eligible model or required window is available, compaction is skipped.

If a summary or its checks fail, the framework records `context_compaction_skipped` where possible and continues with original context. Workflow cancellation still propagates. This is fail-open for the run, but fail-closed for applying a summary: an unvalidated summary is never used.

## Task snapshots

When task context compaction is enabled, submitting an attempt refreshes the measurement; callers can also request a refresh. At the threshold, the framework stores a validated snapshot as a content-addressed blob (`application/vnd.multiagent.context-summary+json`) with its scope, covered references and hashes, event cursor, prior summary, producer, validation, and token counts. It links the snapshot reference from open tasks in the task or group scope, replacing the previous snapshot reference where possible. Frozen tasks are not changed, and original results and events are retained.

A later task attempt reads the task envelope, the latest intact snapshot, and events after the snapshot cursor. It also receives the recent results and pinned contract content literally. Task summaries are restricted to local models because task envelopes have no data-class grants.

## Events and visibility

Run events are stored in `run_events`; task events are stored in `team_events`.

- `context_warning` records the scope and run/task identifiers, step and attempt when applicable, candidate model, context-window evidence, estimated tokens, ratio, configured warning and threshold, level, whether compaction is pending, and estimator. Task warnings also identify their task/group snapshot scope and include the measured context breakdown.
- `context_compacted` records the summary reference and hash, cursor or step, tokens before/after/saved, covered source count, pinned items, summary model/level, escalation and checker details, summary-call and token accounting, cost evidence, and duration. Task snapshot events additionally report the linked and unlinked task IDs. A `context_compaction_skipped` event records why no summary was applied.

The local GUI shows human-readable warnings and compaction facts in framework run detail and session detail. It does not expose prompt text in those event summaries.

## Measure quality

`tools/evals/routing/export_runs.py` exports only `context_warning` and `context_compacted` facts in each comparator record's `context_events` field (event type, step/attempt, level and ratios/thresholds, plus before/after token counts and summary reference where present). It does not export prompts or artifacts. `compare.py` compares already-adjudicated records offline; it does not call models or judge quality.

For an A/B comparison, use the same frozen cases, input and rubric hashes, comparable configuration identity, and execution surface; label the no-compaction and compaction arms as separate routes. Record `accepted` and `critical_error` adjudications and export each arm's finished runs, then compare the paired records. The comparator excludes pairs whose input, rubric, config, or surface identities differ, so use a shared `config_hash` only when the evaluation harness is otherwise comparable. Inspect `context_events` to confirm which arm actually warned or compacted.

## Limits

- Profiles are disabled unless explicitly enabled and assigned. Workflow compaction is limited to the generic workflow path; it does not compact external Claude, Codex, or Copilot session histories.
- The estimate is a conservative character-based estimate, not a provider tokenizer. Compaction is post-step, not mid-call.
- Original outputs, task results, and events are retained; summaries are derived artifacts.
- Task summaries currently have no workflow `before_call` or usage-recording callback, so do not treat their summary calls as accounted against workflow call budgets or usage telemetry.
- A task's unknown context window produces a warning and no ratio; `unknown_window: block` does not block task attempts.
- This feature does not provide embedding retrieval, project-wide persistent memory, or automatic compaction by default.
