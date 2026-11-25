# SEG-T13: one label function for phases and stages, the integration round in progress (parches\ronda.txt), the notice,
# and the agent in charge named in the stage header.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t13-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$subRoot = Join-Path $temp 'projects'
$roundFile = Join-Path $channel 'parches\ronda.txt'
$integrationDir = Join-Path $channel 'pruebas\integracion'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Get-Summary([string]$html, [string]$id) {
    $m = [regex]::Match($html, "<details[^>]*data-seg-id=""$([regex]::Escape($id))""[^>]*>\s*<summary>(.*?)</summary>", 'Singleline')
    Assert-True $m.Success "summary present for $id"
    $m.Groups[1].Value
}
# First state badge of a summary: text and class.
function Get-Badge([string]$summary) {
    $m = [regex]::Match($summary, '<span class="state (s-[a-z-]+)">([^<]*)</span>')
    Assert-True $m.Success "state badge in: $summary"
    [pscustomobject]@{ Class = $m.Groups[1].Value; Text = [System.Net.WebUtility]::HtmlDecode($m.Groups[2].Value) }
}
function New-Stage([string]$name, [string]$estado = 'abierta', [string[]]$extra = @()) {
    New-Item -ItemType Directory -Force (Join-Path $channel "tareas\$name") | Out-Null
    Set-Content (Join-Path $channel "tareas\$name\ETAPA.md") (@("# $name · Etapa de prueba", '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', "| Estado | $estado |") + $extra + @('', '## Historial de decisiones', '', '- 2026-10-11 · Claude-Opus · Se abre la etapa')) -Encoding utf8
}
function New-Task([string]$stage, [string]$code, [string]$state, [string]$patch = 'ninguno', [string]$deps = '-', [string[]]$rows = @()) {
    $lines = @("# $stage-$code Tarea $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", "| Depende de | $deps |", "| Parche | $patch |", '| Agente | Claude |',
        '', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', '| 2026-10-11 10:00 | Claude-Opus | creada | x |') + $rows
    Set-Content (Join-Path $channel "tareas\$stage\$code-x.md") $lines -Encoding utf8
}
function Invoke-Viewer {
    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots $subRoot | Out-Null
    [IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html'))
}
function Set-Time([string]$path, [string]$time) { (Get-Item -LiteralPath $path).LastWriteTime = [datetime]$time }

try {
    New-Item -ItemType Directory -Force $repo, $subRoot, (Join-Path $channel 'parches\pendientes'), $integrationDir | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    git -C $repo init -q | Out-Null
    git -C $repo -c user.name=t -c user.email=t@t.test commit -q --allow-empty -m base | Out-Null
    $head = (git -C $repo rev-parse HEAD).Trim()

    # ---- phases: every label, one stage with nine phases ----
    $phaseRows = 1..9 | ForEach-Object { $t = @{1 = 'T01, T02'; 2 = 'T03'; 3 = 'T04'; 4 = 'T05'; 5 = 'T06'; 6 = 'T07'; 7 = 'T08, T09'; 8 = 'T10'; 9 = 'T11' }[$_]; "| Fase $_ | $t | x |" }
    New-Stage 'PHA' 'abierta' (@('', '## Fases de tareas', '', '| Fase | Tareas | Descripción |', '|---|---|---|') + $phaseRows)
    New-Task 'PHA' 'T01' 'requiere cambios'
    New-Task 'PHA' 'T02' 'disponible'
    New-Task 'PHA' 'T03' 'terminada' '`501-x.patch`'
    New-Task 'PHA' 'T04' 'integrada'
    New-Task 'PHA' 'T05' 'terminada' 'ninguno'
    New-Task 'PHA' 'T06' 'terminada' '`601-x.patch`'
    New-Task 'PHA' 'T07' 'en curso'
    New-Task 'PHA' 'T08' 'integrada'
    New-Task 'PHA' 'T09' 'disponible'
    New-Task 'PHA' 'T10' 'disponible'
    New-Task 'PHA' 'T11' 'pendiente'

    # ---- stages: one per label ----
    New-Stage 'S01'; New-Task 'S01' 'T01' 'requiere cambios'; New-Task 'S01' 'T02' 'integrada'
    New-Stage 'S02'; New-Task 'S02' 'T01' 'terminada' '`501-x.patch`'; New-Task 'S02' 'T02' 'integrada'
    New-Stage 'S03'; New-Task 'S03' 'T01' 'integrada'; New-Task 'S03' 'T02' 'integrada'
    # S04: closed and verified (closing commit, report and green evidence).
    New-Stage 'S04' 'cerrada' @("| Commit de cierre | ``$head`` |", '| Reporte de integración | 2026-10-11_1200 |')
    New-Task 'S04' 'T01' 'integrada'
    Set-Content (Join-Path $integrationDir '2026-10-11_1200_integracion.md') '# Pruebas integracion' -Encoding utf8
    Set-Time (Join-Path $integrationDir '2026-10-11_1200_integracion.md') '2026-10-11 10:00'
    Set-Content (Join-Path $integrationDir 'LECTURAS.md') @('| Reporte | Resultado | Pruebas |', '|---|---|---|', '| 2026-10-11_1200_integracion.md | OK | 100 passed in 1s |') -Encoding utf8
    New-Stage 'S05' 'cerrada'; New-Task 'S05' 'T01' 'integrada'
    New-Stage 'S06'; New-Task 'S06' 'T01' 'terminada' '`601-x.patch`'; New-Task 'S06' 'T02' 'integrada'
    New-Stage 'S07'
    New-Task 'S07' 'T01' 'en curso' 'ninguno' '-' @('| 2026-10-11 11:00 | Claude-Opus | delegada | Claude Sonnet 5.5 (T2): pieza S07 |')
    New-Task 'S07' 'T02' 'en curso'
    New-Task 'S07' 'T03' 'disponible'
    New-Stage 'S08'; New-Task 'S08' 'T01' 'integrada'; New-Task 'S08' 'T02' 'disponible'
    New-Stage 'S09'; New-Task 'S09' 'T01' 'disponible'; New-Task 'S09' 'T02' 'disponible'
    New-Stage 'S10'; New-Task 'S10' 'T01' 'pendiente'; New-Task 'S10' 'T02' 'pendiente'

    # ---- round in progress: ronda.txt written at 12:34, the only report is older ----
    Set-Content $roundFile @('501-x.patch') -Encoding utf8
    Set-Time $roundFile '2026-10-11 12:34'
    $html = Invoke-Viewer

    $expected = [ordered]@{
        'seg:PHA:fase-1' = @('Changes requested', 's-requiere-cambios')
        'seg:PHA:fase-2' = @('Integrating · tests since 12:34', 's-en-curso')
        'seg:PHA:fase-3' = @('Phase complete', 's-integrada')
        'seg:PHA:fase-4' = @('Phase complete', 's-integrada')
        'seg:PHA:fase-5' = @('Phase completed · integration pending', 's-pendiente')
        'seg:PHA:fase-6' = @('In progress', 's-en-curso')
        'seg:PHA:fase-7' = @('Partial progress · 1 of 2', 's-pendiente')
        'seg:PHA:fase-8' = @('Available', 's-disponible')
        'seg:PHA:fase-9' = @('Waiting for dependencies', 's-idle')
        'seg:PHA' = @('Changes requested', 's-requiere-cambios')
        'seg:S01' = @('Changes requested', 's-requiere-cambios')
        'seg:S02' = @('Integrating · tests since 12:34', 's-en-curso')
        'seg:S03' = @('Stage complete · closure pending', 's-pendiente')
        'seg:S04' = @('Closed', 's-integrada')
        'seg:S05' = @('Closure unverified', 's-unknown')
        'seg:S06' = @('Stage completed · integration pending', 's-pendiente')
        'seg:S07' = @('In progress', 's-en-curso')
        'seg:S08' = @('Partial progress · 1 of 2', 's-pendiente')
        'seg:S09' = @('Available', 's-disponible')
        'seg:S10' = @('Waiting for dependencies', 's-idle')
    }
    foreach ($id in $expected.Keys) {
        $badge = Get-Badge (Get-Summary $html $id)
        Assert-True ($badge.Text -ceq $expected[$id][0]) "$id label '$($expected[$id][0])' but was '$($badge.Text)'"
        Assert-True ($badge.Class -eq $expected[$id][1]) "$id class $($expected[$id][1]) but was $($badge.Class)"
    }

    # The card's Estado is secondary data, never the headline; no header says "abierta" as its state.
    Assert-True ($html -notmatch '<span class="state [^"]*">abierta</span>') 'no stage header uses the card Estado as its state'
    $s07 = [System.Net.WebUtility]::HtmlDecode((Get-Summary $html 'seg:S07'))
    Assert-True ($s07 -match '<span class="meta seg-estado">stage Open</span>') 'S07 shows the card Estado as secondary data'
    Assert-True ((Get-Summary $html 'seg:S04') -notmatch 'seg-estado') 'a closed stage does not repeat its Estado'
    Assert-True ((Get-Summary $html 'seg:S05') -notmatch 'seg-estado') 'an unverified closure does not repeat its Estado'

    # The agent in charge is named next to the worker in the stage header.
    Assert-True ($s07 -match 'T01 · assigned to Claude · Claude Sonnet 5\.5 \(T2\) since 11:00 · No activity signal · pieza S07') 'S07 header: task, agent in charge, worker'
    Assert-True ($s07 -match 'working: Claude on T02') 'S07 header: task without delegation names the agent'

    # Integration column and notice while the round runs.
    foreach ($row in @([regex]::Matches($html, '<tr><td><a [^>]*>(?:S02|PHA)-T0[13] Tarea T0[13]</a>.*?</tr>', 'Singleline') | ForEach-Object Value)) {
        $text = [System.Net.WebUtility]::HtmlDecode($row)
        if ($text -match 'S02-T01') { Assert-True ($text -match 'Integrating · tests since 12:34') 'S02-T01 row: Integrating' }
        if ($text -match 'PHA-T03') { Assert-True ($text -match 'Integrating · tests since 12:34') 'PHA-T03 row: Integrating' }
    }
    $t06 = [System.Net.WebUtility]::HtmlDecode([regex]::Match($html, '<tr><td><a [^>]*>PHA-T06 Tarea T06</a>.*?</tr>', 'Singleline').Value)
    Assert-True ($t06 -match 'Integration required · pending' -and $t06 -notmatch 'Integrating') 'a patch outside the round is not being integrated'
    $plain = [System.Net.WebUtility]::HtmlDecode($html)
    Assert-True ($plain -match 'Generated during an integration \(round: 501-x\.patch\); data may change when it finishes') 'notice with the round'
    Assert-True ($plain.IndexOf('Generated during an integration') -lt $plain.IndexOf('<nav>')) 'notice at the start of the page'

    # ---- the report of the round appears (newer than the last application): tests are over ----
    Set-Content (Join-Path $integrationDir '2026-10-11_1300_integracion.md') '# Pruebas integracion' -Encoding utf8
    Set-Time (Join-Path $integrationDir '2026-10-11_1300_integracion.md') '2026-10-11 12:50'
    $html = Invoke-Viewer
    Assert-True ((Get-Badge (Get-Summary $html 'seg:S02')).Text -ceq 'Stage completed · integration pending') 'S02 after the report: integration pending'
    Assert-True ((Get-Badge (Get-Summary $html 'seg:PHA:fase-2')).Text -ceq 'Phase completed · integration pending') 'phase after the report: integration pending'
    Assert-True ($html -notmatch 'Integrating · tests') 'no Integrating label after the report'
    $t01 = [System.Net.WebUtility]::HtmlDecode([regex]::Match($html, '<tr><td><a [^>]*>S02-T01 Tarea T01</a>.*?</tr>', 'Singleline').Value)
    Assert-True ($t01 -match 'Integration required · pending') 'S02-T01 row after the report'
    Assert-True ([System.Net.WebUtility]::HtmlDecode($html) -match 'Generated during an integration \(round: 501-x\.patch\)') 'the round is still open (not committed): notice stays'

    # ---- a report older than the last application does not end the round ----
    Set-Time (Join-Path $integrationDir '2026-10-11_1300_integracion.md') '2026-10-11 12:00'
    $html = Invoke-Viewer
    Assert-True ((Get-Badge (Get-Summary $html 'seg:S02')).Text -ceq 'Integrating · tests since 12:34') 'older report: still integrating'

    # ---- the commit clears the round ----
    Remove-Item $roundFile
    $html = Invoke-Viewer
    Assert-True ([System.Net.WebUtility]::HtmlDecode($html) -notmatch 'Generated during an integration') 'no notice without ronda.txt'
    Assert-True ((Get-Badge (Get-Summary $html 'seg:S02')).Text -ceq 'Stage completed · integration pending') 'no round: integration pending'
    Set-Content $roundFile @('', '  ') -Encoding utf8
    $html = Invoke-Viewer
    Assert-True ([System.Net.WebUtility]::HtmlDecode($html) -notmatch 'Generated during an integration') 'no notice with an empty ronda.txt'
    Assert-True ($html -notmatch 'Integrating · tests') 'no label with an empty ronda.txt'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T13 status labels PASS'
