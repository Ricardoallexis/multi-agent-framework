# SEG-T11: active delegations close on takeover / card state, and show real worker activity.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t11-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$subRoot = Join-Path $temp 'projects'
$subDir = Join-Path $subRoot 'proj\session\subagents'
$now = [datetime]'2026-10-11T12:00:00'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Utc([datetime]$local) { $local.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') }
# Writes agent-<id>.jsonl: a text record at $minutesAgo, or a tool_use (optionally answered).
function New-Subagent([string]$id, [int]$minutesAgo, [string]$command = '', [int]$toolMinutesAgo = 0, [switch]$answered) {
    $lines = @()
    $lines += (@{ type = 'user'; timestamp = (Utc $now.AddMinutes(-$minutesAgo - 5)); message = @{ role = 'user'; content = 'SECRET-CONTENT' } } | ConvertTo-Json -Compress -Depth 6)
    if ($command) {
        $at = if ($toolMinutesAgo) { $toolMinutesAgo } else { $minutesAgo }
        $lines += (@{ type = 'assistant'; timestamp = (Utc $now.AddMinutes(-$at)); message = @{ role = 'assistant'; content = @(@{ type = 'tool_use'; id = "tu_$id"; name = 'Bash'; input = @{ command = $command } }) } } | ConvertTo-Json -Compress -Depth 8)
        if ($answered) { $lines += (@{ type = 'user'; timestamp = (Utc $now.AddMinutes(-$minutesAgo)); message = @{ role = 'user'; content = @(@{ type = 'tool_result'; tool_use_id = "tu_$id"; content = 'ok' }) } } | ConvertTo-Json -Compress -Depth 8) }
    } else {
        $lines += (@{ type = 'assistant'; timestamp = (Utc $now.AddMinutes(-$minutesAgo)); message = @{ role = 'assistant'; content = @(@{ type = 'text'; text = 'hola' }) } } | ConvertTo-Json -Compress -Depth 8)
    }
    $path = Join-Path $subDir "agent-$id.jsonl"
    Set-Content $path $lines -Encoding utf8
    (Get-Item $path).LastWriteTime = $now.AddMinutes(-$minutesAgo)
}
function Write-Card([string]$code, [string]$state, [string[]]$rows) {
    $lines = @("# AAA-$code Tarea $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Depende de | - |', '| Parche | ninguno |', '| Agente | Claude |',
        '', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', '| 2026-10-11 10:00 | Claude-Opus | creada | x |') + $rows
    Set-Content (Join-Path $channel "tareas\AAA\$code-x.md") $lines -Encoding utf8
}
function Deleg([string]$time, [string]$ref, [string]$piece = 'pieza') { "| 2026-10-11 $time | Claude-Opus | delegada | Claude Sonnet 5.5 (T2, esfuerzo medio): $piece [ref: agent-$ref] |" }

try {
    New-Item -ItemType Directory -Force $repo, $subDir, (Join-Path $channel 'tareas\AAA'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    git -C $repo init -q | Out-Null
    Set-Content (Join-Path $channel 'tareas\AAA\ETAPA.md') @('# AAA · Etapa de prueba', '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', '| Estado | abierta |', '', '## Fases de tareas', '', '| Fase | Tareas | Descripción |', '|---|---|---|', '| Fase 1 | T01, T02, T03, T04, T05, T06, T07, T08, T09 | x |', '', '## Historial de decisiones', '', '- 2026-10-11 · Claude-Opus · Se abre la etapa') -Encoding utf8

    New-Subagent 'work1' 3
    New-Subagent 'test1' 20 'python -m pytest tests -q' 20
    New-Subagent 'done1' 20 'python -m pytest tests -q' 18 -answered
    New-Subagent 'idle1' 30
    New-Subagent 'dead1' 90
    New-Subagent 'longtest' 90 'pwsh probar.ps1' 90
    Write-Card 'T01' 'en curso' @((Deleg '11:00' 'work1' 'pieza uno'))
    Write-Card 'T02' 'en curso' @((Deleg '11:30' 'test1' 'pieza dos'))
    Write-Card 'T03' 'en curso' @((Deleg '11:00' 'idle1' 'pieza tres'), (Deleg '11:05' 'dead1' 'pieza cuatro'))
    Write-Card 'T04' 'en curso' @((Deleg '11:00' 'gone1' 'pieza cinco'), '| 2026-10-11 11:10 | Claude-Opus | delegada | Claude Haiku (T1): sin referencia |')
    Write-Card 'T05' 'en curso' @((Deleg '11:00' 'work1' 'pieza reasignada'), '| 2026-10-11 11:20 | Codex | tomada | relevo |')
    Write-Card 'T06' 'terminada' @((Deleg '11:00' 'work1' 'pieza terminada'))
    Write-Card 'T07' 'en curso' @((Deleg '11:00' 'work1' 'pieza devuelta'), '| 2026-10-11 11:30 | Claude-Opus | devuelta | Claude Sonnet 5.5: ok |')
    Write-Card 'T08' 'en curso' @((Deleg '11:00' 'done1' 'pieza ocho'))
    Write-Card 'T09' 'en curso' @((Deleg '10:00' 'longtest' 'pieza nueve'))

    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots $subRoot -NowOverride $now | Out-Null
    $html = [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))

    Assert-True ($html -match 'T01: Claude Sonnet 5\.5 \(T2\) desde 11:00 · Trabajando · pieza uno') 'T01 Trabajando with piece'
    Assert-True ($html -match 'T02: Claude Sonnet 5\.5 \(T2\) desde 11:30 · Ejecutando prueba · desde 11:40 · pieza dos') 'T02 Ejecutando prueba'
    Assert-True ($html -match 'desde 11:00 · Sin actividad reciente · última hace 30 min · pieza tres') 'T03 sin actividad'
    Assert-True ($html -match 'desde 11:05 · Probablemente detenido · pieza cuatro') 'T03 detenido'
    Assert-True ($html -match 'desde 11:00 · sin señal de actividad · pieza cinco') 'T04 file missing'
    Assert-True ($html -match 'Claude Haiku \(T1\) desde 11:10 · sin señal de actividad · sin referencia') 'T04 no reference'
    # SEG-T12: the Trabajaron column lists every delegation of the task (piece included); what must not appear is an activity line.
    Assert-True ($html -notmatch 'T05: [^<]*pieza reasignada') 'T05 closed by takeover'
    Assert-True ($html -notmatch 'T06: [^<]*pieza terminada') 'T06 closed by card state'
    Assert-True ($html -notmatch 'T07: [^<]*pieza devuelta') 'T07 returned'
    Assert-True ($html -match 'desde 11:00 · Sin actividad reciente · última hace 20 min · pieza ocho') 'T08 answered tool is not a running test'
    Assert-True ($html -match 'desde 10:00 · Probablemente detenido · pieza nueve') 'T09 long test over threshold'
    Assert-True ($html -notmatch '\[ref:') 'reference kept out of the displayed piece'
    Assert-True ($html -notmatch 'SECRET-CONTENT') 'subagent content never printed'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T11 worker activity PASS'
