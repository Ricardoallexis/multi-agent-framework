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
multiagent gui [--port N] [--real] [--no-browser] [--bundle NAME=FOLDER]... [--trust-functions NAME]...
python -m multiagent.gui [--port N] [--real] [--no-browser] [--bundle NAME=FOLDER]... [--trust-functions NAME]...
```

The GUI defaults to Mock mode. Use `--real` to leave the configured provider
mode in effect; real provider calls may cost money. The server binds only to a
loopback host and refuses other hosts. It creates a random 256-bit local token
in `~/.multiagent/gui-token` and opens
`http://127.0.0.1:<port>/gui/#token=…`. The page stores the token in
`sessionStorage` and removes it from the URL. `--no-browser` prints the URL
without opening it, and with the one-use approval code added to the fragment
(`#token=…&approval=…`); without that option the printed URL carries neither,
and the browser that the command opens gets both (see [Approvals](#approvals)).
The default port is `APP_PORT` (8000).

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
- `--bundle` lasts for that start only: pass the option each time. To keep a bundle, install it
  (see [Installed packages](#installed-packages) below).
- A repeated name stops the launch: `ERROR: bundle name in use: NAME`. That covers two `--bundle`
  options with the same name and a `--bundle` whose name an installed package already has. The same
  name with the same folder is not a clash.
- A wrong option stops the launch before the server starts and prints one line,
  for example `ERROR: --bundle uses the format name=folder` or
  `ERROR: Bundle folder not found: ...`.
- Register only bundles you trust: the bundle's `contracts.py` is imported and
  runs as code.
- A bundle may also bring a `functions.py` that registers [function steps](FUNCTION_STEPS.md). It is never
  imported just for being in the folder. `--trust-functions NAME` (repeatable, for a `--bundle` or an installed
  package) lets it load, after the scan and the approval below, once per package and fingerprint. A name that is
  not registered stops the launch: `ERROR: unknown bundle for --trust-functions: NAME`. The API and the MCP server
  never grant this trust, and reinstalling a package asks for it again.
- A bundle from outside the framework is scanned before its code loads when SkillSpector is installed, and
  the risky cases wait for your click in «Aprobaciones» (see [Bundle scans in Approvals](#bundle-scans-in-approvals)).

### Installed packages

An installed package is a bundle that stays registered across starts. Use the CLI against a running
server (`multiagent gui`):

```console
multiagent bundle install NAME FOLDER [--actor user:local]
multiagent bundle list
multiagent bundle remove NAME [--actor user:local]
multiagent bundle reinstall NAME FOLDER [--actor user:local]
```

- **Install copies the folder** to `<data_dir>/installed/NAME/v1/`. The copy and the origin must have
  the same fingerprint, and symbolic links or junctions are refused. After that the origin is not read
  again: edit the copy's source and run `reinstall` to change what runs.
- **Data and code.** A package whose files are all `.yaml`, `.yml`, `.md`, `.txt`, `.json` or `.csv` has
  level `data` and is validated whole before it is registered. Anything else (a `.py` file, or a file
  without a suffix) makes it level `code`: its first load goes through the bundle scan and the approval
  in «Aprobaciones», as for a `--bundle` folder. Installed copies are always scanned.
- **At every start** the framework recomputes the fingerprint of each copy. A copy that was edited or
  deleted does not load and shows `state: "changed"` or `"missing"` (`reason: "installed_bundle_changed"`);
  the other packages load.
- **Remove** takes the package out of the registry and out of the running process. The copy and the
  package's runs are kept (`kept_data: true`); nothing is deleted. **Reinstall** builds `v2`, validates
  it and only then switches; if it fails, the previous version stays. Both answer `409 bundle_busy`
  while the package has a queued, running, waiting or paused run.
- The paths are never shown. See [Installed packages](API.md#installed-packages) for the HTTP API.

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

The views sit inside a three-zone shell (GUI2-T02, reworked in GUI3-T03 and GUI3-T04):

- **Left sidebar** (`nav`). On a desktop there is no top bar: the brand («Multi-Agent»,
  and «MOCK» when the server uses Mock) sits in the sidebar corner. Top to bottom:
  - «+ Nueva sesión» (the task box at `#new`, see Views);
  - a «Buscar» row that opens Ctrl+K (see Views);
  - the fixed rows: «Pendientes» (with the number that waits for you), «Proyectos» and
    «Apps y agentes», each with a small drawn icon;
  - «Más», which folds every other section: «Sesiones», «Ejecuciones», «Uso»,
    «Evaluación», «Equipos», «Planes», «Mapa» and «Personalizar», plus any extension view
    that declares no group. A section you pin in «Personalizar» moves from «Más» to the
    fixed rows. Each section appears once and keeps its own route, so nothing is removed;
  - the sessions of Claude, Codex and Copilot plus the framework runs, grouped by project
    (the folder name of a session, the project name of a run). The list header has
    «Actividades» (a toggle, `aria-pressed`, that lists open activities instead) and
    «Ver todas» (opens «Sesiones»). Each item has a status dot (working, finished, error;
    also written as text for screen readers) and each group counts its items per app;
  - a footer that stays at the bottom: the connection state, «Personalizar» (the gear) and
    «Ayuda» (the help-mode toggle).

  The first time, a short note under «Más» says: «Las demás secciones están en «Más».
  Puedes fijarlas en la barra.» It goes away with «Entendido» or when «Más» is opened, and
  the browser remembers that. Without storage it shows on every load and can be closed.

  The sidebar uses `/gui/api/sessions` and `/gui/api/runs`; the work map (`#map`, below)
  shows projects, stages and tasks. `GET /gui/api/sessions/counts` returns totals per
  source and status for other clients. Session readers keep a bounded per-file index (up
  to 5,000 entries) and reuse source listings for 2 seconds, so a transcript that only
  grew is not read again.
- **Centre** (`main`): the view of the current route. In a session, consecutive tool calls
  and results fold into one block, "Trabajó N min · K herramientas", that opens to show
  them. While a session or a run is open, a thin bar over the content holds the
  «Contexto» button.
- **«Contexto» panel** (`aside`, formerly the right panel): Cambios, Subagentes, Tokens and
  Fuentes for the session or run open in the centre, plus the sections extensions
  register. The panel starts closed at every width. Its button (`aria-expanded`/
  `aria-controls`) is offered only while a session or a run is open; a dot beside it says
  that the closed panel has something to show. The aside stays in the DOM, hidden, when
  nothing is open. If you open it, the browser remembers that choice (`localStorage`). Each
  section body is a small component in `js/panel-slots.js`.
  - *Which cards are drawn.* Each card has a mode, set in «Personalizar»: «Automática»
    (drawn only when it has content), «Siempre» (drawn even when empty, with its «Sin …»
    text) or «Nunca». Something urgent, such as a held task or document alerts that wait
    for a decision, is drawn in every mode. A card that is not drawn stays mounted, so it
    keeps loading and feeding the dot. «Esperan tu decisión» starts in «Nunca».
  - Subagentes holds the subagents panel (Activos/Terminados tabs).
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

Below about 900 px the sidebar becomes a drawer opened by the «Menú» button (Esc or a
click outside closes it; so does choosing a route, also with Intro in Ctrl+K) and the
«Contexto» panel stacks under the content. A minimal bar keeps «Menú», the brand and
«MOCK». Shared UI components in `js/ui/` provide status chips, progress bars, tabs, level
badges and `icon.js`, 24 inline SVG icons drawn in the page (decorative, with no icon
package and nothing from the network). Status text accompanies color; tabs support arrow
keys, Home and End, and progress indicators expose their values accessibly. The interface
uses a skip link, visible focus and 44 px touch targets. The look is neutral: the action
colour is near-black in the light theme and near-white in the dark one, buttons have no
border, and the type scale runs from 12 to 24 px with weights up to 600. The theme follows
`prefers-color-scheme`; «Personalizar» can change the action colour (see Views).

**Help mode.** The «?» disclosures and the hints are hidden until asked for. «Ayuda» in
the sidebar footer, or F1, turns help mode on and off (the button has `aria-pressed`);
«Mostrar siempre los «?»» in «Personalizar» is the same switch. With the mode off, the «?»
beside each screen title, the ones in the approval cards and the one-line hint under a form
field stay visible. With it on, every explanation is shown as before. Each «?» still opens
its text, and the text stays in the page for `aria-describedby` either way. The choice is
remembered in the browser. If a browser does not let the page intercept F1, the button does
the same.
**Widths and breakpoints.** The GUI is checked at four reference widths: 360 and
390 px (phone), 768 px (tablet) and a desktop of 900 px or more. Every stylesheet
and extension uses the same two breakpoints and no others, written at the top of
`app.css`:

| Breakpoint | Applies to | What changes |
| --- | --- | --- |
| `max-width: 899.98px` | tablet and phone | The sidebar becomes a drawer, the «Contexto» panel stacks under the content, grids fall to one column, and «Ejecuciones» shows the list and the selected run one after the other. |
| `max-width: 599.98px` | phone | The content padding shrinks, the bar over the content keeps its sides, and a run that waits for a person gets a fixed action bar. |

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

**One task box.** «Nueva sesión» starts from a single box (`js/new-session.js`): the task
text and, in one row of 44 px controls, «＋», «Modo», «Modelo», «Proyecto» and «Enviar».
The same box serves the three flows. **Modo** is a selector: **Orquestador** is the guided
flow above (bundle, workflow, team and plan) and **Sesión única** is one task answered by
one model or one executor, with no workflow to pick. The mode is remembered in the browser;
with nothing stored, or with storage blocked, it is «Orquestador». There is no screen that
asks how to start. **«＋»** opens a menu with **Usar un ejecutor** and **Ver como
formulario**; Esc closes it and the focus returns to «＋». **Modelo** lists the connected
models and ends in «Conectar otro… (N sin conectar)», which opens «Apps y agentes».
**Proyecto** lists the names of recent runs (`GET /gui/api/runs?limit=50`); in the executor
flow it is disabled («No aplica al ejecutor»), because the launch contract takes no
project. When a project came from this selector, the guided flow does not ask for it again;
a project that only the model proposed is still checked in the summary. Limit notices sit
under the box, and «Estado de Gemini y Copilot» shows only in «Sesión única». Nothing is
created until a launch button is pressed, and each mode keeps its own confirmation.
**Connected models and executors.** In the orchestrator flow the **Modelo** selector
(`js/model-picker.js`, `model-picker-model.js`) lists the connected models (Gemini, OpenAI,
Ollama); the executors (Claude, Codex, Copilot, agy) are reached from «＋» › **Usar un
ejecutor**. Under the selector, the chosen model shows the time it was checked and its
quota. A row that is not connected folds under «Sin conectar (n)» and says the one step
it lacks: «Falta instalarlo»,
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

*What the picker cannot know.* No session is checked by calling a model. Copilot's
session counts as signed in only while its local quota cache of the current month is
valid; without a valid cache its row keeps «No se puede comprobar la sesión sin gastar
cuota» (`session_unverifiable`) and it is not selectable. A Gemini key is not tried:
Gemini is selectable when its key is set and shows «Sin comprobar» until a real message
proves it. agy has no session check besides its `/usage` quota reading. See
[Limits, «Sin comprobar» and Copilot's cache](#limits-sin-comprobar-and-copilots-cache).

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
general bundle declares `local_default` (Ollama) and `cloud_fast` (Gemini), so this mode
needs Ollama running or Gemini connected; choosing the cloud model needs the cloud
confirmation (`422 cloud_confirmation_required` without it). **Revisar y lanzar** shows the routing preview, and **Lanzar** creates the run
through `POST /gui/api/runs` with an idempotency key (a server that is not Mock also
asks for the real-model confirmation). The run keeps its usage records, budgets and
egress policy, and the column follows it at `#new/run/<run id>` like any other run.

**Sesión única with an executor.** **Usar un ejecutor**, in the «＋» menu of the box, lists the four executors with
the reason each one is not available, their remaining quota and, for agy, its notice.
The models and efforts come from `GET /gui/api/composer/executors`. **Revisar
lanzamiento** opens a confirmation with the executor, model, effort, remaining quota,
the task and, for agy, its notice; only **Aprobar y lanzar** sends the launch, so every
launch is approved one by one. Copilot is selectable only with a valid quota cache (see
above), and always needs a model. The server
(`POST /gui/api/executors/launch`, with the token) refuses a request that lacks
`"confirmed": true` (409 `confirmation_required`), creates a task and calls the same
executor service as the API, with its checks: a disabled executor (`executor_disabled`),
Copilot without a model (`model_required`), and agy's switch, notice, quota floors and
single running launch. A refused launch cancels the task it created, and the refusal
and its reason are shown. After a launch, **Seguimiento de la tarea** polls
`GET /gui/api/executors/launch/{task_id}` every 4 s until the task ends, and shows the
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

*Message box.* The thread ends with a box that writes to the inbox of the run
(`js/inbox-model.js`, in `js/thread.js`): **Nota** (for the record, the run goes
on), **Ajustar**, **Interrumpir** and **Cancelar**. The last three ask for a
confirmation first. A message is only appended through
`POST /gui/api/runs/{id}/messages` (`GET` lists them), with the actor
(`user:operator`) fixed by the server and an idempotent `message_id`; the run
reads it at the boundary of its next step, never when it is posted: «Cancelar»
ends the run as cancelled, «Interrumpir» and «Ajustar» make it wait for you, and
«Nota» is recorded. None of them approves, accepts or rejects anything: a run
that waits for you is still settled with its cards. The tasks have the same
routes (`/gui/api/tasks/{id}/messages`). The box is part of the opt-in
availability policy (`AVAILABILITY_ENABLED`, off by default): while it is off,
`GET /gui/api/inbox/policy` says so with its reason, the box shows that reason and
sends nothing, and the server answers `409` with `availability_disabled`. A run
that has ended no longer takes messages.

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

Each tool in the list is a fold: its 44 px summary shows the name, the state in words and,
while folded, the missing step in one line. A tool with a pending step starts open; the
others start folded. The console step and the copyable commands are unchanged.
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

*Probar conexión and the provider certificates.* A model card has **Probar conexión**
(`POST /gui/api/connections/{id}/probe`). It makes one request that generates no text:
the model list of Gemini or OpenAI, or Ollama's health. It runs only when the person
presses it; **Volver a comprobar** and the list still make no call to Gemini or OpenAI.
An antivirus or a proxy that inspects TLS makes a provider fail with a certificate error
(`reason: tls_certificate`, for every provider). The row then carries
`tls_fix: {"trust": "system", "label": ...}` and the card shows **Usar los certificados de
Windows** (**Usar los certificados del sistema** outside Windows), in both modes.
Pressing it sends `POST /gui/api/connections/{id}/tls-trust` with `{"trust": "system"}`:
the server writes `provider_tls_trust` in `gui-settings.json`, records the event
`provider.tls_trust_changed` (`connection_id`, `from`, `to`), drops the cached provider
clients and probes again. It is the person's decision about whom to trust; nothing in the
framework presses it. **Volver a los certificados de siempre** writes `certifi`. The GUI
never takes a file: `file:<path>` (a PEM of your own) goes in `.local/.env`, in the
advanced mode, and the server answers `422` to it. The setting covers the HTTP clients of
Gemini, OpenAI and Ollama, not the executors; see
[Provider certificates](BACKENDS.md#provider-certificates).

*The step that is missing.* Each card gives one step, never the whole list:

- **Install or sign in.** **Abrir consola** asks for confirmation first. The
  confirmation names the fixed, dated official command (winget or npm, never a remote
  script) and its source, and only **Confirmar y abrir consola** sends
  `POST /gui/api/setup/console`; **Cancelar** sends nothing. The server opens a visible
  Windows console that shows the command and waits for the person to press Enter in it,
  so nothing runs hidden. The request does not wait for the console: the server answers
  `202` with a console id as soon as the console opens, and the view asks
  `GET /gui/api/setup/console` every 4 seconds until the state is `closed` (the card then
  shows the exit code and the app is measured again) or `failed`. Only one console can be
  open at a time, and the lock is held until it closes. After installing Claude, the
  console also checks its publisher signature. The view never reads what is typed in the
  console.
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
  session cannot be checked without spending quota. A Copilot session is known only
  through its quota cache (see [Limits](#limits-sin-comprobar-and-copilots-cache)), so
  its card offers the sign-in console as optional.

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
| `GET /gui/api/runs/{id}/messages` (`?after=`, `?limit=`), `POST /gui/api/runs/{id}/messages` | The messages of the person's inbox for a run, and writing one (`{message_id, kind: note\|adjust\|interrupt\|cancel, text}`). The actor (`user:operator`) is fixed by the server, the post is idempotent by `message_id` and only appends; it answers `409` `availability_disabled` while the availability policy is off. It never approves anything. |
| `GET /gui/api/tasks/{id}/messages`, `POST /gui/api/tasks/{id}/messages` | The same for a task, read by its background attempt. |
| `GET /gui/api/inbox/policy` | Whether the availability policy is on and, when it is not, the reason (read only). |
| `GET /gui/api/pending/background` | Runs that are `running` or `queued`, for «En fondo» in «Pendientes» (read only). |
| `GET /gui/api/runs/{id}/tool-approval/preview?permission_request_id=` | Content and diff of a pending `file.write` approval, only when the rebuilt arguments match its fingerprint; writes nothing. |
| `GET /gui/api/connections`, `POST /gui/api/connections/{id}/check` | Cached connection facts for the seven model providers and executor apps, or a fresh check of one; no model is called and no key value is returned. |
| `GET /gui/api/setup/status` | For «Apps y agentes»: whether launching is allowed (`gui_allow_launch`) and the console is supported, the read-only presence of each permission template, and the fixed, dated install and sign-in plans (read only; no template file is read, only checked for presence). |
| `POST /gui/api/setup/console` | Open the visible Windows console for a fixed `install` or `login` step of a tool; the body is only `{"id", "step"}` (any other field is 422, an unsupported step 400). It answers `202` with a `console_id` as soon as the console starts, without waiting for it to close (`200` when the step has nothing to open). It answers `403 launch_disabled` when `gui_allow_launch` is off, `409 console_already_open` while another console is open and `409 launch_unsupported` outside Windows. |
| `GET /gui/api/setup/console` | State of the latest console: `none`, `open`, `closed` (with the exit code and the new measurement) or `failed` (read only). |
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

The detail of a run shows its summary first and puts the rest in three tabs
(`js/runs-layout.js`, `js/run-detail.js`; arrows, Home and End move between them):
**Pasos** (the default: steps and counters), **Resultados** (outputs by step and attempt,
with **Comparar**) and **Detalles** (the human history, the inputs and the technical
events). The summary, errors, the limit notice and the human actions stay above the tabs
while the run waits for a person, and below them otherwise. The tab goes back to «Pasos»
when another run is chosen and is not written to the hash, so `#runs/<id>`, `#run=<id>`
and «Abrir en el chat» do not change.
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

**Pendientes** (`#pending`) is a screen, not a popover. Its sidebar row is a link with the
number of items that wait for a person: runs in review, proposed team versions and plans,
continuity decisions and tasks that wait for the user. The count is also in the tab title,
as `(2) Multiagent`. The screen has tabs: **Todo**, **Aprobaciones**, **Decisiones**,
**Alertas de documentos** and **Esperan tu decisión**. The tabs after «Todo» are the views
that register with `group: "pending"`; a tab shows a count when its view declares a
`pendingType` (`approval`, `continuity` or `task`) and the pending API counts that type
(«Alertas de documentos» has no count). The arrows, Home and End move between tabs and
change the hash. The old addresses still open their tab: `#approvals`, `#approvals/<id>`
(the card), `#continuity`, `#document_alerts` and `#task_waiting`. These views are not rows
of «Más».

One poll serves the sidebar row and the screen: `GET /gui/api/pending`, read-only and
protected by the token, every 5 s while the browser tab is visible and every 30 s while it
is hidden; it stops when nothing reads it. **Todo** is the list: each item says its kind
and why it waits and links to the run's thread, or to the team, plan, continuity or task
view. The link only changes view: it approves and starts nothing. **Actualizar** reads the
list again. **Activar avisos** is off by default. Pressing it asks the browser for
permission, and only then does the browser on this PC raise a notification for each new
item. The choice is kept in this browser, and **Desactivar avisos** turns it off. Nothing
is sent to another device.

Under the list, **En fondo** shows the runs that are `running` or `queued`
(`GET /gui/api/pending/background`, read only; it does not change the count or the
contract of `GET /gui/api/pending`), each with its state and a link to its thread.
**Cancelar** asks for a confirmation and then writes a `cancel` message to the
run's inbox (`POST /gui/api/runs/{id}/messages`): the run stops at its next step
boundary, not at once, and nothing is approved. With the availability policy off,
the section says so and the button is disabled.

**Ctrl+K** (Cmd+K on a Mac), or the «Buscar» row of the sidebar, opens one native dialog
with a search field. It finds screens (every row of the sidebar and of «Más»), the tabs of
«Pendientes», sessions, runs and help titles, matching without accents. Up and Down move,
Intro opens the entry, and one Esc closes the dialog, also with text typed; the focus goes
back to where it was. On a phone, choosing a route closes the drawer. A help entry shows
its text inside the dialog. The dialog only navigates and explains: it never approves,
allows or launches anything, and it talks to no server.

**Personalizar** (`#customize`, reached from «Más», the gear in the sidebar footer and
Ctrl+K) has four blocks:

- *Barra lateral*: which sections are pinned to the sidebar (**Fijar**); the rest stay in
  «Más» and in Ctrl+K.
- *Contexto*: when each card of the panel is drawn, «Automática», «Siempre» or «Nunca».
- *Colores*: «Neutro» (the default), «Azul», «Verde» and «Violeta», each a radio with its
  swatch. A palette changes only `--accent`, `--on-accent` and `--accent-soft`, in the
  light and the dark theme (`.app[data-accent]` in `app.css`); backgrounds, text and the
  warning tones do not change, and every palette keeps 4.5:1 contrast.
- *Ayuda*: «Mostrar siempre los «?»», the same state as F1.

**Restablecer** goes back to the defaults (see
[What this browser remembers](#what-this-browser-remembers)). Choices apply without a
reload.
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
and a «?» disclosure with a concise explanation (shown in help mode, see Layout). The
disclosures are accessible by keyboard and can be closed with Esc.

Stop either GUI server with Ctrl+C. The `multiagent gui` command prints
«GUI detenida.» after clean shutdown.
On Windows, a batch launcher may additionally prompt `Terminate batch job
(Y/N)?`; answer `Y`.

## What this browser remembers

The GUI keeps a few choices in `localStorage`, only in `js/shell-state.js`, and only on
this PC. Every read and write is guarded: when storage is blocked (a private window, site
data cleared) the page works with the defaults and the choices last only while it stays
open. «Personalizar» says so when the browser does not remember.

| Key (`multiagent.gui.…`) | What it keeps | Default |
| --- | --- | --- |
| `right-panel` | «Contexto» open or closed | closed |
| `context-cards` | the mode of each card (`auto`, `always`, `never`) | each card's own default |
| `pinned-sections` | the sections pinned to the sidebar | none |
| `more-notice` | that the note under «Más» was seen | not seen |
| `help-mode` | help mode on or off | off |
| `accent` | the colour palette (`neutral`, `azul`, `verde`, `violeta`) | `neutral` |
| `new-session-mode` | «Orquestador» or «Sesión única» | orchestrator |
| `pending-notifications` | whether browser notifications are on | off |

**Restablecer** in «Personalizar» goes back on four of them: the pinned sections, the
card modes, help mode and the colour palette. It does not touch the panel, the note
under «Más», the new-session mode or the notification switch. A value that is not valid
is ignored, and an unknown palette reads as «Neutro». None of these keys holds a secret
or any run content.

## Packages

«Paquetes» (`#packages`, in «Más», available in simple mode) lists the installed packages and installs
new ones without a console. Each row shows the package name, its level (`data` or `code`), its version,
its origin and its state. The origin is the `source_label`, the name of the file you picked, never a path.

- **Install from a `.zip`.** Pick the file in the file selector and press «Instalar». The browser sends the
  file as JSON with the bytes in base64 (`POST /gui/api/packages/zip`, `{name, filename, zip_base64,
  trust_functions}`), so `application/json` stays the only body the GUI routes accept. The name is taken
  from the file name (without `.zip`, any character outside letters, digits, `_` and `-` becomes `-`, at
  most 120) and you can change it.
- **Limits.** The `.zip` may be 25 MB at most. The server reads `content-length` before the body and answers
  `413` with `details.reason = "zip_too_large"` above the limit. Extracted it may hold 50 MB and 2000 files;
  the real bytes are counted while writing, not the sizes the archive declares.
- **What is refused** (`422`, `details.reason`): `zip_unsafe_entry` for an absolute path, a drive letter, `..`,
  a backslash in a name, a symbolic link and a repeated name (also one that differs only by case);
  `zip_invalid` for a file that is not a `.zip`, a damaged or encrypted one, or one whose content does not
  match what it declares; `zip_too_large` for too many files or too many bytes. A `.zip` with one folder on
  top, like the one Windows makes with «Enviar a › Carpeta comprimida», is unwrapped. The `__MACOSX` entries
  are ignored. The archive is extracted into `installed/.staging/<id>/`, copied and checked like a folder
  install, and the staging folder is removed whether it works or not.
- **Simple mode.** A package of level `code` answers `409 advanced_mode_required` with
  `action: "install_code"`; a package of data only installs. The folder route
  (`POST /gui/api/packages/folder`, `{name, folder, trust_functions}`) and `trust_functions: true` also need
  the advanced mode (`install_folder`, `trust_functions`), checked in the order `trust_functions`,
  `install_folder`, `install_code`. In advanced mode the view also offers «Instalar desde una carpeta» and
  the box «Confiar en las funciones de este paquete»; a package whose copy is new is never trusted
  unless you tick it at that moment.
- **The example package.** «Instalar el paquete de ejemplo» (`POST /gui/api/packages/example`) registers
  `examples/bundles/general` by reference: `source: "shipped"`, name `general`, no copy. It stays exempt
  from the scan because it is code of the product, and it is only offered in a checkout of the product
  (`409 example_not_available` otherwise). It works in simple mode.
- **A package that changed or is missing** is shown degraded, with «Reinstalar desde un archivo»
  (`POST /gui/api/packages/NAME/reinstall-zip`, `{filename, zip_base64}`) and «Quitar». Reinstalling builds
  a new version and switches only if it validates; it is never trusted again by itself.
- **«Quitar»** asks first: «Se quita de la lista. Se conservan sus datos». It takes the package out of the
  registry and out of the running process (`POST /gui/api/packages/NAME/remove`); the installed copy and the
  data of its runs stay on disk, and a package with runs in progress answers `409 bundle_busy`.

`GET /gui/api/packages` answers `{packages, example_available, mode}`. Every route needs the local token and
`application/json`; the actor is `user:gui` and the channel `gui`. `/api/v1/installed` is unchanged and
still takes no `trust_functions`.

## Simple and advanced mode

The GUI has a simple mode and an advanced mode. The mode belongs to the installation, not
to a browser: it lives on the server, in `<local_dir>/gui-settings.json`, and it is the
same in every browser. It starts as `simple`.

```json
{"schema": "maf.gui-settings/1", "mode": "simple", "updated_at": "…", "updated_by": "user:gui@gui"}
```

- A missing or broken file reads as `simple` and is not rewritten until the first change.
- The file is written atomically (a temporary file in the same folder, then `os.replace`),
  so a crash never leaves a half-written file or a `.tmp` behind.
- The keys are a closed list (`ALLOWED_VALUES` in `multiagent/services/gui_settings.py`).
  A key or a value outside it is a 422 `invalid_request`. A key that was never written
  does not appear in the answer.
- Each change keeps `updated_at` and `updated_by` in the same file. There is no event.

| Route | What it does |
| --- | --- |
| `GET /gui/api/settings` | the settings: `schema`, `mode`, the keys written, `updated_at`, `updated_by` |
| `POST /gui/api/settings/mode` | body `{"mode": "simple" \| "advanced"}`; answers like the route above |
| `GET /gui/api/session` | `{"mock", "version", "mode"}` |

Both `/gui/api/settings` routes need the local token, and the `POST` needs `application/json`.

**What the server enforces.** Only two things depend on the mode, installing code and
trusting a package's functions. They call `services.gui_settings.require_advanced_mode(action)`,
which in simple mode answers 409 `invalid_state` with
`details = {"reason": "advanced_mode_required", "action": …}`. The actions are
`install_code`, `install_folder` and `trust_functions`. No other route changes, and
`/api/v1` is the same in both modes. What each mode shows is decided by the browser.

**What each mode shows** (SIM-T02). The browser decides it, from the `mode` of `GET /gui/api/session`;
nothing about the mode is kept in `localStorage`. `js/gui-mode.js` holds the value of the page
(`currentMode`, `setCurrentMode`, `onModeChange`, `visibleIn`) and `saveMode(token, mode)` posts the change.
The switch **Modo avanzado** in «Personalizar» changes it and the GUI draws again without reloading.

- **Advanced** shows everything, as it always did.
- **Simple** shows only a closed list. The sidebar has «Nueva sesión», «Buscar», «Pendientes»,
  «Proyectos», «Apps y agentes» and the list of sessions. «Más» has «Uso», «Personalizar» and
  «Ayuda» (a row that turns help mode on, not a view). The panel «Contexto» stays in both modes.
- Ctrl+K searches only what the mode shows: sections, the tabs of «Pendientes», sessions and help texts.
- In simple mode a run of the list opens its thread (`#new/run/<id>`), not `#runs/<id>`.
- A section you pinned that is advanced is hidden in simple mode, not released: it is pinned again
  when you go back to advanced.
- A route of an advanced view in simple mode (for example `#runs` from a link) shows the line
  «Esta vista es del modo avanzado» with the button «Activar modo avanzado». The address does not change.

`registerView` has a `mode` key, `"simple"` or `"advanced"`. **A view with no `mode` is advanced**, so a
new view stays out of simple mode until it says `mode: "simple"`; any other value is refused. The built-in
sections declare it in `SECTIONS` (`js/main.js`): `sessions`, `usage`, `customize` and `packages` are simple, and
`runs`, `evaluation`, `teams`, `plans`, `map` and `tree` are advanced.

**What each screen hides in simple mode** (SIM-T03). Each screen reads `currentMode()` when it draws. Advanced mode
draws what it always drew; the pure decisions live in `js/thread-model.js` (`showRawOutput`, `threadLinksFor`),
`js/new-session-model.js` (`plusMenuEntries`, `cardOrigin`) and `extensions/apps/model.js` (`missingAction`).

| Screen | Simple mode hides | Simple mode keeps |
| --- | --- | --- |
| Run thread | «JSON completo», «Llamadas a modelo», «Ver pasos anteriores» and the earlier attempts, «Ver detalle» (`#runs/<id>`), the links «Ver plan» and «Ver equipo», the model or function of a step, the attempt number of a stop, and the JSON text of a final object output | the readable output of a review, the final object output as readable fields, the decision buttons |
| Session | «Tokens de este mensaje»; a run event with no known text shows its type, not its JSON | the messages, tools and the rest of the items |
| «Nueva sesión» | the line «Origen: …» of the suggested cards and of the summary, and «Ver como formulario» in the «＋» menu | «Usar un ejecutor» |
| «Apps y agentes» | the lines to paste (`enabled: true`, `OPENAI_ENABLED=true`, the key name), «Copiar» and the command text of the console steps | the console buttons (install, sign in), «Volver a comprobar», «Probar conexión» and the certificate buttons |

In «Apps y agentes» a step that edits a file (enable an executor or a model, an API key) is one line,
«Este paso se hace en modo avanzado» (help key `apps.advanced_step`). API keys stay in advanced mode.

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
registerView({ id: "projects", owner: "projects", label: "Proyectos", component: ProjectView, topic: "projects.view", order: 40,
  group: "primary", icon: "folder" });  // sidebar row + "#projects/<id>"
registerSlot("projects", ProjectCard, { title: "Proyecto", topic: "projects.card", owner: "projects", order: 40 });
```

- `registerView({ id, owner, label, component, topic, order, group, icon, pendingType, mode })` adds
  a sidebar row and the route `#<id>` or `#<id>/<item>`. The ids of the built-in screens are reserved.
  `topic` is optional and is a help key of the extension (`"projects.view"`, in the same
  `name.topic` form as the slot topics). `main.js` draws the page heading from `label` and
  places the «?» button of `topic` beside it, so the view must not draw a heading of its
  own: the name would show twice. A `topic` that is not a help key is refused.
  The last four fields are optional and are checked at registration:
  - `group`: `"primary"` (a fixed row of the sidebar), `"pending"` (a tab of «Pendientes»,
    not a row) or `"more"` (under «Más»; the default).
  - `icon`: the name of one of the drawn icons of `js/ui/icon.js` (`folder`, `apps`,
    `shield`, `file`, `clock`, …); the default is `dot`. Any other name is refused.
  - `pendingType`: `"approval"`, `"continuity"` or `"task"`, for a view of `group: "pending"`;
    it says which items of `GET /gui/api/pending` the tab counts. Without it the tab has no
    count.
  - `mode`: `"simple"` or `"advanced"` (SIM-T02). Simple mode shows only the views that say
    `"simple"`; without it the view is advanced. Any other value is refused.
- `registerSlot(name, component, { title, topic, owner, order, defaultMode })` adds a section
  after the four fixed ones of the «Contexto» panel, with its title and its «?» help
  (`topic` is a key of the extension's own help). The names of the fixed sections are
  reserved. `defaultMode` is `"auto"` (the default), `"always"` or `"never"`: when the card
  is drawn until the person chooses in «Personalizar». The component receives `onContent`
  and calls `onContent({ has, urgent })` to say whether it has something to show and
  whether that is urgent (urgent shows in every mode); a card that never calls it counts as
  having content.
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

## Result document

A completed run shows a «Documento» card under its thread, in the simple and in the advanced
mode. It holds the result of the run as Markdown and a «Guardar como Markdown» button.

- **What the document is.** The title is the first line of the workflow's `description`
  (120 characters at most), or its `id`. The body is the output of the result step
  (`result_step`, or the last completed step) rendered by `multiagent/documents.py`
  from the step's contract: `<`, `>` and `&` in values are escaped.
- **How it is shown.** As plain text in a `<pre>`. The Markdown is not drawn and no HTML is
  injected, so the card works under the GUI's CSP and Trusted Types.
- **How it is saved.** The browser builds the file: a `Blob` of type
  `text/markdown;charset=utf-8` and a temporary link with `download`. The server writes to no
  path. The file name is the title as a slug (lowercase, no accents, `-` between words, 60
  characters at most), the first 8 characters of the run id and `.md`, for example
  `resumen-de-documento-3f2a9c1b.md`.
- **When there is no document.** A run that has not completed answers 409 `invalid_state` with
  `details.reason` `run_not_completed`; a completed run with no step output answers `no_result`.
  The card is not shown and the thread shows no error.

| Where | Route or command |
| --- | --- |
| Thread card | `GET /gui/api/runs/{run_id}/document` (token): `{schema: "maf.run-document/1", run_id, title, step_id, filename, markdown}` |
| API | `GET /api/v1/runs/{run_id}/document.md`: the Markdown, with `Content-Disposition: attachment` |
| CLI | `multiagent run-document RUN_ID [--output FILE.md]`: prints the Markdown, or writes it and refuses to overwrite an existing file |

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

## Limits, «Sin comprobar» and Copilot's cache

«Nueva sesión» and «Apps y agentes» say what is connected, what is unverified and what
has reached its limit, without spending any quota.

**How each tool is measured without spending.** Nothing here generates with a model.
The source is the one each app already leaves for itself:

| Tool | Source | What it gives |
| --- | --- | --- |
| Claude | Its own session files; the `statusLine` JSON only if `usage_claude_statusline_file` is set | Tokens per message, the reset when a limit was hit, a percent only with the status-line file |
| Codex | `token_count` events in its session files | Tokens, and the percent and reset of the 5-hour and weekly windows |
| Copilot | Its local quota cache (below) | Premium interactions left, percent and monthly reset |
| agy | `agy -p /usage --output-format text` | The percent left and the reset of each window |
| Gemini | None: the API reports no quota | The card says «La API de Gemini no informa la cuota.» |

**What is read from Copilot's cache.** The file is `copilot-user-cache.json` under
`%LOCALAPPDATA%\copilot`, or the path in `USAGE_COPILOT_CACHE_FILE`
(`usage_copilot_cache_file`); a Mock server never looks for the default path. The
reader (`multiagent/sessions/usage_sources.py`) opens it read-only, skips the `//`
comment lines the file starts with, and takes the newest entry (`retrievedAt`) of the
current UTC month. From it, and only from it, it reads these fields:

- `quota_reset_date_utc`;
- inside `quota_snapshots.premium_interactions`: `entitlement`, `remaining` (or
  `quota_remaining`), `percent_remaining`, `unlimited`, `overage_permitted` and
  `timestamp_utc`.

Every other field is never read and never stored, whatever it holds: the account login,
tracking identifiers, endpoints and the rest of the JSON. The file is read only up to
256 KB and to three levels of nesting. Any doubt gives «sin dato»: a missing,
oversized or malformed file, an entry from another month, a reset that already passed,
an entitlement or balance that is missing (unless the plan is unlimited), or a percent
over 100.

A valid cache makes Copilot's row `verification: verified` and its session signed in,
so Copilot becomes selectable; a balance of zero or less shows «Límite alcanzado» even
when paid overage is allowed, because overage is money the framework never spends.

**What «Sin comprobar» means.** Gemini with its key set and enabled is selectable
(`verification: unverified`) and shown as «Sin comprobar»: no probe call is made, since
that would spend quota. The first real message is the probe. Its outcome is kept in the
memory of the process only (`multiagent/model_checks.py`), with no migration, and is lost
on restart, when the row goes back to «Sin comprobar»:

| Outcome | Row says |
| --- | --- |
| `verified` | «Conectado» |
| `key_invalid` | «Clave inválida» |
| `limit_reached` | «Límite alcanzado», with the reset time when the provider gave one |
| `no_access` | «Sin acceso» |

The key is never stored and the provider's own error text is never kept in the error.
**The real Gemini key is tried by the user at the end, never by an agent:** automated
checks use a fake Gemini client.

**«Límite alcanzado».** A limited row, run or launch carries
`limit: {code: "limit_reached", resets_at, target, suggestions}`, with up to three
connected alternatives. The GUI writes «Límite alcanzado · vuelve a las HH:MM» in local
time, or «Límite alcanzado · sin hora conocida» when no reset was observed; the time is
never guessed. Where a limit is shown, the available alternatives appear as buttons that
only change the selection and keep the text already typed.

**A new message and a long task.** A limit does not behave the same for both:

| Entry | Class | With a limit |
| --- | --- | --- |
| «Nueva sesión» free text | new message | Never switches model. Says «Límite alcanzado» and keeps the word search. |
| «Sesión única» with a model | new message | Does not switch; shows the time, suggests the available ones and keeps the text. |
| «Sesión única» with an executor | new message | The task is created with `continuity.mode = manual`, so it asks and never hands off by itself. |
| Planner with a model | new message | Already has no fallback. |
| Workflow runs (GUI, `multiagent run`, `POST /api/v1/runs`, MCP, worker), approved plans, executor tasks by CLI or API | long task | The router moves to the fallback as before; each attempt records `retry_at`. |

A run that exhausts its fallbacks fails as before. Its detail shows «Límite alcanzado»
and **Reintentar**, which opens «Nueva sesión» and relaunches nothing.

**agy's floors are a warning.** When the plan has less than 15 % left in a window that
applies, the connection row stays selectable and carries `warning`
(`reason`, `remaining_percent`, `resets_at`); the picker shows «Quedan X %; bajo el
piso del 15 %». **Aprobar y lanzar** is the person's confirmation: it sends
`accept_quota_warning: true`, valid for that one new launch only (never for a resume or
an automatic one). At 0 % the plan limit always stops the launch as «límite alcanzado»,
and the framework never spends agy's AI credits. See
[`EXECUTORS.md`](EXECUTORS.md#controls-and-launch).

A generic run that selects a cloud model needs the person's cloud confirmation
(`confirm_cloud` in `POST /gui/api/runs`, `--confirm-cloud` in `multiagent run`); the
server answers `422 cloud_confirmation_required` without it.

## Approvals

«Aprobaciones» (`#approvals`, an extension) is a tab of «Pendientes» (`#approvals` and
`#approvals/<id>` still open it); its cards and the click that decides are unchanged. It
lists the decisions that only a click in the GUI can give, each
as a card. Three kinds exist: a send that carries a secret (`secret_send`), the OSV.dev query of a bundle scan
(`osv_query`) and the load of a flagged bundle (`bundle_load`). The last two are described in
[Bundle scans in Approvals](#bundle-scans-in-approvals); the first is stopped by the
[secret gate](security/secret-gate.md), which is always on.

A secret card shows why the send was held, the destination (a model, or an executor and its model), where it
came from (a run, a task, «Nueva sesión» or the planner), the secret types and a masked sample. It never
shows the secret. **Aprobar este envío** authorizes that exact text for that destination once; **Rechazar**
closes the request so nothing goes out. There is no «remember» and no «approve all», and a different text
asks again. Opening or refreshing the view only lists the requests; it never decides or spends one.

How the click is protected:

- **Startup code.** `multiagent gui` makes a random code at each start. The browser it opens receives it in the
  URL fragment next to the token, and `auth.js` exchanges it once with `POST /gui/api/approvals/session` for an
  `HttpOnly`, `SameSite=Strict` cookie limited to `/gui/api/approvals`. With `--no-browser` the printed URL
  carries the code, so open that URL yourself.
- **One browser.** Only the first exchange works for each start. A second tab or browser, or a page opened
  without the code, sees «Otra pestaña o navegador ya tomó las aprobaciones: reinicia la GUI», and the card
  asks you to restart the GUI. Restarting invalidates every cookie and code.
- **One code for each decision.** The card asks for a challenge code tied to that approval, its SHA-256 and the
  browser session (`POST /gui/api/approvals/{id}/challenge`), and sends the decision with it in the same click
  (`POST /gui/api/approvals/{id}/decision`). The code is valid once and for 120 seconds; **Intentar de nuevo**
  asks for another.

What happens next depends on the origin. A run waiting on `secret_review` goes back to the queue when you approve
and fails when you reject. A held executor task is checked right after the decision: if the text is the same, it
launches once; a rejection never launches it. In «Nueva sesión», a held message stays in the box and the card
links to «Aprobaciones»; after you decide, send it again and it goes with its `approval_id`. The tray, the CLI, the
HTTP API and MCP answer `user_click_required` for these approvals.

The first real **Aprobar** is yours. The tests approve with a test client and seeded values, and no agent or
worker presses the button in the real GUI. The guide to the [secret gate](security/secret-gate.md) says what
the gate does not see and the risk that remains on a PC where programs run as the same user.

## Bundle scans in Approvals

«Aprobaciones» (`#approvals`, the extension `multiagent/gui/static/extensions/approvals/`) shows the two
decisions of a bundle scan as their own cards. Both come from an external bundle that was asked to load; see
[Bundle scan](security/bundle-scan.md) for what the scan does and what each verdict means.

- **Consulta a api.osv.dev** (`osv_query`). It names the bundle, the host `api.osv.dev` and the manifests whose
  package names and versions would leave the computer, or says that none were found. **Permitir la consulta**
  starts the scan; **No permitir** leaves the bundle «sin analizar».
- **Cargar este bundle** (`bundle_load`). It shows the verdict (Seguro, Con avisos, No instalar or Sin analizar),
  the coverage of each folder with the ones left unanalyzed marked, and the top 10 findings as plain text (level,
  rule, file and line, with the message cut at 200 characters) and how many more there are. A note says that the
  scan has no language model and that many high findings are false positives. A partial «No instalar» report keeps
  that recommendation and separately says «Cobertura parcial». The unchecked «Asumo los riesgos de cargar este
  bundle» box keeps **Cargar este bundle** disabled until checked; the server also requires it. **No cargar** stays
  available without the box. The box clears after a decision or when the card becomes stale.
- While a scan runs the card says «Escaneando · unos 25 s por carpeta», and the view polls
  `GET /gui/api/bundles/{name}/scan` every 3 s (every 10 s when idle) and refreshes when the scan ends.
- Each decision is a one-use approval bound to the bundle's fingerprint, like the other approvals of the view: it
  asks for a fresh code and sends the decision in the same click. There is no «remember» or «approve all».
- An edited bundle has a new fingerprint and is scanned again; a result for the old one does not carry over.

Reading the scan status never starts a scan. The cards only appear when a load request (`409
bundle_scan_pending` or `bundle_scan_blocked`) created the approval.

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

The GUI pages (`/gui`, `/gui/`) and the files under `/gui/static/` are sent with
`Cache-Control: no-cache`: the browser may keep a copy but revalidates it, so after an
update it does not run stale scripts. `StaticFiles` still answers `304` with `ETag` and
`Last-Modified` when nothing changed. A response that already sets `Cache-Control`, such as
the `no-store` event stream, keeps its own, and `/gui/api/*` is unchanged.

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
