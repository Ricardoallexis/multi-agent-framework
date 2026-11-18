# SEG-T21: the viewer recognizes the estado «congelada» in task cards and stage cards. A frozen task or stage is shown apart: it is not
# pending, not available, not counted in the stage total, it makes no item in «Qué tienes que hacer ahora» and it raises no warning.
# Synthetic channel only.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t21-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$now = [datetime]'2026-11-18T12:00:00'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
$ast = [Management.Automation.Language.Parser]::ParseFile($viewer, [ref]$null, [ref]$null)
$flagFunction = $ast.Find({ param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Update-RowWatchFlags' }, $true)
Invoke-Expression $flagFunction.Extent.Text
$script:EnCursoWatch = $false; $script:SinActividadWatch = $false
Update-RowWatchFlags 'congelada' $null
Assert-True (-not $script:EnCursoWatch -and -not $script:SinActividadWatch) 'a frozen row does not schedule work or inactivity refresh'
Update-RowWatchFlags 'en curso' $null
Assert-True ($script:EnCursoWatch -and $script:SinActividadWatch) 'an active row still schedules both refreshes'
$script:EnCursoWatch = $false; $script:SinActividadWatch = $false
Update-RowWatchFlags 'en curso' 'synthetic wait'
Assert-True ($script:EnCursoWatch -and -not $script:SinActividadWatch) 'an active row with Espera does not schedule inactivity refresh'
function Write-Card([string]$stage, [string]$code, [string]$state, [string]$title, [string]$espera = '-') {
    $lines = @("# $stage-$code · $title", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Agente | Claude-Opus |', '| Preferente | Cualquiera |', '| Depende de | - |', '| Parche | ninguno |', '| Commit | - |', "| Espera | $espera |",
        '', '## Qué falta', '', 'Nada', '', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', '| 2026-11-18 10:00 | Claude-Opus | creada | x |')
    Set-Content (Join-Path $channel "tareas\$stage\$code-x.md") $lines -Encoding utf8
}
function Write-Stage([string]$stage, [string]$state, [string]$tasks) {
    Set-Content (Join-Path $channel "tareas\$stage\ETAPA.md") @("# $stage · Etapa de prueba $stage", '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', "| Estado | $state |", '', '## Fases de tareas', '', '| Fase | Tareas | Descripción |', '|---|---|---|', "| Fase 1 | $tasks | x |", '', '## Historial de decisiones', '', '- 2026-11-18 · Claude-Opus · prueba') -Encoding utf8
}
function Run-Viewer {
    Set-ViewerPlanCache $channel $now
    $out = & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -NowOverride $now 3>&1 4>&1 5>&1 6>&1
    $warnings = @($out | Where-Object { $_ -is [System.Management.Automation.WarningRecord] } | ForEach-Object { $_.Message })
    $html = [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))
    [pscustomobject]@{ Html = $html; Warnings = $warnings }
}
# One stage <details> of the Tareas tab, from its title up to the next stage or the frozen heading.
function Get-Section([string]$html, [string]$title) {
    $m = [regex]::Match($html, "(?s)<details class=""panel board seg-stage""[^>]*>\s*<summary><h2>$title.*?</details>\s*(?=<details class=""panel board seg-stage""|<h2 class=""seg-frozen-title""|</div>)")
    Assert-True $m.Success "stage section $title present"
    $m.Value
}

try {
    New-Item -ItemType Directory -Force $repo, (Join-Path $channel 'tareas\AAA'), (Join-Path $channel 'tareas\BBB'), (Join-Path $channel 'tareas\CCC'), (Join-Path $channel 'orquestacion'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    Set-Content (Join-Path $channel 'AGENTES.md') @('| Agente | Modelo | Aplicación | Estado | Último | Cursor | Sesión |', '|---|---|---|---|---|---|---|', '| Claude-Opus | Opus | Claude | ACTIVO | x | c | s1 |') -Encoding utf8
    git -C $repo init -q | Out-Null

    # AAA (abierta): one frozen task, one available. BBB (congelada): an available task. CCC (abierta): every task frozen.
    Write-Stage 'AAA' 'abierta' 'T01, T02'
    Write-Card 'AAA' 'T01' 'congelada' 'Tarea congelada uno'
    Write-Card 'AAA' 'T02' 'disponible' 'Tarea disponible dos'
    Write-Stage 'BBB' 'congelada' 'T01'
    Write-Card 'BBB' 'T01' 'en curso' 'Tarea de etapa congelada' '¿Decisión sintética pendiente?'
    Write-Stage 'CCC' 'abierta' 'T01'
    Write-Card 'CCC' 'T01' 'congelada' 'Unica congelada'

    $run = Run-Viewer
    $html = $run.Html
    Assert-True ($run.Warnings.Count -eq 0) "no warnings for frozen cards or stages, got: $($run.Warnings -join ' | ')"

    # The frozen task is recognized: its own state badge, not pending, not available.
    $aaa = Get-Section $html 'AAA'
    Assert-True ($aaa -match '<span class="state s-congelada">congelada</span>') 'the frozen task shows the state congelada'
    $rowFrozen = [regex]::Match($aaa, '(?s)<tr><td><a [^>]*>[^<]*Tarea congelada uno.*?</tr>').Value
    Assert-True ($rowFrozen -and $rowFrozen -notmatch 's-pendiente|s-disponible|Disponible|Bloqueada') 'the frozen task row is neither pendiente nor disponible'
    Assert-True ($aaa -match '0/1' -and $aaa -match '0 de 1 terminadas o integradas · 1 congeladas') 'the frozen task is not part of the stage total'
    Assert-True ($html -match '1 disponibles · 1 congeladas') 'the summary card counts one available and one frozen'
    Assert-True ($aaa -match '1 disponible · 1 congelada') 'the stage header lists congelada apart'
    # Its label only follows the non-frozen tasks (T02 is available).
    Assert-True ($aaa -match '<span class="state s-disponible">(?:Lista|Disponible)</span>') 'the stage label ignores the frozen task'

    # A stage where every task is frozen reads Congelada, not «En espera de dependencias».
    $ccc = Get-Section $html 'CCC'
    Assert-True ($ccc -match '<span class="state s-congelada">Congelada</span>' -and $ccc -notmatch 'En espera de dependencias') 'a stage with only frozen tasks reads Congelada'

    # The frozen stage is shown apart, after the active ones, with no card among the active stages and no pending item for the user.
    $frozenAt = $html.IndexOf('<h2 class="seg-frozen-title">Etapas congeladas</h2>')
    Assert-True ($frozenAt -ge 0) 'a section «Etapas congeladas» exists'
    $bbbAt = $html.IndexOf('BBB · Etapa de prueba BBB')
    Assert-True ($bbbAt -gt $frozenAt) 'the frozen stage BBB is inside the frozen section'
    Assert-True ($html.IndexOf('AAA · Etapa de prueba AAA') -lt $frozenAt) 'active stages come before the frozen section'
    $bbb = [regex]::Match($html.Substring($bbbAt - 200), '(?s)<details class="panel board seg-stage".*?</details>').Value
    Assert-True ($bbb -match '<span class="state s-congelada">Congelada</span>' -and $bbb -notmatch '<details[^>]* open>' -and $bbb -cnotmatch 'Lista|Disponible' -and $bbb -match 'etapa congelada') 'BBB reads Congelada, collapsed, with its closure line'
    Assert-True ($bbb -match 'Etapa congelada · estado en la ficha: en curso') 'the tasks of a frozen stage read congelada and keep their card state as a note'
    Assert-True ($bbb -notmatch 'Decisión sintética pendiente' -and $html -notmatch 'espera-sin-actividad') 'a frozen stage creates neither a task Espera badge nor an inactivity alert'
    Assert-True ($bbb -notmatch '<span class="state s-disponible">' -and $bbb -notmatch 'cierre no verificado') 'BBB is not available and not an unverified closure'
    Assert-True ($html -match 'trabaja en AAA' -and $html -notmatch 'trabaja en BBB') 'the available task of the active stage is a to-do item, the one of the frozen stage is not'
    Assert-True ($html -notmatch '<span class="gate">BBB</span>') 'the frozen stage makes no active summary card'
    Assert-True ($html -notmatch 'Cierre no verificado') 'a frozen stage is not an unverified closure'

    # Without any frozen stage the section does not appear.
    Remove-Item (Join-Path $channel 'tareas\BBB') -Recurse -Force
    $run = Run-Viewer
    Assert-True ($run.Html -notmatch '<h2 class="seg-frozen-title">') 'no frozen section without frozen stages'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T21 estado congelada PASS'
