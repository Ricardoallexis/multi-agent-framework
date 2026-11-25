# SEG-T19 (RULE 36): "Waiting for activation". `esperar-condicion.ps1` (no AI model) keeps one state file per wait in
# <channel>\orquestacion\esperas\activas\<id>.json (moved to ...\terminadas when `cumplida` or `vencida`). A task or agent that would show
# "No activity" but has a live watcher (`tarea` / `agente`), or a pending card that says `esperando: ...`, shows "Waiting for activation" with its
# reason, condition, target, start time, last and next check, and honest progress ("NN % · by HH:MM · <basis>" or "No estimate").
# The "Outside task work" section lists watchers (active, with a problem: stopped / lost / error, and finished ones collapsed).
# Synthetic state files only (written here, as the watcher writes them); a percentage never appears without its basis, 100 % never without `met_at`,
# and neither the action script, log path, error message, nor matched condition content is printed.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t19-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$subRoot = Join-Path $temp 'projects'
$copilotRoot = Join-Path $temp 'copilot'
$codexRoot = Join-Path $temp 'codex'
$activeDir = Join-Path $channel 'orquestacion\esperas\activas'
$doneDir = Join-Path $channel 'orquestacion\esperas\terminadas'
$now = [datetime]'2026-10-11T12:00:00'
$label = 'Waiting for activation'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Json($value) { $value | ConvertTo-Json -Depth 8 }

# --- Channel: three applications, tasks of a stage AAA ---
function Write-Card([string]$code, [string]$state, [string]$owner, [string]$missing = '') {
    $lines = @("# AAA-$code · Tarea de prueba $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Depende de | - |', '| Parche | ninguno |', "| Agente | $owner |")
    if ($missing) { $lines += @('', '## Qué falta', '', $missing) }
    $lines += @('', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', "| 2026-10-11 10:00 | $owner | creada | x |")
    Set-Content (Join-Path $channel "tareas\AAA\$code-x.md") $lines -Encoding utf8
}
# --- State files, as esperar-condicion.ps1 writes them ---
function Cond([string]$type, [string]$value, [bool]$met = $false, $progress = $null, $estimated = $null, [string]$base = 'No estimate (depends on an agent taking action)', $metAt = $null) {
    [ordered]@{ tipo = $type; valor = $value; cumplida = $met; met_at = $metAt; avance = $progress; estimado = $estimated; base = $base; confianza = $null }
}
function New-Wait([string]$id, [hashtable]$fields = @{}, [string]$folder = 'activas') {
    $wait = [ordered]@{ id = $id; motivo = "motivo de $id"; tarea = ''; agente = ''; estado = 'vigilando'; condiciones = @(); avance = $null; estimado = $null
        base = 'No estimate (depends on an agent taking action)'; confianza = $null; met_at = $null; vence = '2026-10-11T23:30:00'; error = $null
        inicio = '2026-10-11T11:30:00'; ultima_comprobacion = '2026-10-11T11:58:00'; proxima_comprobacion = '2026-10-11T12:00:00'; pid = $PID
        registro = 'C:\secret-dir\registro-secreto.log'; accion = 'C:\secret-dir\accion-secreta.ps1' }
    foreach ($key in $fields.Keys) { $wait[$key] = $fields[$key] }
    $dir = Join-Path $channel "orquestacion\esperas\$folder"
    New-Item -ItemType Directory -Force $dir | Out-Null
    [IO.File]::WriteAllText((Join-Path $dir "$id.json"), (Json $wait), [Text.UTF8Encoding]::new($false))
}
function Reset-Waits { Remove-Item -LiteralPath (Join-Path $channel 'orquestacion\esperas') -Recurse -Force -ErrorAction SilentlyContinue }
function Run-Viewer {
    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots $subRoot -CopilotRoot $copilotRoot -CodexRoot $codexRoot -NowOverride $now | Out-Null
    [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))
}
function Get-AgentsTab([string]$html) {
    $agents = $html.Substring($html.IndexOf('<div class="tab" id="agentes">'))
    $agents.Substring(0, $agents.IndexOf('cloud-assignment-group'))
}
# The state cell (second cell) of the row of a task in the Tareas tab.
function Get-StateCell([string]$html, [string]$code) {
    $row = [regex]::Match($html, "(?s)<tr><td><a href=""[^""]*"">AAA-$code ·.*?</tr>")
    Assert-True $row.Success "row of $code in the Tareas tab"
    $cell = [regex]::Match($row.Value, '(?s)</td><td>(.*?)</td><td>')
    Assert-True $cell.Success "state cell of $code"
    $cell.Groups[1].Value
}
function Get-AppCard([string]$agents, [string]$application) {
    $m = [regex]::Match($agents, "(?s)<section class=""panel agent-card"">\s*<div class=""card-head""><h2>$application</h2>.*?</section>")
    Assert-True $m.Success "card of $application present"
    $m.Value
}
function Get-CardStatus([string]$card) { [regex]::Match($card, '(?s)<div class="card-head"><h2>[^<]+</h2><span class="state ([^"]+)">([^<]*)</span>') }
# The block of one agent inside its application card, up to the next agent.
function Get-AgentBlock([string]$card, [string]$name) {
    $m = [regex]::Match($card, "(?s)<div class=""agent-name"">\s*<div class=""card-head""><h3>$([regex]::Escape($name))</h3><span class=""state ([^""]+)"">([^<]*)</span>(.*?)(?=<div class=""agent-name""|</section>|\z)")
    Assert-True $m.Success "block of $name in its card"
    $m
}
function Get-Waiters([string]$agents) {
    $m = [regex]::Match($agents, '(?s)<div class="wait-group" id="vigilantes">.*?</section>')
    Assert-True $m.Success 'the Watchers list is inside Outside task work'
    $m.Value
}
function Get-WaitItem([string]$waiters, [string]$id) {
    $parts = @($waiters -split '<div class="agent-task wait-item">' | Select-Object -Skip 1)
    $found = @($parts | Where-Object { $_ -match "^<strong>$([regex]::Escape($id))" })
    Assert-True ($found.Count -eq 1) "exactly one item for $id in Watchers (found $($found.Count))"
    $found[0]
}

try {
    New-Item -ItemType Directory -Force $repo, $subRoot, $copilotRoot, $codexRoot, (Join-Path $channel 'tareas\AAA'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    Set-Content (Join-Path $channel 'AGENTES.md') @('| Agente | Modelo | Aplicación | Estado | Último | Cursor | Sesión |', '|---|---|---|---|---|---|---|',
        '| Claude-Opus | Opus | Claude | ACTIVO | x | c | s1 |', '| Claude-Sonnet | Sonnet | Claude | ACTIVO | x | c | s4 |', '| Codex | gpt | Codex | ACTIVO | x | c | s2 |', '| Copilot-Auto | auto | Copilot | ACTIVO | x | c | s3 |') -Encoding utf8
    git -C $repo init -q | Out-Null
    Set-Content (Join-Path $channel 'tareas\AAA\ETAPA.md') @('# AAA · Etapa de prueba', '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', '| Estado | abierta |', '', '## Historial de decisiones', '', '- 2026-10-11 · Claude-Opus · Se abre la etapa') -Encoding utf8

    Write-Card 'T01' 'en curso' 'Claude-Opus'
    Write-Card 'T02' 'pendiente' 'Codex' 'esperando: la ventana de integración (#548) con el commit 30cf061'
    Write-Card 'T03' 'pendiente' 'Copilot-Auto' 'esperando: el reinicio del límite diario'
    Write-Card 'T04' 'terminada' 'Claude-Opus'
    Write-Card 'T05' 'disponible' 'Claude-Opus'
    Write-Card 'T06' 'en curso' 'Claude-Opus'
    Write-Card 'T07' 'disponible' 'Claude-Opus'
    Write-Card 'T08' 'pendiente' 'Claude-Opus' 'falta revisar el diseño con el usuario'

    # --- Without any watcher: the tracker is the one of before ---
    $html = Run-Viewer
    $agents = Get-AgentsTab $html
    Assert-True ($agents -notmatch 'id="vigilantes"') 'no wait folder: no Watchers list'
    # A pending card that says `esperando: ...` already shows Waiting for activation, with no watcher registered.
    $cell = Get-StateCell $html 'T03'
    Assert-True ($cell -match '^<span class="state s-espera">Waiting for activation</span>') 'T03: a pending card with `esperando:` shows Waiting for activation'
    Assert-True ($cell -match 'Reason: el reinicio del límite diario' -and $cell -match 'Reactivates: Copilot-Auto' -and $cell -match 'Progress: No estimate · No active watcher is registered' -and $cell -match 'Card status: Pending') 'T03: card reason, agent, no estimate, no watcher'
    Assert-True ($cell -notmatch 'Condition:' -and $cell -notmatch 'since ' -and $cell -notmatch 'next check') 'T03: no invented condition or times'
    Assert-True ((Get-StateCell $html 'T08') -notmatch $label) 'T08: a pending card without `esperando:` does not show Waiting for activation'
    Assert-True ((Get-StateCell $html 'T01') -notmatch $label -and (Get-StateCell $html 'T05') -notmatch $label) 'In progress / Available tasks: no label'
    $card = Get-AppCard $agents 'Copilot'
    $status = Get-CardStatus $card
    Assert-True ($status.Groups[1].Value -eq 's-espera' -and $status.Groups[2].Value -eq '1 waiting for activation') "Copilot card: only the card waits, got: $($status.Groups[2].Value)"
    Assert-True ((Get-AgentBlock $card 'Copilot-Auto').Groups[2].Value -eq $label) 'the agent shows Waiting for activation'

    # --- The watchers ---
    $dead = Start-Process -FilePath (Get-Command pwsh).Source -ArgumentList '-NoProfile', '-Command', 'exit 0' -PassThru -Wait -WindowStyle Hidden
    # W1: Codex waits for a process AND an integration round: one condition without an estimate dominates, so there is no overall estimate.
    New-Wait 'ESP-W1' @{ motivo = 'relanzar a Codex con la ventana de integración'; tarea = 'AAA-T02'; agente = 'Codex'
        condiciones = @((Cond 'proceso' '63128'), (Cond 'reporte' 'ok' $false 55 '2026-10-11T12:09:00' 'Approximate: round started at 11:54; median of the last 5 rounds is 14 min (usual range: 12–16 min)')) }
    # W2: a waiting agent with no task, waiting for an exact time. Another day for the estimate of its second condition is covered below.
    New-Wait 'ESP-W2' @{ motivo = 'reintentar con Claude-Sonnet tras el reinicio'; agente = 'Claude-Sonnet'; avance = 60; estimado = '2026-10-11T13:00:00'; base = 'Exact: the specified time is 13:00'
        condiciones = @((Cond 'hora' '2026-10-11T13:00:00' $false 60 '2026-10-11T13:00:00' 'Exact: the specified time is 13:00')); inicio = '2026-10-11T11:00:00' }
    # W3: a completed task whose integration takes longer than usual; its agent is working on another task (en curso).
    New-Wait 'ESP-W3' @{ motivo = 'relanzar a Claude-Opus al terminar la integración'; tarea = 'AAA-T04'; agente = 'Claude-Opus'; avance = 95; base = 'Approximate: round started at 11:20; taking longer than usual (past the 90th percentile, 17 min): progress held at 95 % until a report is available'
        condiciones = @((Cond 'reporte' 'ok' $false 95 $null 'Approximate: round started at 11:20; taking longer than usual (past the 90th percentile, 17 min): progress held at 95 % until a report is available')) }
    # Watchers that promise nothing: lost (heartbeat older than 3 intervals), stopped (process gone), or in error (message never shown).
    New-Wait 'ESP-W4' @{ motivo = 'espera perdida'; tarea = 'AAA-T06'; agente = 'Claude-Opus'; ultima_comprobacion = '2026-10-11T11:40:00'; proxima_comprobacion = '2026-10-11T11:42:00'; condiciones = @((Cond 'proceso' '1')) }
    New-Wait 'ESP-W5' @{ motivo = 'espera detenida'; tarea = 'AAA-T07'; agente = 'Claude-Opus'; pid = $dead.Id; ultima_comprobacion = '2026-10-11T11:59:00'; proxima_comprobacion = '2026-10-11T12:01:00'; condiciones = @((Cond 'proceso' '1')) }
    New-Wait 'ESP-W6' @{ motivo = 'espera con error'; tarea = 'AAA-T08'; agente = 'Claude-Opus'; estado = 'error'; error = 'SECRET-ERROR-MESSAGE en C:\secret-dir'; ultima_comprobacion = '2026-10-11T11:59:00'; proxima_comprobacion = '2026-10-11T12:01:00'; condiciones = @((Cond 'estado' 'ZZZ-T01=terminada')) }
    # Finished waits (collapsed in the list).
    New-Wait 'ESP-D1' @{ motivo = 'relanzar a Codex'; tarea = 'AAA-T02'; agente = 'Codex'; estado = 'cumplida'; avance = 100; met_at = '2026-10-11T09:41:00'; base = 'Conditions met'; inicio = '2026-10-11T09:00:00'; ultima_comprobacion = '2026-10-11T09:41:00' } 'terminadas'
    New-Wait 'ESP-D2' @{ motivo = 'espera vencida'; agente = 'Claude-Sonnet'; estado = 'vencida'; base = 'Expired: the maximum wait time elapsed before the conditions were met'; inicio = '2026-10-10T08:00:00'; ultima_comprobacion = '2026-10-10T20:00:00' } 'terminadas'
    # Noise the viewer must skip: not JSON, JSON that is not an object.
    Set-Content (Join-Path $activeDir 'basura.json') 'esto no es json' -Encoding utf8
    Set-Content (Join-Path $activeDir 'lista.json') '[1, 2, 3]' -Encoding utf8

    $html = Run-Viewer
    $agents = Get-AgentsTab $html

    # --- Tasks tab: label and task details ---
    $cell = Get-StateCell $html 'T02'
    Assert-True ($cell -match '^<span class="state s-espera">Waiting for activation</span>') 'T02: label on the task'
    Assert-True ($cell -match '<div class="meta">Reason: relanzar a Codex con la ventana de integración</div>') 'T02: reason'
    Assert-True ($cell -match '<div class="meta">Condition: Process 63128 \(pending\); Report ok \(pending\)</div>') 'T02: conditions'
    Assert-True ($cell -match '<div class="meta">Reactivates: Codex · since 11:30 · last checked 11:58 · next check 12:00</div>') 'T02: target and check times'
    Assert-True ($cell -match '<div class="meta">Progress: No estimate \(depends on an agent taking action\)</div>') 'T02: an unknown condition dominates, so progress has no estimate'
    Assert-True ($cell -match '<div class="meta">Maximum wait time: until 23:30 \(not progress\)</div>') 'T02: the time limit is shown separately from progress'
    Assert-True ($cell -match 'Card status: Pending' -and $cell -notmatch 'No active watcher is registered') 'T02: card state is preserved; the watcher takes precedence over the card'
    $cell = Get-StateCell $html 'T04'
    Assert-True ($cell -match '^<span class="state s-espera">Waiting for activation</span>' -and $cell -match 'Reactivates: Claude-Opus') 'T04 (completed): a live watcher for the task labels it too'
    Assert-True ($cell -match '<div class="meta">Progress: 95 % · no estimated time · Approximate: round started at 11:20; taking longer than usual') 'T04: progress with its overdue explanation'
    Assert-True ((Get-StateCell $html 'T01') -notmatch $label -and (Get-StateCell $html 'T05') -notmatch $label) 'tasks without a wait have no label'
    foreach ($code in 'T06', 'T07', 'T08') { Assert-True ((Get-StateCell $html $code) -notmatch $label) "$code`: a lost, stopped, or failing watcher does not label the task" }

    # --- Agentes tab: the agent and its application card (rule 33) ---
    $codex = Get-AppCard $agents 'Codex'
    $status = Get-CardStatus $codex
    Assert-True ($status.Groups[1].Value -eq 's-espera' -and $status.Groups[2].Value -eq '1 waiting for activation') "Codex card status, got: $($status.Groups[2].Value)"
    $block = Get-AgentBlock $codex 'Codex'
    Assert-True ($block.Groups[1].Value -eq 's-espera' -and $block.Groups[2].Value -eq $label) 'Codex agent chip shows Waiting for activation'
    Assert-True ($block.Groups[3].Value -match '<div class="agent-task"><strong>AAA-T02 · [^<]*</strong><span class="state s-pendiente">Assigned · Pending</span><span class="state s-espera">Waiting for activation</span>') 'Codex task block carries the label'
    Assert-True ($block.Groups[3].Value -match '<div class="agent-current agent-waiting"><h4>Waiting for activation</h4>.*Reactivates: Codex · since 11:30') 'Codex: waiting block with details'
    Assert-True ($codex -cnotmatch 'no activity') 'the Codex card no longer says no activity'

    $claude = Get-AppCard $agents 'Claude'
    $status = Get-CardStatus $claude
    Assert-True ($status.Groups[1].Value -eq 's-en-curso' -and $status.Groups[2].Value -eq '1 with active tasks · 1 waiting for activation') "Claude card status, got: $($status.Groups[2].Value)"
    $opus = Get-AgentBlock $claude 'Claude-Opus'
    Assert-True ($opus.Groups[2].Value -eq 'Assigned · In progress') 'an agent working on an In progress task keeps its status'
    Assert-True ($opus.Groups[3].Value -match '<div class="agent-current agent-waiting"><h4>Waiting for activation</h4>' -and $opus.Groups[3].Value -match 'Reason: relanzar a Claude-Opus al terminar la integración') 'the waiting block is present'
    $sonnet = Get-AgentBlock $claude 'Claude-Sonnet'
    Assert-True ($sonnet.Groups[1].Value -eq 's-espera' -and $sonnet.Groups[2].Value -eq $label) 'an agent with no task but a watcher (`agente`) shows Waiting for activation'
    Assert-True ($sonnet.Groups[3].Value -match '<div class="meta">Progress: 60 % · by 13:00 · Exact: the specified time is 13:00</div>') 'Claude-Sonnet: 60% progress, estimated time, and basis'
    Assert-True ($claude -notmatch 'no activity:') 'a waiting agent is not listed among inactive agents'
    $copilot = Get-AppCard $agents 'Copilot'
    Assert-True ((Get-CardStatus $copilot).Groups[2].Value -eq '1 waiting for activation') 'Copilot card: pending card with `esperando:` (no watcher)'

    # --- Watchers in Outside task work ---
    $waiters = Get-Waiters $agents
    Assert-True ($agents.IndexOf('id="fuera-de-tareas"') -lt $agents.IndexOf('id="vigilantes"') -and $agents.IndexOf('id="vigilantes"') -lt $agents.IndexOf('<div class="agent-grid">')) 'Watchers appear inside Outside task work, before the application cards'
    Assert-True ($waiters -match '<h3>Watchers</h3><span class="state s-espera">3 active · 3 with problems</span>') "list header, got: $([regex]::Match($waiters, '<h3>Watchers</h3><span[^>]*>[^<]*').Value)"
    $w1 = Get-WaitItem $waiters 'ESP-W1'
    Assert-True ($w1 -match '^<strong>ESP-W1 · AAA-T02</strong> <span class="state s-espera">Waiting for activation</span>') 'W1 item: id, task, label'
    Assert-True ($w1 -match '<li>Process 63128 \(pending\): No estimate \(depends on an agent taking action\)</li>') 'W1: condition without an estimate'
    Assert-True ($w1 -match '<li>Report ok \(pending\): 55 % · by 12:09 · Approximate: round started at 11:54; median of the last 5 rounds is 14 min \(usual range: 12–16 min\)</li>') 'W1: condition with its own estimate and basis'
    $w2 = Get-WaitItem $waiters 'ESP-W2'
    Assert-True ($w2 -match '^<strong>ESP-W2</strong>' -and $w2 -match 'Reactivates: Claude-Sonnet · since 11:00' -and $w2 -notmatch '<ul class="wait-conditions">') 'W2 item: no task, one condition (no per-condition list)'
    Assert-True ((Get-WaitItem $waiters 'ESP-W3') -match 'Progress: 95 % · no estimated time') 'W3 item'
    Assert-True ((Get-WaitItem $waiters 'ESP-W4') -match '<span class="state s-unknown">Watcher lost</span>' -and (Get-WaitItem $waiters 'ESP-W4') -match 'No heartbeat: last checked at 11:40, more than 3 intervals ago') 'W4: lost watcher'
    Assert-True ((Get-WaitItem $waiters 'ESP-W5') -match '<span class="state s-unknown">Watcher stopped</span>' -and (Get-WaitItem $waiters 'ESP-W5') -match 'The watcher process no longer exists; this wait will not reactivate automatically') 'W5: stopped watcher'
    Assert-True ((Get-WaitItem $waiters 'ESP-W6') -match '<span class="state s-requiere-cambios">Watcher error</span>' -and (Get-WaitItem $waiters 'ESP-W6') -match 'retrying at the next check \(12:01\)') 'W6: watcher error'
    $fin = [regex]::Match($waiters, '(?s)<details class="wait-finished"><summary>Completed \(2\)</summary><ul>(.*?)</ul></details>')
    Assert-True $fin.Success 'finished watchers in a collapsed list "Completed (2)"'
    Assert-True ($waiters -notmatch '<details class="wait-finished" open') 'the finished list is collapsed'
    Assert-True ($fin.Groups[1].Value -match '<li><strong>ESP-D1</strong> · Met · task AAA-T02 · reactivated Codex · 09:00 → 09:41 · relanzar a Codex</li>') 'finished: met line'
    Assert-True ($fin.Groups[1].Value -match '<li><strong>ESP-D2</strong> · Expired · reactivated Claude-Sonnet · 2026-10-10 08:00 → 2026-10-10 20:00 · espera vencida</li>') 'finished: expired line, older times include the date'
    Assert-True ($fin.Groups[1].Value.IndexOf('ESP-D1') -lt $fin.Groups[1].Value.IndexOf('ESP-D2')) 'finished list newest first'

    # --- Only registered text, times, statuses, and numbers ---
    Assert-True ($html -notmatch 'SECRET-ERROR-MESSAGE' -and $html -notmatch 'secret-dir' -and $html -notmatch 'registro-secreto' -and $html -notmatch 'accion-secreta') 'neither the error message, the log path nor the action script are printed'

    # --- Edge cases ---
    # A 100% estimate is trusted only with `met_at`; a percentage is never shown without its basis.
    Reset-Waits
    New-Wait 'ESP-E1' @{ tarea = 'AAA-T02'; agente = 'Codex'; avance = 100; estimado = '2026-10-11T12:00:00'; base = 'Conditions met'
        condiciones = @((Cond 'proceso' '1' $true 100 '2026-10-11T11:50:00' 'Met'), (Cond 'hora' '2026-10-11T13:00:00' $false 100 '2026-10-11T13:00:00' 'Exact: the specified time is 13:00')) }
    $html = Run-Viewer
    $cell = Get-StateCell $html 'T02'
    Assert-True ($cell -match 'Progress: No estimate · Conditions met' -and $cell -notmatch '100 %') '100% without met_at is not shown'
    $w = Get-WaitItem (Get-Waiters (Get-AgentsTab $html)) 'ESP-E1'
    Assert-True ($w -match '<li>Process 1 \(met\): No estimate · Met</li>' -and $w -notmatch '100 %') '100% without met_at on a condition is not shown either'
    Reset-Waits
    New-Wait 'ESP-E2' @{ tarea = 'AAA-T02'; agente = 'Codex'; avance = 100; met_at = '2026-10-11T11:55:00'; estimado = '2026-10-11T11:55:00'; base = 'x'
        condiciones = @((Cond 'proceso' '1' $true 100 '2026-10-11T11:50:00' 'Met' '2026-10-11T11:50:00'), (Cond 'hora' '2026-10-11T13:00:00' $false 60 '2026-10-11T13:00:00' 'Exact: the specified time is 13:00')) }
    $html = Run-Viewer
    $w = Get-WaitItem (Get-Waiters (Get-AgentsTab $html)) 'ESP-E2'
    Assert-True ($w -match '<li>Process 1 \(met\): 100 % · by 11:50 · Met</li>') '100% with met_at is shown with its basis'
    Assert-True ((Get-StateCell $html 'T02') -match 'Progress: 100 % · by 11:55 · x') '100% overall with met_at'
    Reset-Waits
    New-Wait 'ESP-E3' @{ tarea = 'AAA-T02'; agente = 'Codex'; avance = 60; estimado = '2026-10-12T08:00:00'; base = ''
        condiciones = @((Cond 'entrada' 'SECRET-MATCH' $false $null $null 'No estimate (depends on an agent taking action)'), (Cond 'archivo' 'C:\secret-dir\file.txt' $false)) }
    $html = Run-Viewer
    $cell = Get-StateCell $html 'T02'
    Assert-True ($cell -match 'Progress: No estimate</div>' -and $cell -notmatch '60 %') 'a percentage without a basis is not shown'
    Assert-True ($cell -match 'Condition: Text \(pending\); File \(pending\)' -and $html -notmatch 'SECRET-MATCH' -and $html -notmatch 'secret-dir') 'the value of a Text / File condition is not shown'
    # An estimate on another day includes its date.
    Reset-Waits
    New-Wait 'ESP-E4' @{ tarea = 'AAA-T02'; agente = 'Codex'; avance = 30; estimado = '2026-10-12T08:00:00'; base = 'Exact: the specified time is 08:00' }
    $cell = Get-StateCell (Run-Viewer) 'T02'
    Assert-True ($cell -match 'Progress: 30 % · by 2026-10-12 08:00 · Exact: the specified time is 08:00') 'an estimate on another day includes the date and time'
    # A percentage outside the valid range is ignored.
    Reset-Waits
    New-Wait 'ESP-E5' @{ tarea = 'AAA-T02'; agente = 'Codex'; avance = 140; estimado = '2026-10-11T12:30:00'; base = 'x' }
    Assert-True ((Get-StateCell (Run-Viewer) 'T02') -match 'Progress: No estimate · x') 'a percentage above 100 is ignored'

    # Heartbeat: exactly 3 intervals is still alive, older is lost; without an interval it cannot be judged.
    Reset-Waits
    New-Wait 'ESP-H1' @{ tarea = 'AAA-T02'; agente = 'Codex'; ultima_comprobacion = '2026-10-11T11:54:00'; proxima_comprobacion = '2026-10-11T11:56:00' }
    Assert-True ((Get-StateCell (Run-Viewer) 'T02') -match '^<span class="state s-espera">') '3 intervals old: still waiting'
    Reset-Waits
    New-Wait 'ESP-H1' @{ tarea = 'AAA-T02'; agente = 'Codex'; ultima_comprobacion = '2026-10-11T11:53:59'; proxima_comprobacion = '2026-10-11T11:55:59' }
    $html = Run-Viewer
    Assert-True ((Get-StateCell $html 'T02') -match '^<span class="state s-espera">' -and (Get-StateCell $html 'T02') -match 'No active watcher is registered') 'older than 3 intervals: the watcher is lost, the task shows only what its own card says'
    Assert-True ((Get-WaitItem (Get-Waiters (Get-AgentsTab $html)) 'ESP-H1') -match 'Watcher lost') 'older than 3 intervals: watcher lost'
    Reset-Waits
    New-Wait 'ESP-H2' @{ tarea = 'AAA-T04'; agente = 'Claude-Opus'; ultima_comprobacion = '2026-10-11T10:00:00'; proxima_comprobacion = '2026-10-11T10:00:00' }
    Assert-True ((Get-StateCell (Run-Viewer) 'T04') -match '^<span class="state s-espera">') 'no interval to compare: not called lost'

    # Watcher stopped, but the card still says `esperando:`.
    Reset-Waits
    New-Wait 'ESP-H3' @{ tarea = 'AAA-T02'; agente = 'Codex'; pid = $dead.Id }
    $cell = Get-StateCell (Run-Viewer) 'T02'
    Assert-True ($cell -match 'No active watcher is registered') 'a stopped watcher does not count; the card''s own `esperando:` still applies'

    # No waits: back to the previous tracker state (T18 cards).
    Reset-Waits
    $html = Run-Viewer
    $agents = Get-AgentsTab $html
    Assert-True ($agents -notmatch 'id="vigilantes"') 'no watchers: no list'
    Assert-True ((Get-CardStatus (Get-AppCard $agents 'Claude')).Groups[2].Value -eq '1 with active tasks') 'no watchers: Claude card is unchanged'
    # Only finished watchers: the list is collapsed and says none is active.
    New-Wait 'ESP-D3' @{ estado = 'cumplida'; avance = 100; met_at = '2026-10-11T09:41:00'; agente = 'Codex' } 'terminadas'
    $waiters = Get-Waiters (Get-AgentsTab (Run-Viewer))
    Assert-True ($waiters -match 'none active' -and $waiters -match 'No active watchers' -and $waiters -match 'Completed \(1\)') 'only finished watchers: none active, one finished'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T19 Waiting for activation PASS'
