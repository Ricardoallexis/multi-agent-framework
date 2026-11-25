# Offices

An office groups existing area bundles under a single `office.yaml` manifest (`maf.office/1`).

## Format

Here is an example `office.yaml`:

```yaml
schema: maf.office/1
id: office-example              # ^[a-z0-9][a-z0-9-]{0,39}$
name: Example Office
description: Example office definition for testing.
local_only: false
orchestrator:
  enabled: true
  name: Coordination
  default_plan:                # proposed in Mock or without a model planner
    - area: reception
      workflow: take_request
    - area: proposal
      workflow: draft_proposal
      after: [reception]
areas:
  - id: reception              # same pattern as the office id
    name: Reception
    path: areas/reception      # relative POSIX path inside the office folder
    team_sha256: "0000000000000000000000000000000000000000000000000000000000000000"  # optional
    sensitivity: normal        # normal | sensitive (optional)
    local_only: false
    requires: [model.text, model.structured]
relations:
  - from: reception
    to: proposal
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

**GUI.** The «Oficinas» view lists each office with its coordinator, areas and members with their states, and a fix for
degraded states. From the view, a person can import a folder or `.zip` through the install card, export an office or
area, reimport, and remove an office or area. An import into an existing office uses the same install card.

In a repository checkout, «Inicio» offers «Instalar la oficina de ejemplo» only as an explicit action; the example is
installed only when the person presses it. An installed copy does not include the example files, so the person imports
an office of their own. The example is never installed by default.

## Requirements

Each area declares in `requires` what it needs, as namespaced capability ids, never a provider. The app crosses that with what the person has connected and answers one of two things: "You can cover this with A or with B", or "You need an agent that can X" with a "Connect another" action. `requires` only informs in the MVP: the router does not read it.

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
| `model.text` | write text |
| `model.tools` | use tools |
| `model.structured` | give structured responses |
| `model.local` | run on your computer |
| `executor.code` | work with code |
| `executor.files` | work with files |

Candidates are saved connections' catalog models, active executors and, in Mock, `mock` ("Mock (example)"). A candidate covers an area when it has every requirement. If none does, `missing` lists what the closest candidate (most requirements; ties by id) lacks, and `action` is `"connect"`. An id outside the list is shown as written ("You need an agent that can model.vision").

An area that is "Sensitive" (`sensitivity: sensitive`) or "Local only" (`local_only`, on the area or the office) counts only local candidates, and carries the note "This area is Sensitive: it can only use agents on your team today" or "This area is Local only: it never leaves your computer" ("Local only" wins when both apply).

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

**Where the levels live.** `office.yaml` declares the first level. What a person sets is kept in the entry of the office in `installed/offices.json`, under the optional key `privacy` (`{areas: {id: {sensitivity, local_only, set_by, set_at, source, cloud_allowed, cloud: {set_by, set_at, source}}}, office: {local_only, set_by, set_at, source, cloud_allowed, cloud: {set_by, set_at, source}}}`). An office without it reads as before, and `OFFICES_SCHEMA` does not change. `source` is `"person"` for a setting somebody made and `"office"` for the level the `office.yaml` declares. Cloud permission is person-only; it is never read from `office.yaml`.

**Raising.** `raise_level(office_id, area_id, sensitivity=, local_only=, actor=)` goes from no level to any, from `normal` to `sensitive` and from `local_only: false` to `true`; `area_id = None` is the whole office and takes only `local_only`. It works from the services, the API and the CLI, and the event `office.privacy_changed` records `office`, `area`, `from`, `to`, `direction` and the actor (a level reads `unset`, `normal` or `sensitive`, plus `+local_only` when it applies). Asking for something lower is `409 invalid_state` with `details.reason = "office_level_lowering_needs_gui"`.

**Cloud permission.** `privacy()` returns the office and each area's own `cloud: {allowed, set_by, set_at, source}`, plus `cloud_effective` for each area. Permission is `false` by default; an area's explicit value overrides the office, otherwise it inherits the office. It is a stored permission set once for the office or an area, not a per-run confirmation. `POST .../privacy/raise` and `multiagent office privacy` can set `cloud_allowed: false` / `--no-cloud` without an approval, taking effect immediately. `POST .../privacy/cloud-request` returns `{approval_id, sha256}` for a hidden one-use `office_privacy_change` approval; only the GUI spends its code to grant the exact office or area scope. The CLI's `--request-cloud` starts the request and directs the person to «Privacidad»; neither `/api/v1` nor the CLI grants it. Reimport keeps person-set cloud permissions and never imports one from `office.yaml`.

An area without a level does not use the permission until it has one (it asks before the cloud). On a new run of an allowed «Normal» area, `RunServices.create` applies the stored permission to the internal request and records `cloud_permission_applied` with the office, area, scope, setter and time. Revoking permission affects new runs; a queued run keeps the authorization already stored with it. `sensitive` and `restricted` still block cloud use. Without permission, an automatic step with a local preferred model and cloud fallback is pinned to `local`; a cloud-preferred automatic step fails before saving with `422 cloud_permission_required`. Profiles and routing preferences already exclude cloud candidates when the request has no cloud confirmation. Mock and dry runs are unchanged. See [Model routing](model-routing.md#automatic-cloud-permission).

```text
GET  /api/v1/offices/{id}/privacy           # {office, local_only, cloud, areas: [{id, sensitivity, local_only, data_class, cloud, cloud_effective, ...}], note}
POST /api/v1/offices/{id}/privacy/raise     # {actor, area?, sensitivity?, local_only?, cloud_allowed?: false}
POST /api/v1/offices/{id}/privacy/cloud-request # {area?} -> {approval_id, sha256}
multiagent office privacy OFFICE [--area AREA] [--sensitive] [--local-only] [--no-cloud | --request-cloud] [--actor user:local]
```

**Lowering** has no route in `/api/v1` and no command: it is done in «Privacidad» of the GUI. `request_lowering` creates the hidden one-use approval `office_privacy_change` (never listed in «Aprobaciones» or «Pendientes»), bound by its `sha256` to `{office, area, from, to}`. The person decides it with the 120-second code. `lower_level` spends it once, stores the lower level and writes the event. Without an approved, unspent approval of exactly that change, or if the level moved since the person saw it, it is `409 invalid_state`, `details.reason = "office_privacy_change_not_approved"`.

**Reimport** keeps the highest level between what the area had and what the new `office.yaml` declares: `sensitive` over `normal`, any level over none, and `local_only` is only added. A level a person raised therefore survives a reimport that declares less, with `source: "person"`; `local_only` of the whole office does too. An area that joins takes what the office declares.

**The note** of «Privacidad» is fixed (`PRIVACY_NOTE`, returned by `privacy()` as `note`):

- "Passwords, keys and tokens: I will always ask you about them one at a time."
- "Areas marked Local only."
- "With an executor (Claude, Codex, Copilot, agy), the agent can read the entire office working directory, not just the task."

**GUI privacy view.** «Privacidad» shows the level and cloud permission for the office and each area. Raising a level
and removing cloud permission take effect immediately. Lowering a level and allowing cloud models require the one-use
approval code; these changes are available only in the GUI, not through `/api/v1` or the CLI. An area marked
«Sensible» or «Solo local» never uses the cloud, even when cloud permission is allowed.

For an area with no level, simple mode asks «¿Qué tan privados son los datos de <área>?» once, before the first send
outside the PC. The person chooses «Normal», «Sensible» or «Solo local»; the GUI saves the choice through
`privacy/raise` and retries the request once. If it fails again, the error is shown without asking again. In advanced
mode the `office_level_required` error is shown with a link to «Privacidad». «Normal» can use cloud models only when
cloud permission is allowed there.

The GUI routes are `GET /gui/api/offices/{id}/privacy`, `POST /gui/api/offices/{id}/privacy/raise`,
`POST /gui/api/offices/{id}/privacy/lower-request`, `POST /gui/api/offices/{id}/privacy/lower`,
`POST /gui/api/offices/{id}/privacy/cloud-request` and `POST /gui/api/offices/{id}/privacy/cloud`. The GUI asks for
the one-use code for a lower level or cloud permission; raising a level or removing cloud permission does not ask for it.

**Executors.** A launch of an executor (`ExecutorLaunchRequest`) does not name a package, so there is no path from an area to cover. The `restricted` class of an executor launch (`executors/privacy.py`) is still set by its caller, not by an office.

## The install card

The install card reviews an office before it is added to the installed packages. Preparing an office validates and
copies it under `installed/.staging/<stage_id>/`; it does not register its areas or load its code. The card is bound to
the prepared folder's SHA-256 fingerprint. If its content changes, installation stops and the office must be prepared
again.

The card shows the office name, each area that is new, changed or removed, its members and role assignments,
requirements, privacy level, local-only setting, and declared workflows, contracts and roles. A reimport omits areas
that have not changed. Adding one area shows the office it will join. The fixed notice is:

> Someone else wrote the instructions in this office. An active executor follows them within its working directory.

### Data-only and code offices

A data-only office can be prepared in either GUI mode. An office containing code (including `contracts.py`,
`functions.py`, or scripts in a skill) needs advanced mode with the `install_code` action. If it contains
`functions.py`, it also needs the `trust_functions` action. In simple mode the preparation fails with
`advanced_mode_required`; no code is run.

After the one-use office approval, code is installed only through the office batch path. A `functions.py` file is
trusted and loaded only for its installed area. The existing API and CLI office import paths remain data-only and
continue to reject code with `office_has_code`.

### Scan and approvals

When the optional vulnerability scanner is available, the office uses one vulnerability-query approval for its whole
fingerprint. The approval lists the dependency manifests across its areas. After approval, the scanner checks each
area in sequence and stores the result under that area's package name and fingerprint. Without the scanner in Mock,
or when scanning is off, the office is marked `not_installed` or `off` and no vulnerability-query approval is created.
See [Bundle scan](security/bundle-scan.md) for the scan states and the data sent to the vulnerability service.

The card also creates one hidden `office_install` approval, bound to the same fingerprint and the office. Together
with the optional vulnerability-query approval, this is at most two decisions for an office. Its location records
the stage ID, level and whether the person must acknowledge risk. It is not listed under general pending approvals;
the office card decides it using the GUI's existing one-use code. A decision is usable once. The server requires
`risk_ack: true` when the office contains code or a scan state needs review. If the query is still pending when the
card is first assembled, that acknowledgement remains attached to the same approval while the scan finishes.

Approving the card installs or updates all affected areas together, proposes and approves each changed area's team,
and seeds every scanned area's cache so the first run does not ask for the same scan again. Unchanged areas retain
their approved team versions. When adding an area, the scan covers the existing areas and the new area as one office;
the office decision covers the scan results for all of them. The event is `office.installed` for a new office or
`office.reimported` for an update and includes the approval ID.

Choose **Cancel** to reject the pending office approval and discard the stage. A stage expires after one hour and
cannot be installed afterward. Passwords, keys and tokens still require their own approvals, and each plan remains a
separate decision.

The service methods are `stage_zip`, `stage_folder`, `card`, `install_staged` and `discard_stage` on
`OfficeServices`. OFI-T04 does not add `/api/v1` routes or CLI commands; the GUI routes are supplied by VOF-T02.

## Export and import

`multiagent office export ID [--area AREA] [--include-code] [--output FILE.zip]` exports an installed office or one area from its installed copies. Without `--output`, the CLI writes `ID.zip` or `ID-AREA.zip` in the current directory and refuses to overwrite an existing file. The API endpoint is `POST /api/v1/offices/{id}/export`; it accepts `{area?, include_code?}` and returns the archive as `zip_base64`.

The archive contains a rewritten `office.yaml`, the selected files from each area, and a `manifest.json` using schema `maf.office-export/1`. The manifest records each file's SHA-256 and size, the team digest for every exported area, and any portability warnings. Each exported area uses `path: areas/<area-id>` in `office.yaml`.

By default, an area contributes `workflows/*.yaml`, `prompts/**`, `catalog/agents.yaml`, `team.yaml`, `contracts.yaml`, `README.md`, and `samples/**`. Every included file must use a suffix from `CODE_FREE_SUFFIXES`. Other files are left out and reported in `warnings`. `--include-code` additionally includes only the area's root `contracts.py` and `functions.py`; it does not include other Python files. Text files in the archive are screened by the secret gate. If a secret is found, the export fails with `422 export_secret_found`; the response identifies only the path, line, and secret type.

The export never includes databases, runs, `data_dir`, `.env`, `.local`, logs, input resources, `executor_runs`, or `catalog/models.yaml`. A workflow step that selects a model defined in the area's omitted `catalog/models.yaml` produces an `export_model_not_portable` warning identifying the workflow and step; the archive is still created.

An exported office can be imported with `multiagent office import FILE.zip` or `POST /api/v1/offices` using `zip_base64`. An area-only archive has one area and can be added to another installed office with `--into ID` or the API's `into` field. When an archive contains `manifest.json`, import requires its schema, file list, sizes, and hashes to match the extracted files exactly. A mismatch fails with `422 office_manifest_mismatch` and the affected path. Archives without a manifest remain supported for hand-authored imports. Safe extraction rejects unsafe paths and symbolic links before import.

## Scene

`GET /api/v1/offices/{id}/scene` and `multiagent office scene ID` return a read-only `maf.office-scene/1` snapshot. The
same document is available with `--json`. It contains the office id, name and state; the coordinator state and its
current plan; each area's data class, degraded fixes, state, label and members; labelled office relations; and whether
the run list was truncated. It deliberately omits machine paths, run inputs and outputs, and step text.

The scene never checks connections: it uses the latest result checked in «Apps y agentes» or the installation detail;
without a prior check, it does not mark `requirement_missing`.

Runs in `queued`, `running`, `waiting_human` or `paused` remain visible. Other runs are included only when updated in
the last 24 hours. Each area is read with one database query, capped at 500 runs; `truncated` is true when an area
reaches that cap. An area with no database returns an empty run list. The scene reads persisted step-role, member and
model data; it does not load an area package.
When the last step of a run names no role, the scene gives it to the one agent member of the area whose `agent` is the step's agent; with no match or several, it stays with the area.

| Source state | Scene state |
| --- | --- |
| `running` or `paused` | `doing` |
| `queued` after a request for changes | `changes` |
| `queued` without a request for changes | `ready` |
| `waiting_human` | `review` |
| `failed` or `interrupted` | `failed` |
| `completed` in the window | `done` |
| `cancelled` or `rejected` in the window | `cancelled` |

When multiple sources apply, the scene uses the L17 priority order:
`review` > `changes` > `failed` > `doing` > `blocked` > `ready` > `todo` > `done` > `cancelled`. With no source, the
state is `idle` and its label is `Libre`. Labels come from the shared task-label map. A member's `worker` is the model
id of the latest attempt of the winning step, or `—` when there is none.
An area's state, label and since are the L17 winner across its own candidates and all member candidates; a member's
worker is not promoted to the area.

The coordinator follows open office plan proposals and approved plans with unfinished tasks. `proposed` is `review`;
an approved plan is `doing` until every task is `done` or `cancelled`, then `done`. Rejected proposals do not count.
The «Oficinas» view draws on this schema without changing it.

## Orchestrator

`multiagent office plan-draft OFFICE TEXT [--confirm-cloud]` and
`POST /api/v1/offices/{id}/plan-draft` prepare a plan for an installed office.
The draft is not saved, approved, or started. Saving it still requires the
confirmation handled by the new-session plan flow.

From «Oficinas», «Pídele algo a la oficina» opens «Nueva sesión» with that
office selected. Its coordinator can prepare a plan draft from the workflows
in its areas; opening the screen does not save or run anything. «Pedir a esta
área» opens that area's options in «Sesión única».

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

Two level-1 (data-only) office templates live in `examples/offices/` with synthetic data, `preferred_model: local_default`, and `checkpoint_after: true`. Each has a `drafting` agent and a `review` role assigned to a `Reviewer` who checks its output. They are examples to import, not defaults: they are not part of the installed package, nothing loads them at startup, and the framework ships no default offices.

- **`examples/offices/cafe`** (`cafe` — *Example Café*):
  - `menu` (`normal`, `[model.text]`): `weekly_menu` workflow producing `WeeklyMenu`.
  - `purchasing` (`normal`, `[model.structured]`): `order_list` workflow producing `OrderList`.
  - `notices` (`normal`, `[model.text]`): `write_notice` (`Notice`) and `reply_message` (`Reply`).
  - Default plan: `menu` (`weekly_menu`), then `notices` (`write_notice`).
- **`examples/offices/school`** (`school` — *Example School*):
  - `planning` (`normal`, `[model.text]`): `lesson_plan` workflow producing `LessonPlan`.
  - `follow-up` (`sensitive`, `local_only: true`, `[model.text, model.local]`): `progress_note` workflow producing `ProgressNote`.
  - `notices` (`normal`, `[model.text]`): byte-identical copy of `cafe/areas/notices`.
  - Default plan: `planning` (`lesson_plan`), then `notices` (`write_notice`).

Import a template from a repository checkout (teams stay `proposed` until approved in the GUI):

```text
multiagent office import examples/offices/cafe
multiagent office import examples/offices/school
```

The `examples/` directory is only present in a repository checkout; it is not included in the built wheel (`pyproject.toml`).
