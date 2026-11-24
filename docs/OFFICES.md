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

## Install, remove and reimport

An office of **level 1** has data only: every file has a suffix of `.yaml`, `.yml`, `.md`, `.txt`, `.json` or `.csv`. It installs from a folder or a `.zip` through the services (`services.offices`), the API (`/api/v1/offices`, see [Offices](API.md#offices)) or the CLI:

```text
multiagent office import PATH [--into OFFICE] [--actor user:local]   # a folder or a .zip
multiagent office list
multiagent office show OFFICE
multiagent office reimport OFFICE PATH
multiagent office remove OFFICE [--area AREA]
```

**Import.** The folder is copied (or the `.zip` extracted with the safety checks of installed packages) to `installed/.staging/<uuid>/`, which is removed at the end, and then:

1. `office.yaml` is read and validated (the codes above). More than 20 areas, a file over 1 MiB, more than 2000 files or 50 MiB give `office_too_large`.
2. If any file has another suffix, the import stops with `office_has_code`. This runs before any area is loaded, so no code of the office is imported or executed. An office with code needs the advanced mode of the GUI.
3. Each area is validated as an installed package (every workflow, and its team). If it declares `team_sha256`, the digest of its canonical team (`multiagent.teams.digest.team_sha256`, the same one `TeamServices.propose` stores) must match, or `office_team_mismatch`.
4. The areas are registered together with `InstalledServices.apply_batch`: packages `<office>__<area>`, `source: "office"`, `office: <id>`. If one fails, none stays and the preparation is removed.
5. The team of each area that has a `team.yaml` is **proposed**. Installing an office never approves a team; a person does that in the GUI.
6. The office is recorded in `installed/offices.json` (`maf.installed-offices/1`), its `office.yaml` is copied to `installed/.offices/<id>/v<n>/`, and the event `office.installed` goes to the team events.

**One area.** With `into`, the folder or `.zip` holds an `office.yaml` with exactly one area (the format of «Export area»). It is checked against the installed office: an id already there gives `office_area_exists`, and a relation to an area the office does not have is not added and comes back as a warning. The copy of `office.yaml` moves to `v<n+1>`.

**Remove.** `remove` takes the office and its areas out of the registry and the process; `remove_area` takes one. The package copies and `data_dir/bundles/<name>/` stay, and the answer has `kept_data: true`. When an area leaves, the plan items and relations that name it leave the new copy of `office.yaml`, each with a warning. The last area cannot be removed alone (`office_last_area`). An area is never reinstalled or removed through `bundle reinstall` or `bundle remove` (`bundle_in_office`).

**Reimport.** Each area is compared by the fingerprint of its package: new areas are installed, changed ones reinstalled and the ones that are gone removed, all in one `apply_batch`. An unchanged area keeps its package version and its approved team version; the answer says so in `changes: {added, changed, removed, unchanged}`. With a run of an affected area in progress, nothing changes (`bundle_busy`). The office version goes up when an area or `office.yaml` changed; otherwise the registry is left as it was.

**Start.** The areas load as installed packages. `offices.json` is only read; an office is `degraded` when an area is missing or its package is not `ok`. Without offices there is no `offices.json` and nothing new is read.

Events (team events, ref = the office id): `office.installed`, `office.removed` and `office.reimported` (also for one area added or removed).

## Requirements

Each area declares in `requires` what it needs, as namespaced capability ids, never a provider. The app crosses that with what the person has connected and answers one of two things: «Lo puedes cubrir con A o con B», or «Te falta un agente que pueda X» with a «Conectar otro» action. `requires` only informs in the MVP: the router does not read it.

The catalog is not edited. New capabilities are derived from the table below (the builder fixes it, design §13).

| Source | Gives |
| --- | --- |
| catalog `text_generation` | `model.text` |
| catalog `structured_output: true` | `model.structured` |
| catalog `tier: local` | `model.local` |
| a namespaced id the model already declares in `capabilities` | the same id (this is how `model.tools` enters) |
| an active executor | `model.text`, `executor.code`, `executor.files` |
| the Mock backend | every `model.*` (so it counts as local), never `executor.*` |

Texts (simple mode, none names a provider):

| Id | Text |
| --- | --- |
| `model.text` | escribir texto |
| `model.tools` | usar herramientas |
| `model.structured` | dar respuestas con estructura |
| `model.local` | correr en tu equipo |
| `executor.code` | trabajar con código |
| `executor.files` | trabajar con archivos |

Candidates are saved connections' catalog models, active executors and, in Mock, `mock` («Mock (ejemplo)»). A candidate covers an area when it has every requirement. If none does, `missing` lists what the closest candidate (most requirements; ties by id) lacks, and `action` is `"connect"`. An id outside the list is shown as written («Te falta un agente que pueda model.vision»).

An area that is «Sensible» (`sensitivity: sensitive`) or «Solo local» (`local_only`, on the area or the office) counts only local candidates, and carries the note «Esta área es Sensible: hoy solo puede usar agentes de tu equipo» or «Esta área es Solo local: nunca sale de tu equipo» («Solo local» wins when both apply).

Code: `multiagent/offices/requirements.py` (pure: `coverage`, `office_requirements`, `model_capabilities`, `executor_capabilities`); `services.offices.requirements` gathers the candidates.

## Sensitivity

Each area has a privacy level, and every run that starts from an area carries the data class of that level. There is no new rule or path in the engine: `RunServices.create` applies the class when the package of the run is an area of an installed office (`offices.office_of(bundle)`), so runs from the GUI, the API, the CLI and a plan all get it.

| Level | Data class | What a run of the area does |
| --- | --- | --- |
| `normal` | `non_sensitive` | As any run. |
| `sensitive` | `sensitive` | The request is stored with `sensitive: true`, so the cloud cannot be confirmed (`confirm_cloud` grants nothing for sensitive data, see [model routing](model-routing.md)). |
| `local_only: true`, in the area or in the whole office | `restricted` | As `sensitive`, and also `confirm_cloud`, `execution_mode: human_guided` and a `step_modes` entry with `human_guided` are refused with `422 invalid_request`, `details.reason = "area_local_only"` (a guided run would copy the prompt out of the machine). |
| none (`sensitivity` is optional) | `None` | A local run goes on. A run that asks for the cloud (`confirm_cloud: true`) is `409 invalid_state`, `details.reason = "office_level_required"`, with `details.office` and `details.area`. |

`local_only` wins over `sensitivity`. A package that is not an area of an office is not touched, and its runs carry no `data_class`. The run answer (`create`, `get` and the run list of the services and of `/api/v1`) has `data_class` for the runs of an area; it is the class of the area now, not a record of the class when the run started.

**Plans.** Each task of a plan creates its run through `RunServices.create`, so it carries the class of its area. Today a task does not receive the output of another (`services/plans.py`), so no class travels between tasks. When one does, the task takes the highest class of its own area and of every task it reads: `restricted` over `sensitive` over `non_sensitive`. Nothing of a «Solo local» area is ever an input to a task that can reach a cloud model.

**Where the levels live.** `office.yaml` declares the first level. What a person sets is kept in the entry of the office in `installed/offices.json`, under the optional key `privacy` (`{areas: {id: {sensitivity, local_only, set_by, set_at, source}}, office: {local_only, ...}}`). An office without it reads as before, and `OFFICES_SCHEMA` does not change. `source` is `"person"` for a level somebody set and `"office"` for the one the `office.yaml` declares.

**Raising.** `raise_level(office_id, area_id, sensitivity=, local_only=, actor=)` goes from no level to any, from `normal` to `sensitive` and from `local_only: false` to `true`; `area_id = None` is the whole office and takes only `local_only`. It works from the services, the API and the CLI, and the event `office.privacy_changed` records `office`, `area`, `from`, `to`, `direction` and the actor (a level reads `unset`, `normal` or `sensitive`, plus `+local_only` when it applies). Asking for something lower is `409 invalid_state` with `details.reason = "office_level_lowering_needs_gui"`.

```text
GET  /api/v1/offices/{id}/privacy           # {office, local_only, areas: [{id, sensitivity, local_only, data_class, set_by, set_at, source}], note}
POST /api/v1/offices/{id}/privacy/raise     # {actor, area?, sensitivity?, local_only?}
multiagent office privacy OFFICE [--area AREA] [--sensitive] [--local-only] [--actor user:local]
```

**Lowering** has no route in `/api/v1` and no command: it is done in «Privacidad» of the GUI. `request_lowering` creates the hidden one-use approval `office_privacy_change` (never listed in «Aprobaciones» or «Pendientes»), bound by its `sha256` to `{office, area, from, to}`. The person decides it with the 120-second code. `lower_level` spends it once, stores the lower level and writes the event. Without an approved, unspent approval of exactly that change, or if the level moved since the person saw it, it is `409 invalid_state`, `details.reason = "office_privacy_change_not_approved"`.

**Reimport** keeps the highest level between what the area had and what the new `office.yaml` declares: `sensitive` over `normal`, any level over none, and `local_only` is only added. A level a person raised therefore survives a reimport that declares less, with `source: "person"`; `local_only` of the whole office does too. An area that joins takes what the office declares.

**The note** of «Privacidad» is fixed (`PRIVACY_NOTE`, returned by `privacy()` as `note`):

- «Contraseñas, claves y tokens: siempre te pregunto, uno por uno.»
- «Las áreas marcadas «Solo local».»
- «Con un ejecutor (Claude, Codex, Copilot, agy), el agente puede leer toda la carpeta de trabajo de la oficina, no solo la tarea.»

**Executors.** A launch of an executor (`ExecutorLaunchRequest`) does not name a package, so there is no path from an area to cover. The `restricted` class of an executor launch (`executors/privacy.py`) is still set by its caller, not by an office.

## Export and import

`multiagent office export ID [--area AREA] [--include-code] [--output FILE.zip]` exports an installed office or one area from its installed copies. Without `--output`, the CLI writes `ID.zip` or `ID-AREA.zip` in the current directory and refuses to overwrite an existing file. The API endpoint is `POST /api/v1/offices/{id}/export`; it accepts `{area?, include_code?}` and returns the archive as `zip_base64`.

The archive contains a rewritten `office.yaml`, the selected files from each area, and a `manifest.json` using schema `maf.office-export/1`. The manifest records each file's SHA-256 and size, the team digest for every exported area, and any portability warnings. Each exported area uses `path: areas/<area-id>` in `office.yaml`.

By default, an area contributes `workflows/*.yaml`, `prompts/**`, `catalog/agents.yaml`, `team.yaml`, `contracts.yaml`, `README.md`, and `samples/**`. Every included file must use a suffix from `CODE_FREE_SUFFIXES`. Other files are left out and reported in `warnings`. `--include-code` additionally includes only the area's root `contracts.py` and `functions.py`; it does not include other Python files. Text files in the archive are screened by the secret gate. If a secret is found, the export fails with `422 export_secret_found`; the response identifies only the path, line, and secret type.

The export never includes databases, runs, `data_dir`, `.env`, `.local`, logs, input resources, `executor_runs`, or `catalog/models.yaml`. A workflow step that selects a model defined in the area's omitted `catalog/models.yaml` produces an `export_model_not_portable` warning identifying the workflow and step; the archive is still created.

An exported office can be imported with `multiagent office import FILE.zip` or `POST /api/v1/offices` using `zip_base64`. An area-only archive has one area and can be added to another installed office with `--into ID` or the API's `into` field. When an archive contains `manifest.json`, import requires its schema, file list, sizes, and hashes to match the extracted files exactly. A mismatch fails with `422 office_manifest_mismatch` and the affected path. Archives without a manifest remain supported for hand-authored imports. Safe extraction rejects unsafe paths and symbolic links before import.

## Orchestrator

`multiagent office plan-draft OFFICE TEXT [--confirm-cloud]` and
`POST /api/v1/offices/{id}/plan-draft` prepare a plan for an installed office.
The draft is not saved, approved, or started. Saving it still requires the
confirmation handled by the new-session plan flow.

The planner catalog contains only workflows from the office's areas. A model
proposal is checked again after generation; a task outside those areas is
rejected with `422 invalid_request`, `details.reason = "office_plan_outside_office"`.
Drafts use the stable group id `office-<id>-<8 hex>` so the office scene can
recognize their group without matching similarly named offices.

`orchestrator.default_plan` is a deterministic template. Each stage uses the
plan item's id, its area's name and bundle, and its workflow. `after` becomes
`depends_on`, including an empty list when no dependency was declared, so
parallel stages stay parallel. The operator's request fills the first required
text input of each workflow; a workflow without one receives no input.

| Model and office privacy | Planner | Fallback reason | Cloud calls |
| --- | --- | --- | --- |
| Mock | `deterministic`, using `default_plan` or the request template | `mock` | None |
| Local model | `model`, with `sensitive` data class when any area is not normal | None | None |
| Cloud model; any area is sensitive or restricted | `deterministic`, using `default_plan` or the request template | `office_not_normal` | None |
| Cloud model; all areas normal; no confirmation | No draft | `cloud_confirmation_required` | None |
| Cloud model; no protected area, at least one area has no level; confirmed | No draft; `office_level_required` identifies the area | None | None |
| Cloud model; all areas normal; confirmed | `model`, with `non_sensitive` data class | None | One |

If a deterministic fallback has no `default_plan`, it splits the request as
the new-session planner does and matches each part against the office catalog.
If no part matches, it returns `409 invalid_state` with
`details.reason = "office_plan_unavailable"` and the advice to ask for one
thing at a time.

Office-aware interpretation uses the same restricted catalog. If any area is
sensitive, restricted, or has no level and the configured model is not local,
it makes no model call and returns keyword mode with
`reason = "office_not_normal"`. With no selected office, planning and
interpretation keep their existing behavior.

## Templates

Two level-1 (data-only) office templates live in `examples/offices/` with synthetic Spanish data, `preferred_model: local_default`, `checkpoint_after: true`, and teams whose `revision` role assigns a `Responsable` person to review outputs from `redaccion`:

- **`examples/offices/cafeteria`** (`cafeteria` — *Cafetería de ejemplo*):
  - `menu` (`normal`, `[model.text]`): `menu_semana` workflow producing `MenuSemana`.
  - `compras` (`normal`, `[model.structured]`): `lista_pedido` workflow producing `ListaPedido`.
  - `avisos` (`normal`, `[model.text]`): `escribir_aviso` (`Aviso`) and `responder_mensaje` (`Respuesta`).
  - Default plan: `menu` (`menu_semana`), then `avisos` (`escribir_aviso`).
- **`examples/offices/escuela`** (`escuela` — *Escuela de ejemplo*):
  - `planeacion` (`normal`, `[model.text]`): `plan_clase` workflow producing `PlanClase`.
  - `seguimiento` (`sensitive`, `local_only: true`, `[model.text, model.local]`): `nota_avance` workflow producing `NotaAvance`.
  - `avisos` (`normal`, `[model.text]`): byte-identical copy of `cafeteria/areas/avisos`.
  - Default plan: `planeacion` (`plan_clase`), then `avisos` (`escribir_aviso`).

Import a template from a repository checkout (teams stay `proposed` until approved in the GUI):

```text
multiagent office import examples/offices/cafeteria
multiagent office import examples/offices/escuela
```

The `examples/` directory is only present in a repository checkout; it is not included in the built wheel (`pyproject.toml`).
