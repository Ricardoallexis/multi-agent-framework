# Local project tracker

These PowerShell sources implement the local `Seguimiento.cmd` project tracker
and its cloud-assignment workflow. They are versioned separately from the
operator's private channel data.

The normal launcher lives beside the private channel and invokes the versioned
scripts in this directory. It passes the private channel path explicitly. The
channel contains task cards, histories, assignment JSON, package reservations,
ideas, generated HTML, and loopback launchers; none of that data belongs in
this directory or in version control.

The entry points that consume private state require an explicit path:

- `ver-seguimiento.ps1 -ChannelPath <channel>\sistema -RepoPath <repository>`
- `preparar-paquete-cloud.ps1 -ChannelPath <channel>\sistema -RepoPath <repository>`
- `importar-paquete-nube.ps1 -ChannelPath <channel>\sistema -RepoPath <repository>`
- `gestionar-asignaciones-cloud.ps1 -SystemRoot <channel>\sistema`

The tools do not upload packages or claim live provider activity. Upload and
post-preparation state changes remain explicit operator actions.

## Cross-application workers

The orchestrator and worker may use different applications (channel rule 33). The **Agents** tab lists each delegated, active worker on the card for its own application ("working for <orchestrator> on <task>", with its model, actual activity, and tokens for the current window), as well as on the orchestrator's delegation line. An application card does not say "no activity" while it has active workers; a worker marked "probably stopped" is listed but does not count as active.

The application is determined by the worker reference (`agent-` for Claude, `copilot:` for Copilot, `codex:` for Codex, `agy:` for Antigravity), or by the worker name when there is no reference ("Copilot gpt-6-luna"). Activity files are read only for timestamps, status, and model names, never for their contents.

There is a single application table, `$script:WorkerApps` in `ver-seguimiento.ps1`. To add an application, add one entry; no agent-specific cases are needed: `Key`, `Application` (card name), `RefPattern` and `RefId` (reference), `BarePattern`, `NamePattern`, `ShowAppPrefix`, `TailBytes`, `FindFile` (where its activity file is), `ReadModel` (how its model is read), and `PendingTest` (how a running test is detected). Tests are in `tests/SEG-T17/`.

Antigravity (`agy`, GEM-T04): its reference is `agy:<conversation_id>` (UUID), and its workers are named `Antigravity-<family>`. Activity is based on the modification time of `conversations\<id>.db` under `-AgyRoot` (default `~\.gemini\antigravity-cli`) or, if that file is absent, the JSON saved by the orchestrator at `orquestacion\herramientas\agy-<n>.json` (only `conversation_id` is read). Those sources do not include a model, so the worker keeps its registered name, which already includes it. The GEM-T04 session reader (`multiagent/sessions/`) is still missing.

Antigravity metrics (GEM-T06): plan usage comes from `multiagent usage-report plans` (app `antigravity`; "no data" when unavailable). Token counts come from the task card's Tokens field (`tokens-tarea.ps1`) or, if the worker has no such field, from its run output in `orquestacion\herramientas\agy-*.json` / `agy-*.ndjson`, matched by `agy:<conversation_id>` (`input_tokens` excludes cache tokens: total = input + cached + output; one run per output name). The observed model is the ndjson `init.model`.

## Frozen stages and tasks

The `congelada` state (ALCANCE.md) is recognized on task and stage cards. A frozen task has its own badge and does not count as pending, available, or in its stage total. Frozen stages appear in a separate, collapsed **Frozen stages** section, without a summary card, items requiring your attention, or warnings.

## Work outside task cards

Work not attached to a task card (audits, proposals, research, reviews, integrations, maintenance, scheduled sessions) is recorded with `registrar-delegacion.ps1 -Actividad "<title>" -Tipo <type> [-Pedido "<user quote>"]` instead of `-Tarea`, using the same worker fields (`-Trabajador`, `-Nivel`, `-Esfuerzo`, `-Etiqueta`, `-Referencia`, `-Pieza`); close it with `-Devuelta -Resultado`. The record is stored in `<channel>\orquestacion\ACTIVIDADES\ejecuciones.jsonl` (one line per event: `opened`, `delegated`, `returned`, `closed`) and in the readable `ACTIVIDADES.md` index. Each activity has a stable ID (`ACT-AAAAMMDD-NN`).

The **Agents** tab has a **Outside task work** section at the top. It lists each active activity with its type, title, and orchestrator, and each worker with its application, model, actual activity (Working, Running test, No recent activity, Probably stopped), and token counts when available (`usage_tokens`, either a total or `{ total, input, output }`). Completed activities appear in a collapsed list. Each active worker also appears on the card for its application ("working for <orchestrator> on <activity>") and counts as active; one marked "probably stopped" is listed but does not count. If the record exists but cannot be read, the section reports that instead of showing an empty list. Activity files are read only for timestamps, status, and model names, never for their contents. The record is also monitored by the viewer (`Get-Stamp`). Tests are in `tests/SEG-T18/`.

## Waiting for activation (rule 36)

Someone waiting only for an external event (an integration window, a dependency, an approval, or a usage-limit reset) leaves their task `pendiente` with `esperando: ...` and ends the run. `esperar-condicion.ps1` (no AI model) monitors the condition and relaunches the worker. The watcher stores one state file per wait at `<channel>\orquestacion\esperas\activas\<id>.json` (moving it to `terminadas` when the wait is met or expires): `id`, `motivo`, `tarea`, `agente`, `estado` (`vigilando`, `error`, `cumplida`, `vencida`), `condiciones` (each with `tipo`, `valor`, `cumplida`, `met_at`, `avance`, `estimado`, `base`), global `avance` / `estimado` / `base`, `met_at`, `vence`, `inicio`, `ultima_comprobacion`, `proxima_comprobacion`, and `pid`.

Where a task or agent would otherwise show no activity, the viewer displays **Waiting for activation** when a live watcher exists for that task (`tarea`) or agent (`agente`), or when a `pendiente` task has `esperando: ...` under the protocol heading `## Qué falta`. It shows the reason, condition (type and value; the values of `entrada` and `archivo` are hidden), who will be reactivated, start time, last and next check, and progress. This appears on the task row (**Tasks** tab), the agent, and the card for its application (**Agents** tab; rule 33: "1 waiting for activation"). An agent working on an `en curso` task keeps its state and also gets a wait block. **Outside task work** includes a **Watchers** list: active watchers, watchers with a problem (stopped: the process no longer exists; lost: the heartbeat is more than 3 intervals old; error: the last check failed and will be retried), and completed watchers in a collapsed list. A watcher with a problem does not label a task because it cannot promise to reactivate anyone.

Progress is shown as "NN % · at HH:MM · <base>" or "No estimate": never show a percentage without its basis, never show 100% without `met_at` (when the condition was observed as met), and keep the watcher's maximum duration (`vence`) separate from progress. With multiple conditions, if any pending condition has no estimate, there is no overall estimate; otherwise, use the slowest condition. Estimates are calculated as follows: `hora` is exact (with reset confidence when it comes from a usage limit); `reporte` has no percentage until integration starts, then uses an approximate median of the last 5 rounds in `RESUMEN.md`, with the usual quartile range, capped at 95%; beyond the 90th percentile it says "taking longer than usual". `estado`, `commit`, `entrada`, `proceso`, and `archivo` depend on an agent taking action and have no estimate. Only recorded text, times, states, and numbers are shown: never error messages, action scripts, log paths, or what a condition found. While a watcher is live, the page also refreshes every minute so a watcher that stops reporting can be marked as lost. Tests are in `tests/SEG-T19/`.

## Optional core API reader

Markdown remains the viewer's canonical and default source. `-LeerApi` adds a read-only tab that fetches groups (`GET /api/v1/task-groups`), tasks (`GET /api/v1/tasks`), and events (`GET /api/v1/team-events`) from `-ApiBaseUrl` (default `http://127.0.0.1:8000`). `-ApiTimeoutSeconds` sets the per-request timeout. Example:

```powershell
pwsh -File .\tools\seguimiento\ver-seguimiento.ps1 -ChannelPath <canal>\sistema -RepoPath <repositorio> -NoAbrir -LeerApi -ApiBaseUrl http://127.0.0.1:8000
```

The tab validates required fields and checks each group's counts against the API task list. HTTP, timeout, JSON, or schema errors are shown as unknown/unavailable, never as a successful empty board. Only a valid empty set of groups, tasks, and events is labelled as API-confirmed empty. States use the core's literal names; `accepted` is not translated to `integrada` or `probada`. Tokens and costs not provided by the API remain unknown. Git evidence and integration reports continue to appear separately from the local checkout/channel. The reader only makes GET requests and does not use a private CLI or read SQLite.

Fixtures and targeted consumer tests are in `tests/ORQ-T40/`.

## Visible waits (P-615)

A task card may include the row `| Espera | <who> — <what> (since YYYY-MM-DD HH:mm, #<entry>) |` (written by the channel's `marcar-espera.ps1`; the card's `Estado` does not change). `<who>` is `usuario`, `agente <Name>`, `tarea <ID>`, `cuota (reinicia <time>)`, or `ventana de integración`. Without this row, or with `—`, the task is working. The viewer shows:

- **Task-card badge** (**Tasks** tab): **Waiting for your decision**, highlighted, when the user is awaited; **Waiting for X**, grey, for any other wait. Below it are the question, who asked it, when, and the channel entry. A `terminada` or `integrada` card with a stale row does not show a badge.
- **Waiting for your decision** panel, always visible above the tabs, with a count (also in the page title) of unresolved user waits: task, question, requester, timestamp, and entry number, oldest first. With no waits, it shows **Waiting for your decision: 0**.
- **Resolved, needs clearing**: a wait is considered resolved when a LATER channel entry (`LOG.md`, a number greater than the row's `#<entry>`) of type `decisión` or `respuesta` cites the task ID in its title or a `- tarea:` line. The viewer does not delete anything: it keeps listing the wait as resolved until the task owner runs `marcar-espera.ps1 -Quitar`; resolved waits no longer count. Without `LOG.md`, no wait is considered resolved.
- **No activity**: an `en curso` card with no `Espera` row and no live watcher (rule 36), whose latest `Historial` row is older than `-SinActividadHoras` (6 hours by default). This indicates that work may have stopped without notice; it is not a card state. While there are `en curso` cards without a wait, the page also refreshes every 15 minutes so the label appears even when no file changes.

Tests are in `tests/FLU-T06/` (synthetic task cards and `LOG.md`; the `marcar-espera.ps1` part is tested in the channel, in `validacion-esperas`).
