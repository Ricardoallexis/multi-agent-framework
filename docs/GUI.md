# Operator GUI

The local operator GUI at `/gui/` is the supported local interface for 1.0:
`multiagent gui`, `python -m multiagent.gui` and `run_ui_windows.bat` all open
it. The GUI is served by the local API and is an operator view, not a public API
or a separate orchestration layer.

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
preferences without creating or executing a run. The chat launches framework
workflows, not Claude, Codex or Copilot executor tasks. "Ver como formulario"
opens the create-run form with the same workflow and answers. The conversation
model is pure (`js/new-session-model.js`) and is tested with Node. The proposal comes from
`POST /gui/api/new-session/interpret` (`{"text"}`; it answers `source` `model` with a
`proposal`, or `source` `keywords` with a `reason`) and creates nothing.

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
previews the rendered prompt before creating the run.
Run details show skipped steps, counters, and human-action history. Results are
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
| `GUI_ALLOW_LAUNCH` | `true` | Allow the GUI to launch Claude Desktop sessions. |
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
