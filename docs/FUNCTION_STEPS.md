# Function steps

A workflow step can name a synchronous function registered by trusted host code. The workflow supplies only the id and literal arguments; it does not supply Python code. A package of level `code` may bring a `functions.py` that registers functions with `register_function(function_id, fn, description=...)`. It is never imported just because it sits in the folder: it loads only with explicit trust (`multiagent gui --trust-functions NAME`, repeatable, or the checkbox in advanced mode), after the bundle scan and approval, once per package and fingerprint. A new fingerprint (a reinstall) drops the old functions and asks for trust again. An id is never redefined: one that already exists (`core.merge_outputs`, or another package's) gives `function_conflict`, and a `functions.py` that fails to import gives `functions_import_failed` with the exception type only. Without trust, validation reports `unknown_function` with the fix «Este paquete trae funciones; confía en ellas en la ficha del paquete». The API and the MCP server never grant trust. The id follows the capability pattern: two to four dot-separated lowercase segments, such as `core.merge_outputs`.

```yaml
- id: collect
  function: core.merge_outputs
  arguments: {}
  contract: CollectedOutputs
```

The function receives read-only `{"inputs": ..., "steps": {step_id: validated_output}}` from the run, plus the read-only arguments mapping. It returns a dictionary validated against the step's output contract. The completed step has an artifact and start/completion events with the function id, but no provider call, usage row or call budget reservation. See `examples/bundles/general/workflows/collect_summary.yaml` for a complete workflow.

Function steps may also declare `description`, `when` and `checkpoint_after`. They cannot declare `agent`, prompt fields, skills, models, tools, `requires_web` or other model settings. Validation reports `unknown_function` for an unregistered id. Execution reports `function_output_invalid` when the returned object fails the contract and `function_failed` when the function raises; the run error and failure event expose a bounded, secret-redacted exception message (up to 300 characters) and never the traceback.

Workflow input and prompt previews identify a function step without a prompt or agent. Routing previews show it as `function` without a profile, model or effort. A run request that sets a routing preference or step execution mode for a function step returns `function_step_option_invalid` (HTTP 422); the CLI reports the same code.
