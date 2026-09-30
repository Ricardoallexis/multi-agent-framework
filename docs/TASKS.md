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

A group has an `id`, a `title`, an `authorized` flag, a `default_requires`
value for new dependencies (`accepted` by default), and a `default_gate` for its
tasks (see [Gates](#gates)). Tasks of a group can be claimed only while the
group is authorized, so a person can prepare a plan and approve it before any
work starts.

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
| `validating` | Its gate is running. It keeps its files locked. |
| `needs_changes` | Review asked for changes; it goes back to the same assignee. |
| `accepted` | Validated. Releases the tasks that wait for it. Terminal. |
| `failed` | Failed. Terminal. |
| `cancelled` | Cancelled. Terminal. |

`submitted` and `accepted` are deliberately different: finishing a task does not
release its dependents; validating it does. A task without a gate is accepted
explicitly by a person or client; a task with a gate is accepted only by passing
it.

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

## Gates

A **gate** is an ordered list of checks that a `submitted` task must pass to be
accepted. It is declared on the task or, as a default for all its tasks, on its
group; a task's own non-empty gate wins. A task without a gate is accepted by
hand as before; with a gate, the manual `accept` action is refused and the gate
is the only way to accept it.

| Check | Passes when | Fails when |
| --- | --- | --- |
| `command:<name>` | The project command `<name>` exits with code 0. | Another exit code, or the command's timeout. |
| `tests`, `commit` | Aliases of `command:test` and `command:commit`. | |
| `review` | An actor other than the assignee approved the current attempt, after its last rejection. | The last review of the attempt is a rejection. |

A gate has at most 10 distinct checks. Run it with `run_gate`: the task moves to
`validating`, the checks run in order, and the first one that does not pass
stops the run.

| Outcome | When | The task becomes |
| --- | --- | --- |
| `passed` | Every check passed. | `accepted`; its dependents are released. |
| `failed` | A command exited with another code or timed out, or a review was rejected. | `needs_changes`; its dependents stay blocked. |
| `error` | A command is not configured, cannot start, or the commands file is invalid: a configuration problem, not a defect of the work. | `submitted` again. |
| `pending` | A `review` check has no review of this attempt yet. | `submitted` again. |

Each executed check is stored with the attempt, the gate run, its status, the
command name, the exit code, the duration, and the last 4000 characters of its
output (`results`). Reviews are recorded with `review` as `review.approved` or
`review.rejected` events and do not change the task's state. If a gate run is
interrupted and the task stays `validating`, `abort_gate` returns it to
`submitted`. A dependency with `requires: "submitted"` stays satisfied while its
task is `validating`.

The gate runs within the call, and each command has its own timeout; running
gates in the background is planned.

### Project commands

Commands are defined only in a local file that you control, by default
`<local directory>/project_commands.toml` (set another path with
`PROJECT_COMMANDS_FILE`). A task, a group, or a request can only **name** a
command; it can never bring one.

```toml
[commands.test]
argv = ["python", "-m", "pytest", "-q"]
timeout = 1800            # seconds, 1 to 7200; 600 by default

[commands.lint]
argv = ["ruff", "check", "."]
cwd = "/home/me/project"  # optional absolute folder; by default the parent of the local directory
```

- `argv` runs **without a shell**: operators such as `&`, `|`, or `;` are plain
  arguments. The only placeholders are `{task_id}`, `{group_id}`, and
  `{attempt}`, each as a whole argument; their values must look like ids.
- The command inherits the environment of the framework process. When it times
  out, the command and the processes it started are stopped.
- stdout and stderr are combined, and only the last 4000 characters are kept in
  the local database. Do not configure commands that print secrets.
- `project_commands()` (and `GET /api/v1/project-commands`) lists names,
  timeouts, and folders, never the arguments.

> **Security:** the HTTP API can run these commands through `gate/run`. It
> accepts only local hosts and same-site requests, but it is **not**
> authentication: never expose the server to a network. Without a commands
> file, no command can run.

### Gate example

```python
from multiagent.services import ApplicationServices

services = ApplicationServices.create()
tasks, gates = services.tasks, services.gates
gates.set_group_gate("launch", ["tests"], actor="user")         # every task of the group
gates.set_task_gate("release", ["review", "tests"], actor="user")  # this task needs a review too

# ... "backend" is delivered: claim, start, submit ...
result = gates.run_gate("backend", actor="user")
result["outcome"]              # "passed", "failed", "error", or "pending"
gates.results("backend")       # stored results by attempt and run

gates.review("release", actor="agent:reviewer", approved=True, comment="looks good")
```

```bash
multiagent project-commands
multiagent task-group gate launch --check tests --actor user
multiagent task gate set release --check review --check tests --actor user
multiagent task gate run backend --actor user
multiagent task review release --approve --actor agent:reviewer --comment "looks good"
multiagent task gate results backend
```

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
| Show a task's gate | `gates.get_gate(id)` | `GET /api/v1/tasks/{id}/gate` | `task gate show` |
| Set a task's gate | `gates.set_task_gate(id, checks, actor=…)` | `PUT /api/v1/tasks/{id}/gate` | `task gate set` |
| Set a group's gate | `gates.set_group_gate(id, checks, actor=…)` | `PUT /api/v1/task-groups/{id}/gate` | `task-group gate` |
| Run a gate | `gates.run_gate(id, actor=…)` | `POST /api/v1/tasks/{id}/gate/run` | `task gate run` |
| Abort a gate | `gates.abort_gate(id, actor=…, cause=…)` | `POST /api/v1/tasks/{id}/gate/abort` | `task gate abort` |
| Gate results | `gates.results(id, attempt=)` | `GET /api/v1/tasks/{id}/gate/results` | `task gate results` |
| Review | `gates.review(id, actor=…, approved=…)` | `POST /api/v1/tasks/{id}/reviews` | `task review` |
| Project commands | `gates.project_commands()` | `GET /api/v1/project-commands` | `project-commands` |

Errors use the shared format in [`API.md`](API.md#structured-errors):
`not_found` (404), `invalid_request` (422, including cycles and malformed
paths), and `invalid_state` (409, including locked files, unauthorized groups,
and stale revisions).
