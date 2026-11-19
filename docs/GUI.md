# Operator GUI

The local operator GUI at `/gui/` is the supported local interface for 1.0:
`multiagent gui`, `python -m multiagent.gui` and `run_ui_windows.bat` all open
it. The GUI is served by the local API and is an operator view, not a public API
or a separate orchestration layer.

For copying prompts to an external model, checking responses, and submitting them in the GUI, see [Human-guided execution](HUMAN_GUIDED.md).

## Start

From an installed project environment, run either command, or use
`GUI.cmd` from the private channel folder `.local\canal-pareja` of your
working copy, when you have one:

```console
multiagent gui [--port N] [--real] [--no-browser] [--bundle NAME=FOLDER]...
python -m multiagent.gui [--port N] [--real] [--no-browser] [--bundle NAME=FOLDER]...
```

The GUI defaults to Mock mode. Use `--real` to leave the configured provider
mode in effect; real provider calls may cost money. The server binds only to a
loopback host and refuses other hosts. It creates a random 256-bit local token
in `~/.multiagent/gui-token` and opens
`http://127.0.0.1:<port>/gui/#token=…`. The page stores the token in
`sessionStorage` and removes it from the URL. `--no-browser` prints the URL
without opening it. The default port is `APP_PORT` (8000).

## Register bundles

The **Origen** selector offers the included workflows and registered bundles.
A *definition bundle* is a folder with its own workflows, agents,
prompts and output contracts (see the [bundle contract](API.md)); registering one
adds it to the **Origen del workflow** selector of **Ejecuciones**, with its own workflows.

Register bundles when you start the GUI with the repeatable
`--bundle NAME=FOLDER` option:

```console
multiagent gui --bundle general=examples/bundles/general --bundle team=examples/teams/software_development
python -m multiagent.gui --bundle general=examples/bundles/general
```

- `NAME` is what the selector shows (`Bundle: NAME`). It uses letters, digits, `_` and
  `-`, starts with a letter or digit, and has at most 120 characters.
- `FOLDER` must exist; a relative path is read from the folder where you run
  the command, and `~` is expanded. The GUI never shows folder paths.
- Registration lasts for that start only. There is no environment variable or
  settings entry for it, so pass the option each time.
- A wrong option stops the launch before the server starts and prints one line,
  for example `ERROR: --bundle uses the format name=folder` or
  `ERROR: Bundle folder not found: ...`.
- Register only bundles you trust: the bundle's `contracts.py` is imported and
  runs as code.

The repository ships two bundles to try. Both run in Mock mode (the default),
with synthetic results, no model calls and no API keys.

| Bundle | Folder | Workflows |
| --- | --- | --- |
| General (neutral example) | `examples/bundles/general` | `summarize_document`, `research_options`, `review_checklist` |
| Software development team | `examples/teams/software_development` | `software_development` |

The general bundle is not tied to any field. Its workflows declare a
description, a description per step, and their inputs with a label, a
description and an example, all in Spanish:

- `summarize_document` (2 steps) summarizes a document for a given audience and
  length; you review the summary.
- `research_options` (3 steps) gathers findings from your question and context,
  compares at least two options, and recommends one; you review the
  recommendation. It does not browse the web.
- `review_checklist` (2 steps) checks a specification or other document against a
  checklist and issues a verdict; you review it.

The software development bundle has seven steps, so each run needs seven model
calls and the default limit of 4 stops it early. Start the GUI with
`MAX_LLM_CALLS=7` (`$env:MAX_LLM_CALLS = "7"` in PowerShell,
`export MAX_LLM_CALLS=7` in a POSIX shell); a limit of 10 leaves room for
requested changes. Its input is `feature_request`, and it pauses for review twice
(see its [README](../examples/teams/software_development/README.md)).

To make your own bundle, copy `examples/bundles/general`, rename the workflow
ids, and edit the prompts, agents and contracts; keep a sample output for each
contract in `SAMPLE_OUTPUTS` so Mock runs work.

## Layout

The views sit inside a three-zone shell (GUI2-T02):

- **Left sidebar** (`nav`): "Nueva sesión" (the guided chat at `#new`, see
  Views), a search box and the sessions of Claude, Codex and Copilot
  plus the framework runs, grouped by project (the folder name of a session, the
  project name of a run). Each item has a status dot (working, finished, error;
  also written as text for screen readers) and each group counts its items per
  app. The sidebar uses `/gui/api/sessions` and `/gui/api/runs`; the work map
  (`#map`, below) shows projects, stages and tasks. `GET /gui/api/sessions/counts`
  returns totals per source and status for other clients. Session readers keep a
  bounded per-file index (up to 5,000 entries) and reuse source listings for 2
  seconds, so a transcript that only grew is not read again.
- **Centre** (`main`): the view of the current route. In a session, consecutive
  tool calls and results fold into one block, "Trabajó N min · K herramientas",
  that opens to show them.
- **Right panel** (`aside`): Cambios, Subagentes, Tokens and Fuentes for the
  session or run open in the centre. A button in the top bar collapses it
  (`aria-expanded`/`aria-controls`) and the browser remembers the choice in
  `localStorage`. Each section body is a small component in `js/panel-slots.js`.
  Subagentes holds the subagents panel (Activos/Terminados tabs).
  - *Cambios*: the files the session changed (tool calls that write or edit),
    with the +/- lines only when the source counts them (the session readers do
    not yet, so it shows the number of edits and never an invented count); for a
    run, the files its results list (`files`, `changed_files` or `files_changed`).
  - *Tokens*: orchestrator against workers (the session and its subagents; for a
    run, the steps), the estimated cost and, for a run, the price evidence behind
    it (`GET /gui/api/runs/{id}/usage`, the same read-only data as
    `/api/v1/runs/{id}/usage`). A figure nobody reported reads «desconocido»,
    never 0; the apps report no prices, so their sessions have no cost estimate.
  - *Fuentes*: the working folder, files read, web pages (without query or
    fragment), searches and attachments of a session; the workflow, prompts,
    contracts and input names of a run. Paths and names only, never message text.

  The three sections share one loader per session or run that reads only
  `/gui/api` routes, page by page from a cursor, every 10 s (30 s for a session
  that is not active, 60 s for a finished run) and only while the panel is visible.

Below about 900 px the sidebar becomes a drawer opened by the "Menú" button
(Esc or a click outside closes it) and the right panel stacks under the content.
Shared UI components in `js/ui/` provide status chips, progress bars, tabs and
level badges. Status text accompanies color; tabs support arrow keys, Home and
End, and progress indicators expose their values accessibly. The interface uses
a skip link, visible focus and 44 px touch targets; the palette follows
`prefers-color-scheme`.

**Widths and breakpoints.** The GUI is checked at four reference widths: 360 and
390 px (phone), 768 px (tablet) and a desktop of 900 px or more. Every stylesheet
and extension uses the same two breakpoints and no others, written at the top of
`app.css`:

| Breakpoint | Applies to | What changes |
| --- | --- | --- |
| `max-width: 899.98px` | tablet and phone | The sidebar becomes a drawer, the right panel stacks under the content, grids fall to one column, and «Ejecuciones» shows the list and the selected run one after the other. |
| `max-width: 599.98px` | phone | The top bar wraps and its title is kept for screen readers only, the content padding shrinks, the «Pendientes» list fills the screen, and a run that waits for a person gets a fixed action bar. |

Grid and flex children can shrink (`min-width: 0`), controls keep the 44 px touch
height (`--target`), and `pre` blocks and wide tables scroll inside themselves, so
the page does not scroll sideways at any reference width. `tests/test_gui_rwd.py`
checks these rules statically, one test per view.

**Trying a width on the PC.** The GUI only listens on `127.0.0.1` (see
[Security model](#security-model)), so a phone on the network cannot open it: the
narrow layouts are for a narrow window on the same PC. Make the browser window
narrow, or open the developer tools (F12) and turn on the device toolbar
(Ctrl+Shift+M in Edge and Chrome), then set the width to 360, 390 or 768 and
reload. Nothing has to be installed.

## Views

**Nueva sesión** (`#new`) is a guided chat (GUI2-T03). It greets with "¿Qué
quieres hacer?" and three starters (Trabajar en un workflow, Continuar una
ejecución, Ayuda), also available as the commands `/workflow`, `/continuar` and
`/ayuda` in the writing box. Free text proposes workflows as cards (name,
description, origin). A model reads the text (GUI2-T17): the server sends it,
with the declared workflows and their inputs, to the configured provider
(`GUI_NEW_SESSION_MODEL`, default `local_default`; Mock answers from a fixture,
so the result is deterministic) and checks the answer against those workflows
and inputs. A valid proposal shows one card marked "Propuesto por: un modelo",
and choosing it prefills the inputs it filled; the summary still waits for
**Lanzar**. A cloud model asks for explicit confirmation before any text is sent
to it, regardless of `EGRESS_DEFAULT`. Declining uses word matching without a
model call. Each interpretation call records provider usage and estimated cost,
including failed calls. Configured budget alerts apply to these calls; a cut
limit prevents the call and leaves word matching available. The text enters the
prompt as untrusted data, and the egress policy
of the framework (`EGRESS_DEFAULT`) applies to the outgoing prompt. With no
model, an error, a refused prompt, `GUI_NEW_SESSION_INTERPRET=false` or an
answer that does not fit, the chat compares the words with the names and
descriptions of the workflows instead, as before, and says so. After choosing a
workflow the chat asks its inputs one at a time ("Pregunta N de M", with the
label, description and example; the project name comes last, and the optional
extra-context inputs are left to the form). A summary card shows the answers and
the rendered prompts with **Lanzar**, **Editar** and **Ver como formulario**;
nothing is created until **Lanzar**, which posts to the same
`POST /gui/api/runs` as the form (the real-model confirmation of a non-Mock
server is kept). The writing box accepts an answer with **Enviar** (Enter sends;
Shift+Enter adds a line); starter and choice chips are options for the chat.
Before launch, the summary can override mode, profile, level, model and effort
where the preview exposes choices; `POST /gui/api/runs/preview` validates these
preferences without creating or executing a run. The orchestrator flow launches
framework workflows; only «Sesión única» can launch an executor task (Claude, Codex,
Copilot or agy), and only after the confirmation described below. "Ver como formulario"
opens the create-run form with the same workflow and answers. The conversation
model is pure (`js/new-session-model.js`) and is tested with Node. The proposal comes from
`POST /gui/api/new-session/interpret` (`{"text"}`; it answers `source` `model` with a
`proposal`, or `source` `keywords` with a `reason`) and creates nothing.

**Mode.** «Nueva sesión» first asks «¿Cómo quieres empezar?» (`js/new-session.js`).
**Orquestador** is the guided flow above: bundle, workflow, team and plan. **Sesión
única** is one task answered by one model or one executor, with no workflow to pick.
**Cambiar modo** goes back to the question. Nothing is created until a launch button
is pressed, and each mode has its own confirmation.

**Connected models and executors.** The orchestrator flow shows a picker
(`js/model-picker.js`, `model-picker-model.js`) with the connected models (Gemini,
OpenAI, Ollama) and executors (Claude, Codex, Copilot, agy), each with the time it was
checked and, for an executor, its quota. A row that is not connected folds under
«No conectados (n)» and says the one step it lacks: «Falta instalarlo»,
«Falta iniciar sesión», «Falta configurar la clave», «Falta habilitarlo», or a reason
such as «No responde» or «La cuota está en pausa». Choosing a row only marks it: it
launches nothing and does not change the workflow's routing.

*How «connected» is decided.* The picker reads `GET /gui/api/connections`
(`multiagent/services/connections.py`), which spends no model. It reuses what the
framework already knows: whether the program is installed and its version, the
signed-in state that each app reports without generating, whether the key setting is
set (the value is never returned), the executor's enabled and paused state, the usage
plan and, for Ollama, its health probe. The answer is cached: if the read fails,
**Reintentar** asks again, and `POST /gui/api/connections/{id}/check` re-checks one row.
A row is selectable only when it is enabled and has no reason. The same facts come from
`multiagent connections [--check ID]` and `GET /api/v1/connections`.

*What the picker cannot know.* Copilot never appears as connected: its session cannot
be checked without spending model quota, so its row says «No se puede comprobar la
sesión sin gastar cuota» (`session_unverifiable`). A Gemini key is not tried either, so
Gemini stays in «No conectados» with «No se pudo comprobar el acceso»
(`reachability_unknown`). agy has no session check besides its `/usage` quota reading.

*The step that is missing.* Each row names one step: install the program, sign in,
configure the key in `.local/.env`, or enable it. The picker only tells; it installs
nothing and signs in to nothing. `multiagent setup install <app>` and
`multiagent setup login <app>` open a console on this PC for the person to run the
command. **Conectar otro**, under the picker, links to «Apps y agentes» (below),
where the same step can be done; the link is drawn only when that view is loaded.
Enabling an executor is also the person's step: an executor starts disabled,
and agy needs two things done on this PC, its declaration in `catalog/models.yaml` and
the third-party notice in `.local/.env` (see [`EXECUTORS.md`](EXECUTORS.md#antigravity-agy-supervised)).
The GUI shows agy's notice but never accepts it for the person.

**Sesión única with a model.** The task is a text box. The model choices are the
connected providers' models that the `answer` step of `ask_once` admits. That workflow
is in the general bundle and has one step and one model call; the model goes in the
step's `routing_preferences`, and the server validates it. A provider with no admitted
model, or one that is not connected, is listed with its reason. The catalog of the
general bundle declares only `local_default` (Ollama), so this mode needs Ollama
running. **Revisar y lanzar** shows the routing preview, and **Lanzar** creates the run
through `POST /gui/api/runs` with an idempotency key (a server that is not Mock also
asks for the real-model confirmation). The run keeps its usage records, budgets and
egress policy, and the column follows it at `#new/run/<run id>` like any other run.

**Sesión única with an executor.** **Usar un ejecutor** lists the four executors with
the reason each one is not available, their remaining quota and, for agy, its notice.
The models and efforts come from `GET /gui/api/composer/executors`. **Revisar
lanzamiento** opens a confirmation with the executor, model, effort, remaining quota,
the task and, for agy, its notice; only **Aprobar y lanzar** sends the launch, so every
launch is approved one by one. Copilot is not selectable (see above). The server
(`POST /gui/api/executors/launch`, with the token) refuses a request that lacks
`"confirmed": true` (409 `confirmation_required`), creates a task and calls the same
executor service as the API, with its checks: a disabled executor (`executor_disabled`),
Copilot without a model (`model_required`), and agy's switch, notice, quota floors and
single running launch. A refused launch cancels the task it created, and the refusal
and its reason are shown. After a launch, **Seguimiento de la tarea** polls
`GET /gui/api/executors/launch/{task_id}` every 2 s until the task ends, and shows the
state, the last attempt, its notes and evidence, and a link to the recorded session in
«Sesiones». Running the executor spends that app's quota; the framework does not read
or change the app's own settings.

**The chat follows the run.** After **Lanzar** or «Continuar una ejecución», the
column stays in «Nueva sesión» and follows the run at `#new/run/<run id>`
(`js/thread.js`, with the pure state in `js/thread-model.js` and the cards in
`js/thread-cards.js`). The thread comes from the read-only, token-protected
`GET /gui/api/threads/runs/{run_id}`, which rebuilds the conversation on every
read from the run's stored events and state. Each entry is a message: the launch
(workflow, project, mode, and links to the task and the bound team), each
completed step with the model, function or person that produced it, the messages
sent to the run, each stop, and the final output or error. The page asks for new
entries every 4 s (`?after=<cursor>`, one entry back, merged by identity) and
stops once the run is completed, failed, cancelled or rejected. Every message
links to «Ver detalle», the run in «Ejecuciones».

*Stops as messages.* A run that waits for a person becomes a card in the thread
with the actions the server accepts for that wait: «Aprobar», «Pedir cambios» (a
note and optional comments, each with a part of the output, a type, a text and an
optional quote) and «Rechazar». Each one asks for a confirmation first, then calls
the route that «Ejecuciones» already uses (`POST /gui/api/runs/{id}/approve`,
`/changes` or `/reject`) with the actor fixed by the server. Only the
current stop offers actions; earlier stops stay in the thread as text.

*Review.* The stop for a review shows the output of the attempt under review
(`js/readable.js`): fields, lists and tables, with long text folded and
nesting limited to three levels, and the complete JSON folded under «JSON
completo». «Ver pasos anteriores» loads the earlier outputs of the same run on
request, and the number of model calls against the maximum is shown.

*Tool call.* The stop for a `file.write` call shows what will be written. For a
new file it lists every line as an addition; for an existing file it shows the
content, the lines that change against the current file, and a notice when lines
will be removed. The same preview appears in the «Acciones humanas» of
«Ejecuciones». It comes from the read-only
`GET /gui/api/runs/{id}/tool-approval/preview?permission_request_id=<id>`, which
writes nothing. Calls to other tools show no preview.

*Team and plan.* A proposed team version opens as a thread of one stop at
`#new/team/<version id>`, read from `GET /gui/api/threads/teams/{version_id}`. Its
card shows the team mode and, for each position, its rules, permissions, members
and the model that its agent resolves to in the bundle catalog, or the reason
when none can be resolved; **Aprobar** and **Rechazar** use the team routes
described under «Equipos», which show the same card. A first version is compared
with an empty team. A plan proposal opens at `#new/plan/<proposal id>`, read from
`GET /gui/api/threads/plans/{proposal_id}`: its tasks with their inputs, a
message for each bundle whose roles have no approved team (it links to the
proposed team version, or to «Equipos»; **Aprobar** stays disabled and says why),
and, once approved, one message for each task with its run. **Aprobar** and
**Rechazar** send the revision on screen as `expected_revision` to the routes
described under «Planes». Pending runs, team versions and plans in «Pendientes» link to
these threads.

*Limits.* The cards show text only: nothing the server or a model wrote is
interpreted as HTML or Markdown. The preview of `file.write` appears only when the
arguments rebuilt from the step declaration and the run inputs have the same
fingerprint (`arguments_sha256`) as the pending request; otherwise the card says
why it cannot show them (the approval changed, file tools are not configured, the
step no longer declares the call, or the arguments depend on an earlier step's
output) and the call can still be approved or rejected without it. The thread of
a run is read by polling every 4 s, not pushed; the team and plan threads are
read once and reload after a decision. The chat answers nothing by itself: every
approval is a person's click.

**Apps y agentes** (`#apps`, the extension `multiagent/gui/static/extensions/apps/`) shows
the same connection status as the picker, in full, and only the step each tool is
missing. The executors (Claude Code, Codex CLI, GitHub Copilot CLI, Antigravity) come
first and the models (Gemini, OpenAI, Ollama) after them. **Actualizar** reads the lists
again from `GET /gui/api/connections` and `GET /gui/api/setup/status`; it spends no model
and opens nothing.

*What each card shows.* Whether the app is installed and its version, its session, its
state (enabled, disabled, or for agy also whether the notice is accepted), the
permission templates, the quota the usage plans report, the reason it is not ready, and
when it was checked. For a model, the session line is whether its key is set, shown only
as the setting name and the file, for example `GEMINI_API_KEY` in `.local/.env`; the
value is never read, shown or asked for, and Ollama shows whether its local service
answers. The templates line is read only: it says whether the template of Claude Code
(`docs/permissions/claude-settings.template.json`) or of Codex
(`docs/permissions/codex-config.template.toml`) is present and whether it is active
(`.claude/settings.json`, `.codex/config.toml`), and that Copilot uses options for each
run and agy its supervised modes. See [Permission profiles](PERMISSIONS.md#what-the-gui-shows).

*The step that is missing.* Each card gives one step, never the whole list:

- **Install or sign in.** **Abrir consola** asks for confirmation first. The
  confirmation names the fixed, dated official command (winget or npm, never a remote
  script) and its source, and only **Confirmar y abrir consola** sends
  `POST /gui/api/setup/console`; **Cancelar** sends nothing. The server opens a visible
  Windows console that shows the command and waits for the person to press Enter in it,
  so nothing runs hidden. Only one console can be open at a time, and the card shows the
  exit code when it closes. After installing Claude, the console also checks its
  publisher signature. The view never reads what is typed in the console.
- **Copy instead of open.** Where the console is not available, the card shows the same
  command with **Copiar** to run in your own terminal: on a system that is not Windows,
  when `GUI_ALLOW_LAUNCH` (`gui_allow_launch`) is `false`, and after a refusal.
- **Key.** The card names the setting and the file (for example `OPENAI_API_KEY` in
  `.local/.env`) with **Copiar** for the name. It never accepts or writes the value.
- **Enable.** The card shows the exact lines to add, with **Copiar**: for an executor,
  `enabled: true` under `executor_routing` in `catalog/models.yaml` and, for agy, the
  notice line in `.local/.env` with the notice text; for OpenAI, `OPENAI_ENABLED=true`.
  **Copiar** writes nothing to any file: pasting them and restarting is the person's step
  (see [Choosing and launching from the GUI](EXECUTORS.md#choosing-and-launching-from-the-gui)).
- **Information only.** agy has no install button and no sign-in command: the card says
  to install it from its official distribution and to sign in yourself, because its
  session cannot be checked without spending quota. A Copilot session cannot be checked
  either, so its card offers the sign-in console as optional.

**Volver a comprobar** measures that one tool again through
`POST /gui/api/connections/{id}/check`, without generating with any model, and is the
step to use after installing, signing in or restarting. When a console closes, the
card rechecks that tool by itself; a path added to `PATH` by an install can show only in
consoles opened afterwards.

*Limits.* The GUI installs nothing by itself and signs in to nothing: it opens a console
for the person, who presses Enter. It does not enable an executor, accept agy's notice,
write a key or token, or touch the apps' own settings or the Google account. The console
exists only on Windows. The reasons and the quota text come as the services report them,
so one can appear in English. See [Install and sign in](EXECUTORS.md#installing-and-signing-in-to-an-executor)
for the command line.

**Sesiones** is the default view. It combines a read-only, filterable and
paginated list of Claude Code, Claude Desktop, Codex, GitHub Copilot, and
framework-run sessions. Details are rendered according to item kind and
include subagents; keyboard navigation and live follow over server-sent events
(SSE) are supported.

The **agent tree** is available at `#tree/<session-id>` from a session's
«Árbol de agentes» link. It places the session above its children, shows
available status, model, level, elapsed time and token data, and offers a
filter for active agents, a selected-node detail and a short change log. It
refreshes from `GET /gui/api/sessions/{id}` and
`GET /gui/api/sessions/{id}/children`, follows the children SSE stream, and
polls every 10 seconds as a fallback. The tree supports arrow-key navigation,
Home/End, Enter/Space to select and Esc to clear the selection. It does not
claim to show every delegation or review relationship.

The **Uso** view (`#usage`) has **Planes**, **Global** and **API y presupuesto**
tabs. It reads `GET /gui/api/usage/plans`, `/gui/api/usage/tokens` and
`/gui/api/usage/costs` for plan measurements, token totals by app, model, level
and role, and estimated framework provider costs with price evidence. Missing
and reset measurements stay unavailable rather than becoming zero. The service
does not expose a budget or an alert/cutoff mode, so the view does not
calculate budget percentages or offer those controls.

The **Evaluación** view (`#evaluation`) is read only. It lists the evaluation sets
that have saved runs, each with its latest score, whether that run was Mock or
live, and its state against the set's baseline: «Regresión» (the latest run
regressed), «Sin línea base» or «Al día». Opening a set (`#evaluation/<set name>`)
shows its saved runs and compares the latest one with the baseline: each case as
regressed, improved, changed or unchanged, the cases found in only one run, and
what changed in the fingerprint (agent or prompt, workflow, models, or the set).
A regression is a result of the comparison, not an error. The view never runs a
set or sets a baseline; use `multiagent eval` or the API for that. It reads
`GET /gui/api/evaluations`, `/evaluations/runs?set_name=`, `/evaluations/runs/{id}`
and `/evaluations/comparison?set_name=`; the sets are read from the newest 1000
saved runs. See `docs/EVALUATION.md`.

The **Equipos** view (`#teams`) lists the bundles that define a team (`team.yaml`),
each with its approved version and its latest pending proposal. Opening one
(`#teams/<bundle>`) shows its organization chart as a nested list, one item per
position with its purpose, its occupants and its state written out («Ocupado»,
«Vacante» or «Pendiente de aprobar»; a position the proposal removes is marked),
the reviews and delegations apart from the hierarchy, and a notice when the team
has several roots. The Mermaid text of the chart sits in a code block with a
«Copiar» button; the view draws no diagram and loads no Mermaid library. The
versions table opens the diff of each version. A pending proposal can be approved
or rejected, each behind a confirmation with an optional note; the server records
the decision as `user:operator` on the `gui` channel (the page cannot choose
either), and the view then reloads and shows the result or the service error as it
came (for example `invalid_state` when the proposal was already decided). Proposing
a version stays in `multiagent team propose` and the API. It reads
`GET /gui/api/teams`, `/teams/{bundle}/versions`, `/teams/versions/{id}` and
`/teams/{bundle}/org-chart?version_id=`, and decides with
`POST /gui/api/teams/versions/{id}/approve` and `/reject` (body `{"note"?}`). See
[`TEAMS.md`](TEAMS.md).

The **Planes** view (`#plans`) lists saved plan proposals (filterable by
status: proposed, approved or rejected) with their group, revision, task count
and creator. Opening one (`#plans/<id>`) displays its tasks, dependencies,
workflow/bundle, `preferred` assignee and revision history. While a proposal is
still `proposed`, an operator can edit its tasks, reassign one task
(`preferred`, `workflow` or `bundle`), approve the visible revision (creating
the group/tasks and advancing ready ones) or reject it, each behind a
confirmation box with an optional note; the server sets the actor to
`user:operator` and the channel to `gui`. Edits, reassignments and approvals send
the displayed `revision` as `expected_revision`: on a `409` conflict (stale
revision, closed proposal or existing task id) the view notifies the operator
and reloads the proposal. It reads `GET /gui/api/plan-proposals` and
`/plan-proposals/{id}`, and acts via `POST /gui/api/plan-proposals/{id}/edit`,
`/reassign`, `/approve` and `/reject`.
An approved proposal shows **Avanzar plan**. After a confirmation it calls
`POST /gui/api/plan-proposals/{id}/advance` (no body; the server fixes the actor
and channel as above) and says which tasks moved, or that there was nothing to
advance, which is not an error. It advances once: press it again after a task's
run finishes. If a bundle's tasks use `role:` steps and its team is not approved,
approval is refused with `team_not_approved` and nothing is created; approve the
team first (see [planning](PLANNING.md#approve-the-team-first)).
See [planning](PLANNING.md) for proposal creation, approval, execution, and
current limits.

The **Mapa** view (`#map`) combines projects, stages and tasks with linked
sessions and their children. `GET /gui/api/map?project=<id>&only=active|all`
returns the read-only map; the project filter is optional and `only` defaults
to `active`. The view offers tree and list presentations, active-only and
finished-item folding, and a manual refresh. It follows the child streams of up
to 300 sessions, coalesces refresh requests and polls every 10 seconds as a
fallback; stable node identities let it reconcile new snapshots while keeping
unchanged rows. It shows only allowlisted titles and state, never private child
labels. Nodes and relation chips are keyboard-focusable and have text labels,
reduced motion is respected, status is not conveyed by color alone, and the
palette is checked for text contrast of at least 4.5:1 in light and dark themes.

The token-protected GUI routes these views use:

| Route | Purpose |
| --- | --- |
| `GET /gui/api/sessions/counts` | Session totals by source and status. |
| `GET /gui/api/sessions/{id}/children` | Child agents for the right panel, tree and map. |
| `GET /gui/api/stream?session=<id>&only=children` | Lightweight child-change notices. |
| `GET /gui/api/map?project=<id>&only=active\|all` | Project, task and linked-session map. |
| `GET /gui/api/usage/plans`, `/usage/tokens`, `/usage/costs` | Plan measurements, token totals and estimated costs for Uso. |
| `GET /gui/api/evaluations`, `/evaluations/runs`, `/evaluations/runs/{id}`, `/evaluations/comparison?set_name=` | Evaluation sets, saved runs and the latest run against the baseline (read only). |
| `GET /gui/api/teams`, `/teams/{bundle}/versions`, `/teams/versions/{id}`, `/teams/{bundle}/org-chart` | Team bundles with their approved and pending versions, versions with their diff, and the org chart with its Mermaid text. |
| `POST /gui/api/teams/versions/{id}/approve`, `/reject` | Decide a proposed team version with an optional `note`; the actor (`user:operator`) and channel (`gui`) are fixed by the server. |
| `GET /gui/api/plan-proposals`, `/plan-proposals/{id}` | List saved plan proposals (optional `status`, `group_id`) and read one proposal with its tasks and history. |
| `POST /gui/api/plan-proposals/{id}/edit`, `/reassign`, `/approve`, `/reject` | Edit tasks, reassign one task, approve or reject a plan proposal; `user:operator` and `gui` are fixed by the server. |
| `POST /gui/api/plan-proposals/{id}/advance` | Advance the plan of an approved proposal once («Avanzar plan»); a proposal that is not approved is a conflict, and nothing to advance is not an error. |
| `GET /gui/api/threads/runs/{run_id}` (`?after=<cursor>`) | The conversation of a run for «Nueva sesión» (`#new/run/<id>`): launch, steps, messages, stops with the actions the server accepts, and the final output, rebuilt from the stored events (read only). |
| `GET /gui/api/threads/teams/{version_id}` | The thread of a team version (`#new/team/<id>`): its card and one stop with Approve and Reject (read only; the decision uses the teams routes). |
| `GET /gui/api/threads/plans/{proposal_id}` | The thread of a plan proposal (`#new/plan/<id>`): tasks and inputs, bundles without an approved team, and the run of each task once approved (read only; the decision uses the plan-proposal routes). |
| `GET /gui/api/runs/{id}/tool-approval/preview?permission_request_id=` | Content and diff of a pending `file.write` approval, only when the rebuilt arguments match its fingerprint; writes nothing. |
| `GET /gui/api/connections`, `POST /gui/api/connections/{id}/check` | Cached connection facts for the seven model providers and executor apps, or a fresh check of one; no model is called and no key value is returned. |
| `GET /gui/api/setup/status` | For «Apps y agentes»: whether launching is allowed (`gui_allow_launch`) and the console is supported, the read-only presence of each permission template, and the fixed, dated install and sign-in plans (read only; no template file is read, only checked for presence). |
| `POST /gui/api/setup/console` | Open the visible Windows console for a fixed `install` or `login` step of a tool; the body is only `{"id", "step"}` (any other field is 422, an unsupported step 400). It answers `403 launch_disabled` when `gui_allow_launch` is off, `409 console_already_open` while another console is open and `409 launch_unsupported` outside Windows. |
| `POST /gui/api/executors/launch` | Launch a «Sesión única» task with an executor; the body needs `confirmed: true` (otherwise 409 `confirmation_required`) and Copilot needs a `model`. It creates the task and calls the executor service with its checks. |
| `GET /gui/api/executors/launch/{task_id}` | Status of that task and its attempts, for the follow-up (read only). |
| `GET /gui/api/composer/executors` | Models and efforts that each enabled executor admits, for the executor choice. |
| `GET /gui/api/runs/{id}/usage` | Token and cost details for the right panel. |
| `GET /gui/api/runs/{id}/steps/{step_id}/attempts` | Completed attempts of one step (producer, input, output, review, tokens). |
| `GET /gui/api/runs/{id}/steps/{step_id}/attempts/compare?a=1&b=2` | Field-by-field and unified line diff between two completed attempts. |
| `POST /gui/api/runs/preview` | Validate and preview a workflow without creating a run. |
| `POST /gui/api/runs` | Create the confirmed framework run. |

These are local GUI routes under `/gui/api`, not additions to the public
`/api/v1` contract.

**Ejecuciones** creates a framework run using the selected workflow's inputs.
Workflows may declare inputs with a name, label, description, example,
required flag and type (`text`, `long_text` or `choice`); the form shows the
description and example, and uses a selector for choices. Workflows without
declared inputs continue to derive them from prompt placeholders. The GUI
previews the rendered prompt before creating the run. An optional **Modo**
selector defaults to «Sin modo» (same behavior as before, inheriting any
`mode` from the bundle's approved `team.yaml`) and sends `operating_mode`
(`supervised` or `autonomous`) to `POST /gui/api/runs` only when a mode is
chosen.
Run details show skipped steps, counters, and human-action history. Each action
shows who took it and through which channel, as the event recorded them: the GUI
records `user:operator` and `gui` (shown as «Operador · gui»), and an approval
sent through the API or the CLI shows `api`. The text is what the caller declared,
not a verified identity, and an event written before actors were recorded shows
«Sin dato». When a run
has an `operating_mode_set` event, the run detail header shows the effective
mode and whether it came from the run or the bound team; when no mode is set,
nothing extra is shown (see [Run operating modes](PERMISSIONS.md#run-operating-modes)). When a
step runs under a role from the bound approved team, both the step list and its
completed attempts show the role, member, and team version. Results are
grouped by step with each completed attempt's producer, review status, tokens,
and output; when a step has two or more completed attempts, **Comparar** lets
the operator pick two attempts and inspect field-by-field differences (with
unified line diffs for multi-line or long text fields). Attempts that failed
before producing an output and the full prompt sent to a model attempt are not
stored today, so they are not shown. Available
human actions include approve, request changes, reject, regenerate, cancel,
and submit a human-produced result.
Under «Pedir cambios», the operator can add up to 20 comments alongside or
instead of free text. Each comment has a part of the output (General, a
top-level field, or a typed path), a change type, required text, and an
optional quote. Comments appear in the attempt review and action history.

**Ejecuciones on a phone** (`js/runs-layout.js`). At 900 px or more the list of
runs and the selected run sit side by side. At 899.98 px or less they go one after
the other: the list first, then the selected run, or the create form, alone, with
«← Volver a la lista» at the top. Each row of the list shows the run's status, so
a run waiting for a person says so. When a run waits, «Acciones humanas» is
marked with the reason for the wait («Esperando tu revisión · paso …»), and on a
phone the pending action stays in a fixed bar at the bottom of the run: the
reason, «Aprobar» or «Aprobar llamada» for a review or a tool call, and a button
that jumps to the full set of actions. The bar leaves the screen once the run no
longer waits.

**Pendientes** is a button in the top bar with the number of items that wait for a
person: runs in review, proposed team versions and plans, continuity decisions
and tasks that wait for the user. It reads the read-only, token-protected
`GET /gui/api/pending` (every 5 s while the tab is visible, every 30 s while it is
hidden) and shows the same number in the tab title, as `(2) Multiagent`. The
button opens a list; on a tablet or wider it is a panel under the top bar, and on
a phone it fills the screen. Each item says its kind and why it waits and links to
the run's thread, or to the team, plan, continuity or task view; following the
link closes the list. The link only changes view: it approves and starts nothing.
**Activar avisos** is off by default. Pressing it asks the browser for permission,
and only then does the browser on this PC raise a notification for each new item.
The choice is kept in this browser, and **Desactivar avisos** turns it off.
Nothing is sent to another device.

**Salida externa** (HIL2-T02) appears in the run detail while a run waits for a
human step or a review. Paste the JSON output, optionally pick evidence files
(the page reads them and sends them as base64), and press **Vista previa**: it
shows the normalized output, each file with its type, size and SHA-256, and when
the preview expires; the run does not change. **Confirmar** asks once more and
then shows the receipt (id, SHA-256 of the manifest and of each file, who, channel
and time). The routes are `POST /gui/api/runs/{id}/external-output/preview` and
`POST /gui/api/runs/{id}/external-output/confirm`; they call the same services as
the API, and the server fixes the actor (`user:operator`) and the channel (`gui`):
a body that carries an `actor` is rejected. Confirm returns the run view plus a
`receipt` field. An error shows its code and reason (`details.reason` or
`details.problems`) and keeps what was typed; an expired or used preview says so
and lets the operator prepare another. The page never shows evidence content.

Contextual help is available throughout the GUI: controls have a short hint
and a «?» disclosure with a concise explanation. The disclosures are
accessible by keyboard and can be closed with Esc.

Stop either GUI server with Ctrl+C. The `multiagent gui` command prints
«GUI detenida.» after clean shutdown.
On Windows, a batch launcher may additionally prompt `Terminate batch job
(Y/N)?`; answer `Y`.

## Adding a view, a panel section or help texts

Tasks that add to the GUI in parallel do not edit shared files (`index.html`,
`main.js`, `route.js`, `help-text.js`, `right-panel.js`, `tests/test_gui_help.py`).
Each one adds a folder, `multiagent/gui/static/extensions/<name>/`, where `<name>`
is lowercase letters and `_` (`projects`, `doc_alerts`):

| File | Role |
| --- | --- |
| `index.js` | Required entry module. Calls the register functions of `js/extensions.js` when it loads, passing its folder name as `owner`. |
| `style.css` | Optional stylesheet. The page adds it; `index.html` gets no line. Every selector starts from a class or id prefixed with the name (`.doc-alerts-card`), and colours come from the `app.css` variables. |
| `help.js` | Optional help texts: `export const HELP = { ... }` as strict JSON, like `js/help-text.js`, with every key under `<name>.`. The entry module passes it to `registerHelp(HELP, "<name>")`. |
| anything else | Helpers of the extension (components, models). Import only from the folder itself, `../../js/` and `../../vendor/`. |

```js
// extensions/projects/index.js
import { html } from "../../vendor/htm-preact-standalone.module.js";
import { registerHelp, registerView } from "../../js/extensions.js";
import { registerSlot } from "../../js/panel-slots.js";   // the same function, re-exported
import { Help, helpFor } from "../../js/help.js";
import { HELP } from "./help.js";

registerHelp(HELP, "projects");
function ProjectView({ token, status, mock, route }) { /* route = { section, id } */ }
function ProjectCard({ token, route, sessionId }) { /* one section of the right panel */ }
registerView({ id: "projects", owner: "projects", label: "Proyectos", component: ProjectView, order: 40 });  // button + "#projects/<id>"
registerSlot("projects", ProjectCard, { title: "Proyecto", topic: "projects.card", owner: "projects", order: 40 });
```

- `registerView({ id, owner, label, component, order })` adds the sidebar button and the
  route `#<id>` or `#<id>/<item>`. The ids of the built-in screens are reserved.
- `registerSlot(name, component, { title, topic, owner, order })` adds a section after the
  four fixed ones of the right panel, with its title and its «?» help (`topic` is a
  key of the extension's own help). The names of the fixed sections are reserved.
- `registerUsageSection({ id, owner, label, component, order })` adds a tab to the «Uso»
  screen after Planes, Global and API y presupuesto (`component` gets `token`). The
  ids `plans`, `global` and `costs` are reserved. `usage.js` draws the registered tabs
  and knows no extension by name.
- Every registration passes the folder name as `owner`; `registerHelp` takes it as its
  second argument. If a module throws after registering, the loader removes its
  views, slots, Uso sections and help texts by that owner.
- Topics are plain strings, every control uses `helpFor("<name>.<topic>")` and every
  section heading is followed by its `<${Help} topic=... />`, as in the built-in views.
  Heading ids start with the name (`doc-alerts-title`): ids are shared by the page.
- The folder is found by `GET /gui/api/extensions` (token required) on every call,
  so a new folder needs no restart and no list anywhere; the page loads the entry
  modules before the first render, but never waits longer than 3 s in all: if the
  list hangs, fails or is not valid, or a module is slow or throws, the core screens
  draw anyway and the page lists what is missing («No se pudo cargar: ...»). An
  extension that arrives after that registers late and the page draws again.
- Nothing changes in the CSP: modules come in with `import()` and sheets as
  `<link>` elements, both same-origin, so `script-src 'self'; style-src 'self'` and
  Trusted Types stay as they are. The wheel ships the folders (`pyproject.toml`).
- `tests/test_gui_help.py` and `tests/test_gui_extensions.py` find the folders
  by this convention and check the help, code and stylesheet rules above; the
  test of a new extension goes in its own file (`tests/test_gui_<name>.py`).

The built-in screens still list their stylesheets in `index.html`; new views do not.

## Continuity in the GUI

A task that hits a usage limit waits (`continuity.hold`, `docs/EXECUTORS.md`). The extension
`static/extensions/continuity/` (CNT-T06) shows that wait and lets a person decide; the data
comes from `multiagent/gui/continuity_api.py`, under the GUI token like every `/gui/api` route.

- **Decision card.** Who reached which limit (app and model), when it resets in local time
  and from which source (the app's notice, the plan's rule or the operator's fixed pause,
  marked as an estimate), the saved work (a patch of N files, or why there is none) and the
  hand-offs used (`0/1`). Choices: «Esperar hasta HH:MM», «Delegar a <app · model · level>»
  (with what would be sent) and «Decidir después». The card never acts for the person: each
  choice opens a confirmation that says what will be saved, and «Decidir después» saves
  nothing. A limit with no reset time (no credits) offers no «Esperar». There is no «Cancelar».
  The conversation history never travels to another app.
- **Where it shows.** The screen «Decisiones» (`#continuity`, the tasks that wait, until the
  project view brings «Qué tienes que hacer»), the right-panel section «Continuidad» (the card
  of the task held on the open session) and the node «En espera · hasta HH:MM» of the agent
  tree (the children of the session carry one `kind: "task"` node per held task, with
  `note: "waiting_limit"` and `reset_at`; the tree already draws that state).
- **Uso → Continuidad.** The mode, the wait threshold, the hand-off limit, «never during an
  integration», «automatic only per project» and the candidate apps with their state and
  policy. Read only: they are the contract defaults until the policy of CNT-T05 is configurable.
- **Routes.** `GET /gui/api/continuity/policy`, `GET /gui/api/continuity/decisions`,
  `GET /gui/api/continuity/session/{session_id}` and
  `POST /gui/api/tasks/{task_id}/continuity/decision` with `{decision_id, decision: "wait" |
  "delegate", executor_id?, expected_revision}`. A stale revision is a 409; repeating a
  `decision_id` returns the first result.
- **What a decision does.** It writes `hold.decision` and the event `continuity.decided`
  (`ref` is the `decision_id`). `wait` accepts the wait; the task resumes by itself at the
  reset. `delegate` records the chosen candidate and sets `hold.blocked` to
  `delegation_requested`, so `resume_due` leaves the task for a person; it starts no other
  executor and changes no assignee (the hand-off is CNT-T05), and `wait` takes it back. The
  GUI never acts for the person and never reads or writes an application.

## Open a session in its application

- **Codex:** follows a `codex://threads/<id>` link.
- **GitHub Copilot:** follows a `ghapp://sessions/<id>` link; the application
  asks the user to confirm.
- **Claude:** after confirmation in the page, the server starts
  `claude --desktop --resume <id>` in a new visible console from the user's
  home folder, with a cleaned environment. Set `GUI_ALLOW_LAUNCH=false` to
  disable launch; the page then shows the command to copy.

Claude and Copilot show new content after reopening the application. Codex
blocks writes while the application has the thread open; the GUI viewer only
reads the session.

## Configuration

Settings are read from the environment using the uppercase names shown below,
or from the project's normal settings file. Session roots may be supplied as
a JSON list or as values separated by `;`.

| Setting | Default | Purpose |
| --- | --- | --- |
| `GUI_SESSION_ROOTS` | `[]` | Allowed working-folder roots for listing sessions; empty means none are listed. |
| `GUI_SESSION_SOURCES` | `["claude", "codex", "copilot", "antigravity", "framework"]` | Session sources to read. |
| `GUI_CLAUDE_HOME` | unset | Override Claude's session store location. |
| `GUI_CODEX_HOME` | unset | Override Codex's session store location. |
| `GUI_COPILOT_HOME` | unset | Override Copilot's session store location. |
| `GUI_ANTIGRAVITY_HOME` | unset (`~/.gemini/antigravity-cli`) | Override Antigravity's (agy) session store location. |
| `GUI_CLAUDE_DESKTOP_DIR` | unset | Override Claude Desktop's session directory. |
| `GUI_ALLOW_LAUNCH` | `true` | Allow the GUI to launch Claude Desktop sessions and the setup consoles of «Apps y agentes». |
| `GUI_NEW_SESSION_INTERPRET` | `true` | Let a model read the free text of Nueva sesión and propose a workflow and inputs; `false` always compares words. |
| `GUI_NEW_SESSION_MODEL` | `local_default` | Catalog model that reads that text (a local model sends nothing out). |
| `GUI_TOKEN_FILE` | `~/.multiagent/gui-token` | GUI access-token file. |
| `GUI_TOKEN_OUTSIDE_HOME` | `false` | Allow the token file outside the user's home folder. |
| `GUI_HEARTBEAT_SECONDS` | `20.0` | SSE heartbeat interval. |
| `GUI_POLL_SECONDS` | `0.5` | Poll interval for GUI run/session updates. |
| `GUI_STREAM_MAX_SECONDS` | `0.0` | Maximum SSE connection duration; zero keeps it open until the client leaves. |

When `GUI_SESSION_ROOTS` is empty, the GUI explains how to configure it rather
than listing sessions. The store-location overrides are optional; unset values
use the readers' normal locations.

## Security model

Every `/gui/api/*` request requires the local token in an
`Authorization: Bearer <token>` header. The API middleware also enforces a
Host allow-list and same-origin checks to mitigate DNS rebinding and CSRF.
State-changing requests accept JSON only. The GUI uses a strict Content
Security Policy with Trusted Types; its Preact and htm dependencies are
vendored under `multiagent/gui/static/vendor/`, with their licenses alongside.
SSE events use a resumable cursor.

The token is a local access credential, not an account or network access
mechanism. Keep the server on loopback and the token file private. Allowing a
token path outside the home folder is an explicit operator choice and is
refused by default.

## Read-only sessions and limitations

Session readers in `multiagent/sessions/` do not write or persist data.
SQLite stores are opened read-only, lines larger than 1 MB are skipped,
reasoning is never shown, and all session content is rendered as plain text.
Only sessions whose working folder is under a configured
`GUI_SESSION_ROOTS` path are listed.

Copilot desktop-only data is not read. The Antigravity (agy) reader lists conversations
from `~/.gemini/antigravity-cli/conversation_summaries.db` (metadata only: no models, no tokens, no messages).
Framework-run status is reduced to
active or idle. Long sessions do not have a "jump to end" control. The session
viewer is read-only; the separate Runs view is where the GUI creates and
manages framework runs.

The GUI HTTP routes live under `/gui/api`, are token-protected, and are not
part of the public `/api/v1` contract. The equivalent local, read-only session
commands are `multiagent sessions list`, `multiagent sessions show`, and
`multiagent sessions items`.
