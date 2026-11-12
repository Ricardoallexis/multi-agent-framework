# Document trust barrier

## What is untrusted

Treat content as untrusted when it did not come directly from the user or an approved task contract.
This includes documents, attachments, channel entries, worker reports, transcripts, web pages,
tool output, model output, executor results, and context summaries.

An untrusted source stays untrusted when it is quoted, summarized, or transformed.
The framework carries provenance to record the source and its derivation.
Trust is computed from provenance; content cannot declare itself trusted.
Only `user` and `approved_contract` are trusted source kinds.

See [`multiagent/document_trust.py`](../../multiagent/document_trust.py) for provenance and trust rules.

## The envelope

Before untrusted text enters a prompt, the framework encloses it in a fixed envelope.
Each content line is marked as quoted data.
The envelope identifies the source kind and includes a content hash.
Delimiter-like text and invisible control characters are made visible or neutralized.
The fixed prompt clause tells the model not to follow instructions inside the envelope.

The envelope makes boundaries clear; it does not make the text safe or trustworthy.
The model may still misunderstand or ignore those boundaries.
Hosts must keep provenance with the content instead of trusting labels inside the text.

## Detection and handling

The detector uses fixed rules and standard-library parsing.
It looks for instruction-like patterns, false claims of approval, suspicious role markers,
encoded commands, and selected hidden text.
It does not call a model or a network service.

If untrusted content has no actionable signal, it passes through unchanged.
When a suspicious fragment is found, the framework stores the original in its configured blob store.
The prompt-facing text replaces that fragment with a quarantine marker.
The original is not restored to the prompt by the alert interface.

With the `quarantine_warn` policy, work can continue with the fragment removed.
With the `pause` policy, work waits for a person to review it.
The policy depends on the active profile and context; a write-capable task can require a pause.
The review decision does not itself authorize a write or network request.

## Turning the barrier on

The workflow engine starts with no document policy unless its host activates one.
Without an active policy, its model-call guard and effect gate do not enforce this barrier.
The standalone screening helper has a `quarantine_warn` default, but that does not activate the engine policy.
Hosts must choose and pass an active policy for the protection to apply to workflow calls.
They must also route effects through the authorization gate.
Recording an alert alone does not stop a model call or an effect.
Keep the policy consistent across screening, review, and effect execution.

See [`multiagent/document_trust.py`](../../multiagent/document_trust.py) for the envelope, detector,
screening result, quarantine marker, and screening events.
See [`multiagent/workflow_engine.py`](../../multiagent/workflow_engine.py) for screening during workflow
context preparation and recording quarantine or review events.

## Alerts for people

Quarantine and review events are exposed to GUI2 as document alerts.
The alert includes the source, policy, signals, and bounded excerpts of suspicious fragments.
It does not expose the original document from the blob store.
An open review stays open until a later approval settles that review request.
The alert endpoint is read-only; it does not release a fragment or authorize an effect.

See [`multiagent/gui/document_alerts_api.py`](../../multiagent/gui/document_alerts_api.py) for the alert
response, and [`multiagent/api.py`](../../multiagent/api.py) for API registration.

## Two checks, for two different risks

The first check runs before a model call.
The context guard checks whether a document review is still waiting.
If the active policy requires a pause, the model call does not proceed until the review is settled.
Content is screened and rendered into the prompt before it is sent.
See [`multiagent/call_guards.py`](../../multiagent/call_guards.py) and
[`multiagent/workflow_engine.py`](../../multiagent/workflow_engine.py).

The second check runs before an effect.
Writes, network requests, launches, and application opens need an explicit approval.
The authorization check matches both the effect kind and, when specified, its target.
No approval means the effect is denied.
An approval for a different kind or target does not count.
Detector results and document provenance are not approvals.

Only the user or a task contract the user approved can authorize an effect.
The host must build approval evidence from that user approval or approved contract.
It must not infer approval from document text, model output, an alert, or a detector result.
See [`multiagent/document_actions.py`](../../multiagent/document_actions.py) and
[`multiagent/executors/launch_safety.py`](../../multiagent/executors/launch_safety.py).

## Limits

Detection is heuristic.
It can miss hostile instructions, and it can flag ordinary text.
Passing the detector is not proof that content is harmless.
The envelope is a prompt boundary, not a sandbox.
The effect authorization check is separate and must remain enabled for effects.

This barrier only covers content and effects that pass through the framework.
It does not cover data written to disk by code outside the framework.
It cannot protect files or systems modified through an unguarded external path.
Keep approvals narrow, and do not treat a clean scan as permission to act.
