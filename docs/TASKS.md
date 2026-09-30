# Tasks, dependencies, and team events

Multi-Agent Framework can coordinate work as **tasks** grouped in **task groups**
(a stage or phase of work). Tasks declare which other tasks they depend on, and
the framework decides which ones can start. Every change goes through a single
transition function that records who did it and why in a **team event log**.

The model is domain-neutral: a task can be done by a framework run, an external
agent, or a human. Actors are free text such as `user` or `agent:reviewer`; the
framework never names model providers. Actors separate responsibilities; they do
not authenticate anyone.

Tasks are available through the Python facade (`services.tasks`), the HTTP API,
and the CLI, with the same operations and errors in all three. Everything is
stored in the local SQLite database (migration `004_tasks.sql`).

## Groups

A group has an `id`, a `title`, an `authorized` flag, and a `default_requires`
value for new dependencies (`accepted` by default). Tasks of a group can be
claimed only while the group is authorized, so a person can prepare a plan and
approve it before any work starts.

## Task states

Each task has a stored `state` and an effective `status`. An `open` task is
reported as `blocked` or `ready`, computed from its dependencies; those two
values are never stored.

| Status | Meaning |
| --- | --- |
| `blocked` | Open, but at least one dependency is not satisfied. |
| `ready` | Open, and every dependency is satisfied. It can be claimed. |
| `claimed` | Taken by an assignee; work has not started. |
| `running` | Being worked on. `attempt` counts the starts. |
| `paused` | Paused by its assignee. It keeps its files locked. |
| `submitted` | Delivered, but not validated yet. |
| `needs_changes` | Review asked for changes; it goes back to the same assignee. |
| `accepted` | Validated. Releases the tasks that wait for it. Terminal. |
| `failed` | Failed. Terminal. |
| `cancelled` | Cancelled. Terminal. |

`submitted` and `accepted` are deliberately different: finishing a task does not
release its dependents; validating it does. In this version a person or client
accepts a task explicitly; automatic gates (for example, tests that must pass)
are planned.

## Dependencies

A dependency says that one task waits for another. It is satisfied when the task
it waits for is:

- `accepted`, with `requires: "accepted"` (the default), or
- `submitted` or `accepted`, with `requires: "submitted"`.

Dependencies never form cycles: creating a task or adding a dependency that
would close a cycle is rejected with `invalid_request`, and `details.cycle`
lists the tasks involved. A dependency on a failed or cancelled task keeps the
dependent task `blocked`, with the reason in `blockers`; the framework does not
cancel dependents automatically. Remove the dependency or cancel the task to
resolve it.

## Transitions

All state changes use one operation, `transition(task_id, action, actor, cause,
expected_revision)`:

| Action | Allowed from | Leads to | Notes |
| --- | --- | --- | --- |
| `claim` | `ready` | `claimed` | The actor becomes the assignee. The group must be authorized, and the task's files must be free. |
| `unclaim` | `claimed`, `paused`, `needs_changes` | `open` | Clears the assignee and frees the files. |
| `start` | `claimed`, `needs_changes` | `running` | Assignee only. Increments `attempt`. |
| `pause` | `running` | `paused` | Assignee only. |
| `resume` | `paused` | `running` | Assignee only. |
| `submit` | `running` | `submitted` | Assignee only. |
| `accept` | `submitted` | `accepted` | Releases dependents. |
| `request_changes` | `submitted` | `needs_changes` | Keeps the assignee. |
| `fail` | `claimed`, `running`, `paused`, `needs_changes` | `failed` | |
| `cancel` | any non-terminal status | `cancelled` | |

Any other combination is rejected with `invalid_state`, and `details` lists the
current status and the allowed ones. A refused transition changes nothing and
records no event.

- **File locks.** A task can declare a `file_scope`: relative paths or folders it
  will change. `claim` is refused while another assignee holds a claimed,
  running, paused, or needs-changes task whose files overlap (the same path, or
  one path inside the other). `details.conflicts` names those tasks.
- **Concurrent clients.** Every change increments the task's `revision`. Pass
  `expected_revision` to make the change fail with `invalid_state` if someone
  else changed the task first.
- **Follow-up work.** An accepted task is not reopened; create a new task that
  depends on it.

## Team events

Each change writes one event in the same transaction as the change:
`group.created`, `group.updated`, `task.created`, `dependency.added`,
`dependency.removed`, `task.claimed`, `task.unclaimed`, `task.started`,
`task.paused`, `task.resumed`, `task.submitted`, `task.accepted`,
`task.changes_requested`, `task.failed`, `task.cancelled`, and `task.ready`
(a dependent became ready). An event carries `seq`, `type`, `group_id`,
`task_id`, `actor`, `cause`, `ref`, a small `payload` (at most 2048 bytes), and
`created_at`.

`seq` is one sequence for the whole team, strictly increasing. Read only newer
events with `after`: an unfiltered read returns consecutive numbers, and a read
filtered by group or task skips the events of other groups or tasks. Clients
keep their own `after` cursor.

## Example

Four tasks where two can run in parallel: `plan -> (backend || docs) -> release`.

```python
from multiagent.services import ApplicationServices

tasks = ApplicationServices.create().tasks
tasks.create_group("launch", "Product launch", actor="user", authorized=True)
tasks.create_task("launch", "plan", "Write the plan", actor="user")
tasks.create_task("launch", "backend", "Build the service", actor="user",
                  depends_on=["plan"], file_scope=["service"])
tasks.create_task("launch", "docs", "Write the guide", actor="user",
                  depends_on=["plan"], file_scope=["docs"])
tasks.create_task("launch", "release", "Release", actor="user",
                  depends_on=["backend", "docs"])

tasks.ready_tasks()                          # [plan]
for action in ("claim", "start", "submit"):
    tasks.transition("plan", action, actor="agent:planner")
tasks.transition("plan", "accept", actor="user", cause="plan reviewed")
tasks.ready_tasks()                          # [backend, docs]: they can run in parallel
tasks.get_task("release")["blockers"]        # backend and docs, both open
tasks.events(after=0)                        # the whole history, oldest first
```

The same steps from the CLI (against a running server):

```bash
multiagent task-group create launch --title "Product launch" --authorized --actor user
multiagent task create launch plan --title "Write the plan" --actor user
multiagent task create launch backend --title "Build the service" --depends-on plan --file service --actor user
multiagent task create launch docs --title "Write the guide" --depends-on plan --file docs --actor user
multiagent task create launch release --title "Release" --depends-on backend --depends-on docs --actor user
multiagent tasks --ready
multiagent task do plan claim --actor agent:planner
multiagent task do plan start --actor agent:planner
multiagent task do plan submit --actor agent:planner
multiagent task do plan accept --actor user --cause "plan reviewed"
multiagent team-events --after 0
```

`--depends-on backend:submitted` sets `requires` for one dependency, and
`--revision N` passes `expected_revision`.

## Interfaces

| Operation | Python (`services.tasks`) | HTTP | CLI |
| --- | --- | --- | --- |
| List groups | `list_groups()` | `GET /api/v1/task-groups` | `task-groups` |
| Create a group | `create_group(id, title, actor=…)` | `POST /api/v1/task-groups` | `task-group create` |
| Show a group | `get_group(id)` | `GET /api/v1/task-groups/{id}` | `task-group show` |
| Update a group | `update_group(id, actor=…)` | `PATCH /api/v1/task-groups/{id}` | `task-group update` |
| List tasks | `list_tasks(group_id=, status=, assignee=)` | `GET /api/v1/tasks?group=&status=&assignee=` | `tasks` |
| Ready tasks | `ready_tasks(group_id=)` | `GET /api/v1/tasks/ready` | `tasks --ready` |
| Create a task | `create_task(group, id, title, actor=…)` | `POST /api/v1/tasks` | `task create` |
| Show a task | `get_task(id)` | `GET /api/v1/tasks/{id}` | `task show` |
| Add a dependency | `add_dependency(id, depends_on, actor=…)` | `POST /api/v1/tasks/{id}/dependencies` | `task dep add` |
| Remove a dependency | `remove_dependency(id, depends_on, actor=…)` | `DELETE /api/v1/tasks/{id}/dependencies/{depends_on}?actor=` | `task dep remove` |
| Change state | `transition(id, action, actor=…)` | `POST /api/v1/tasks/{id}/transitions` | `task do` |
| Team events | `events(after=, group_id=, task_id=, limit=)` | `GET /api/v1/team-events` | `team-events` |

Errors use the shared format in [`API.md`](API.md#structured-errors):
`not_found` (404), `invalid_request` (422, including cycles and malformed
paths), and `invalid_state` (409, including locked files, unauthorized groups,
and stale revisions).
