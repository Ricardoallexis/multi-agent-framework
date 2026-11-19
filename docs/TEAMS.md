# Teams

A team is an optional `team.yaml` file inside a definition bundle. It declares roles, human or agent members, role assignments, and relationships between roles.

> **Current runtime scope:** a run binds the bundle's approved team version, and only workflow steps that name a `role` use it (see [Roles in workflow steps](#roles-in-workflow-steps)). Other team fields (`contracts`, `capabilities`, `permissions`, relationships) are not enforced at run time.

## Team definition (`team.yaml`)

Here is a trimmed excerpt from [`examples/bundles/general/team.yaml`](../examples/bundles/general/team.yaml):

```yaml
schema_version: 1
id: general_team
description: Example team definition for the general bundle combining bundle agents and a human lead.

roles:
  - id: lead
    purpose: Coordinates reviews and approves final deliverables.
    rules:
      - Approve outputs only after quality review passes.
    skills: []
    contracts:
      - DocumentSummary
      - Recommendation
      - ReviewReport
    capabilities:
      - doc.review
    permissions: []

members:
  - id: human_lead
    kind: person
    name: Human Coordinator
  - id: doc_analyst_agent
    kind: agent
    agent: document_analyst

assignments:
  - role: lead
    member: human_lead
  - role: analyst
    member: doc_analyst_agent

relationships:
  - type: reports_to
    from: analyst
    to: lead
  - type: reviews
    from: reviewer
    to: writer
  - type: delegates_to
    from: lead
    to: analyst
```

Every model rejects unknown fields:

- **Top level:** `schema_version` (must be `1`), `id`, optional `description`, `roles`, `members`, `assignments`, and `relationships`.
- **Top-level `mode` (optional):** `supervised` or `autonomous` (validated with `invalid_mode`); see [Run operating modes](PERMISSIONS.md#run-operating-modes).
- **`roles[]`:** `id` (unique across roles), `purpose`, optional `rules`, `skills`, `contracts`, `capabilities` (validated capability IDs), and `permissions` (`read`, `write`, `destructive`, `external`).
- **`members[]`:** `id` (unique across members), `kind` (`agent` or `person`), `agent` (required for `agent` and forbidden for `person`), and optional `name`.
- **`assignments[]`:** `role` and `member`, referencing defined role and member IDs.
- **`relationships[]`:** `type` (`reports_to`, `reviews`, or `delegates_to`), `from`, and `to` (distinct role IDs; `reports_to` must be acyclic).

`multiagent/teams/schema.py` also supports `.yml` and `.json` files via `load_team`, while bundles load `team.yaml`.

## Bundle and least-privilege validation

Loading a bundle checks `team.yaml` syntax, required fields, duplicate IDs, unknown role or member references, unknown bundle agents (`unknown_agent`), self-relationships, and `reports_to` cycles.

Validating a bundle (`services.definitions.validate_bundle`, `POST /api/v1/bundles/{name}/validate`, or `multiagent bundle-validate <name>`) cross-references `team.yaml` against the bundle's workflows, output contracts, skills, and tool registry:

- **Errors** fail with `422 invalid_definition` and `details.team`:
  - `team_unknown_contract`: a role names a contract absent from `contracts.py`.
  - `team_unknown_skill`: a role names a skill whose `.md` file is missing from `skills/`.
  - `team_permission_missing`: a workflow step's agent holds one or more roles, the step's tools resolve, and the union of those roles' `permissions` lacks a required tool impact class (`read`, `write`, `destructive`, `external`).
- **Warnings** return `200` (`valid: true`) with a `warnings` list:
  - `team_agent_unassigned`: a workflow step's agent has no assigned role in the team.
  - `team_tool_unresolved`: a step's tool or capability cannot be resolved statically.
  - `team_permission_unused`: a role declares a permission that none of its assigned members' steps need.

## Versions, diffs, and human approval

Team definitions in a bundle follow a versioned approval lifecycle (`tmv-<12 hex>`):

1. **Propose:** validates the bundle and `team.yaml`, computes the canonical JSON `sha256`, and creates a `proposed` version (or `draft` with `draft=True` / `--draft`).
2. **No-change guard:** if the `sha256` matches the currently active `approved` version, `propose` fails with `422 invalid_request` (`details.reason: "no_changes"`) and saves nothing.
3. **Submit:** moves a `draft` version to `proposed`.
4. **Approve or reject:** only `proposed` versions can be decided (`409 invalid_state` otherwise), and only a `user:` actor can decide (`422 invalid_request` with `details.reason: "confirmation_required"` otherwise). Decided versions are immutable.
5. **Active version:** approving a new version makes it active (`active: true`); earlier approved versions stay in history (`active: false`).
6. **Structural diff:** each version computes a diff against the relevant approved version (or an explicit `against` version ID in `services.teams.diff`) across `team_fields` (`id`, `description`, `schema_version`), `roles`, `members`, `assignments`, and `relationships`.

When a new run is queued for a bundle that has an approved team version, `services.runs.create` logs a `team_version_bound` event with `id`, `version_id`, `bundle`, `team_id`, `version`, `sha256`, and `team` (the approved team itself). The event is written in the same transaction as the run, so no worker can take the run before it is bound. Bundles without `team.yaml` or without an approved version do not emit this event.

## Roles in workflow steps

A model step declares `role` beside `agent` in its workflow YAML. For example, the
[`summarize_document` workflow](../examples/bundles/general/workflows/summarize_document.yaml)
has `extract_key_points` with `agent: document_analyst`; the
[`general` team](../examples/bundles/general/team.yaml) assigns that agent's
member to `analyst`. Add the role to that step as follows:

```yaml
- id: extract_key_points
  agent: document_analyst
  role: analyst
  prompt_id: general/extract_key_points
  prompt_version: 1
  contract: KeyPoints
```

Approve the bundle's `team.yaml` before starting a run with a role. At run
creation, the latest approved team version is saved in the run's
`team_version_bound` event. The role is resolved from that saved version, even
if a different team version is approved later. A run without an approved team
cannot execute a step that declares a role.

- **Member and agent:** the role must have exactly one assigned `agent` member
  whose `agent` matches the step's agent. With `agent: null`, the role must have
  exactly one assigned agent member; its agent runs the step. A role does not
  replace an agent explicitly named by the step.
- **Prompt and context:** the step gets a `## Role: <id>` section containing
  that role's `purpose` and `rules`. It uses the role's skills when the step
  declares no skills. A step receives only its own role section; prior step
  outputs still flow through the workflow as usual.
- **Trace:** `step_started` records `role`, `member`, `team_id`,
  `team_version_id`, and `team_version`. These same fields appear on completed
  attempts and on `step_started` rows of the run report. Steps without a role
  omit them.

For a run ID, `multiagent status <run_id>` shows the `step_started` events.
`multiagent run-report <run_id>` returns the JSON report; add
`--format markdown` for its **Step roles** list. Use
`multiagent attempts <run_id> extract_key_points` for the completed attempts
of that step. The HTTP equivalents are `GET /api/v1/runs/{run_id}` and
`GET /api/v1/runs/{run_id}/events` for the trace,
`GET /api/v1/runs/{run_id}/report` for the report, and
`GET /api/v1/runs/{run_id}/steps/{step_id}/attempts` for attempts. The
[API guide](API.md#step-attempts-and-comparison) describes the response fields.
The operator GUI shows role, member, and team version in the run's step list
and completed attempts, with `run.role` help; see the
[GUI guide](GUI.md#views).

Role binding errors are `422 invalid_definition` at run creation, or a failed
run before a model call if the workflow definition changed while queued. The
issue code identifies the cause:

| Issue code | Meaning |
| --- | --- |
| `role_without_team` | No approved team version was bound to this run. |
| `team_binding_invalid` | The bound event has no readable team or its team does not match the recorded `sha256`. |
| `unknown_role` | The bound team version has no role with the step's `role` ID. |
| `role_member_not_found` | The role has no assigned agent member for the step's agent, or none at all with `agent: null`. |
| `role_member_ambiguous` | More than one assigned agent member qualifies; specify an agent when that distinguishes them. |

Steps without a `role` run as before, with or without a bound team. Function
steps cannot name a role. Team permission validation currently uses the
step's explicit agent assignments; it does not check `step.role` when
`agent: null`.

## Decision register records

After `approve` or `reject` persists the team version decision, `TeamServices` records an entry in the decision register (`services.decisions.record_review`):

- `title`: `Team <team_id> version <n>`
- `body`: the bundle name, `sha256`, and counts of added, removed, and changed `roles`, `members`, `assignments`, and `relationships`
- `proposed_by`: the actor who proposed or submitted the version
- `decided_by`: the `user:` actor who decided
- `accepted`: `true` on approve, `false` on reject
- `outcome`: `approved` or `rejected` (with `: <note>` when a note is supplied)
- `team_id`: the team `id`; `project`: the bundle name

On success, the approve/reject response includes `decision_id`. If recording the decision fails, the team approval or rejection remains valid and the response carries `decision_id: null` and `decision_record_error` with the exception class name.

## Organization chart

`org_chart` builds a tree model from `reports_to` relationships and keeps `reviews` and `delegates_to` separate in `links`. It rejects unknown roles, multiple managers per role (`multiple_managers`), and `reports_to` cycles (`reports_to_cycle`).

- **Roots:** one top role sets `root` and `forest: false`; multiple top roles set `root: null`, list them in `roots`, and set `forest: true`.
- **Position statuses:** `occupied` (has assigned members), `vacant` (no assigned members), `pending` (unapproved version, or a role added or whose occupants changed in a later proposed version), and `pending_removal: true` (a role removed by a later proposed version).
- **Source selection:** without `version_id`, it selects the approved version (overlaying the latest later `proposed` version if present), or else the latest `proposed`, `draft`, or `team.yaml` on disk. Passing `version_id` renders that version without the pending overlay.
- **Mermaid:** `mermaid` contains a deterministic `flowchart TD` diagram using `-->` for `reports_to`, `-.->|<type>|` for `reviews` and `delegates_to`, and `occupied`, `vacant`, and `pending` classes.

## Python, HTTP, and CLI usage

### Python

```python
from multiagent.services import ApplicationServices

services = ApplicationServices.create(bundles={"general": "examples/bundles/general"})

validation = services.definitions.validate_bundle("general")
v1 = services.teams.propose("general", actor="agent:codex", channel="python")
approved = services.teams.approve(v1["id"], actor="user:ana", channel="python", note="Approved")
chart = services.teams.org_chart("general")
print(approved["decision_id"], chart["root"], chart["mermaid"])
```

`services.teams` also provides `submit`, `reject`, `get`, `list`, `approved`, and `diff`.

### HTTP

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/bundles/{name}/validate` | Validate bundle workflows and `team.yaml` |
| `POST` | `/api/v1/teams/{bundle}/versions` | Propose a version (`{actor, draft?, channel?}`) |
| `GET` | `/api/v1/teams/{bundle}/versions` | List versions (`?status=draft\|proposed\|approved\|rejected`) |
| `GET` | `/api/v1/teams/versions/{version_id}` | Get one version and its diff |
| `POST` | `/api/v1/teams/versions/{version_id}/submit` | Submit a `draft` version (`{actor, channel?}`) |
| `POST` | `/api/v1/teams/versions/{version_id}/approve` | Approve a `proposed` version (`{actor, note?, channel?}`) |
| `POST` | `/api/v1/teams/versions/{version_id}/reject` | Reject a `proposed` version (`{actor, note?, channel?}`) |
| `GET` | `/api/v1/teams/{bundle}/approved` | Get the active approved version or `null` |
| `GET` | `/api/v1/teams/{bundle}/org-chart` | Get the org chart JSON and Mermaid (`?version_id=`) |

### CLI

```powershell
multiagent bundle-validate general
multiagent team propose general --actor user:local
multiagent team propose general --draft --actor user:local
multiagent team submit <version_id> --actor user:local
multiagent team list general --status proposed
multiagent team show <version_id>
multiagent team approve <version_id> --actor user:local --note "Approved"
multiagent team reject <version_id> --actor user:local --note "Needs changes"
multiagent team approved general
multiagent team org-chart general --mermaid
```

Pass `--version <version_id>` to `multiagent team org-chart` to inspect a specific version.

## Operator GUI

The **Equipos** view (`#teams` and `#teams/<bundle>`) in the local operator GUI lists registered bundles that define `team.yaml`, shows the nested organization chart with position statuses and Mermaid source, displays version diffs, and lets the operator approve or reject pending proposals as `user:operator` on the `gui` channel. Proposing new versions stays in the CLI and HTTP API; see [Operator GUI — Views](GUI.md#views).

The **team card** shows what a version sets: the team `mode` and, for each position, its rules, permissions and members, plus the model that its agent resolves to in the bundle's catalog (or the reason when it cannot be resolved). It appears in **Equipos** and, for a proposed version, as a one-stop thread of the chat at `#new/team/<version id>` with **Aprobar** and **Rechazar**; both call the same approve and reject routes as above, with the actor and channel fixed by the server. A first version is compared with an empty team, mode included. The card is read from `GET /gui/api/teams/versions/{id}` and the thread from `GET /gui/api/threads/teams/{version_id}`; the card is plain text and changes nothing.
