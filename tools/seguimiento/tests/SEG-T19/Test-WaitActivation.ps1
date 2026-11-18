# SEG-T19 (REGLAS 36): «En espera de activación». esperar-condicion.ps1 (no AI model) keeps one state file per wait in
# <channel>\orquestacion\esperas\activas\<id>.json (moved to ...\terminadas when cumplida or vencida). A task or an agent that would read «sin actividad»
# but has a live watcher (tarea / agente), or a pendiente card that says «esperando: ...», reads «En espera de activación» with the motivo, the
# condition, who it reactivates, since when, the last and next check and the honest progress («NN % · hacia las HH:MM · <base>» or «sin estimación»).
# The «Fuera de tareas» section lists the watchers (live, with a problem: detenido / perdido / con error, and the finished ones collapsed).
# Synthetic state files only (written here, as the watcher writes them); a percentage never shows without its base, 100 % never without met_at, and
# neither the action script, the log path, the message of an error nor what a condition matched are printed.
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
$label = 'En espera de activación'

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
function Cond([string]$type, [string]$value, [bool]$met = $false, $progress = $null, $estimated = $null, [string]$base = 'sin estimación (depende de que un agente actúe)', $metAt = $null) {
    [ordered]@{ tipo = $type; valor = $value; cumplida = $met; met_at = $metAt; avance = $progress; estimado = $estimated; base = $base; confianza = $null }
}
function New-Wait([string]$id, [hashtable]$fields = @{}, [string]$folder = 'activas') {
    $wait = [ordered]@{ id = $id; motivo = "motivo de $id"; tarea = ''; agente = ''; estado = 'vigilando'; condiciones = @(); avance = $null; estimado = $null
        base = 'sin estimación (depende de que un agente actúe)'; confianza = $null; met_at = $null; vence = '2026-10-11T23:30:00'; error = $null
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
    Assert-True $m.Success 'the «Vigilantes» list is inside «Fuera de tareas»'
    $m.Value
}
function Get-WaitItem([string]$waiters, [string]$id) {
    $parts = @($waiters -split '<div class="agent-task wait-item">' | Select-Object -Skip 1)
    $found = @($parts | Where-Object { $_ -match "^<strong>$([regex]::Escape($id))" })
    Assert-True ($found.Count -eq 1) "exactly one item for $id in «Vigilantes» (found $($found.Count))"
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
    Assert-True ($agents -notmatch 'id="vigilantes"') 'no esperas folder: no «Vigilantes» list'
    # A pendiente card that says «esperando: ...» is already «En espera de activación», with no watcher registered.
    $cell = Get-StateCell $html 'T03'
    Assert-True ($cell -match '^<span class="state s-espera">En espera de activación</span>') 'T03: the pendiente card with «esperando:» reads En espera de activación'
    Assert-True ($cell -match 'Motivo: el reinicio del límite diario' -and $cell -match 'Reactiva a: Copilot-Auto' -and $cell -match 'Avance: sin estimación · sin vigilante activo registrado' -and $cell -match 'Estado en la ficha: pendiente') 'T03: motivo from the card, agent, sin estimación, sin vigilante'
    Assert-True ($cell -notmatch 'Condición:' -and $cell -notmatch 'desde ' -and $cell -notmatch 'próxima revisión') 'T03: nothing invented (no condition, no times)'
    Assert-True ((Get-StateCell $html 'T08') -notmatch $label) 'T08: a pendiente card without «esperando:» is not En espera de activación'
    Assert-True ((Get-StateCell $html 'T01') -notmatch $label -and (Get-StateCell $html 'T05') -notmatch $label) 'tasks en curso / disponible: no label'
    $card = Get-AppCard $agents 'Copilot'
    $status = Get-CardStatus $card
    Assert-True ($status.Groups[1].Value -eq 's-espera' -and $status.Groups[2].Value -eq '1 en espera de activación') "Copilot card: only the card waits, got: $($status.Groups[2].Value)"
    Assert-True ((Get-AgentBlock $card 'Copilot-Auto').Groups[2].Value -eq $label) 'the agent reads En espera de activación'

    # --- The watchers ---
    $dead = Start-Process -FilePath (Get-Command pwsh).Source -ArgumentList '-NoProfile', '-Command', 'exit 0' -PassThru -Wait -WindowStyle Hidden
    # W1: Codex waits for a process AND an integration round: one condition without estimate dominates, so no overall estimate.
    New-Wait 'ESP-W1' @{ motivo = 'relanzar a Codex con la ventana de integración'; tarea = 'AAA-T02'; agente = 'Codex'
        condiciones = @((Cond 'proceso' '63128'), (Cond 'reporte' 'ok' $false 55 '2026-10-11T12:09:00' 'aproximada: ronda iniciada a las 11:54; mediana de las últimas 5 rondas 14 min (lo habitual: entre 12 y 16 min)')) }
    # W2: a waiting agent with no task, waiting for a time (exact). Another day for the estimate of its second condition is covered below.
    New-Wait 'ESP-W2' @{ motivo = 'reintentar con Claude-Sonnet tras el reinicio'; agente = 'Claude-Sonnet'; avance = 60; estimado = '2026-10-11T13:00:00'; base = 'exacta: la hora indicada (13:00)'
        condiciones = @((Cond 'hora' '2026-10-11T13:00:00' $false 60 '2026-10-11T13:00:00' 'exacta: la hora indicada (13:00)')); inicio = '2026-10-11T11:00:00' }
    # W3: a delivered task whose integration takes longer than usual; its agent is working on another task (en curso).
    New-Wait 'ESP-W3' @{ motivo = 'relanzar a Claude-Opus al terminar la integración'; tarea = 'AAA-T04'; agente = 'Claude-Opus'; avance = 95; base = 'aproximada: ronda iniciada a las 11:20; tarda más de lo habitual (pasó el percentil 90, 17 min): avance detenido en 95 % hasta que haya reporte'
        condiciones = @((Cond 'reporte' 'ok' $false 95 $null 'aproximada: ronda iniciada a las 11:20; tarda más de lo habitual (pasó el percentil 90, 17 min): avance detenido en 95 % hasta que haya reporte')) }
    # Watchers that promise nothing: lost (heartbeat older than 3 intervals), stopped (process gone), with an error (message never shown).
    New-Wait 'ESP-W4' @{ motivo = 'espera perdida'; tarea = 'AAA-T06'; agente = 'Claude-Opus'; ultima_comprobacion = '2026-10-11T11:40:00'; proxima_comprobacion = '2026-10-11T11:42:00'; condiciones = @((Cond 'proceso' '1')) }
    New-Wait 'ESP-W5' @{ motivo = 'espera detenida'; tarea = 'AAA-T07'; agente = 'Claude-Opus'; pid = $dead.Id; ultima_comprobacion = '2026-10-11T11:59:00'; proxima_comprobacion = '2026-10-11T12:01:00'; condiciones = @((Cond 'proceso' '1')) }
    New-Wait 'ESP-W6' @{ motivo = 'espera con error'; tarea = 'AAA-T08'; agente = 'Claude-Opus'; estado = 'error'; error = 'SECRET-ERROR-MESSAGE en C:\secret-dir'; ultima_comprobacion = '2026-10-11T11:59:00'; proxima_comprobacion = '2026-10-11T12:01:00'; condiciones = @((Cond 'estado' 'ZZZ-T01=terminada')) }
    # Finished waits (collapsed in the list).
    New-Wait 'ESP-D1' @{ motivo = 'relanzar a Codex'; tarea = 'AAA-T02'; agente = 'Codex'; estado = 'cumplida'; avance = 100; met_at = '2026-10-11T09:41:00'; base = 'condiciones cumplidas'; inicio = '2026-10-11T09:00:00'; ultima_comprobacion = '2026-10-11T09:41:00' } 'terminadas'
    New-Wait 'ESP-D2' @{ motivo = 'espera vencida'; agente = 'Claude-Sonnet'; estado = 'vencida'; base = 'vencida: se agotó el tiempo máximo sin cumplirse'; inicio = '2026-10-10T08:00:00'; ultima_comprobacion = '2026-10-10T20:00:00' } 'terminadas'
    # Noise the viewer must skip: not JSON, JSON that is not an object.
    Set-Content (Join-Path $activeDir 'basura.json') 'esto no es json' -Encoding utf8
    Set-Content (Join-Path $activeDir 'lista.json') '[1, 2, 3]' -Encoding utf8

    $html = Run-Viewer
    $agents = Get-AgentsTab $html

    # --- Tareas tab: the label and the detail on the task ---
    $cell = Get-StateCell $html 'T02'
    Assert-True ($cell -match '^<span class="state s-espera">En espera de activación</span>') 'T02: label on the task'
    Assert-True ($cell -match '<div class="meta">Motivo: relanzar a Codex con la ventana de integración</div>') 'T02: motivo'
    Assert-True ($cell -match '<div class="meta">Condición: proceso 63128 \(pendiente\); reporte ok \(pendiente\)</div>') 'T02: conditions'
    Assert-True ($cell -match '<div class="meta">Reactiva a: Codex · desde 11:30 · última revisión 11:58 · próxima revisión 12:00</div>') 'T02: who it reactivates, since when, last and next check'
    Assert-True ($cell -match '<div class="meta">Avance: sin estimación \(depende de que un agente actúe\)</div>') 'T02: an unknown condition dominates, so sin estimación'
    Assert-True ($cell -match '<div class="meta">Tiempo máximo del vigilante: hasta 23:30 \(no es avance\)</div>') 'T02: the time limit is shown apart from the progress'
    Assert-True ($cell -match 'Estado en la ficha: pendiente' -and $cell -notmatch 'sin vigilante activo registrado') 'T02: card state kept; the watcher wins over the card'
    $cell = Get-StateCell $html 'T04'
    Assert-True ($cell -match '^<span class="state s-espera">En espera de activación</span>' -and $cell -match 'Reactiva a: Claude-Opus') 'T04 (terminada): a live watcher for the task labels it too'
    Assert-True ($cell -match '<div class="meta">Avance: 95 % · sin hora estimada · aproximada: ronda iniciada a las 11:20; tarda más de lo habitual') 'T04: «NN % · <time> · <base>» with the overdue mark'
    Assert-True ((Get-StateCell $html 'T01') -notmatch $label -and (Get-StateCell $html 'T05') -notmatch $label) 'tasks without a wait: no label'
    foreach ($code in 'T06', 'T07', 'T08') { Assert-True ((Get-StateCell $html $code) -notmatch $label) "$code`: a lost / stopped / failing watcher does not label the task" }

    # --- Agentes tab: the agent and its application card (rule 33) ---
    $codex = Get-AppCard $agents 'Codex'
    $status = Get-CardStatus $codex
    Assert-True ($status.Groups[1].Value -eq 's-espera' -and $status.Groups[2].Value -eq '1 en espera de activación') "Codex card status, got: $($status.Groups[2].Value)"
    $block = Get-AgentBlock $codex 'Codex'
    Assert-True ($block.Groups[1].Value -eq 's-espera' -and $block.Groups[2].Value -eq $label) 'Codex agent chip reads En espera de activación'
    Assert-True ($block.Groups[3].Value -match '<div class="agent-task"><strong>AAA-T02 · [^<]*</strong><span class="state s-pendiente">Asignada · Pendiente</span><span class="state s-espera">En espera de activación</span>') 'Codex task block carries the label'
    Assert-True ($block.Groups[3].Value -match '<div class="agent-current agent-waiting"><h4>En espera de activación</h4>.*Reactiva a: Codex · desde 11:30') 'Codex: the waiting block with the detail'
    Assert-True ($codex -cnotmatch 'sin actividad') 'the Codex card no longer says sin actividad'

    $claude = Get-AppCard $agents 'Claude'
    $status = Get-CardStatus $claude
    Assert-True ($status.Groups[1].Value -eq 's-en-curso' -and $status.Groups[2].Value -eq '1 con tarea activa · 1 en espera de activación') "Claude card status, got: $($status.Groups[2].Value)"
    $opus = Get-AgentBlock $claude 'Claude-Opus'
    Assert-True ($opus.Groups[2].Value -eq 'Asignada · En curso') 'an agent working on a task en curso keeps its status'
    Assert-True ($opus.Groups[3].Value -match '<div class="agent-current agent-waiting"><h4>En espera de activación</h4>' -and $opus.Groups[3].Value -match 'Motivo: relanzar a Claude-Opus al terminar la integración') 'but its waiting block is there'
    $sonnet = Get-AgentBlock $claude 'Claude-Sonnet'
    Assert-True ($sonnet.Groups[1].Value -eq 's-espera' -and $sonnet.Groups[2].Value -eq $label) 'an agent with no task but a watcher (agente) reads En espera de activación'
    Assert-True ($sonnet.Groups[3].Value -match '<div class="meta">Avance: 60 % · hacia las 13:00 · exacta: la hora indicada \(13:00\)</div>') 'Claude-Sonnet: 60 % · hacia las 13:00 · base'
    Assert-True ($claude -notmatch 'sin actividad:') 'a waiting agent is not listed among the ones sin actividad'
    $copilot = Get-AppCard $agents 'Copilot'
    Assert-True ((Get-CardStatus $copilot).Groups[2].Value -eq '1 en espera de activación') 'Copilot card: the pendiente card with «esperando:» (no watcher)'

    # --- «Vigilantes» in «Fuera de tareas» ---
    $waiters = Get-Waiters $agents
    Assert-True ($agents.IndexOf('id="fuera-de-tareas"') -lt $agents.IndexOf('id="vigilantes"') -and $agents.IndexOf('id="vigilantes"') -lt $agents.IndexOf('<div class="agent-grid">')) '«Vigilantes» sits inside «Fuera de tareas», before the application cards'
    Assert-True ($waiters -match '<h3>Vigilantes</h3><span class="state s-espera">3 activos · 3 con problemas</span>') "list header, got: $([regex]::Match($waiters, '<h3>Vigilantes</h3><span[^>]*>[^<]*').Value)"
    $w1 = Get-WaitItem $waiters 'ESP-W1'
    Assert-True ($w1 -match '^<strong>ESP-W1 · AAA-T02</strong> <span class="state s-espera">En espera de activación</span>') 'W1 item: id, task, label'
    Assert-True ($w1 -match '<li>proceso 63128 \(pendiente\): sin estimación \(depende de que un agente actúe\)</li>') 'W1: the condition without estimate'
    Assert-True ($w1 -match '<li>reporte ok \(pendiente\): 55 % · hacia las 12:09 · aproximada: ronda iniciada a las 11:54; mediana de las últimas 5 rondas 14 min \(lo habitual: entre 12 y 16 min\)</li>') 'W1: the condition with its own estimate and base'
    $w2 = Get-WaitItem $waiters 'ESP-W2'
    Assert-True ($w2 -match '^<strong>ESP-W2</strong>' -and $w2 -match 'Reactiva a: Claude-Sonnet · desde 11:00' -and $w2 -notmatch '<ul class="wait-conditions">') 'W2 item: no task, one condition (no per-condition list)'
    Assert-True ((Get-WaitItem $waiters 'ESP-W3') -match 'Avance: 95 % · sin hora estimada') 'W3 item'
    Assert-True ((Get-WaitItem $waiters 'ESP-W4') -match '<span class="state s-unknown">Vigilante perdido</span>' -and (Get-WaitItem $waiters 'ESP-W4') -match 'su última revisión fue a las 11:40, hace más de 3 intervalos') 'W4: lost'
    Assert-True ((Get-WaitItem $waiters 'ESP-W5') -match '<span class="state s-unknown">Vigilante detenido</span>' -and (Get-WaitItem $waiters 'ESP-W5') -match 'ya no existe: esta espera no se reactivará sola') 'W5: stopped'
    Assert-True ((Get-WaitItem $waiters 'ESP-W6') -match '<span class="state s-requiere-cambios">Vigilante con error</span>' -and (Get-WaitItem $waiters 'ESP-W6') -match 'reintenta en la próxima \(12:01\)') 'W6: with error'
    $fin = [regex]::Match($waiters, '(?s)<details class="wait-finished"><summary>Terminados \(2\)</summary><ul>(.*?)</ul></details>')
    Assert-True $fin.Success 'finished watchers in a collapsed list «Terminados (2)»'
    Assert-True ($waiters -notmatch '<details class="wait-finished" open') 'the finished list is collapsed'
    Assert-True ($fin.Groups[1].Value -match '<li><strong>ESP-D1</strong> · cumplida · tarea AAA-T02 · reactivó a Codex · 09:00 → 09:41 · relanzar a Codex</li>') 'finished: cumplida line'
    Assert-True ($fin.Groups[1].Value -match '<li><strong>ESP-D2</strong> · vencida · reactivó a Claude-Sonnet · 2026-10-10 08:00 → 2026-10-10 20:00 · espera vencida</li>') 'finished: vencida line, older times with their date'
    Assert-True ($fin.Groups[1].Value.IndexOf('ESP-D1') -lt $fin.Groups[1].Value.IndexOf('ESP-D2')) 'finished list newest first'

    # --- Only registered text, times, status and numbers ---
    Assert-True ($html -notmatch 'SECRET-ERROR-MESSAGE' -and $html -notmatch 'secret-dir' -and $html -notmatch 'registro-secreto' -and $html -notmatch 'accion-secreta') 'neither the error message, the log path nor the action script are printed'

    # ---- Edge cases ----
    # 100 % is only believed with met_at; a percentage never goes without its base.
    Reset-Waits
    New-Wait 'ESP-E1' @{ tarea = 'AAA-T02'; agente = 'Codex'; avance = 100; estimado = '2026-10-11T12:00:00'; base = 'condiciones cumplidas'
        condiciones = @((Cond 'proceso' '1' $true 100 '2026-10-11T11:50:00' 'cumplida'), (Cond 'hora' '2026-10-11T13:00:00' $false 100 '2026-10-11T13:00:00' 'exacta')) }
    $html = Run-Viewer
    $cell = Get-StateCell $html 'T02'
    Assert-True ($cell -match 'Avance: sin estimación · condiciones cumplidas' -and $cell -notmatch '100 %') '100 % without met_at is not shown'
    $w = Get-WaitItem (Get-Waiters (Get-AgentsTab $html)) 'ESP-E1'
    Assert-True ($w -match '<li>proceso 1 \(cumplida\): sin estimación · cumplida</li>' -and $w -notmatch '100 %') '100 % without met_at on a condition is not shown either'
    Reset-Waits
    New-Wait 'ESP-E2' @{ tarea = 'AAA-T02'; agente = 'Codex'; avance = 100; met_at = '2026-10-11T11:55:00'; estimado = '2026-10-11T11:55:00'; base = 'x'
        condiciones = @((Cond 'proceso' '1' $true 100 '2026-10-11T11:50:00' 'cumplida' '2026-10-11T11:50:00'), (Cond 'hora' '2026-10-11T13:00:00' $false 60 '2026-10-11T13:00:00' 'exacta')) }
    $html = Run-Viewer
    $w = Get-WaitItem (Get-Waiters (Get-AgentsTab $html)) 'ESP-E2'
    Assert-True ($w -match '<li>proceso 1 \(cumplida\): 100 % · hacia las 11:50 · cumplida</li>') '100 % with met_at is shown, with its base'
    Assert-True ((Get-StateCell $html 'T02') -match 'Avance: 100 % · hacia las 11:55 · x') '100 % overall with met_at'
    Reset-Waits
    New-Wait 'ESP-E3' @{ tarea = 'AAA-T02'; agente = 'Codex'; avance = 60; estimado = '2026-10-12T08:00:00'; base = ''
        condiciones = @((Cond 'entrada' 'SECRET-MATCH' $false $null $null 'sin estimación (depende de que un agente actúe)'), (Cond 'archivo' 'C:\secret-dir\file.txt' $false)) }
    $html = Run-Viewer
    $cell = Get-StateCell $html 'T02'
    Assert-True ($cell -match 'Avance: sin estimación</div>' -and $cell -notmatch '60 %') 'a percentage without a base is not shown'
    Assert-True ($cell -match 'Condición: entrada \(pendiente\); archivo \(pendiente\)' -and $html -notmatch 'SECRET-MATCH' -and $html -notmatch 'secret-dir') 'the value of an entrada / archivo condition is not shown'
    # an estimate on another day carries its date
    Reset-Waits
    New-Wait 'ESP-E4' @{ tarea = 'AAA-T02'; agente = 'Codex'; avance = 30; estimado = '2026-10-12T08:00:00'; base = 'exacta: la hora indicada (08:00)' }
    $cell = Get-StateCell (Run-Viewer) 'T02'
    Assert-True ($cell -match 'Avance: 30 % · hacia el 2026-10-12 08:00 · exacta: la hora indicada \(08:00\)') 'another day: «hacia el <fecha> <hora>»'
    # a percentage out of range is not believed
    Reset-Waits
    New-Wait 'ESP-E5' @{ tarea = 'AAA-T02'; agente = 'Codex'; avance = 140; estimado = '2026-10-11T12:30:00'; base = 'x' }
    Assert-True ((Get-StateCell (Run-Viewer) 'T02') -match 'Avance: sin estimación · x') 'a percentage above 100 is ignored'

    # the heartbeat: exactly 3 intervals is still alive, older is lost; with no interval it cannot be judged
    Reset-Waits
    New-Wait 'ESP-H1' @{ tarea = 'AAA-T02'; agente = 'Codex'; ultima_comprobacion = '2026-10-11T11:54:00'; proxima_comprobacion = '2026-10-11T11:56:00' }
    Assert-True ((Get-StateCell (Run-Viewer) 'T02') -match '^<span class="state s-espera">') '3 intervals old: still waiting'
    Reset-Waits
    New-Wait 'ESP-H1' @{ tarea = 'AAA-T02'; agente = 'Codex'; ultima_comprobacion = '2026-10-11T11:53:59'; proxima_comprobacion = '2026-10-11T11:55:59' }
    $html = Run-Viewer
    Assert-True ((Get-StateCell $html 'T02') -match '^<span class="state s-espera">' -and (Get-StateCell $html 'T02') -match 'esperando|sin vigilante activo registrado') 'older than 3 intervals: the watcher is lost, the task only shows what its own card says'
    Assert-True ((Get-WaitItem (Get-Waiters (Get-AgentsTab $html)) 'ESP-H1') -match 'Vigilante perdido') 'older than 3 intervals: Vigilante perdido'
    Reset-Waits
    New-Wait 'ESP-H2' @{ tarea = 'AAA-T04'; agente = 'Claude-Opus'; ultima_comprobacion = '2026-10-11T10:00:00'; proxima_comprobacion = '2026-10-11T10:00:00' }
    Assert-True ((Get-StateCell (Run-Viewer) 'T04') -match '^<span class="state s-espera">') 'no interval to compare: not called lost'

    # the watcher that is gone but whose card still says «esperando:»
    Reset-Waits
    New-Wait 'ESP-H3' @{ tarea = 'AAA-T02'; agente = 'Codex'; pid = $dead.Id }
    $cell = Get-StateCell (Run-Viewer) 'T02'
    Assert-True ($cell -match 'sin vigilante activo registrado') 'a stopped watcher does not count: the card''s own «esperando:» says it'

    # no esperas at all: back to the tracker of before (T18's cards)
    Reset-Waits
    $html = Run-Viewer
    $agents = Get-AgentsTab $html
    Assert-True ($agents -notmatch 'id="vigilantes"') 'no watchers: no list'
    Assert-True ((Get-CardStatus (Get-AppCard $agents 'Claude')).Groups[2].Value -eq '1 con tarea activa') 'no watchers: the Claude card is the one of before'
    # only finished watchers: the list shows them collapsed and says none is active
    New-Wait 'ESP-D3' @{ estado = 'cumplida'; avance = 100; met_at = '2026-10-11T09:41:00'; agente = 'Codex' } 'terminadas'
    $waiters = Get-Waiters (Get-AgentsTab (Run-Viewer))
    Assert-True ($waiters -match 'ninguno activo' -and $waiters -match 'No hay vigilantes activos' -and $waiters -match 'Terminados \(1\)') 'only finished watchers: none active, one finished'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T19 En espera de activación PASS'
