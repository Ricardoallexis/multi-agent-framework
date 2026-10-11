# Tasks, dependencies, and team events

Multi-Agent Framework can coordinate work as **tasks** grouped in **task groups**
(a stage or phase of work). Tasks declare which other tasks they depend on, and
the framework decides which ones can start. Every change goes through a single
transition function that records who did it and why in a **team event log**.

The model is domain-neutral: a task can be done by a framework run, an external
agent, or a human. Actors are free text such as `user` or `agent:reviewer`; the
framework never names model providers. Actors separate responsibilities; they do
not authenticate anyone.

Tasks are available through the Python facade (`services.tasks`, `services.gates`,
and `services.envelopes`), the HTTP API, and the CLI, with the same operations
and errors in all three. Everything is stored in the local SQLite database
(migrations `004_tasks.sql`, `005_gates.sql`, and `006_envelopes.sql`).

## Groups

A group has an `id`, a `title`, an `authorized` flag, a separate lifecycle
state (`open` or `closed`), an optimistic `revision`, a `default_requires`
value for new dependencies (`accepted` by default), and a `default_gate` for its
tasks (see [Gates](#gates)). Tasks of a group can be claimed only while the
group is authorized. A closed group cannot accept new tasks unless creation
explicitly reopens it in the same transaction. Closing requires a non-empty
group whose tasks are all `submitted` or `accepted` and whose current delivery
evidence is valid; deliveries with patches also require verified integration.

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
| `reopen` | `submitted`, `accepted`, `failed`, `cancelled` | `open` | Requires a cause and current expected revision. Clears assignee, locks, domain and current release facts; keeps attempt and history. Abort a validating gate first. |

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
- **Scope changes.** `update_scope` requires the current revision. Open tasks
  hold no locks; claimed and needs-changes tasks lock the full scope; paused
  tasks partition the full scope into disjoint shared and workspace paths. Only
  the assignee may change claimed, paused, or needs-changes work. If a paused
  task changes scope without a new partition, the new scope stays globally
  locked. Starting needs-changes work rechecks and reacquires its global lock.
  Running and terminal tasks must be paused or reopened first.
- **Follow-up work.** Reopen a submitted or terminal task with a cause and
  expected revision, or create a new task that depends on it.
- **Group lifecycle.** Every group mutation advances its revision only when a
  value changes. `set_group_state` requires a cause and expected revision; group
  closure checks delivery and integration facts in the same transaction.

## Team events

Each change writes one event in the same transaction as the change:
`group.created`, `group.updated`, `task.created`, `dependency.added`,
`dependency.removed`, `task.claimed`, `task.unclaimed`, `task.started`,
`task.paused`, `task.resumed`, `task.submitted`, `task.accepted`,
`task.changes_requested`, `task.failed`, `task.cancelled`, `task.reopened`, `task.scope_updated`,
`group.lifecycle_changed`, and `task.ready`
(a dependent became ready), plus `task.context_updated` and `result.submitted`
(see [Envelopes and results](#envelopes-and-results)). An event carries `seq`, `type`, `group_id`,
`task_id`, `actor`, `cause`, `ref`, a small `payload` (at most 2048 bytes), and
`created_at`.

`seq` is one sequence for the whole team, strictly increasing. Read only newer
events with `after`: an unfiltered read returns consecutive numbers, and a read
filtered by group or task skips the events of other groups or tasks. Clients can
keep their own `after` cursor or let the framework keep one per consumer (see
[Event cursors](#event-cursors)).

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

## Envelopes and results

An agent should not need the whole conversation of the tasks it depends on. The
framework hands it two small, validated JSON documents instead, plus references
to anything bigger.

- A **task envelope** (`multiagent.task_envelope/1`) is what an agent needs to
  start a task: objective, effective status, dependencies by reference, file
  scope, gate, base commit, and the event sequence to read from.
- A **task result** (`multiagent.task_result/1`) is what an agent delivers: a
  bounded summary, decisions, contract changes, tests not run, requests, and its
  patch as a blob. Dependents receive the result, not the conversation.

Both documents are data only. They never carry a command, a credential, or an
absolute path of the user, and free text (`objective`, `constraints`, `summary`)
is never interpreted. A reader ignores fields it does not know, and a field such
as `command` in a submitted result is dropped, never stored.

### Task envelope

`get_envelope` computes the envelope from the task, its dependencies, their
results, and its gate. It is not stored.

```json
{
  "schema": "multiagent.task_envelope/1",
  "task_id": "G5-T02", "group_id": "G5", "revision": 7, "attempt": 1,
  "assignee": "agent:builder", "status": "ready",
  "objective": "Implement the retry policy for failed model calls.",
  "depends_on": [{"task_id": "G5-T01", "requires": "accepted", "status": "accepted", "result_ref": "result://G5-T01/1"}],
  "artifact_refs": ["blob:sha256:4de80d71962b58a468c70570c9e11eabca31b9a470e19bdea5b0801181bfb9df"],
  "context_refs": ["doc://docs/ARCHITECTURE.md#model-router@2263537"],
  "file_scope": ["multiagent/model_router.py"],
  "constraints": ["no push"],
  "gate": ["tests"], "base_commit": "2263537", "events_after": 212
}
```

| Field | Comes from |
| --- | --- |
| `task_id`, `group_id`, `revision`, `attempt`, `assignee`, `status` | The task (`status` is the effective status). |
| `objective` | The task's objective, or its title when it has none. |
| `depends_on` | Each dependency with its `requires`, effective `status`, and, when it has a result, the `result_ref` of its latest attempt. |
| `artifact_refs` | The patch blobs of those results. |
| `context_refs`, `constraints`, `base_commit` | The task context (below). |
| `file_scope`, `gate` | The task's file scope and its effective gate. |
| `events_after` | The head of the team event log when the envelope was computed. |

`get_envelope` takes the optional `known_hash` of an envelope you already have.
The hash is the sha256 of the envelope's canonical JSON (sorted keys, compact
separators, UTF-8) **without `events_after`**, so an event of another task does
not invalidate it. If nothing changed you get `{"unchanged": true, "hash": ...,
"revision": ...}` instead of the whole envelope. `events_after` only tells you
where to start reading events; keep following the log with an
[event cursor](#event-cursors), not with the hash.

### Task context

`set_task_context` sets what the envelope cannot derive: a `base_commit` (7 to 40
lowercase hex characters, or empty), up to 20 `context_refs` (see
[References](#references)), and up to 20 `constraints` of at most 200 characters
each. Only the fields you give change; the task's `revision` goes up and a
`task.context_updated` event is written. The context of a task that is closed
(`accepted`, `failed`, `cancelled`) or `validating` cannot change.

### Results

`submit_result` stores the result of the current attempt and submits the task in
one transaction, so a result never exists without its `submit` and the other way
around. The task must be `running` and the actor must be its assignee, the same
rules as the `submit` action; the framework fills `schema`, `task_id`,
`attempt`, and `agent`.

```json
{
  "summary": "Added the retry policy with exponential backoff and a cap of three attempts.",
  "decisions": ["Retry only on transient errors"],
  "contract_changes": "",
  "tests_not_run": ["pytest"],
  "requests": [{"to": "user", "what": "apply the patch"}],
  "base_commit": "2263537",
  "patch": {"blob": "sha256:4de80d71962b58a468c70570c9e11eabca31b9a470e19bdea5b0801181bfb9df",
            "files": ["multiagent/model_router.py"]}
}
```

| Field | Limit |
| --- | --- |
| `summary` (required) | 1 to 1500 characters. |
| `decisions` | Up to 20 items of at most 300 characters. |
| `contract_changes` | Up to 1500 characters. |
| `tests_not_run` | Up to 20 items of at most 200 characters. |
| `requests` | Up to 10 items `{"to", "what"}` of at most 100 and 200 characters. |
| `base_commit` | Empty or 7 to 40 lowercase hex characters. |
| `patch` | `{"blob", "files"}`: a blob already stored with `put_blob`, and up to 200 relative paths. |
| Whole result | At most 16 KiB of canonical JSON. |

A result is optional: the plain `submit` action keeps working without one.
Submitting a result is **idempotent**: the same task, attempt, and patch with the
same content returns the stored result with `"duplicate": true` and writes
nothing, so a client can retry after a lost response. The same task, attempt, and
patch with other content is an `invalid_state` error, and a new attempt (after
`needs_changes`) accepts a new result. A result without a patch uses the empty
patch key, so there is one such result per attempt.

`submit_result` writes `task.submitted` and `result.submitted` (with the
attempt, the result reference, and the patch blob and size, never the summary).
Read results with `get_result` (by reference or by task, latest attempt unless
you pass `attempt`) and `list_results` (summaries of every attempt, newest
first).

### References

References are text with a fixed grammar. The core resolves only `result:` and
`blob:`; the others are validated and returned as text for the agent to read
with its own tools, so the framework knows nothing about your repository or your
documents.

| Scheme | Form | Resolved by the framework |
| --- | --- | --- |
| `result:` | `result://<task_id>/<attempt>` | Yes, with `get_result`. |
| `blob:` | `blob:sha256:<64 hex>` | Yes, with `get_artifact`. |
| `doc:` | `doc://<relative path>[#anchor][@commit]` | No. |
| `report:` | `report://<id>` | No. |
| `rules@` | `rules@sha256:<64 hex>` | No. |

A reference has at most 200 characters, no control characters, and no
credentials. A `doc:` path is relative, uses `/`, and has no `..` or drive.

### Blobs and artifacts

A blob is content addressed by its sha256 and stored once: the bytes live under
the data folder (`blobs/<first two hex>/<sha256>`) and the metadata in the
database. `put_blob` takes the bytes, a `media_type` (`type/subtype`, lowercase),
and an optional `expected_sha256` to check the content. Storing the same content
again changes nothing (`created` is false and the first media type stays). A
blob is at most 8 MiB.

`get_artifact` reads a blob **by ranges** (`offset`, and `length` up to 1 MiB,
64 KiB by default), so what you do not ask for never enters your context.
`text/*` and `application/json` come back as UTF-8, without cutting a character
at the end of the range, and everything else as base64. The framework never
executes or interprets a blob: a patch is applied with your own tools.

### Event cursors

A consumer is any name you choose (`agent:reviewer`, `viewer`). The framework keeps
one cursor per consumer and scope, where the scope is empty (the whole team),
`group:<id>`, or `task:<id>`, so a consumer can resume after an interruption
without keeping its own state.

`read_events` returns the events after the consumer's cursor, oldest first, with
the `cursor`, the log `head`, and `has_more`. It can keep only some event types
(`types`) or only the events of tasks currently assigned to the consumer
(`assigned_to_me`). **Reading does not move the cursor**: you move it with
`ack_events` once you have handled the events, normally to the `seq` of the last
one. A cursor never moves backwards, and it cannot go past the head of the log.
If a consumer crashes before acknowledging, it reads the same events again
(delivery is at least once).

### Envelope example

```python
from multiagent.services import ApplicationServices

services = ApplicationServices.create()
tasks, envelopes = services.tasks, services.envelopes
tasks.create_group("ship", "Ship it", actor="user", authorized=True)
tasks.create_task("ship", "backend", "Build the service", actor="user")
tasks.create_task("ship", "release", "Release", actor="user", depends_on=["backend"])
envelopes.set_task_context("release", actor="user", base_commit="2263537",
                           context_refs=["doc://docs/ARCHITECTURE.md@2263537"])

# the agent that does "backend" delivers a patch and a result
patch = envelopes.put_blob(b"diff --git a/service.py b/service.py\n...", actor="agent:builder",
                           media_type="text/x-patch")
for action in ("claim", "start"):
    tasks.transition("backend", action, actor="agent:builder")
envelopes.submit_result("backend", {
    "summary": "Built the service.",
    "patch": {"blob": f"sha256:{patch['sha256']}", "files": ["service.py"]},
}, actor="agent:builder")

# the next agent reads a small envelope, then only what it needs
envelope = envelopes.get_envelope("release")
envelope["depends_on"]                       # status and result_ref of each dependency
envelopes.get_result(envelope["depends_on"][0]["result_ref"])["summary"]
envelopes.get_artifact(envelope["artifact_refs"][0], length=2000)["content"]

# and follows the team log from where it stopped
batch = envelopes.read_events("agent:releaser", scope="group:ship")
envelopes.ack_events("agent:releaser", batch["events"][-1]["seq"], scope="group:ship")
```

```bash
multiagent task context release --actor user --base-commit 2263537
multiagent blob put service.patch --actor agent:builder --media-type text/x-patch
multiagent task result submit backend --actor agent:builder --file result.json
multiagent task envelope release
multiagent artifact get blob:sha256:4de80d71962b58a468c70570c9e11eabca31b9a470e19bdea5b0801181bfb9df --length 2000
multiagent cursor events agent:releaser --mine
multiagent cursor ack agent:releaser --seq 213
```

`task context` also takes `--context-ref` and `--constraint` (repeatable) and
`--clear-context-refs` and `--clear-constraints` to empty those lists;
`task result show` takes a task id (with `--attempt`) or a `result://` reference;
`artifact get --output FILE` writes the bytes to a file; and `cursor` takes
`--scope`, `--type` (repeatable), and `--limit`.
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
| Set group lifecycle | `set_group_state(id, state, actor=…, cause=…, expected_revision=…)` | `POST /api/v1/task-groups/{id}/state` | `task group state set ID open|closed --actor A --cause C --revision N` |
| Update a group | `update_group(id, actor=…)` | `PATCH /api/v1/task-groups/{id}` | `task-group update` |
| List tasks | `list_tasks(group_id=, status=, assignee=)` | `GET /api/v1/tasks?group=&status=&assignee=` | `tasks` |
| Ready tasks | `ready_tasks(group_id=)` | `GET /api/v1/tasks/ready` | `tasks --ready` |
| Create a task | `create_task(group, id, title, actor=…, context_refs=…)` | `POST /api/v1/tasks` | `task create` (`--context-ref REF` repeatable; closed group: add `--reopen-group --group-revision N --cause C`) |
| Update task scope | `update_scope(id, file_scope=…, actor=…, expected_revision=…)` | `POST /api/v1/tasks/{id}/scope` | `task scope set ID --actor A --revision N --file PATH` (repeat `--file`, `--shared`, `--workspace`) |
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
| Set a task's context | `envelopes.set_task_context(id, actor=…)` | `PUT /api/v1/tasks/{id}/context` | `task context` |
| Task envelope | `envelopes.get_envelope(id, known_hash=)` | `GET /api/v1/tasks/{id}/envelope` | `task envelope` |
| Store a blob | `envelopes.put_blob(content, actor=…)` | `POST /api/v1/blobs` | `blob put` |
| Read a blob | `envelopes.get_artifact(ref, offset=, length=)` | `GET /api/v1/artifacts` | `artifact get` |
| Submit a result | `envelopes.submit_result(id, result, actor=…)` | `POST /api/v1/tasks/{id}/results` | `task result submit` |
| Show a result | `envelopes.get_result(ref_or_id, attempt=)` | `GET /api/v1/results` | `task result show` |
| List results | `envelopes.list_results(id)` | `GET /api/v1/tasks/{id}/results` | `task result list` |
| Show a cursor | `envelopes.get_cursor(consumer, scope=)` | `GET /api/v1/cursors/{consumer}` | `cursor show` |
| Read events | `envelopes.read_events(consumer, scope=, types=, assigned_to_me=, limit=)` | `GET /api/v1/cursors/{consumer}/events` | `cursor events` |
| Acknowledge events | `envelopes.ack_events(consumer, seq, scope=)` | `POST /api/v1/cursors/{consumer}/ack` | `cursor ack` |

Errors use the shared format in [`API.md`](API.md#structured-errors):
`not_found` (404), `invalid_request` (422, including cycles and malformed
paths), and `invalid_state` (409, including locked files, unauthorized groups,
and stale revisions).

## ORQ-4 A: native task snapshots

`tasks.plan_snapshot(snapshot, source_key=…, source_ref=…, snapshot_sha256=…, actor=…)`
validates and previews an exact `{groups, tasks, dependencies}` batch without
writing. `import_snapshot` applies it in one transaction. The SHA-256 is over
UTF-8 canonical JSON of exactly those three fields (sorted keys, compact
separators). Repeating a source key and hash returns the original receipt;
reusing a key with another hash conflicts. The receipt is available through
`get_snapshot(source_key)` and remains after `rollback_snapshot(source_key,
actor=…)`.

Snapshots preserve only current native states `open`, `claimed`, `paused`,
`needs_changes`, and `submitted`. They create one `snapshot.imported` event,
not reconstructed task transitions, results, gate runs, or acceptance evidence.
An imported task starts at attempt 0 with prior attempts marked unknown. An
imported paused task starts a new attempt when it is resumed; an imported
submitted task has no execution epoch, so it cannot run a gate until it is
started in the current core. Task and group views expose source provenance.

Locks are explicit metadata on the core task rows. `claimed` snapshots must
hold their whole `file_scope` globally; `paused` snapshots partition that scope
between `shared_scope` and `workspace_scope` and supply the sibling
`workspace_domain` field. Missing workspace identity is treated conservatively.
`needs_changes`, `open`, and `submitted` imports hold no file locks. Starting an
imported `needs_changes` task or resuming an imported paused task rechecks the
global scope against current locks before acquiring it. Native claim always
acquires the whole scope globally, even when `actor_domain` is supplied;
workspace identity only lets work coexist with a paused imported workspace
lock in a different known domain. Active claimed/running locks remain global.
The first resume of an imported paused task marks its `task.resumed` event with
`epoch_start: true`. Rollback removes only
the imported batch and is refused after batch activity, changed baseline rows,
external references, or an acknowledged event cursor; it never rewinds other
events or deletes the audit receipt.

## ORQ-4 B: legacy release milestones

Legacy release dependencies and current delivery facts are stored separately
from native `accepted` / `submitted` dependencies. `create_task(...,
release_dependencies=[{"depends_on_id": ..., "source_ref": ...}])` creates the
task and its legacy edges in one transaction. A missing prerequisite returns
`not_found` and leaves no task, edge, or event. Native and legacy edges share
duplicate-pair and cycle validation, while each edge remains in its own table.

Each group has an optional `legacy_release_mode` (`terminada`, `probada`, or
`integrada`). Imported legacy snapshots default a missing mode to `probada`;
an explicit null mode remains unresolved and blocks every legacy edge. The
mode is selected from the group of the dependent task, including across-group
edges. A verified integration releases any mode. A delivery with no patch also
releases any mode. A patch delivery releases `terminada` immediately, waits for
integration under `integrada`, and under `probada` requires the latest passed
test to match the current patchset. Failed, missing, or stale test evidence
does not release `probada`. Native acceptance, submission, and gate results do
not substitute for these facts.

`record_delivery_fact` replaces the current delivery and clears prior test and
integration facts. Test facts must match the current patchset when recorded.
Explicit reset and new-round transitions clear the full current fact row;
pause/resume preserves the same round. Facts keep provenance references and
are evidence records, not gate passes or reconstructed history.

The API exposes release-mode, dependency, and fact operations under the
`/api/v1/task-groups/{id}/release-mode` and `/api/v1/tasks/{id}/release-*`
routes. `GET /api/v2/tasks/{id}/context` returns the exact v1 task envelope,
the complete group dependency graph with `dependent_task_id`, release policy,
facts and provenance, plus a fingerprint. The fingerprint hashes canonical
JSON of `{task_envelope without events_after, release_context}`. Supplying the
same `known_fingerprint` returns `{unchanged: true, fingerprint}`. Its `after`
parameter only rejects a cursor beyond the event head; this read neither
fetches nor acknowledges events. V1 envelope requests fail closed with
`required_capability: release_dependencies/v1` when the task itself has legacy
release dependencies. Native v1 envelope hashes and result contracts remain
unchanged.

Snapshot B imports group modes, typed native/legacy edges, and submitted-task
delivery evidence atomically under the A snapshot hash and receipt. Rollback
compares these rows with the imported baseline and refuses if batch activity,
external references, changed facts/edges, or an acknowledged cursor makes it
unsafe. Receipts written before B retain contract version 1 through migration
008, so a version-1 snapshot can still be retried idempotently and rolled back
using its original A fields, including a submitted task without imported B
evidence. New snapshots use version 2. Historical events and task results are
never synthesized.

## ORQ-4 C: task and group lifecycle

`reopen` is an explicit task transition. It accepts a required cause and
`expected_revision`, moves eligible submitted or terminal tasks to `open`, and
clears the assignee, current locks, workspace domain, pending snapshot epoch,
and current release facts. It keeps the monotonic attempt count, result records,
provenance, gate history, and prior events. A task in `validating` must first be
returned to `submitted` through the explicit gate-abort operation.

`update_scope` changes `file_scope`, lock policy, and optional workspace domain
in one `BEGIN IMMEDIATE` transaction. It checks the revision, normalizes paths,
validates the state-specific lock partition, detects overlapping locks, and
records only a hash and counts in `task.scope_updated`. A matching no-op still
checks the supplied revision and then leaves the revision and event log alone.
The HTTP body accepts `file_scope`, `actor`, `expected_revision`, optional
`lock_policy`, and optional `workspace_domain`.

Groups have an independent `lifecycle_state` and `revision`; these fields do not
change `authorized` or imported `source_state`. Lifecycle updates are audited
with `group.lifecycle_changed`, and historical close events remain in the log
after reopening. `create_task` accepts `reopen_group`, `expected_group_revision`, `cause`, and
validated `context_refs`; the task row stores those refs as part of the initial
insert. When a closed group is reopened, its state change and task creation
commit or roll back together. Snapshot v2 groups
may include `lifecycle_state`; omission defaults to `open`, while v1 hashes and
receipts keep their original shape. Snapshot rollback checks the imported
lifecycle baseline and does not invent historical timestamps or events.
