# SEG-T14: integration report names with seconds (yyyy-MM-dd_HHmmss_fff, probar.ps1 since ORQ-T37) next to the old
# yyyy-MM-dd_HHmm: stage closures are verifiable with either form, and the last reading of the summary is the latest
# by time across both forms.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t14-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$repoRoot = $repo
$channel = Join-Path $temp 'channel'
$subRoot = Join-Path $temp 'projects'
$integrationDir = Join-Path $channel 'pruebas\integracion'
$readsPath = Join-Path $integrationDir 'LECTURAS.md'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Get-Summary([string]$html, [string]$id) {
    $m = [regex]::Match($html, "<details[^>]*data-seg-id=""$([regex]::Escape($id))""[^>]*>\s*<summary>(.*?)</summary>", 'Singleline')
    Assert-True $m.Success "summary present for $id"
    $m.Groups[1].Value
}
function Get-Badge([string]$summary) {
    $m = [regex]::Match($summary, '<span class="state (s-[a-z-]+)">([^<]*)</span>')
    Assert-True $m.Success "state badge in: $summary"
    [pscustomobject]@{ Class = $m.Groups[1].Value; Text = [System.Net.WebUtility]::HtmlDecode($m.Groups[2].Value) }
}
function New-ClosedStage([string]$name, [string[]]$closureRows) {
    New-Item -ItemType Directory -Force (Join-Path $channel "tareas\$name") | Out-Null
    Set-Content (Join-Path $channel "tareas\$name\ETAPA.md") (@("# $name · Etapa de prueba", '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', '| Estado | cerrada |') + $closureRows + @('', '## Historial de decisiones', '', '- 2026-10-11 · Claude-Opus · Se abre la etapa')) -Encoding utf8
    $lines = @("# $name-T01 Tarea T01", '', '| Campo | Valor |', '|---|---|', '| Estado | integrada |', '| Depende de | - |', '| Parche | ninguno |', '| Agente | Claude |',
        '', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', '| 2026-10-11 10:00 | Claude-Opus | creada | x |')
    Set-Content (Join-Path $channel "tareas\$name\T01-x.md") $lines -Encoding utf8
}
function New-Report([string]$stem) { Set-Content (Join-Path $integrationDir "${stem}_integracion.md") '# Pruebas integracion' -Encoding utf8 }
function Set-Reads([string[]]$rows) { Set-Content $readsPath (@('| Reporte | Resultado | Pruebas |', '|---|---|---|') + $rows) -Encoding utf8 }
function Invoke-Viewer {
    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots $subRoot | Out-Null
    [IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html'))
}
# The text of "Latest integration test", decoded, and the class of its state badge.
function Get-LastReading([string]$html) {
    $m = [regex]::Match($html, '<strong>Latest integration test:</strong>\s*(.*?)</p>', 'Singleline')
    Assert-True $m.Success 'the Integración panel shows the last reading'
    $badge = [regex]::Match($m.Groups[1].Value, '<span class="state (s-[a-z-]+)">')
    # Text is what follows the state badge ("<id> · <N passed>").
    $rest = [regex]::Replace($m.Groups[1].Value, '<span class="state[^>]*>[^<]*</span>', '')
    [pscustomobject]@{ Class = if ($badge.Success) { $badge.Groups[1].Value } else { '' }; Text = ([System.Net.WebUtility]::HtmlDecode(([regex]::Replace($rest, '<[^>]+>', ''))) -replace '\s+', ' ').Trim() }
}
function Get-Closure([string]$stage) {
    $stageText = Get-Content -LiteralPath (Join-Path $channel "tareas\$stage\ETAPA.md") -Raw -Encoding utf8
    $task = [pscustomobject]@{ Id = "$stage-T01"; State = 'integrada'; Commit = ''; Patch = 'ninguno' }
    $readsText = Get-Content -LiteralPath $readsPath -Raw -Encoding utf8
    Get-StageClosure $stage $stageText @($task) $false $readsText
}

try {
    New-Item -ItemType Directory -Force $repo, $subRoot, (Join-Path $channel 'parches\pendientes'), $integrationDir | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    git -C $repo init -q | Out-Null
    git -C $repo -c user.name=t -c user.email=t@t.test commit -q --allow-empty -m base | Out-Null
    $head = (git -C $repo rev-parse HEAD).Trim()

    # verificar-etapas.ps1 (channel) loads the closure functions from the viewer by AST; do the same here, so a new
    # helper that Get-StageClosure needs but the verifier does not load would fail this test.
    $needed = 'Get-Field', 'Get-CommitToken', 'Resolve-IntegrationCommit', 'Test-TaskPatchRequired', 'Get-StageClosure'
    $tokens = $null; $parseErrors = $null
    $ast = [System.Management.Automation.Language.Parser]::ParseFile($viewer, [ref]$tokens, [ref]$parseErrors)
    Assert-True ($parseErrors.Count -eq 0) 'the viewer parses'
    $definitions = @($ast.FindAll({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] }, $true) | Where-Object { $_.Name -in $needed })
    Assert-True ($definitions.Count -eq $needed.Count) 'the five closure functions are defined in the viewer'
    foreach ($definition in $definitions) { . ([ScriptBlock]::Create($definition.Extent.Text)) }

    $newId = '2026-10-11_011321_133'
    $oldId = '2026-10-11_1200'
    $sameMinuteOld = '2026-10-11_0113'

    # ---- stages closed with each way of naming the report ----
    New-ClosedStage 'NEW' @("| Commit de cierre | ``$head`` |", "| Reporte de integración | ``${newId}_integracion.md`` |")
    New-ClosedStage 'OLD' @("| Commit de cierre | ``$head`` |", "| Reporte de integración | ``${oldId}_integracion.md`` |")
    New-ClosedStage 'PAT' @("| Commit de cierre | ``$head`` |", "| Reporte de integración | pruebas/integracion/${newId}_integracion.md (OK; 1071 passed, 1 skipped) |")
    New-ClosedStage 'BAR' @("| Commit de cierre | ``$head`` |", "| Reporte de integración | $newId |")
    New-ClosedStage 'CCF' @("| Commit de cierre | ``$head`` (integración $newId) |")
    New-ClosedStage 'MIS' @("| Commit de cierre | ``$head`` |", "| Reporte de integración | ``2026-10-11_020000_001_integracion.md`` |")
    New-ClosedStage 'RED' @("| Commit de cierre | ``$head`` |", "| Reporte de integración | ``2026-10-11_030000_002_integracion.md`` |")
    New-ClosedStage 'TRU' @("| Commit de cierre | ``$head`` |", "| Reporte de integración | ``2026-10-11_040000_003_integracion.md`` |")
    foreach ($stem in $newId, $oldId, $sameMinuteOld, '2026-10-11_030000_002') { New-Report $stem }
    # TRU names a report with seconds that does not exist; only its minute-only truncation does (and it is green).
    New-Report '2026-10-11_0400'
    Set-Reads @(
        "| ${oldId}_integracion.md | OK | ============ 100 passed in 1s ============ |",
        "| ${newId}_integracion.md | OK | =========== 1071 passed, 1 skipped, 1 warning in 341.53s (0:05:41) ============ |",
        "| ${sameMinuteOld}_integracion.md | OK | 500 passed in 1s |",
        '| 2026-10-11_030000_002_integracion.md | FALLO | ======= 1 failed, 809 passed in 295.93s ======= |',
        '| 2026-10-11_0400_integracion.md | OK | 500 passed in 1s |')

    foreach ($stage in 'NEW', 'PAT', 'BAR', 'CCF') {
        $closure = Get-Closure $stage
        Assert-True ($closure.Status -eq 'complete') "$stage with a seconds report is verifiable (got $($closure.Status): $($closure.Reason))"
        Assert-True ($closure.Report -ceq "${newId}_integracion.md") "$stage report is the full name with seconds (got $($closure.Report))"
    }
    $closure = Get-Closure 'OLD'
    Assert-True ($closure.Status -eq 'complete' -and $closure.Report -ceq "${oldId}_integracion.md") "the old yyyy-MM-dd_HHmm form still verifies (got $($closure.Status): $($closure.Reason))"
    $closure = Get-Closure 'MIS'
    Assert-True ($closure.Status -eq 'unknown' -and $closure.Reason -match 'does not exist') 'a seconds report that does not exist is unverifiable'
    $closure = Get-Closure 'RED'
    Assert-True ($closure.Status -eq 'unknown' -and $closure.Reason -match 'does not confirm') 'a seconds report recorded FALLO is unverifiable'
    $closure = Get-Closure 'TRU'
    Assert-True ($closure.Status -eq 'unknown' -and $closure.Reason -match 'does not exist') 'a seconds id is never cut to its minute: the green minute-only report does not stand in'

    # The page agrees with the function.
    $html = Invoke-Viewer
    foreach ($stage in 'NEW', 'OLD', 'PAT', 'BAR', 'CCF') {
        $badge = Get-Badge (Get-Summary $html "seg:$stage")
        Assert-True ($badge.Text -ceq 'Closed' -and $badge.Class -eq 's-integrada') "$stage shows as a verified closed stage (got '$($badge.Text)' $($badge.Class))"
    }
    foreach ($stage in 'MIS', 'RED', 'TRU') {
        $badge = Get-Badge (Get-Summary $html "seg:$stage")
        Assert-True ($badge.Text -ceq 'Closure unverified' -and $badge.Class -eq 's-unknown') "$stage shows as an unverified closure (got '$($badge.Text)' $($badge.Class))"
    }
    $plain = [System.Net.WebUtility]::HtmlDecode($html)
    Assert-True ($plain -match "href=""[^""]*${newId}_integracion\.md"">Successful integration report") 'the closed stage links the full report name'

    # ---- last reading: the latest by time across both forms, wherever it sits in the file ----
    function Assert-LastReading([string]$what, [string[]]$rows, [string]$stem, [string]$class, [string]$passed) {
        Set-Reads $rows
        $last = Get-LastReading (Invoke-Viewer)
        Assert-True ($last.Text -ceq "$stem · $passed") "$what : last reading '$stem · $passed' but was '$($last.Text)'"
        Assert-True ($last.Class -eq $class) "$what : badge class $class but was $($last.Class)"
    }
    $oldRow = '| 2026-10-10_2351_integracion.md | OK | ============ 786 passed, 1 skipped, 1 warning in 332.99s (0:05:32) ============ |'
    $newRow = "| ${newId}_integracion.md | OK | =========== 1071 passed, 1 skipped, 1 warning in 341.53s (0:05:41) ============ |"
    Assert-LastReading 'new after old' @($oldRow, $newRow) $newId 's-integrada' '1071 passed, 1 skipped, 1 warning'
    Assert-LastReading 'new before old in the file, old earlier in time' @($newRow, $oldRow) $newId 's-integrada' '1071 passed, 1 skipped, 1 warning'
    Assert-LastReading 'only new' @($newRow) $newId 's-integrada' '1071 passed, 1 skipped, 1 warning'
    Assert-LastReading 'only old' @($oldRow) '2026-10-10_2351' 's-integrada' '786 passed, 1 skipped, 1 warning'
    # The same minute: the old name has no seconds, the new one has 21 s, so the new one is later even listed first
    # (compared as full names the old one would win, because "_" sorts after the digits that follow HHmm).
    Assert-LastReading 'same minute' @("| ${newId}_integracion.md | FALLO | ======= 1 failed, 809 passed in 295.93s ======= |", "| ${sameMinuteOld}_integracion.md | OK | 500 passed in 1s |") $newId 's-requiere-cambios' '809 passed'
    # An old report later in the day than a new one wins, even listed before it.
    Assert-LastReading 'old later than new' @("| ${oldId}_integracion.md | OK | 100 passed in 1s |", $newRow) $oldId 's-integrada' '100 passed'
    # Two seconds reports of the same minute are told apart by seconds and milliseconds.
    Assert-LastReading 'seconds and milliseconds' @('| 2026-10-11_011321_900_integracion.md | OK | 11 passed in 1s |', '| 2026-10-11_011321_133_integracion.md | OK | 22 passed in 1s |', '| 2026-10-11_011320_999_integracion.md | OK | 33 passed in 1s |') '2026-10-11_011321_900' 's-integrada' '11 passed'
    # A tie goes to the later row.
    Assert-LastReading 'tie' @('| 2026-10-11_1200_integracion.md | OK | 1 passed in 1s |', '| 2026-10-11_120000_000_integracion.md | OK | 2 passed in 1s |') '2026-10-11_120000_000' 's-integrada' '2 passed'

    Set-Reads @()
    $last = Get-LastReading (Invoke-Viewer)
    Assert-True ($last.Text -ceq 'No reports recorded.') 'no readings: No reports recorded'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T14 report names PASS'
