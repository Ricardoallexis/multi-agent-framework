# SEG-T07: generated "Qué tienes que hacer ahora" list (+ hand-written "Notas") on the Resumen tab.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t07-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function New-Channel([string]$name) {
    $channel = Join-Path $temp $name
    New-Item -ItemType Directory -Force (Join-Path $channel 'tareas'), (Join-Path $channel 'parches\pendientes') | Out-Null
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    $channel
}
function Write-Stage([string]$channel, [string]$name, [string]$authorized, [string[]]$extra = @()) {
    $dir = Join-Path $channel "tareas\$name"
    New-Item -ItemType Directory -Force $dir | Out-Null
    Set-Content (Join-Path $dir 'ETAPA.md') (@("# $name · Etapa de prueba", '', '| Campo | Valor |', '|---|---|', "| Autorizada | $authorized |", '| Estado | abierta |') + $extra) -Encoding utf8
}
function Write-Card([string]$channel, [string]$stage, [string]$code, [string]$state, [string]$deps, [string[]]$extra = @()) {
    $lines = @("# $stage-$code Tarea $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", "| Depende de | $deps |", '| Parche | ninguno |', '| Agente | - |',
        '', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', '| 2026-10-10 09:00 | X | creada | x |') + $extra
    Set-Content (Join-Path $channel "tareas\$stage\$code-x.md") $lines -Encoding utf8
}
function Get-Resumen([string]$channel) {
    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir | Out-Null
    $html = [IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html'))
    $start = $html.IndexOf('Qué tienes que hacer ahora</h2>')
    Assert-True ($start -ge 0) 'todo section present'
    $section = $html.Substring($start)
    [System.Net.WebUtility]::HtmlDecode($section.Substring(0, $section.IndexOf("<h2>Etapas activas</h2>")))
}

try {
    New-Item -ItemType Directory -Force $repo | Out-Null
    git -C $repo init -q | Out-Null

    # Full channel.
    $ch = New-Channel 'full'
    Write-Stage $ch 'AAA' 'sí — usuario'
    Write-Card $ch 'AAA' 'T01' 'integrada' '-'
    Write-Card $ch 'AAA' 'T02' 'disponible' 'T01'
    Write-Card $ch 'AAA' 'T03' 'disponible' 'T04'
    Write-Card $ch 'AAA' 'T04' 'en curso' '-'
    Write-Card $ch 'AAA' 'T05' 'disponible' '-' @('', 'Pide al usuario: elegir X o Y')
    Write-Stage $ch 'BBB' 'T01–T03 pendientes de autorización del usuario. Lo demás sí.'
    Write-Stage $ch 'CCC' 'no'
    Write-Card $ch 'CCC' 'T01' 'disponible' '-'
    Set-Content (Join-Path $ch 'PROPUESTAS.md') @('# Propuestas', '', '| ID | Tema y alcance | Estado | Acción vigente | Evidencia | Sustituida por |', '|---|---|---|---|---|---|',
        '| P-101 | Tema alfa | propuesta | a | e | - |',
        '| P-102 | Tema beta aprobado | aprobada | a | e | - |',
        '| P-103 | Tema gamma | en_revision | a | e | - |') -Encoding utf8
    Set-Content (Join-Path $ch 'parches\pendientes\001-uno.patch') 'x' -Encoding utf8
    Set-Content (Join-Path $ch 'parches\pendientes\002-dos.patch') 'y' -Encoding utf8
    Set-Content (Join-Path $ch 'RESUMEN.md') @('# Resumen', '', '## Qué tienes que hacer ahora', '', 'Nota escrita a mano ZETA.', '', '## Bitácora', 'x') -Encoding utf8

    $s = Get-Resumen $ch
    Assert-True ($s -match 'Tareas esperando tu asignación') 'group 1 heading'
    Assert-True ($s -match 'AAA-T02 · Tarea T02 · di «trabaja en AAA»'.Replace('«', '«')) 'available released card listed'
    Assert-True ($s -match 'AAA-T05 · Tarea T05') 'second available card listed'
    Assert-True ($s -notmatch 'AAA-T03') 'card blocked by dependency not listed'
    Assert-True ($s -notmatch 'AAA-T04') 'en curso card not listed as assignable'
    Assert-True ($s -notmatch 'CCC-T01 · Tarea') 'card of unauthorized stage not assignable'
    Assert-True ($s -match 'file:///') 'links are file URLs'
    Assert-True ($s -match 'Pendiente de tu autorización') 'group 2 heading'
    Assert-True ($s -match '>BBB</a> · T01.T03 pendientes de autorización') 'BBB fragment shown'
    Assert-True ($s -match '>CCC</a> · no') 'unauthorized stage shown'
    Assert-True ($s -notmatch '>AAA</a> ·') 'authorized stage not in group 2'
    Assert-True ($s -match 'P-101</a> · Tema alfa · propuesta') 'propuesta listed'
    Assert-True ($s -match 'P-103</a> · Tema gamma · en_revision') 'en_revision listed'
    Assert-True ($s -notmatch 'P-102') 'aprobada not listed'
    Assert-True ($s.IndexOf('P-103') -lt $s.IndexOf('P-101')) 'proposals newest first'
    Assert-True ($s -match '001-uno\.patch' -and $s -match '002-dos\.patch') 'both patches listed'
    Assert-True ($s -match 'Decisiones que te piden') 'group 5 heading'
    Assert-True ($s -match 'Pide al usuario: elegir X o Y') 'marker line shown'
    Assert-True ($s -match '<h3>Notas</h3>' -and $s -match 'Nota escrita a mano ZETA') 'hand-written notes under Notas'
    Assert-True ($s.IndexOf('Notas') -gt $s.IndexOf('Decisiones que te piden')) 'Notas after generated list'
    Assert-True ($s -notmatch 'Nada pendiente') 'no empty state when items exist'

    # Empty channel.
    $ce = New-Channel 'empty'
    Write-Stage $ce 'AAA' 'sí'
    Write-Card $ce 'AAA' 'T01' 'integrada' '-'
    Set-Content (Join-Path $ce 'RESUMEN.md') '# Resumen' -Encoding utf8
    $e = Get-Resumen $ce
    Assert-True ($e -match 'Nada pendiente de tu parte\.') 'empty state'
    Assert-True ($e -notmatch '<h3>') 'no group headings or Notas when empty'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T07 todo PASS'
