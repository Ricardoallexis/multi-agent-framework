# Offices

An office groups existing area bundles under a single `office.yaml` manifest (`maf.office/1`).

## Format

Here is an example `office.yaml`:

```yaml
schema: maf.office/1
id: estudio-ejemplo            # ^[a-z0-9][a-z0-9-]{0,39}$
name: Example Studio
description: Example office definition for testing.
local_only: false
orchestrator:
  enabled: true
  name: Coordination
  default_plan:                # proposed in Mock or without a model planner
    - area: recepcion
      workflow: take_request
    - area: propuesta
      workflow: draft_proposal
      after: [recepcion]
areas:
  - id: recepcion              # same pattern as the office id
    name: Reception
    path: areas/recepcion      # relative POSIX path inside the office folder
    team_sha256: "0000000000000000000000000000000000000000000000000000000000000000"  # optional
    sensitivity: normal        # normal | sensitive (optional)
    local_only: false
    requires: [model.text, model.structured]
relations:
  - from: recepcion
    to: propuesta
    kind: hands_off            # hands_off | reviews | consults
```

Every model uses `StrictModel` and rejects unknown fields:

- **Top level:** `schema` (must be `maf.office/1`), `id` (`^[a-z0-9][a-z0-9-]{0,39}$`), `name`, optional `description`, optional `local_only` (default `false`), optional `orchestrator`, required non-empty `areas`, and optional `relations`.
- **`orchestrator`:** `enabled` (boolean), optional `name`, and optional `default_plan` list. Without an `orchestrator:` block the office has no orchestrator (`enabled: false`); a block that leaves out `enabled` is on. Each plan item has `area`, `workflow`, optional `after` (list of plan item IDs), and optional `id` (defaults to `area`; two items for the same area must specify distinct `id` values).
- **`areas[]`:** `id` (unique, matching `^[a-z0-9][a-z0-9-]{0,39}$`), `name`, `path` (existing subfolder inside the office directory, using `/` separators; no absolute paths, drive letters, `..`, `\`, or symbolic links/junctions), optional `team_sha256` (64 lowercase hex characters or `null`), optional `sensitivity` (`normal`, `sensitive`, or `null` when omitted), optional `local_only` (default `false`), and optional `requires` (namespaced capability IDs validated via `multiagent.capabilities`). Each area maps to bundle name `<office_id>__<area_id>`.
- **`relations[]`:** `from`, `to`, and `kind` (`hands_off`, `reviews`, or `consults`). A relation naming an area absent from `areas` emits a warning instead of failing.

### Validation codes

Errors raise `OfficeDefinitionError` with `issues` (`{code, message, location}`):

| Code | Meaning |
| --- | --- |
| `office_invalid` | Malformed YAML, wrong `schema`, unknown keys, invalid IDs/values, broken `default_plan` references, or missing area directory. |
| `office_path_unsafe` | Absolute path, drive prefix, `\`, `..`, escaped path, or symbolic link / junction. |
| `office_duplicate_id` | Repeated area `id` or repeated `default_plan` item `id`. |

Warnings are recorded on `OfficeDefinition.warnings`:

| Code | Meaning |
| --- | --- |
| `office_relation_unknown_area` | A relation `from` or `to` field references an area ID not declared in `areas`. |
