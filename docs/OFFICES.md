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

## Contracts as data

A bundle can declare its output contracts in `contracts.yaml`, next to (or instead of) `contracts.py`. The file is read with `yaml.safe_load` and the models are built with `pydantic.create_model`: nothing in it is executed or imported. A bundle with `contracts.yaml` and no `.py` file is a `data` package.

Two neutral contracts live in the core, so a simple office needs to declare nothing: `FreeTextAnswer` (`answer`) and `DocumentSummary` (`title`, `summary`, `key_points`, `open_questions`).

```yaml
schema: maf.contracts/1
contracts:
  Notice:
    description: A short notice for the team.
    fields:
      title:   {type: string, max_length: 120}
      body:    {type: string, document: true}
      urgency: {type: enum, values: [low, normal, high], required: false}
      links:
        type: list
        max_items: 5
        required: false
        items: {type: string}
samples:
  Notice: {title: "Office closed", body: "We reopen on Monday."}
```

Each contract has an optional `description` and its `fields`. A field is required unless it sets `required: false`. A required `string` needs at least one character.

| `type` | Accepts | Extra keys |
| --- | --- | --- |
| `string` | text | `max_length` |
| `integer` | a whole number (not `true`/`false`) | none |
| `number` | a number (not `true`/`false`) | none |
| `boolean` | `true` or `false` | none |
| `enum` | one of the listed texts | `values` (required, distinct, non-empty texts) |
| `list` | a list of one kind of item | `items` (required, another field), `min_items`, `max_items` |
| `object` | a nested set of fields | `fields` (required) |

All types also take `description`. `document: true` marks the one top-level `string` field that holds the document text (at most one per contract).

Limits: contract names match `^[A-Z][A-Za-z0-9]{0,63}$`, field names match `^[a-z][a-z0-9_]{0,63}$` (and cannot start with `model_`), and fields nest at most 6 levels. Unknown keys and types are rejected.

`samples:` holds at most one sample per contract, checked against it. A sample may name the bundle's own contracts or the two neutral ones.

A name can appear in only one of `contracts.py` and `contracts.yaml`, and `contracts.yaml` cannot redefine a core contract (`contract_conflict`). In `contracts.py`, re-exporting `FreeTextAnswer` or `DocumentSummary` as the same class is an alias, not a clash. A bad file fails with `bundle_contracts_invalid` and a location such as `contracts.yaml:Notice.title`.

A `catalog/` folder may hold only one of `agents.yaml` and `models.yaml`: the missing file is read from the app catalog.
