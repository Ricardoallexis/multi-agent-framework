# SEG-T09: collapsible stages and phases (<details>), summaries, buttons and remembered state script.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t09-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Get-Tag([string]$html, [string]$id) {
    $m = [regex]::Match($html, "<details[^>]*data-seg-id=""$([regex]::Escape($id))""[^>]*>")
    Assert-True $m.Success "details present for $id"
    $m.Value
}
function Get-Summary([string]$html, [string]$id) {
    $m = [regex]::Match($html, "<details[^>]*data-seg-id=""$([regex]::Escape($id))""[^>]*>\s*<summary>(.*?)</summary>", 'Singleline')
    Assert-True $m.Success "summary present for $id"
    [System.Net.WebUtility]::HtmlDecode($m.Groups[1].Value)
}
function Write-Stage([string]$name, [string]$state, [string[]]$extra, [string[]]$decisions) {
    $dir = Join-Path $channel "tareas\$name"
    New-Item -ItemType Directory -Force $dir | Out-Null
    Set-Content (Join-Path $dir 'ETAPA.md') (@("# $name · Etapa de prueba", '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', "| Estado | $state |") + $extra + @('', '## Historial de decisiones', '') + $decisions) -Encoding utf8
}
function Write-Card([string]$stage, [string]$code, [string]$state, [string]$agent, [string]$when) {
    $lines = @("# $stage-$code Tarea $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Depende de | - |', '| Parche | ninguno |', "| Agente | $agent |",
        '', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', "| $when | Claude-Opus | creada | x |")
    Set-Content (Join-Path $channel "tareas\$stage\$code-x.md") $lines -Encoding utf8
}

try {
    New-Item -ItemType Directory -Force $repo, (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    git -C $repo init -q | Out-Null

    # Active stage: Fase A complete, Fase B in progress, Fase C not started.
    Write-Stage 'AAA' 'abierta' @('', '## Fases de tareas', '', '| Fase | Tareas | Descripción |', '|---|---|---|',
        '| Fase A | T01, T02 | Base |', '| Fase B | T03, T04 | Núcleo |', '| Fase C | T05 | Cierre |') @('- 2026-05-01 · Claude-Opus · Se abre la etapa')
    Write-Card 'AAA' 'T01' 'integrada' 'Claude-Opus' '2026-05-02 10:00'
    Write-Card 'AAA' 'T02' 'integrada' 'Claude-Opus' '2026-05-03 11:30'
    Write-Card 'AAA' 'T03' 'en curso' 'Claude-Opus' '2026-05-04 09:15'
    Write-Card 'AAA' 'T04' 'disponible' '-' '2026-05-04 09:20'
    Write-Card 'AAA' 'T05' 'disponible' '-' '2026-05-04 09:25'
    # Card-closed stage (no verified commit).
    Write-Stage 'ZZZ' 'cerrada' @('', '## Fases de tareas', '', '| Fase | Tareas | Descripción |', '|---|---|---|', '| Fase Z | T01 | Todo |') @('- 2026-02-10 18:45 · Claude-Sonnet · Etapa cerrada por el usuario')
    Write-Card 'ZZZ' 'T01' 'integrada' 'Claude-Opus' '2026-02-09 08:00'

    & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir | Out-Null
    $html = [IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html'))

    # Initial open/closed state.
    Assert-True ((Get-Tag $html 'seg:AAA') -match '\sopen') 'active stage open'
    Assert-True ((Get-Tag $html 'seg:AAA:fase-a') -notmatch '\sopen') 'complete phase closed'
    Assert-True ((Get-Tag $html 'seg:AAA:fase-b') -match '\sopen') 'current phase open'
    Assert-True ((Get-Tag $html 'seg:AAA:fase-c') -notmatch '\sopen') 'later phase closed'
    Assert-True ((Get-Tag $html 'seg:ZZZ') -notmatch '\sopen') 'completed stage closed'
    Assert-True ((Get-Tag $html 'seg:ZZZ:fase-z') -notmatch '\sopen') 'phase of completed stage closed'
    Assert-True ([regex]::Matches($html, '<details[^>]*seg-stage').Count -eq 2) 'one details per stage'
    Assert-True ([regex]::Matches($html, '<details[^>]*seg-phase').Count -eq 4) 'one details per phase'

    # Summary lines.
    $a = Get-Summary $html 'seg:AAA'
    Assert-True ($a -match '2 integrada' -and $a -match '1 en curso' -and $a -match '2 disponible') 'stage counts by state'
    Assert-True ($a -match 'Última actividad 2026-05-04 09:25') 'stage last activity'
    Assert-True ($a -match 'trabajando: Claude-Opus en T03') 'active worker'
    Assert-True ((Get-Summary $html 'seg:AAA:fase-a') -match 'Fase A.*2 de 2 terminadas o integradas') 'phase A summary'
    Assert-True ((Get-Summary $html 'seg:AAA:fase-b') -match 'Fase B.*0 de 2 terminadas o integradas') 'phase B summary'
    $z = Get-Summary $html 'seg:ZZZ'
    Assert-True ($z -match 'Cerrada el 2026-02-10 18:45') 'closed stage date'
    Assert-True ($z -match '1 integrada') 'closed stage counts'

    # Buttons: two per tab (tareas, historico) plus two per stage.
    Assert-True ([regex]::Matches($html, 'data-seg-scope="tab"').Count -eq 4) 'tab buttons'
    Assert-True ([regex]::Matches($html, 'data-seg-scope="stage"').Count -eq 4) 'stage buttons'
    Assert-True ($html -match '>Expandir todo<' -and $html -match '>Compactar todo<') 'button labels'

    # Script: remembered state, guarded by try/catch.
    $script = [regex]::Match($html, '(?s)<script>(.*)</script>').Groups[1].Value
    Assert-True ($script -match 'localStorage') 'script uses localStorage'
    Assert-True ($html -match 'data-seg-id="seg:') 'ids use the seg: prefix'
    Assert-True ($script -match '(?s)try \{[^}]*localStorage[^}]*\} catch') 'localStorage wrapped in try/catch'
    Assert-True ([regex]::Matches($html, '<script>').Count -eq 1) 'still a single script'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T09 collapse PASS'
