# SEG-T12: Trabajaron column shows, per delegated worker, a short tag and (on click) piece, result, times and tokens;
# registrar-delegacion.ps1 -Etiqueta / -Integracion rows are read, and integration activity is shown on delivered tasks.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t12-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$subRoot = Join-Path $temp 'projects'
$subDir = Join-Path $subRoot 'proj\session\subagents'
$now = [datetime]'2026-10-11T12:00:00'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Utc([datetime]$local) { $local.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') }
# Subagent transcript whose last record is a tool_use that was not answered (a running command).
function New-RunningSubagent([string]$id, [string]$command, [int]$minutesAgo) {
    $lines = @()
    $lines += (@{ type = 'user'; timestamp = (Utc $now.AddMinutes(-$minutesAgo - 2)); message = @{ role = 'user'; content = 'SECRET-CONTENT' } } | ConvertTo-Json -Compress -Depth 6)
    $lines += (@{ type = 'assistant'; timestamp = (Utc $now.AddMinutes(-$minutesAgo)); message = @{ role = 'assistant'; content = @(@{ type = 'tool_use'; id = "tu_$id"; name = 'Bash'; input = @{ command = $command } }) } } | ConvertTo-Json -Compress -Depth 8)
    $path = Join-Path $subDir "agent-$id.jsonl"
    Set-Content $path $lines -Encoding utf8
    (Get-Item $path).LastWriteTime = $now.AddMinutes(-$minutesAgo)
}
function Write-Card([string]$code, [string]$state, [string]$agent, [string[]]$rows, [string]$tokens = '') {
    $lines = @("# AAA-$code Tarea $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Depende de | - |', '| Parche | ninguno |', "| Agente | $(if ($agent -eq 'Claude-Opus') { 'Claude' } else { $agent }) |")
    if ($tokens) { $lines += "| Tokens | $tokens |" }
    $lines += @('', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', "| 2026-10-11 10:00 | $agent | creada | x |") + $rows
    Set-Content (Join-Path $channel "tareas\AAA\$code-x.md") $lines -Encoding utf8
}
function Get-Row([string]$html, [string]$code) {
    $m = [regex]::Match($html, "(?s)<tr><td><a [^>]*>AAA-$code Tarea $code</a>.*?</tr>")
    Assert-True $m.Success "row of $code present"
    $m.Value
}
# The Trabajaron cell is the 8th <td> of the row.
function Get-Cell([string]$row) { ([regex]::Matches($row, '(?s)<td>(.*?)</td>') | ForEach-Object { $_.Groups[1].Value })[7] }

try {
    New-Item -ItemType Directory -Force $repo, $subDir, (Join-Path $channel 'tareas\AAA'), (Join-Path $channel 'orquestacion\AAA'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    git -C $repo init -q | Out-Null
    Set-Content (Join-Path $channel 'tareas\AAA\ETAPA.md') @('# AAA · Etapa de prueba', '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', '| Estado | abierta |', '', '## Fases de tareas', '', '| Fase | Tareas | Descripción |', '|---|---|---|', '| Fase 1 | T01, T02, T03, T04, T05, T06, T07, T08, T09, T10, T11 | x |', '', '## Historial de decisiones', '', '- 2026-10-11 · Claude-Opus · Se abre la etapa') -Encoding utf8

    New-RunningSubagent 'integ1' 'pwsh integrar-sin-ventanas.ps1 -Agente Claude-Opus' 10
    $sonnetTokens = 'Claude-Opus: total 100 = entrada 10 + cacheados 80 + salida 10 (medido); ↳ Claude Sonnet 5.5 (delegado por Claude-Opus, exacto): total 2326270 = entrada 186061 + cacheados 2139919 + salida 290; Total de la tarea: 2326370'
    # T01: label, returned, tokens.
    Write-Card 'T01' 'en curso' 'Claude-Opus' @(
        '| 2026-10-11 11:00 | Claude-Opus | delegada | Claude Sonnet 5.5 (T2, esfuerzo bajo, etiqueta inventario): listar los módulos de la consola actual |',
        '| 2026-10-11 11:30 | Claude-Opus | devuelta | Claude Sonnet 5.5: inventario listo, 12 módulos |') $sonnetTokens
    # T02: no label -> first words of the piece; old row format; no tokens entry.
    Write-Card 'T02' 'en curso' 'Claude-Opus' @('| 2026-10-11 11:10 | Claude-Opus | delegada | Claude Haiku 5.5 (T1, esfuerzo bajo): revisar la API de sesiones y escribir un informe |')
    # T03: two delegations of the same worker, one returned and one still open.
    Write-Card 'T03' 'en curso' 'Claude-Opus' @(
        '| 2026-10-11 09:00 | Claude-Opus | delegada | Claude Sonnet 5.5 (T2, etiqueta implementación): pieza A del parser |',
        '| 2026-10-11 09:40 | Claude-Opus | devuelta | Claude Sonnet 5.5: parser A listo |',
        '| 2026-10-11 10:30 | Claude-Opus | delegada | Claude Sonnet 5.5 (T2, etiqueta pruebas): pieza B de las pruebas |') $sonnetTokens
    # T04: a worker whose ↳ entry has no data.
    Write-Card 'T04' 'en curso' 'Claude-Opus' @('| 2026-10-11 11:00 | Claude-Opus | delegada | Claude Haiku 5.5 (T1): pieza sin datos |') 'Claude-Opus: total 5 = entrada 1 + cacheados 3 + salida 1 (medido); ↳ Claude Haiku 5.5 (delegado por Claude-Opus, sin datos); Total de la tarea: 5'
    # T05: worker only in ejecuciones.jsonl (older flow), with a label field.
    Write-Card 'T05' 'en curso' 'Codex' @()
    $jsonl = @(
        (@{ task = 'AAA-T05'; worker = 'GPT-6 Luna core'; requested_model = 'gpt-6-luna'; requested_effort = 'high'; reason = 'auditoría estática del helper'; status = 'delegated'; recorded_at = ([datetimeoffset][datetime]'2026-10-11T08:00:00').ToString('o'); orchestrator = 'Codex'; label = 'auditoría' } | ConvertTo-Json -Compress),
        (@{ task = 'AAA-T05'; worker = 'GPT-6 Luna core'; reason = 'PASS 31 checks'; status = 'reviewed_complete'; recorded_at = ([datetimeoffset][datetime]'2026-10-11T08:45:00').ToString('o'); orchestrator = 'Codex' } | ConvertTo-Json -Compress))
    Set-Content (Join-Path $channel 'orquestacion\AAA\ejecuciones.jsonl') $jsonl -Encoding utf8
    # T06: a delivered task integrated by another agent (registrar-delegacion.ps1 -Integracion), still running tests.
    Write-Card 'T06' 'terminada' 'Codex' @(
        '| 2026-10-11 11:20 | Codex | terminada | entregada |',
        '| 2026-10-11 11:45 | Claude-Opus | delegada | Claude Haiku 5.5 (T1, integración): integrar con integrar-sin-ventanas.ps1 [ref: agent-integ1] |')
    # T07: the integration already returned.
    Write-Card 'T07' 'integrada' 'Codex' @(
        '| 2026-10-11 10:50 | Codex | terminada | entregada |',
        '| 2026-10-11 11:00 | Claude-Opus | delegada | Claude Haiku 5.5 (T1, integración): integrar con integrar-sin-ventanas.ps1 [ref: agent-gone] |',
        '| 2026-10-11 11:41 | Claude-Opus | devuelta | Claude Haiku 5.5: commit abc1234, reporte OK |')
    # T08: a delivered task with a normal (non-integration) open delegation: nothing is active.
    Write-Card 'T08' 'terminada' 'Claude-Opus' @('| 2026-10-11 11:00 | Claude-Opus | delegada | Claude Sonnet 5.5 (T2): pieza normal olvidada |')
    # T09: label with a level-like word must not become the level; two-word label.
    Write-Card 'T09' 'en curso' 'Claude-Opus' @('| 2026-10-11 11:50 | Claude-Opus | delegada | Claude Haiku 5.5 (etiqueta T3 revisión): pieza con etiqueta rara |')
    # T10: no delegations at all.
    Write-Card 'T10' 'en curso' 'Claude-Opus' @()
    # T11: a long first words of the piece give a short tag.
    Write-Card 'T11' 'en curso' 'Claude-Opus' @('| 2026-10-11 11:55 | Claude-Opus | delegada | Claude Haiku 5.5: integrar con integrar-sin-ventanas.ps1 y avisar |')

    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots $subRoot -NowOverride $now | Out-Null
    $html = [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))

    # 1. With label: tag next to the worker; label and level/effort unchanged; details hold piece, result, hours and tokens.
    $c1 = Get-Cell (Get-Row $html 'T01')
    Assert-True ($c1 -match '<summary>Claude Sonnet 5\.5 \(T2, esfuerzo bajo\) <span class="state s-idle seg-tag">inventario</span></summary>') 'T01 summary: worker label plus tag'
    Assert-True ($c1 -notmatch 'etiqueta') 'T01 the raw "etiqueta" marker is not displayed'
    Assert-True ($c1 -match '<details class="seg-worker">') 'T01 click-to-open details'
    Assert-True ($c1 -match 'desde 11:00 hasta 11:30') 'T01 from and until'
    Assert-True ($c1 -match 'Pieza: listar los módulos de la consola actual') 'T01 piece'
    Assert-True ($c1 -match 'Resultado: inventario listo, 12 módulos') 'T01 result'
    Assert-True ($c1 -match 'Tokens: 2\.3 M · entrada 186 k · salida 290 · exacto') 'T01 tokens from the ↳ entry'
    Assert-True ([regex]::Matches($c1, 'Claude Sonnet 5\.5').Count -eq 1) 'T01 worker named once'
    Assert-True ($c1 -match 'orquestó Claude-Opus') 'T01 orchestrator line kept'

    # 2. Without label: first words of the piece; open delegation says so; no tokens line without a ↳ entry.
    $c2 = Get-Cell (Get-Row $html 'T02')
    Assert-True ($c2 -match 'seg-tag">revisar la API…</span>') 'T02 tag = first words of the piece'
    Assert-True ($c2 -match 'desde 11:10 · sin devolver' -or $c2 -match 'desde 11:10 · en curso') 'T02 open delegation'
    Assert-True ($c2 -notmatch 'Tokens:') 'T02 no tokens line'
    Assert-True ($c2 -match 'Haiku 5\.5 \(T1, esfuerzo bajo\)') 'T02 level and effort label kept'

    # 3. Several delegations of the same worker: one details with all of them.
    $c3 = Get-Cell (Get-Row $html 'T03')
    Assert-True ([regex]::Matches($c3, '<details class="seg-worker">').Count -eq 1) 'T03 one details for the worker'
    Assert-True ([regex]::Matches($c3, 'Claude Sonnet 5\.5').Count -eq 1) 'T03 worker named once'
    Assert-True ($c3 -match 'seg-tag">implementación</span> <span class="state s-idle seg-tag">pruebas</span>') 'T03 both tags'
    Assert-True ($c3 -match 'desde 09:00 hasta 09:40' -and $c3 -match 'Pieza: pieza A del parser' -and $c3 -match 'Resultado: parser A listo') 'T03 returned delegation'
    Assert-True ($c3 -match 'desde 10:30 · en curso · ' -and $c3 -match 'Pieza: pieza B de las pruebas') 'T03 open delegation'
    Assert-True ($c3.IndexOf('pieza A del parser') -lt $c3.IndexOf('pieza B de las pruebas')) 'T03 oldest first'
    Assert-True ([regex]::Matches($c3, 'Tokens:').Count -eq 1) 'T03 tokens once per worker'

    # 4. A ↳ entry without data.
    $c4 = Get-Cell (Get-Row $html 'T04')
    Assert-True ($c4 -match 'Tokens: sin datos') 'T04 tokens sin datos'

    # 5. Worker known only from ejecuciones.jsonl.
    $c5 = Get-Cell (Get-Row $html 'T05')
    Assert-True ($c5 -match 'seg-tag">auditoría</span>') 'T05 label from the JSONL'
    Assert-True ($c5 -match 'desde 08:00 hasta 08:45' -and $c5 -match 'Pieza: auditoría estática del helper' -and $c5 -match 'Resultado: PASS 31 checks') 'T05 JSONL times, piece and result'

    # 6. Integration on a delivered task (-Integracion): visible with its activity while it runs.
    $c6 = Get-Cell (Get-Row $html 'T06')
    Assert-True ($c6 -match 'seg-tag">integración</span>') 'T06 integration tag'
    Assert-True ($c6 -match 'desde 11:45 · en curso · Ejecutando prueba · desde 11:50') 'T06 activity in the Trabajaron cell'
    $cards = $html.Substring($html.IndexOf('<section class="cards">'))
    $cards = $cards.Substring(0, $cards.IndexOf('</section>'))
    Assert-True ($cards -match 'T06: integración · Claude Haiku 5\.5 \(T1\) desde 11:45 · Ejecutando prueba · desde 11:50') 'T06 activity in the Resumen card'
    Assert-True ($cards -notmatch 'T07:' -and $cards -notmatch 'T08:') 'finished integrations and non-integration delegations of delivered tasks are not activity'

    # 7. Integration returned (the task is already integrada).
    $c7 = Get-Cell (Get-Row $html 'T07')
    Assert-True ($c7 -match 'desde 11:00 hasta 11:41' -and $c7 -match 'Resultado: commit abc1234, reporte OK') 'T07 returned integration'
    Assert-True ($html -notmatch 'T07: [^<]*integrar con') 'T07 not an activity line'

    # 8. Non-integration delegation on a delivered task stays out of the activity lines but is listed as worked.
    $c8 = Get-Cell (Get-Row $html 'T08')
    Assert-True ($c8 -match 'pieza normal olvidada' -and $c8 -match 'sin devolver') 'T08 listed, not active'
    Assert-True ($html -notmatch 'T08: [^<]*pieza normal') 'T08 no activity line'

    # 9. A label that looks like a level is not read as one.
    $c9 = Get-Cell (Get-Row $html 'T09')
    Assert-True ($c9 -match '<summary>Claude Haiku 5\.5 <span class="state s-idle seg-tag">T3 revisión</span></summary>') 'T09 label T3 revisión is not a level'
    Assert-True ($html -match 'T09: Claude Haiku 5\.5 desde 11:50') 'T09 activity line without level'

    # 11. The tag taken from the piece stays short.
    $c11 = Get-Cell (Get-Row $html 'T11')
    Assert-True ($c11 -match 'seg-tag">integrar con…</span>') "T11 short tag from a long piece: $c11"

    # 10. No delegations: nothing invented; and the subagent content is never printed.
    $c10 = Get-Cell (Get-Row $html 'T10')
    Assert-True ($c10 -notmatch 'details' -and $c10 -notmatch 'orquestó') 'T10 nothing invented'
    Assert-True ($html -notmatch 'SECRET-CONTENT') 'subagent content never printed'
    Assert-True ($html -notmatch '\[ref:') 'reference kept out of the page'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T12 worker work PASS'
