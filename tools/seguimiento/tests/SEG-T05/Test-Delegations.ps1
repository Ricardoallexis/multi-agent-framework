# SEG-T05: agents grouped by application, active delegations ("trabajando"), and the Trabajaron column.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t05-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Write-Task([string]$code, [string]$state, [string]$agent, [string[]]$rows) {
    $lines = @("# SEG-$code Tarea de prueba $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Parche | ninguno |', "| Agente | $agent |",
        '', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|') + $rows
    Set-Content -LiteralPath (Join-Path $channel "tareas\SEG\$code-x.md") $lines -Encoding utf8
}

try {
    New-Item -ItemType Directory -Force $repo, (Join-Path $channel 'tareas\SEG'), (Join-Path $channel 'orquestacion\SEG'), (Join-Path $channel 'orquestacion\RUT'), (Join-Path $channel 'parches\pendientes') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    git -C $repo init -q | Out-Null
    $registry = @('| Agente | Modelo | Aplicación | Estado | Último | Cursor | Sesión |', '|---|---|---|---|---|---|---|',
        '| Claude-Opus | Opus | Claude | ACTIVO | x | c | s1 |',
        '| Claude-Sonnet | Sonnet | Claude | ACTIVO | x | c | s2 |',
        '| Claude-Fable | Fable | Claude | ACTIVO | x | c | s3 |',
        '| Claude-Opus2 | Opus | Claude | ACTIVO | x | c | s4 |',
        '| Codex | gpt | Codex | ACTIVO | x | c | s5 |',
        '| Copilot-Auto | auto | Copilot | ACTIVO | x | c | s6 |')
    Set-Content (Join-Path $channel 'AGENTES.md') $registry -Encoding utf8

    Write-Task 'T05' 'en curso' 'Claude-Opus' @(
        '| 2026-10-10 09:00 | Claude-Opus | tomada | x |',
        '| 2026-10-10 10:15 | Claude-Opus | delegada | Claude Sonnet 5.5 (T2, esfuerzo bajo): vista de agentes |',
        '| 2026-10-10 10:20 | Claude-Opus | delegada | Haiku Lite (T1, esfuerzo bajo): revisar textos |',
        '| 2026-10-10 11:00 | Claude-Opus | devuelta | Haiku Lite: textos listos |')
    Write-Task 'T06' 'en curso' 'Claude-Opus2' @('| 2026-10-10 08:00 | Claude-Opus2 | tomada | x |')
    Write-Task 'T07' 'pendiente' 'Codex' @('| 2026-10-09 08:00 | Codex | tomada | x |')
    Write-Task 'T08' 'disponible' 'Claude-Sonnet' @()

    # New-format JSONL for T05 (duplicates the card worker: must be deduplicated) and old Codex format for T07.
    Set-Content (Join-Path $channel 'orquestacion\SEG\ejecuciones.jsonl') @(
        '{"task":"SEG-T05","worker":"Claude Sonnet 5.5","requested_model":"claude-sonnet-5-5","requested_effort":"medio","status":"delegated","orchestrator":"Claude-Opus"}',
        'esto no es json') -Encoding utf8
    Set-Content (Join-Path $channel 'orquestacion\RUT\ejecuciones.jsonl') @(
        '{"reason":"x","requested_effort":"medium","requested_model":"gpt-6-luna","worker":"/root/rut_policy","status":"reviewed_complete","task":"SEG-T07"}') -Encoding utf8

    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir | Out-Null
    $html = [IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html'))

    # Agents tab: one card per application.
    $agents = $html.Substring($html.IndexOf('<div class="tab" id="agentes">'))
    $agents = $agents.Substring(0, $agents.IndexOf('cloud-assignment-group'))
    $apps = [regex]::Matches($agents, '<section class="panel agent-card">\s*<div class="card-head"><h2>([^<]+)</h2>') | ForEach-Object { $_.Groups[1].Value }
    Assert-True ((@($apps) -join ',') -eq 'Claude,Codex,Copilot') "one card per application, got: $(@($apps) -join ',')"
    $claude = $agents.Substring($agents.IndexOf('<h2>Claude</h2>'), $agents.IndexOf('<h2>Codex</h2>') - $agents.IndexOf('<h2>Claude</h2>'))
    Assert-True ($claude -match '<h3>Claude-Opus</h3>') 'Claude-Opus listed as active'
    Assert-True ($claude -match '<h3>Claude-Opus2</h3>') 'instance Claude-Opus2 listed under Claude'
    $claudeActive = $claude.Substring(0, $claude.IndexOf('agent-idle'))
    Assert-True ($claudeActive -notmatch '<h3>Claude-Sonnet</h3>' -and $claudeActive -notmatch '<h3>Claude-Fable</h3>') 'idle names are not active blocks'
    Assert-True ($claude -match 'sin actividad: Claude-Sonnet, Claude-Fable</p>') 'sin actividad line with idle Claude names'
    Assert-True ($agents -match 'sin actividad: Copilot-Auto</p>') 'Copilot-Auto idle'
    Assert-True ($agents -match '<h3>Codex</h3>') 'Codex active (pendiente)'
    # Delegations: active shown, returned not.
    Assert-True ($claude -match 'Claude Sonnet 5\.5 \(T2\) desde 10:15 (·|&#183;) sin se(ñ|&#241;)al de actividad (·|&#183;) vista de agentes') 'active delegation line'
    Assert-True ($agents -notmatch 'Haiku Lite \(T1\)') 'returned worker is not shown as working'

    # Trabajaron column of the task table.
    $row = [regex]::Match($html, '<tr><td><a [^>]*>SEG-T05 Tarea de prueba T05</a>.*?</tr>').Value
    Assert-True ($row -ne '') 'T05 row found'
    Assert-True ($row -match 'orquestó Claude-Opus') 'orquestó Claude-Opus'
    Assert-True ($row -match 'Claude Sonnet 5\.5 \(T2, esfuerzo bajo\)') 'Sonnet worker with level/effort'
    Assert-True ($row -match 'Haiku Lite \(T1, esfuerzo bajo\)') 'returned worker still listed as worked'
    Assert-True ([regex]::Matches($row, 'Claude Sonnet 5\.5').Count -eq 1) 'worker deduplicated against JSONL'
    $row7 = [regex]::Match($html, '<tr><td><a [^>]*>SEG-T07 Tarea de prueba T07</a>.*?</tr>').Value
    Assert-True ($row7 -match 'gpt-6-luna \(/root/rut_policy\)') 'Codex JSONL worker with requested model'
    Assert-True ($row7 -match 'orquestó Codex') 'old JSONL orchestrator falls back to card Agente'
    $row8 = [regex]::Match($html, '<tr><td><a [^>]*>SEG-T08 Tarea de prueba T08</a>.*?</tr>').Value
    Assert-True ($row8 -ne '' -and $row8 -notmatch 'orquestó') 'no delegation: nothing invented'

    # Tasks follow their code, each with its own last activity.
    $board = $html.Substring($html.IndexOf('SEG-T05 Tarea de prueba T05'))
    $order = 'SEG-T05','SEG-T06','SEG-T07','SEG-T08' | ForEach-Object { $html.IndexOf("$_ Tarea de prueba") }
    Assert-True ($order[0] -lt $order[1] -and $order[1] -lt $order[2] -and $order[2] -lt $order[3]) 'tasks ordered by code'
    Assert-True ($row -match 'Última actividad 2026-10-10 11:00') 'last activity still shown'

    # Resumen card of the active stage.
    $cards = $html.Substring($html.IndexOf('<section class="cards">'))
    $cards = $cards.Substring(0, $cards.IndexOf('</section>'))
    Assert-True ($cards -match 'Claude Sonnet 5\.5 \(T2\) desde 10:15') 'Resumen card shows trabajando'
    Assert-True ($cards -notmatch 'Haiku Lite') 'Resumen card omits returned worker'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T05 delegations PASS'
