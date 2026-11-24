# Planning and plan approval

A plan is a task group with dependencies and a run specification for each task. The
`multiagent.plan/1` document names an existing task group and maps task IDs to a
workflow, optional bundle, and text inputs. `services.plans.status(document)`
reports task states, blockers, and latest linked runs. `advance(document,
actor=...)` reconciles finished runs and starts ready tasks as ordinary runs
through the existing workflow engine and backends. Call `advance` again after
runs finish to move their tasks and start newly ready work. A failed run fails
its task and leaves dependents blocked. [Task lifecycle](TASKS.md) explains the
underlying states and dependencies.

## Choose a planner

List available names with `multiagent plan planners`, `GET /api/v1/planners`,
or `services.planning.planners()`. The built-in choices are:

| Name | Input and result |
| --- | --- |
| `manual` | Validates the operator's `tasks` list. |
| `deterministic` | Expands an ordered `template.stages` list; each stage depends on the previous one unless `depends_on` is supplied. |
| `model` | Asks the configured new-session model (`gui_new_session_model`) to propose tasks from a goal and available workflows. |

Trusted Python code can register another planner. Every planner returns the same
`multiagent.plan_proposal/1` shape and passes validation of task IDs, dependency
cycles, workflow references, and text inputs. Proposing creates no task or run.
The model planner uses the existing model router, egress and budget guards, and
usage recording. A cloud model requires explicit `confirm_cloud` (CLI:
`--confirm-cloud`); Mock mode can use a synthetic fixture. It does not plan
automatically in the background.

A short manual request file, `request.json`, is:

```json
{
  "group_id": "demo-plan",
  "goal": "Prepare a sample post",
  "tasks": [
    {
      "id": "demo-draft",
      "title": "Draft the post",
      "depends_on": [],
      "workflow": "social_post",
      "inputs": {}
    }
  ]
}
```

The workflow must exist in the active definitions. Replace the example inputs
with the text fields required by that workflow before approving a real plan.
For `deterministic`, supply `group_id`, `goal`, and
`"template": {"prefix": "demo", "stages": [{"id": "draft", "title": "Draft",
"workflow": "social_post"}]}`. For `model`, supply `group_id` and `goal`;
`prefix` is optional.

## Review and decide

Save a returned proposal to keep it in `proposed` state. The saved record has
an ID, revision, status, creator, and history. `get` or `list` returns its
current proposal and history. While it is proposed, `edit` replaces the entire
task list, and `reassign` changes one task's `preferred`, `workflow`, or
`bundle`. Both validate the result, require `expected_revision`, and raise
the revision. Approval also requires the current revision. A stale revision or
a closed proposal returns a conflict. Rejection closes a proposal without
creating tasks or runs. Approved and rejected proposals cannot be edited.

Saving and editing may identify a user, person, agent, or executor as actor.
Only an actor whose ID starts with `user:` can approve or reject. Approval
creates the task group if needed, creates tasks with their dependencies, and
calls `services.plans.advance` once to start ready runs. Further progress
requires another `advance` call (see [Advance by proposal](#advance-by-proposal)).
History records save, edit, reassign,
approve, and reject with actor, time, channel, note, revision, and task changes
for edits. Approval and rejection also attempt to write a decision to
`services.decisions`; the response includes `decision_id`, or
`decision_record_error` if that separate write fails. See
[decisions in the API guide](API.md).

### Approve the team first

A task can use workflow steps with `role:`, which need an approved team for
the bundle (see [teams](TEAMS.md)). Before it creates anything, `approve`
checks that every bundle with such steps has an approved team version. If one
does not, approval fails with a `team_not_approved` conflict (HTTP 409) that
lists each bundle and the roles it needs under `blockers`. No group or task is
created and the proposal stays `proposed`, so approve the team and then the
plan. `get` and `list` carry the same `blockers` list (empty when none), so a
client can warn before approval. The check reuses the step-role binding that
a run uses; it does not copy it.

### Advance by proposal

For an approved proposal, `services.plan_proposals.advance(proposal_id,
actor=..., channel=...)` advances the plan saved in the proposal, so the plan
document does not have to be kept in a file. It returns the plan status, the
tasks that moved under `advanced`, and `nothing_to_advance`. When nothing
moved, `nothing_to_advance` is true and the call is not an error. A proposal
that is not approved is a conflict (`not_approved`).

The plan also advances by itself. When a run linked to a task of an approved
plan ends, the plan advances once, with the actor `system:worker` and the
channel `worker` (the only non-user actor `advance` accepts; approving and
rejecting still need a `user:` actor). This happens when the worker finishes a
run, when `approve`, `reject` or `cancel` end it (the normal case in supervised
mode), and when a task of the plan is accepted (the `accept` action or a passed
gate). The next task starts when its dependency is satisfied: with
`requires="accepted"` (the default) once the person accepts the previous task or
it passes its gate; with `requires="submitted"`, once its run ends. The advance
itself never triggers another one. A lock per group stops two runs that end together from
advancing the plan twice, and a conflict is retried once. Each advance that
moved something leaves the team event `plan.advanced` with
`{proposal_id, run_id, advanced}`; if it still fails, the team event
`plan.advance_failed` carries the `reason` and the run keeps its status. The
events carry the plan's group and the task of the run, and the plan thread in
the GUI shows them. Runs recovered when the server starts do not advance the
plan; use "Advance the plan" (or the call above) for them, and after a
`plan.advance_failed`. The proposal's history is not changed.

## CLI

The CLI talks to the HTTP API. With the server running, use:

```text
multiagent plan planners
multiagent plan propose --planner manual request.json
multiagent plan save proposal.json --actor user:ana
multiagent plan proposals --status proposed
multiagent plan show PROPOSAL_ID
multiagent plan edit PROPOSAL_ID revised.json --revision 1 --actor user:ana
multiagent plan reassign PROPOSAL_ID demo-draft --preferred agent:writer --revision 2 --actor user:ana
multiagent plan approve PROPOSAL_ID --revision 3 --actor user:ana
multiagent plan reject PROPOSAL_ID --actor user:ana --note "Needs a new scope"
```

`proposal.json` is the JSON or YAML proposal returned by `propose`;
`revised.json` contains the full replacement `tasks` list (or an object
with that key). The approval and rejection lines are alternatives for a
proposed ID. The CLI defaults the proposal actor to `user:local` and records
the channel as `cli`. To advance an approved proposal, name it:

```text
multiagent plan advance --proposal PROPOSAL_ID --actor user:ana
```

To inspect and continue a plan from its document, save the `plan` object as
`plan.json` and run:

```text
multiagent plan status plan.json
multiagent plan advance plan.json --actor user:ana
```

## HTTP API

`POST /api/v1/plans/propose` accepts
`{"planner":"manual","request":{...},"confirm_cloud":false}`. Save its
response with `POST /api/v1/plan-proposals` and
`{"proposal":{...},"actor":"user:ana"}`. `GET /api/v1/plan-proposals`
supports `group_id` and `status` filters; `GET
/api/v1/plan-proposals/{id}` includes the revision and history.

Use `POST /api/v1/plan-proposals/{id}/edit` with `tasks`, `actor`, and
`expected_revision`; `/reassign` also needs `task_id` and at least one of
`preferred`, `workflow`, or `bundle`. `/approve` needs `actor` and
`expected_revision`; `/reject` needs `actor`. These requests may include
`channel` and `note`; the default channel is `api`. For example:

```json
{"actor": "user:ana", "expected_revision": 1, "note": "Reviewed dependencies"}
```

Send that body to `POST /api/v1/plan-proposals/{id}/approve`. For an
existing task group, `POST /api/v1/plans/status` accepts
`{"document":{...}}`, and `POST /api/v1/plans/advance` adds
`"actor":"user:ana"`. Both use the `multiagent.plan/1` document, such as
the `plan` field in a proposal. To advance an approved proposal without its
document, send `{"actor": "user:ana"}` (and optionally `"channel"`) to
`POST /api/v1/plan-proposals/{id}/advance`. The [API reference](API.md) lists
every route.

## GUI

The [Planes view](GUI.md) at `/gui/#plans` lists saved proposals and filters
them by status. Open a proposal to inspect tasks, dependencies, assignment,
revision, and history. While it is proposed, edit the task list, reassign a
task, or approve or reject it; the view asks for confirmation and an optional
note. The server records actor `user:operator` and channel `gui`. A stale
revision reloads the proposal. An approved proposal shows **Avanzar el plan**:
after a confirmation it advances the plan once and says which tasks moved, or
that there was nothing to advance. The GUI reviews saved proposals; besides Python, the CLI and
the HTTP API, **Nueva sesión** can prepare one (next paragraph).

**Nueva sesión** can prepare a plan. When the request needs more than one workflow
("summarize the report, then draft an email"), the chat suggests a plan instead of a
single workflow and shows its tasks and the teams it lacks. In Mock the plan comes from the
parts of the request: it is split at `y luego`, `y después`, `después` and `luego`, each
part takes the workflow that shares the most words with it, and each task depends on the
previous one. With a real model the `model` planner drafts it, after the same cloud
confirmation and secret review as the chat. Drafting saves nothing. Saving needs a
confirmation and stores a plan proposal in `proposed` status and, for each bundle whose
`role:` steps have no approved team, a proposed team version. It approves nothing and
creates no task or run: **Aprobar** and **Rechazar** in the proposal's thread are the same
as for any proposal, and approval still needs a `user:` actor and the approved team.

When an office is selected, both the interpreter and planner see only that office's areas.
An office with any area that is sensitive, restricted, or has no privacy level never sends
the request to a cloud model: interpretation falls back to keyword matching, and planning
uses the office's `default_plan` or the deterministic request template. A cloud plan for an
office with an unset area requires its privacy level to be set first. See
[Office orchestration](OFFICES-orchestrator.md) for planner selection and task mapping.

A proposal also opens as a thread of the chat at `#new/plan/<id>` (read only,
`GET /gui/api/threads/plans/{proposal_id}`). Its card lists each task with its
inputs as readable text and has **Aprobar** and **Rechazar**, which send the
revision on screen as `expected_revision` to the routes above. For each bundle
whose `role:` steps have no approved team, a message links to the proposed team
version (or to **Equipos**) and **Aprobar** stays disabled with the reason, the
same `team_not_approved` check that approval makes. Once approved, the thread
shows one message for each task with its run. Pending proposals in **Pendientes**
link to this thread.

## Current limits

- The plan schema and task lifecycle are partial: it does not yet express all
  subtasks, capabilities, contracts, review or integration steps, nor full
  editing during execution. S8.A1 and S8.B1 remain partial.
- Planners do not implement automatic planning, replanning, or all proposed
  external planner integrations. S8.D3 and S8.E1 remain open.
- Approval's group and task creation uses separate task-service transactions.
  An unexpected failure between writes can leave some tasks created while the
  proposal remains `proposed`; a retry can then conflict on an existing task.
  Run creation and decision registration are also separate from proposal
  approval.
- S13.B1 remains partial: escalation policy does not yet include a risk level
  or project context. Concurrent `advance` calls can race on a task claim,
  causing one call to fail with `invalid_state`.
