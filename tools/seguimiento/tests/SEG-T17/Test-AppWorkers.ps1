# SEG-T17 (REGLAS 33): every delegated worker appears in the Agentes card of ITS OWN application, whichever application orchestrates it:
# "trabajando para <orquestador> en <tarea>", its model, its real activity and its per-window tokens. It also stays as a delegation in the
# orchestrator's block, and an application card never says "sin actividad" while it has active workers. Synthetic folders only; the
# content of the sessions is never printed (nor read: only times, status and model names).
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t17-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$subRoot = Join-Path $temp 'projects'
$subDir = Join-Path $subRoot 'proj\session\subagents'
$copilotRoot = Join-Path $temp 'copilot'
$codexRoot = Join-Path $temp 'codex'
$codexDay = Join-Path $codexRoot 'sessions\2026\10\11'
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
# --- Copilot: ~/.copilot/session-state/<id>/events.jsonl, records {type, data, id, timestamp, parentId} ---
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
# --- Codex: ~/.codex/sessions/YYYY/MM/DD/rollout-*-<id>.jsonl, records {timestamp, type, payload} ---
function CxRec([string]$type, [int]$minutesAgo, $payload) { Json @{ timestamp = (Utc $now.AddMinutes(-$minutesAgo)); type = $type; payload = $payload } }
# With -TestCommand the last record is an unanswered shell call running that command (starts 2 minutes before the last write).
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

function Write-Card([string]$code, [string]$state, [string]$owner, [string[]]$rows, [string]$tokens = '') {
    $lines = @("# AAA-$code · Tarea de prueba $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Depende de | - |', '| Parche | ninguno |', "| Agente | $owner |")
    if ($tokens) { $lines += "| Tokens | $tokens |" }
    $lines += @('', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', "| 2026-10-11 10:00 | $owner | creada | x |") + $rows
    Set-Content (Join-Path $channel "tareas\AAA\$code-x.md") $lines -Encoding utf8
}
function Deleg([string]$orchestrator, [string]$time, [string]$worker, [string]$ref, [string]$piece, [string]$level = 'T2, esfuerzo medio') {
    $tail = if ($ref) { " [ref: $ref]" } else { '' }
    "| 2026-10-11 $time | $orchestrator | delegada | $worker ($level): $piece$tail |"
}
function Run-Viewer {
    & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots $subRoot -CopilotRoot $copilotRoot -CodexRoot $codexRoot -NowOverride $now | Out-Null
    $html = [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))
    $agents = $html.Substring($html.IndexOf('<div class="tab" id="agentes">'))
    $agents.Substring(0, $agents.IndexOf('cloud-assignment-group'))
}
# The card of one application inside the Agentes tab, and its worker blocks (they hold no nested div).
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

$cp = @{ work = 'b2222222-0000-4000-8000-000000000001'; done = 'b2222222-0000-4000-8000-000000000002'; stop = 'b2222222-0000-4000-8000-000000000003' }
$cx = @{ test = '01b20000-0000-7000-8000-000000000002'; integ = '01b20000-0000-7000-8000-000000000003' }

try {
    New-Item -ItemType Directory -Force $repo, $subRoot, $copilotRoot, $codexRoot, (Join-Path $channel 'tareas\AAA'), (Join-Path $channel 'orquestacion\AAA'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    Set-Content (Join-Path $channel 'AGENTES.md') @('| Agente | Modelo | Aplicación | Estado | Último | Cursor | Sesión |', '|---|---|---|---|---|---|---|',
        '| Claude-Opus | Opus | Claude | ACTIVO | x | c | s1 |', '| Codex | gpt | Codex | ACTIVO | x | c | s2 |', '| Copilot-Auto | auto | Copilot | ACTIVO | x | c | s3 |') -Encoding utf8
    git -C $repo init -q | Out-Null
    Set-Content (Join-Path $channel 'tareas\AAA\ETAPA.md') @('# AAA · Etapa de prueba', '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', '| Estado | abierta |', '', '## Fases de tareas', '', '| Fase | Tareas | Descripción |', '|---|---|---|', '| Fase 1 | T01, T02, T03, T04 | x |', '', '## Historial de decisiones', '', '- 2026-10-11 · Claude-Opus · Se abre la etapa') -Encoding utf8

    # Activity files: Copilot sessions, Codex threads and Claude subagents (recent mtimes; now is 12:00).
    New-CopilotSession $cp.work 3
    New-CopilotSession $cp.done 20
    New-CopilotSession $cp.stop 90
    New-CodexThread $cx.test 20 -TestCommand 'await run("python -m pytest tests -q SECRET-CMD")'
    New-CodexThread $cx.integ 5
    New-ClaudeSubagent 'same1' 2
    New-ClaudeSubagent 'cdx1' 4

    # T01: Claude-Opus orchestrates a Copilot worker, a Codex worker (with exact tokens), a Claude worker (same application) and a returned Copilot worker.
    Write-Card 'T01' 'en curso' 'Claude-Opus' @(
        (Deleg 'Claude-Opus' '11:00' 'GPT worker' "copilot:$($cp.work)" 'pieza uno'),
        (Deleg 'Claude-Opus' '11:10' 'Codex worker' "codex:$($cx.test)" 'pieza dos'),
        (Deleg 'Claude-Opus' '11:20' 'Claude Sonnet 5.5' 'agent-same1' 'pieza tres'),
        (Deleg 'Claude-Opus' '11:30' 'GPT worker B' "copilot:$($cp.done)" 'pieza cuatro'),
        '| 2026-10-11 11:40 | Claude-Opus | devuelta | GPT worker B: listo |') 'Claude-Opus: total 5 = entrada 1 + cacheados 3 + salida 1 (medido); ↳ Codex worker (delegado por Claude-Opus, exacto por ventana, con 3 hilos hijos): total 485 = entrada 310 + cacheados 130 + salida 45; Total de la tarea: 490'
    # T02: Codex orchestrates a Claude worker.
    Write-Card 'T02' 'en curso' 'Codex' @((Deleg 'Codex' '11:15' 'Claude Sonnet 5.5' 'agent-cdx1' 'pieza cinco'))
    # T03: workers without a usable reference: the application comes from the worker name; one has no application at all; one Copilot session stopped.
    Write-Card 'T03' 'en curso' 'Claude-Opus' @(
        (Deleg 'Claude-Opus' '11:00' 'Copilot gpt-6-luna' '' 'pieza seis'),
        (Deleg 'Claude-Opus' '11:05' 'Copilot Claude Sonnet 5.5' '' 'pieza siete'),
        (Deleg 'Claude-Opus' '11:10' 'GPT worker C' "copilot:$($cp.stop)" 'pieza ocho'),
        (Deleg 'Claude-Opus' '11:15' 'Mystery worker' '' 'pieza sin app'))
    # T04: delivered task with an integration worker (Codex thread) still active.
    Write-Card 'T04' 'terminada' 'Claude-Opus' @((Deleg 'Claude-Opus' '11:50' 'Codex worker I' "codex:$($cx.integ)" 'pieza integra' 'T1, integración'))

    $agents = Run-Viewer

    # One card per registered application, none invented for a worker without an application.
    $apps = [regex]::Matches($agents, '<section class="panel agent-card">\s*<div class="card-head"><h2>([^<]+)</h2>') | ForEach-Object { $_.Groups[1].Value }
    Assert-True ((@($apps) -join ',') -eq 'Claude,Codex,Copilot') "one card per application, got: $(@($apps) -join ',')"
    $copilot = Get-AppCard $agents 'Copilot'
    $codex = Get-AppCard $agents 'Codex'
    $claude = Get-AppCard $agents 'Claude'

    # --- Copilot card: no Copilot agent owns a task, its workers delegated by Claude-Opus are active here ---
    $status = Get-CardStatus $copilot
    Assert-True ($status.Groups[1].Value -eq 's-en-curso') 'Copilot card is active'
    Assert-True ($status.Groups[2].Value -eq '3 trabajadores activos · 1 trabajador probablemente detenido') "Copilot card status, got: $($status.Groups[2].Value)"
    Assert-True ($copilot -notmatch '<span class="state s-idle">sin actividad') 'Copilot card does not say sin actividad while it has active workers'
    Assert-True ($copilot -match 'sin tarea propia: Copilot-Auto</p>' -and $copilot -notmatch 'class="meta agent-idle">sin actividad:') 'FLU-T08: live task workers keep the area active; the idle agent has no own task'
    $w1 = Get-WorkerBlock $copilot 'pieza uno'
    Assert-True ($w1 -match '<strong>Copilot · gpt-5\.6-luna \(T2\)</strong>') 'T01 Copilot worker: app · model (level)'
    Assert-True ($w1 -match 'Activo · trabajando para Claude-Opus en AAA-T01') 'T01 Copilot worker: trabajando para <orquestador> en <tarea>'
    Assert-True ($w1 -match 'Actividad: Trabajando · delegado desde 11:00 · pieza uno') 'T01 Copilot worker: real activity'
    Assert-True ($w1 -match 'Tarea: AAA-T01 · Tarea de prueba T01') 'T01 Copilot worker: task'
    $w6 = Get-WorkerBlock $copilot 'pieza seis'
    Assert-True ($w6 -match '<strong>Copilot gpt-6-luna \(T2\)</strong>' -and $w6 -match 'Activo · trabajando para Claude-Opus en AAA-T03' -and $w6 -match 'Actividad: sin señal de actividad') 'T03 no reference: application from the worker name, no activity file'
    $w7 = Get-WorkerBlock $copilot 'pieza siete'
    Assert-True ($w7 -match 'Copilot Claude Sonnet 5\.5') 'T03 "Copilot Claude Sonnet": the application named first (Copilot) wins'
    $w8 = Get-WorkerBlock $copilot 'pieza ocho'
    Assert-True ($w8 -match 'Probablemente detenido · delegado por Claude-Opus en AAA-T03' -and $w8 -notmatch 'trabajando para' -and $w8 -match 's-unknown') 'T03 stopped Copilot session is listed, not counted as active'
    Assert-True ($copilot -notmatch 'pieza cuatro') 'a returned (devuelta) Copilot worker is not shown'
    Assert-True ($copilot -notmatch 'pieza tres' -and $copilot -notmatch 'pieza cinco') 'workers of other applications are not in the Copilot card'
    Assert-True ($copilot -notmatch 'pieza sin app') 'a worker without application is not placed in any application card'
    # the orchestrator keeps the delegation
    Assert-True ($claude -match 'Copilot · gpt-5\.6-luna \(T2\) desde 11:00 · Trabajando · pieza uno') 'Claude-Opus card still lists the Copilot delegation'

    # --- Codex card: a Codex agent with a task and two workers delegated by Claude-Opus ---
    $status = Get-CardStatus $codex
    Assert-True ($status.Groups[1].Value -eq 's-en-curso' -and $status.Groups[2].Value -eq '1 con tarea activa · 2 trabajadores activos') "Codex card status, got: $($status.Groups[2].Value)"
    Assert-True ($codex -match '<h3>Codex</h3>') 'the Codex agent block is still there'
    $w2 = Get-WorkerBlock $codex 'pieza dos'
    Assert-True ($w2 -match '<strong>Codex · gpt-6-luna \(T2\)</strong>' -and $w2 -match 'Activo · trabajando para Claude-Opus en AAA-T01') 'T01 Codex worker: app · model, trabajando para'
    Assert-True ($w2 -match 'Actividad: Ejecutando prueba · desde 11:38 · delegado desde 11:10 · pieza dos') 'T01 Codex worker: running test since 11:38'
    Assert-True ($w2 -match 'Tokens: 485 · entrada 310 · salida 45 · exacto por ventana') 'T01 Codex worker: per-window tokens'
    $w9 = Get-WorkerBlock $codex 'pieza integra'
    Assert-True ($w9 -match 'Codex · gpt-6-luna \(T1\)' -and $w9 -match 'trabajando para Claude-Opus en AAA-T04' -and $w9 -match 'Actividad: Trabajando' -and $w9 -match 'Tarea: AAA-T04 · Tarea de prueba T04 · integración') 'T04 integration worker of a delivered task'
    Assert-True ($codex -match 'Claude Sonnet 5\.5 \(T2\) desde 11:15 · Trabajando · pieza cinco') 'Codex (orchestrator) card still lists its delegation of the Claude worker'

    # --- Claude card: Claude-Opus owns tasks; a Claude worker delegated by Codex is listed in the Claude area ---
    $status = Get-CardStatus $claude
    Assert-True ($status.Groups[1].Value -eq 's-en-curso' -and $status.Groups[2].Value -eq '1 con tarea activa · 1 trabajador activo') "Claude card status, got: $($status.Groups[2].Value)"
    $w5 = Get-WorkerBlock $claude 'pieza cinco'
    Assert-True ($w5 -match '<strong>Claude Sonnet 5\.5 \(T2\)</strong>' -and $w5 -match 'Activo · trabajando para Codex en AAA-T02' -and $w5 -match 'Actividad: Trabajando · delegado desde 11:15 · pieza cinco') 'T02 Claude worker delegated by Codex'
    Assert-True (@(Get-WorkerBlocks $claude).Count -eq 1) 'Claude workers of a Claude orchestrator stay in the orchestrator block (not duplicated)'
    Assert-True ($claude -match 'Claude Sonnet 5\.5 \(T2\) desde 11:20 · Trabajando · pieza tres') 'T01 same-application Claude worker listed as a delegation of Claude-Opus'
    Assert-True ($claude -match 'Mystery worker \(T2\) desde 11:15 · sin señal de actividad · pieza sin app') 'a worker without application is only a delegation line of its orchestrator'

    # Nothing of the sessions is printed
    $html = [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))
    Assert-True ($html -notmatch 'SECRET-CONTENT' -and $html -notmatch 'SECRET-CMD') 'session content never printed'
    # (the raw history rows of "Última actividad" keep their [ref: ...] as written in the card; the worker blocks never print a reference)
    $blocks = ((@($claude, $codex, $copilot) | ForEach-Object { Get-WorkerBlocks $_ }) -join "`n") -replace '<[^>]+>', ' '
    Assert-True ($blocks -ne '' -and $blocks -notmatch '\[ref:' -and $blocks -notmatch 'agent-|copilot:|codex:') 'worker blocks keep the reference out of the page'
    Assert-True ($blocks -notmatch [regex]::Escape($cp.work) -and $blocks -notmatch [regex]::Escape($cx.test) -and $blocks -notmatch 'same1' -and $blocks -notmatch 'cdx1') 'ids not printed in the worker blocks'
    Assert-True ($html -notmatch 'session-state|rollout-|events\.jsonl') 'file locations not printed'

    # --- Only a stopped worker: the card is not active and says so (nothing is hidden either) ---
    Write-Card 'T01' 'en curso' 'Claude-Opus' @((Deleg 'Claude-Opus' '11:10' 'GPT worker C' "copilot:$($cp.stop)" 'pieza ocho'))
    Remove-Item (Join-Path $channel 'tareas\AAA\T03-x.md')
    $agents = Run-Viewer
    $copilot = Get-AppCard $agents 'Copilot'
    $status = Get-CardStatus $copilot
    Assert-True ($status.Groups[1].Value -eq 's-idle' -and $status.Groups[2].Value -eq 'sin actividad · 1 trabajador probablemente detenido') "Copilot card with only a stopped worker, got: $($status.Groups[2].Value)"
    Assert-True ((Get-WorkerBlock $copilot 'pieza ocho') -match 'Probablemente detenido') 'the stopped worker is still listed'
    Assert-True ($copilot -match 'class="meta agent-idle">sin actividad: Copilot-Auto</p>') 'FLU-T08: stopped task workers preserve the idle label'

    # --- A finished task: its workers are no longer active anywhere ---
    Write-Card 'T01' 'terminada' 'Claude-Opus' @((Deleg 'Claude-Opus' '11:10' 'GPT worker C' "copilot:$($cp.work)" 'pieza nueve'))
    $agents = Run-Viewer
    $copilot = Get-AppCard $agents 'Copilot'
    Assert-True ($copilot -notmatch 'pieza nueve' -and (Get-CardStatus $copilot).Groups[2].Value -eq 'sin actividad') 'a delivered task has no active Copilot worker: the card says sin actividad'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T17 application workers PASS'
