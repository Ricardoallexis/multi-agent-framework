# SEG-T18: activities outside tasks (audits, research, reviews, scheduled sessions...). registrar-delegacion.ps1 -Actividad writes them to
# <channel>\orquestacion\ACTIVIDADES\ejecuciones.jsonl (one line per event: opened, delegated, returned, closed). Seguimiento shows a section
# «Fuera de tareas» in the Agentes tab: each activity en curso with its type, title, orchestrator and every worker (application, model, real
# activity, tokens if recorded), the finished ones in a collapsed list. Every active worker is also listed in the card of ITS OWN application
# (REGLAS 33: «trabajando para <orquestador> en <actividad>») and counts as active there. Synthetic folders only; the content of the sessions is
# never printed (nor read: only times, status and model names).
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t18-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$subRoot = Join-Path $temp 'projects'
$subDir = Join-Path $subRoot 'proj\session\subagents'
$copilotRoot = Join-Path $temp 'copilot'
$codexRoot = Join-Path $temp 'codex'
$codexDay = Join-Path $codexRoot 'sessions\2026\10\11'
$actDir = Join-Path $channel 'orquestacion\ACTIVIDADES'
$actLog = Join-Path $actDir 'ejecuciones.jsonl'
$now = [datetime]'2026-10-11T12:00:00'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Utc([datetime]$local) { $local.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') }
function Json($value) { $value | ConvertTo-Json -Compress -Depth 8 }
function Set-Age([string]$path, [int]$minutesAgo) { (Get-Item $path).LastWriteTime = $now.AddMinutes(-$minutesAgo) }

# --- Claude subagent: <root>\<proj>\<session>\subagents\agent-<id>.jsonl, records {type, timestamp, message} ---
function New-ClaudeSubagent([string]$id, [int]$minutesAgo) {
    New-Item -ItemType Directory -Force $subDir | Out-Null
    $lines = @(
        (Json @{ type = 'user'; timestamp = (Utc $now.AddMinutes(-$minutesAgo - 5)); message = @{ role = 'user'; content = 'SECRET-CONTENT' } }),
        (Json @{ type = 'assistant'; timestamp = (Utc $now.AddMinutes(-$minutesAgo)); message = @{ role = 'assistant'; content = @(@{ type = 'text'; text = 'SECRET-CONTENT' }) } }))
    $path = Join-Path $subDir "agent-$id.jsonl"
    Set-Content $path $lines -Encoding utf8
    Set-Age $path $minutesAgo
}
# --- Copilot: ~/.copilot/session-state/<id>/events.jsonl ---
function CopRec([string]$type, [int]$minutesAgo, $data) { Json @{ type = $type; data = $data; id = [guid]::NewGuid().ToString(); timestamp = (Utc $now.AddMinutes(-$minutesAgo)); parentId = $null } }
function New-CopilotSession([string]$id, [int]$minutesAgo, [string]$model = 'gpt-5.6-luna') {
    $dir = Join-Path $copilotRoot "session-state\$id"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $lines = @(
        (CopRec 'session.start' ($minutesAgo + 40) @{ sessionId = $id; selectedModel = $model; copilotVersion = '1.0' }),
        (CopRec 'user.message' ($minutesAgo + 30) @{ content = 'SECRET-CONTENT'; messageId = 'm1' }),
        (CopRec 'assistant.message' $minutesAgo @{ messageId = 'm2'; content = 'SECRET-CONTENT'; model = $model }))
    $path = Join-Path $dir 'events.jsonl'
    Set-Content $path $lines -Encoding utf8
    Set-Age $path $minutesAgo
}
# --- Codex: ~/.codex/sessions/YYYY/MM/DD/rollout-*-<id>.jsonl ---
function CxRec([string]$type, [int]$minutesAgo, $payload) { Json @{ timestamp = (Utc $now.AddMinutes(-$minutesAgo)); type = $type; payload = $payload } }
function New-CodexThread([string]$id, [int]$minutesAgo, [string]$model = 'gpt-6-luna', [string]$TestCommand = '') {
    New-Item -ItemType Directory -Force $codexDay | Out-Null
    $lines = @(
        (CxRec 'session_meta' ($minutesAgo + 40) @{ id = $id; model_provider = 'openai'; cwd = 'C:\x' }),
        (CxRec 'response_item' ($minutesAgo + 30) @{ type = 'message'; role = 'user'; content = 'SECRET-CONTENT' }),
        (CxRec 'turn_context' ($minutesAgo + 5) @{ model = $model; effort = 'high' }))
    if ($TestCommand) { $lines += CxRec 'response_item' ($minutesAgo + 2) @{ type = 'custom_tool_call'; name = 'exec'; call_id = "call_$id"; input = $TestCommand } }
    else { $lines += CxRec 'response_item' $minutesAgo @{ type = 'message'; role = 'assistant'; content = 'SECRET-CONTENT' } }
    $path = Join-Path $codexDay "rollout-2026-10-11T10-00-00-$id.jsonl"
    Set-Content $path $lines -Encoding utf8
    Set-Age $path $minutesAgo
}

# --- Activity events, as registrar-delegacion.ps1 -Actividad writes them (recorded_at: ISO 8601 with the local offset) ---
function Stamp([string]$time) { [datetimeoffset]::new([datetime]"2026-10-11T${time}:00").ToString('o') }
$script:events = [System.Collections.Generic.List[string]]::new()
function Add-Event([hashtable]$fields) { $script:events.Add((Json $fields)) }
function Opened([string]$id, [string]$orchestrator, [string]$type, [string]$title, [string]$time, [string]$request = '') {
    Add-Event ([ordered]@{ activity = $id; event = 'opened'; orchestrator = $orchestrator; recorded_at = (Stamp $time); title = $title; type = $type; request = $(if ($request) { $request } else { $null }) })
}
function Delegated([string]$id, [string]$orchestrator, [string]$time, [string]$worker, [string]$ref, [string]$piece, [string]$level = 'T2', [string]$effort = 'medio', [string]$label = '') {
    $fields = [ordered]@{ activity = $id; event = 'delegated'; orchestrator = $orchestrator; recorded_at = (Stamp $time); worker = $worker; requested_model = $null
        requested_effort = $(if ($effort) { $effort } else { $null }); level = $(if ($level) { $level } else { $null }); reason = $piece; status = 'delegated'; artifact = $null
        effective_model = $null; usage_tokens = $null; monetary_cost = $null; fork_turns = $null; reference = $(if ($ref) { $ref } else { $null }) }
    if ($label) { $fields['label'] = $label }
    Add-Event $fields
}
function Returned([string]$id, [string]$orchestrator, [string]$time, [string]$worker, [string]$result, $tokens = $null) {
    Add-Event ([ordered]@{ activity = $id; event = 'returned'; orchestrator = $orchestrator; recorded_at = (Stamp $time); worker = $worker; reason = $result; status = 'reviewed_complete'; usage_tokens = $tokens })
}
function Closed([string]$id, [string]$orchestrator, [string]$time, [string]$result) {
    Add-Event ([ordered]@{ activity = $id; event = 'closed'; orchestrator = $orchestrator; recorded_at = (Stamp $time); reason = $result; status = 'reviewed_complete' })
}
function Save-Events { New-Item -ItemType Directory -Force $actDir | Out-Null; [IO.File]::WriteAllLines($actLog, $script:events.ToArray(), [Text.UTF8Encoding]::new($false)) }

function Write-Card([string]$code, [string]$state, [string]$owner, [string[]]$rows) {
    $lines = @("# AAA-$code · Tarea de prueba $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Depende de | - |', '| Parche | ninguno |', "| Agente | $owner |",
        '', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', "| 2026-10-11 10:00 | $owner | creada | x |") + $rows
    Set-Content (Join-Path $channel "tareas\AAA\$code-x.md") $lines -Encoding utf8
}
function Deleg([string]$orchestrator, [string]$time, [string]$worker, [string]$ref, [string]$piece) {
    "| 2026-10-11 $time | $orchestrator | delegada | $worker (T2, esfuerzo medio): $piece [ref: $ref] |"
}
function Run-Viewer {
    & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots $subRoot -CopilotRoot $copilotRoot -CodexRoot $codexRoot -NowOverride $now | Out-Null
    [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))
}
function Get-AgentsTab([string]$html) {
    $agents = $html.Substring($html.IndexOf('<div class="tab" id="agentes">'))
    $agents.Substring(0, $agents.IndexOf('cloud-assignment-group'))
}
function Get-ActivitiesSection([string]$agents) {
    $m = [regex]::Match($agents, '(?s)<section class="panel activities-group" id="fuera-de-tareas">.*?</section>')
    Assert-True $m.Success 'section «Fuera de tareas» present in the Agentes tab'
    $m.Value
}
# The block of one active activity inside the section (they hold nested divs, so split on their opening tag).
function Get-ActivityBlock([string]$section, [string]$id) {
    $parts = @($section -split '<div class="agent-task act-item">' | Select-Object -Skip 1)
    $found = @($parts | Where-Object { $_ -match "^<strong>$([regex]::Escape($id)) · " })
    Assert-True ($found.Count -eq 1) "exactly one block for $id in «Fuera de tareas» (found $($found.Count))"
    $found[0]
}
function Get-AppCard([string]$agents, [string]$application) {
    $m = [regex]::Match($agents, "(?s)<section class=""panel agent-card"">\s*<div class=""card-head""><h2>$application</h2>.*?</section>")
    Assert-True $m.Success "card of $application present"
    $m.Value
}
function Get-WorkerBlocks([string]$card) { @([regex]::Matches($card, '(?s)<div class="agent-task agent-worker">.*?</div>') | ForEach-Object Value) }
function Get-WorkerBlock([string]$card, [string]$piece) {
    $found = @(Get-WorkerBlocks $card | Where-Object { $_ -match [regex]::Escape($piece) })
    Assert-True ($found.Count -eq 1) "exactly one worker block for '$piece' in the card (found $($found.Count))"
    $found[0]
}
function Get-CardStatus([string]$card) { [regex]::Match($card, '(?s)<div class="card-head"><h2>[^<]+</h2><span class="state ([^"]+)">([^<]*)</span>') }

$cp = @{ work = 'c3333333-0000-4000-8000-000000000001'; done = 'c3333333-0000-4000-8000-000000000002'; stop = 'c3333333-0000-4000-8000-000000000003' }
$cx = @{ test = '01c30000-0000-7000-8000-000000000002' }

try {
    New-Item -ItemType Directory -Force $repo, $subRoot, $copilotRoot, $codexRoot, (Join-Path $channel 'tareas\AAA'), (Join-Path $channel 'orquestacion\AAA'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    Set-Content (Join-Path $channel 'AGENTES.md') @('| Agente | Modelo | Aplicación | Estado | Último | Cursor | Sesión |', '|---|---|---|---|---|---|---|',
        '| Claude-Opus | Opus | Claude | ACTIVO | x | c | s1 |', '| Codex | gpt | Codex | ACTIVO | x | c | s2 |', '| Copilot-Auto | auto | Copilot | ACTIVO | x | c | s3 |') -Encoding utf8
    git -C $repo init -q | Out-Null
    Set-Content (Join-Path $channel 'tareas\AAA\ETAPA.md') @('# AAA · Etapa de prueba', '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', '| Estado | abierta |', '', '## Fases de tareas', '', '| Fase | Tareas | Descripción |', '|---|---|---|', '| Fase 1 | T01, T02 | x |', '', '## Historial de decisiones', '', '- 2026-10-11 · Claude-Opus · Se abre la etapa') -Encoding utf8

    # Activity files (recent mtimes; now is 12:00).
    New-CopilotSession $cp.work 3
    New-CopilotSession $cp.done 20
    New-CopilotSession $cp.stop 90
    New-CodexThread $cx.test 20 -TestCommand 'await run("python -m pytest tests -q SECRET-CMD")'
    New-ClaudeSubagent 'same1' 2
    New-ClaudeSubagent 'cdx1' 4
    New-ClaudeSubagent 'task1' 1

    # Tasks, to prove the activities do not disturb them: Claude-Opus (T01) delegates a Claude subagent, Codex (T02) has a task of its own.
    Write-Card 'T01' 'en curso' 'Claude-Opus' @((Deleg 'Claude-Opus' '11:30' 'Claude Haiku 5.5' 'agent-task1' 'pieza de tarea'))
    Write-Card 'T02' 'en curso' 'Codex' @()

    # ACT-01 audit by Claude-Opus: a Copilot, a Codex and a Claude worker en curso, plus a Copilot worker already returned with tokens.
    Opened 'ACT-20261011-01' 'Claude-Opus' 'auditoría' 'Auditoría del framework' '11:00' 'audita el framework completo'
    Delegated 'ACT-20261011-01' 'Claude-Opus' '11:00' 'GPT worker' "copilot:$($cp.work)" 'audita uno' 'T2' 'medio' 'auditoría'
    Delegated 'ACT-20261011-01' 'Claude-Opus' '11:05' 'Codex worker' "codex:$($cx.test)" 'audita dos'
    Delegated 'ACT-20261011-01' 'Claude-Opus' '11:10' 'Claude Sonnet 5.5' 'agent-same1' 'audita tres'
    Delegated 'ACT-20261011-01' 'Claude-Opus' '11:15' 'GPT worker B' "copilot:$($cp.done)" 'audita cuatro'
    Returned 'ACT-20261011-01' 'Claude-Opus' '11:40' 'GPT worker B' 'informe listo' ([ordered]@{ total = 485; input = 310; output = 45 })
    # ACT-02 research by Codex (another application than its workers): a Claude worker and a Copilot worker without usable reference.
    Opened 'ACT-20261011-02' 'Codex' 'investigación' 'Investigación de <costos>' '11:20'
    Delegated 'ACT-20261011-02' 'Codex' '11:20' 'Claude Sonnet 5.5' 'agent-cdx1' 'investiga costos'
    Delegated 'ACT-20261011-02' 'Codex' '11:25' 'Copilot gpt-6-luna' '' 'investiga precios' 'T1' 'bajo'
    # ACT-03 scheduled session of the orchestrator itself: no workers.
    Opened 'ACT-20261011-03' 'Claude-Opus' 'programada' 'Sesión programada nocturna' '11:50'
    # ACT-04 / ACT-05 finished.
    Opened 'ACT-20261011-04' 'Claude-Opus' 'revisión' 'Revisión de parches' '09:00' 'revisa los parches'
    Delegated 'ACT-20261011-04' 'Claude-Opus' '09:00' 'Claude Sonnet 5.5' 'agent-old1' 'revisa parches'
    Returned 'ACT-20261011-04' 'Claude-Opus' '09:30' 'Claude Sonnet 5.5' 'sin hallazgos' 485000
    Closed 'ACT-20261011-04' 'Claude-Opus' '09:31' 'sin hallazgos'
    Opened 'ACT-20261011-05' 'Claude-Opus' 'mantenimiento' 'Limpieza de respaldos' '08:00'
    Closed 'ACT-20261011-05' 'Claude-Opus' '08:05' 'respaldos al día'
    # ACT-06 whose only worker is probably stopped.
    Opened 'ACT-20261011-06' 'Codex' 'mantenimiento' 'Inventario antiguo' '09:40'
    Delegated 'ACT-20261011-06' 'Codex' '09:40' 'GPT worker C' "copilot:$($cp.stop)" 'inventario'
    # Noise the viewer must skip: not JSON, unknown event, events of an activity that was never opened, a worker without name.
    $script:events.Add('esto no es json')
    Add-Event ([ordered]@{ activity = 'ACT-20261011-03'; event = 'something-else'; recorded_at = (Stamp '11:51') })
    Delegated 'ACT-20261011-99' 'Claude-Opus' '11:00' 'Fantasma' '' 'no existe'
    Save-Events

    $html = Run-Viewer
    $agents = Get-AgentsTab $html
    $section = Get-ActivitiesSection $agents
    Assert-True ($section -match '<h2>Fuera de tareas</h2><span class="state s-en-curso">4 en curso</span>') "four activities en curso in the header, got: $([regex]::Match($section, '<h2>Fuera de tareas</h2><span[^>]*>[^<]*').Value)"
    Assert-True ($agents.IndexOf('id="fuera-de-tareas"') -lt $agents.IndexOf('<div class="agent-grid">')) 'the section comes before the application cards'

    # --- ACT-01: type, title, orchestrator, request and each worker with application, model, real activity ---
    $b1 = Get-ActivityBlock $section 'ACT-20261011-01'
    Assert-True ($b1 -match '^<strong>ACT-20261011-01 · Auditoría del framework</strong> <span class="state s-idle">auditoría</span> <span class="state s-en-curso">En curso</span>') 'ACT-01 title, type and status'
    Assert-True ($b1 -match 'Orquesta: Claude-Opus · desde 11:00' -and $b1 -match 'Pedido: audita el framework completo') 'ACT-01 orchestrator, start and request'
    Assert-True ($b1 -match '<strong>Copilot · gpt-5\.6-luna \(T2, esfuerzo medio\)</strong> <span class="state s-idle">Copilot</span> <span class="state s-idle seg-tag">auditoría</span> <span class="state s-en-curso">Trabajando</span><span class="meta"> · delegado desde 11:00 · audita uno</span>') 'ACT-01 Copilot worker: model, application, tag, Trabajando'
    Assert-True ($b1 -match '<strong>Codex · gpt-6-luna \(T2, esfuerzo medio\)</strong> <span class="state s-idle">Codex</span> <span class="state s-en-curso">Ejecutando prueba · desde 11:38</span><span class="meta"> · delegado desde 11:05 · audita dos</span>') 'ACT-01 Codex worker: Ejecutando prueba'
    Assert-True ($b1 -match '<strong>Claude Sonnet 5\.5 \(T2, esfuerzo medio\)</strong> <span class="state s-idle">Claude</span> <span class="state s-en-curso">Trabajando</span><span class="meta"> · delegado desde 11:10 · audita tres</span>') 'ACT-01 Claude worker: Trabajando'
    Assert-True ($b1 -match '<strong>Copilot · gpt-5\.6-luna \(T2, esfuerzo medio\)</strong> <span class="state s-idle">Copilot</span> <span class="state s-integrada">Devuelto</span><span class="meta"> · desde 11:15 hasta 11:40 · audita cuatro · Tokens: 485 · entrada 310 · salida 45 · registrado</span><div class="agent-context">Resultado: informe listo</div>') 'ACT-01 returned worker: Devuelto, result and recorded tokens'

    # --- ACT-02 (Codex orchestrates): Claude worker en curso, Copilot worker without reference (application from its name, no activity file) ---
    $b2 = Get-ActivityBlock $section 'ACT-20261011-02'
    Assert-True ($b2 -match 'Investigación de <costos></strong>' -or $b2 -match 'Investigación de &lt;costos&gt;') 'ACT-02 title'
    Assert-True ($b2 -match 'Orquesta: Codex' -and $b2 -match '<span class="state s-idle">investigación</span>') 'ACT-02 orchestrator and type'
    Assert-True ($b2 -match '<strong>Claude Sonnet 5\.5 \(T2, esfuerzo medio\)</strong> <span class="state s-idle">Claude</span> <span class="state s-en-curso">Trabajando</span>') 'ACT-02 Claude worker'
    Assert-True ($b2 -match '<strong>Copilot gpt-6-luna \(T1, esfuerzo bajo\)</strong> <span class="state s-idle">Copilot</span> <span class="state s-idle">sin señal de actividad</span>') 'ACT-02 Copilot worker without reference'
    # --- ACT-03: no workers ---
    $b3 = Get-ActivityBlock $section 'ACT-20261011-03'
    Assert-True ($b3 -match 'Sesión programada nocturna' -and $b3 -match 'state s-idle">programada' -and $b3 -match 'Sin trabajadores delegados' -and $b3 -match '<span class="state s-en-curso">En curso</span>') 'ACT-03 scheduled session without workers'
    # --- ACT-06: its only worker is probably stopped ---
    $b6 = Get-ActivityBlock $section 'ACT-20261011-06'
    Assert-True ($b6 -match '<span class="state s-unknown">Probablemente detenida</span>' -and $b6 -match '<span class="state s-unknown">Probablemente detenido</span>') 'ACT-06 all workers stopped'
    # --- finished activities: collapsed, not in the active list ---
    Assert-True ($section -notmatch 'ACT-20261011-04 · Revisión' -and $section -notmatch 'ACT-20261011-05 · Limpieza') 'finished activities are not active blocks'
    $fin = [regex]::Match($section, '(?s)<details class="act-finished"><summary>Terminadas \(2\)</summary><ul>(.*?)</ul></details>')
    Assert-True $fin.Success 'finished activities in a collapsed list «Terminadas (2)»'
    Assert-True ($section -notmatch '<details class="act-finished" open') 'the finished list is collapsed'
    Assert-True ($fin.Groups[1].Value -match '<li><strong>ACT-20261011-04</strong> · revisión · Revisión de parches · Claude-Opus · 09:00 → 09:31 · trabajaron: Claude Sonnet 5\.5 \(T2, esfuerzo medio\) \(Tokens: 485 k\) · Resultado: sin hallazgos</li>') 'ACT-04 finished line: span, worker with tokens, result'
    Assert-True ($fin.Groups[1].Value -match '<li><strong>ACT-20261011-05</strong> · mantenimiento · Limpieza de respaldos · Claude-Opus · 08:00 → 08:05 · Resultado: respaldos al día</li>') 'ACT-05 finished line'
    Assert-True ($fin.Groups[1].Value.IndexOf('ACT-20261011-04') -lt $fin.Groups[1].Value.IndexOf('ACT-20261011-05')) 'finished list newest first'
    # The title is encoded in the page (the section above was decoded for matching).
    $rawHtml = [IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html'))
    Assert-True ($rawHtml -match 'Investigaci(ó|&#243;)n de &lt;costos&gt;' -and $rawHtml -notmatch '<costos>') 'activity titles are HTML-encoded'

    # --- Rule 33: each active worker also in the card of its own application ---
    $apps = [regex]::Matches($agents, '<section class="panel agent-card">\s*<div class="card-head"><h2>([^<]+)</h2>') | ForEach-Object { $_.Groups[1].Value }
    Assert-True ((@($apps) -join ',') -eq 'Claude,Codex,Copilot') "one card per application, got: $(@($apps) -join ',')"
    $copilot = Get-AppCard $agents 'Copilot'
    $codex = Get-AppCard $agents 'Codex'
    $claude = Get-AppCard $agents 'Claude'

    $status = Get-CardStatus $copilot
    Assert-True ($status.Groups[1].Value -eq 's-en-curso' -and $status.Groups[2].Value -eq '2 trabajadores activos · 1 trabajador probablemente detenido') "Copilot card status, got: $($status.Groups[2].Value)"
    $w1 = Get-WorkerBlock $copilot 'audita uno'
    Assert-True ($w1 -match '<strong>Copilot · gpt-5\.6-luna \(T2, esfuerzo medio\)</strong>') 'Copilot worker: app · model (level, effort)'
    Assert-True ($w1 -match 'Activo · trabajando para Claude-Opus en Auditoría del framework') 'Copilot worker: trabajando para <orquestador> en <actividad>'
    Assert-True ($w1 -match 'Fuera de tareas: ACT-20261011-01 · auditoría · Auditoría del framework' -and $w1 -match 'Actividad: Trabajando · delegado desde 11:00 · audita uno') 'Copilot worker: activity and real activity'
    $w2 = Get-WorkerBlock $copilot 'investiga precios'
    Assert-True ($w2 -match 'Activo · trabajando para Codex en Investigación de <costos>' -and $w2 -match 'Actividad: sin señal de actividad') 'Copilot worker without reference, delegated by Codex'
    $w3 = Get-WorkerBlock $copilot 'inventario'
    Assert-True ($w3 -match 'Probablemente detenido · delegado por Codex en Inventario antiguo' -and $w3 -notmatch 'trabajando para' -and $w3 -match 's-unknown') 'a stopped worker is listed, not counted as active'
    Assert-True ($copilot -notmatch 'audita cuatro') 'a returned worker is not in the card of its application'
    Assert-True ($copilot -match '<h3>Trabajadores en actividades fuera de tareas</h3>' -and $copilot -match 'sin tarea propia: Copilot-Auto</p>' -and $copilot -notmatch 'class="meta agent-idle">sin actividad:') 'FLU-T08: activity workers keep the area active; the idle agent has no own task'

    $status = Get-CardStatus $codex
    Assert-True ($status.Groups[1].Value -eq 's-en-curso' -and $status.Groups[2].Value -eq '1 con tarea activa · 1 trabajador activo') "Codex card status, got: $($status.Groups[2].Value)"
    $w4 = Get-WorkerBlock $codex 'audita dos'
    Assert-True ($w4 -match '<strong>Codex · gpt-6-luna \(T2, esfuerzo medio\)</strong>' -and $w4 -match 'Activo · trabajando para Claude-Opus en Auditoría del framework' -and $w4 -match 'Actividad: Ejecutando prueba · desde 11:38 · delegado desde 11:05') 'Codex worker: running test'
    Assert-True ($codex -match '<h3>Codex</h3>') 'the Codex agent with its task is still there'

    $status = Get-CardStatus $claude
    Assert-True ($status.Groups[1].Value -eq 's-en-curso' -and $status.Groups[2].Value -eq '1 con tarea activa · 2 trabajadores activos') "Claude card status, got: $($status.Groups[2].Value)"
    $w5 = Get-WorkerBlock $claude 'audita tres'
    Assert-True ($w5 -match '<strong>Claude Sonnet 5\.5 \(T2, esfuerzo medio\)</strong>' -and $w5 -match 'Activo · trabajando para Claude-Opus en Auditoría del framework') 'Claude worker of an activity orchestrated by Claude-Opus (same application) is in the Claude card'
    $w6 = Get-WorkerBlock $claude 'investiga costos'
    Assert-True ($w6 -match 'Activo · trabajando para Codex en Investigación de <costos>' -and $w6 -match 'Actividad: Trabajando · delegado desde 11:20') 'Claude worker delegated by Codex in an activity'
    Assert-True (@(Get-WorkerBlocks $claude).Count -eq 2) 'two activity workers in the Claude card (the delegation of the task T01 stays in its orchestrator block)'
    Assert-True ($claude -match 'Claude Haiku 5\.5 \(T2\) desde 11:30 · Trabajando · pieza de tarea') 'the task delegation of Claude-Opus is unchanged'
    Assert-True ($claude -notmatch 'revisa parches') 'a worker of a finished activity is not shown'

    # --- Nothing of the sessions, ids or locations is printed ---
    Assert-True ($html -notmatch 'SECRET-CONTENT' -and $html -notmatch 'SECRET-CMD') 'session content never printed'
    Assert-True ($html -notmatch 'session-state|rollout-|events\.jsonl') 'file locations not printed'
    $printed = ((@($claude, $codex, $copilot) | ForEach-Object { Get-WorkerBlocks $_ }) -join "`n") + $section -replace '<[^>]+>', ' '
    Assert-True ($printed -notmatch '\[ref:' -and $printed -notmatch 'agent-same1|agent-cdx1|agent-old1' -and $printed -notmatch 'copilot:|codex:') 'references not printed in the worker blocks or the section'
    Assert-True ($printed -notmatch [regex]::Escape($cp.work) -and $printed -notmatch [regex]::Escape($cx.test)) 'session ids not printed'

    # --- Only a stopped worker: the card is idle and says so; the activity is flagged ---
    $script:events.Clear()
    Opened 'ACT-20261011-06' 'Codex' 'mantenimiento' 'Inventario antiguo' '09:40'
    Delegated 'ACT-20261011-06' 'Codex' '09:40' 'GPT worker C' "copilot:$($cp.stop)" 'inventario'
    Save-Events
    $agents = Get-AgentsTab (Run-Viewer)
    $copilot = Get-AppCard $agents 'Copilot'
    $status = Get-CardStatus $copilot
    Assert-True ($status.Groups[1].Value -eq 's-idle' -and $status.Groups[2].Value -eq 'sin actividad · 1 trabajador probablemente detenido') "Copilot card with only a stopped activity worker, got: $($status.Groups[2].Value)"
    Assert-True ((Get-ActivitiesSection $agents) -match '<span class="state s-en-curso">1 en curso</span>') 'the activity is still listed as en curso'
    Assert-True ($copilot -match 'class="meta agent-idle">sin actividad: Copilot-Auto</p>') 'FLU-T08: stopped activity workers preserve the idle label'

    # --- No activities: the section says so, the cards are the ones of SEG-T17 ---
    Remove-Item -LiteralPath $actLog
    $agents = Get-AgentsTab (Run-Viewer)
    $section = Get-ActivitiesSection $agents
    Assert-True ($section -match 'sin actividades en curso' -and $section -match 'No hay actividades en curso fuera de tareas' -and $section -notmatch 'act-finished' -and $section -notmatch 'Actividades no disponibles') 'no registry: empty section, no warning'
    Assert-True ((Get-CardStatus (Get-AppCard $agents 'Copilot')).Groups[2].Value -eq 'sin actividad') 'without activities the Copilot card says sin actividad'
    Assert-True ($agents -notmatch 'agent-activity-workers') 'no activity-workers block in any card'

    # --- Unreadable registry: not presented as an empty list ---
    Save-Events
    $lock = [IO.File]::Open($actLog, 'Open', 'Read', 'None')
    try {
        $agents = Get-AgentsTab (Run-Viewer)
    } finally { $lock.Dispose() }
    $section = Get-ActivitiesSection $agents
    Assert-True ($section -match 'Actividades no disponibles' -and $section -match 'no se presenta como una lista vacía') 'unreadable registry is reported, not shown as empty'
    $agents = Get-AgentsTab (Run-Viewer)
    Assert-True ((Get-ActivitiesSection $agents) -notmatch 'Actividades no disponibles') 'readable again: warning gone'

    # The integration regenerates Seguimiento under StrictMode Latest, and activity rows carry "activity", not "task".
    $script:strictError = $null
    & {
        Set-StrictMode -Version Latest
        try { & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots $subRoot -CopilotRoot $copilotRoot -CodexRoot $codexRoot -NowOverride $now | Out-Null }
        catch { $script:strictError = $_.Exception.Message + ' @ ' + (($_.ScriptStackTrace -split "`n") | Select-Object -First 2) -join ' < ' }
    }
    Assert-True (-not $script:strictError) "viewer runs under StrictMode with activity rows: $script:strictError"
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T18 activities outside tasks PASS'
