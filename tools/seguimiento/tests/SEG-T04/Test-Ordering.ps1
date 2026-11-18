# SEG-T04: closed stages newest-first with closure date, active stages by last activity, tasks by own last row.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t04-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Assert-Order([string]$html, [string[]]$needles, [string]$what) {
    $last = -1
    foreach ($needle in $needles) {
        $index = $html.IndexOf($needle, [StringComparison]::Ordinal)
        Assert-True ($index -ge 0) "$what : missing '$needle'"
        Assert-True ($index -gt $last) "$what : '$needle' out of order"
        $last = $index
    }
}
function New-Commit([string]$name, [string]$isoDate) {
    Set-Content -LiteralPath (Join-Path $repo "$name.txt") $name -Encoding utf8
    git -C $repo add -A | Out-Null
    $env:GIT_AUTHOR_DATE = $isoDate; $env:GIT_COMMITTER_DATE = $isoDate
    git -C $repo commit -q -m $name | Out-Null
    $env:GIT_AUTHOR_DATE = $null; $env:GIT_COMMITTER_DATE = $null
    (git -C $repo rev-parse HEAD).Trim()
}
function Write-Stage([string]$name, [string]$state, [string]$closeCommit, [string]$report, [string[]]$decisions) {
    $dir = Join-Path $channel "tareas\$name"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $lines = @("# $name · Etapa de prueba", '', '| Campo | Valor |', '|---|---|', '| Autorizada | si |', "| Estado | $state |")
    if ($closeCommit) { $lines += "| Commit de cierre | ``$closeCommit`` |" }
    if ($report) { $lines += "| Reporte de integración | ``$report`` |" }
    $lines += @('', '## Historial de decisiones', '') + $decisions
    Set-Content -LiteralPath (Join-Path $dir 'ETAPA.md') $lines -Encoding utf8
}
function Write-Task([string]$stage, [string]$code, [string]$state, [string]$commit, [string[]]$rows) {
    $lines = @("# $stage-$code Tarea de prueba", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Parche | ninguno |', '| Agente | Claude-Opus |')
    if ($commit) { $lines += "| Commit | ``$commit`` |" }
    $lines += @('', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|') + $rows
    Set-Content -LiteralPath (Join-Path $channel "tareas\$stage\$code-x.md") $lines -Encoding utf8
}

try {
    New-Item -ItemType Directory -Force $repo, (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    git -C $repo init -q | Out-Null
    git -C $repo config user.email t@example.com; git -C $repo config user.name Tester
    $base = New-Commit 'base' '2026-01-01T10:00:00'
    $oldClose = New-Commit 'old-close' '2026-03-05T12:00:00'
    $newClose = New-Commit 'new-close' '2026-04-20T08:30:00'

    $reports = @{ 'AAA' = '2026-03-05_1200'; 'BBB' = '2026-04-20_0830' }
    $readLines = @('| Reporte | Resultado | Pruebas |', '|---|---|---|')
    foreach ($id in $reports.Values) {
        Set-Content (Join-Path $channel "pruebas\integracion\${id}_integracion.md") 'ok' -Encoding utf8
        $readLines += "| ${id}_integracion.md | OK | 12 passed in 1s |"
    }
    Set-Content (Join-Path $channel 'pruebas\integracion\LECTURAS.md') $readLines -Encoding utf8

    # Two verified closed stages (the older one is named so that alphabetical order would put it first).
    Write-Stage 'AAA' 'cerrada' $oldClose '2026-03-05_1200_integracion.md' @('- 2026-03-05 · Claude-Opus · Etapa cerrada')
    Write-Task 'AAA' 'T01' 'integrada' $oldClose @('| 2026-03-04 | Claude-Opus | integrada | ok |')
    Write-Stage 'BBB' 'cerrada' $newClose '2026-04-20_0830_integracion.md' @('- 2026-04-20 · Claude-Opus · Etapa cerrada')
    Write-Task 'BBB' 'T01' 'integrada' $newClose @('| 2026-04-19 | Claude-Opus | integrada | ok |')
    # Closed according to its card only (no commit): closure date comes from the history line.
    Write-Stage 'CCC' 'cerrada' '' '' @('- 2026-02-02 · Claude-Sonnet · Planificada', '- 2026-02-10 18:45 · Claude-Sonnet · Etapa cerrada por el usuario')
    Write-Task 'CCC' 'T01' 'integrada' '' @('| 2026-02-09 | Claude-Opus | integrada | ok |')
    # Closed with no date anywhere: unknown, listed last.
    Write-Stage 'DDD' 'cerrada' '' '' @('- sin fecha · nadie · Etapa cerrada')
    Write-Task 'DDD' 'T01' 'integrada' '' @('| 2026-02-01 | Claude-Opus | integrada | ok |')
    # Open stages: EEE has an older task row, FFF a newer ETAPA.md line.
    Write-Stage 'EEE' 'abierta' '' '' @('- 2026-05-01 · Claude-Opus · Se abre la etapa')
    Write-Task 'EEE' 'T01' 'en curso' '' @('| 2026-05-10 09:00 | Claude-Opus | tomada | x |', '| 2026-05-12 17:20 | Claude-Opus | terminada | entrega |', 'no es una fila', '| 2026-99-99 | X | rota | x |')
    Write-Stage 'FFF' 'abierta' '' '' @('- 2026-05-20 · Claude-Sonnet · Replanificada la etapa con nuevos criterios')
    Write-Task 'FFF' 'T01' 'disponible' '' @('| 2026-05-15 | Claude-Opus | liberada | x |')
    # Stage with tasks in non-chronological order.
    Write-Stage 'GGG' 'abierta' '' '' @('- 2026-01-05 · Claude-Opus · Se abre la etapa')
    Write-Task 'GGG' 'T01' 'en curso' '' @('| 2026-06-01 10:00 | Claude-Opus | tomada | x |')
    Write-Task 'GGG' 'T02' 'en curso' '' @('| 2026-06-03 | Claude-Opus | tomada | x |', '| 2026-06-05 14:10 | Claude-Opus | terminada | y |')
    Write-Task 'GGG' 'T03' 'disponible' '' @('| 2026-06-02 08:15 | Claude-Opus | liberada | x |')
    Write-Task 'GGG' 'T04' 'disponible' '' @()

    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir | Out-Null
    $html = [IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html'))

    # 1. Completed stages (Resumen list): newest closure first, then card date, then unknown is not listed there.
    $resumen = $html.Substring($html.IndexOf('<h2>Etapas completadas</h2>'))
    $resumen = $resumen.Substring(0, $resumen.IndexOf('</section>'))
    Assert-Order $resumen @('Cerrada el 2026-04-20 08:30', 'Cerrada el 2026-03-05 12:00') 'closed stages'
    Assert-True ($resumen -match 'Cerrada el 2026-04-20 \d{2}:\d{2} \(hace \d+ (d(í|&#237;)as|d(í|&#237;)a|horas|hora|minutos|minuto)\)') 'closure "hace" text'
    $titleOrder = @($resumen.IndexOf('BBB'), $resumen.IndexOf('AAA'))
    Assert-True ($titleOrder[0] -ge 0 -and $titleOrder[0] -lt $titleOrder[1]) 'BBB must come before AAA'

    # Historical tab: unverified closures, card date and unknown date last.
    $cccAt = $html.IndexOf('Cerrada el 2026-02-10 18:45')
    Assert-True ($cccAt -ge 0) 'card-based closure date shown'
    $cccText = $html.Substring($cccAt, 200)
    Assert-True ($cccText -match 'según la ficha') 'card-based closure labelled "según la ficha"'
    $unknownAt = $html.IndexOf('fecha de cierre desconocida', $html.IndexOf('<h2>Cierres pendientes de verificación</h2>'))
    Assert-True ($unknownAt -gt $cccAt) 'unknown closure date listed after dated ones'
    Assert-True ($html.IndexOf('Cerrada el 2026-02-10 18:45', $html.IndexOf('<h2>Cierres pendientes de verificación</h2>')) -ge 0) 'card closure in pending list'

    # 2. Active stage cards by last activity: FFF (05-20), EEE (05-12 17:20), CCC/DDD/GGG appear by their own activity.
    $cards = $html.Substring($html.IndexOf('<section class="cards">'))
    $cards = $cards.Substring(0, $cards.IndexOf('</section>'))
    Assert-True ($cards -match 'Última actividad: 2026-06-05 14:10 · GGG-T02 terminada \(Claude-Opus\)') 'GGG last activity text'
    Assert-True ($cards -match 'Última actividad: 2026-05-20 00:00 · Replanificada la etapa con nuevos criterios \(Claude-Sonnet\)') 'FFF ETAPA line text'
    Assert-True ($cards -match 'Última actividad: 2026-05-12 17:20 · EEE-T01 terminada \(Claude-Opus\)') 'EEE last activity text'
    Assert-Order $cards @('>GGG<', '>FFF<', '>EEE<') 'active stage cards'

    # 3. Tasks of GGG follow their code (SEG-T05); each still shows its own last activity.
    $board = $html.Substring($html.IndexOf('<h2>GGG'))
    $board = $board.Substring(0, $board.IndexOf('</section>'))
    Assert-Order $board @('GGG-T01', 'GGG-T02', 'GGG-T03', 'GGG-T04') 'task order'
    Assert-True ($board -match 'Última actividad 2026-06-05 14:10') 'task last activity still shown'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T04 ordering PASS'
