# SEG-T08: Tokens cell with delegated workers (exact / estimated / no data) and "Total de la tarea".
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t08-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$arrow = [string][char]0x21B3
$dot = [string][char]0x00B7

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Write-Card([string]$code, [string]$tokens) {
    $lines = @("# TOK-$code Tarea $code", '', '| Campo | Valor |', '|---|---|', '| Estado | integrada |', '| Depende de | - |', '| Parche | ninguno |', '| Agente | Claude-Opus |', "| Tokens | $tokens |",
        '', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', '| 2026-05-02 10:00 | Claude-Opus | creada | x |')
    Set-Content (Join-Path $channel "tareas\TOK\$code-x.md") $lines -Encoding utf8
}
# The HTML of the Tokens cell of one task (cells end at the next </tr> of that row).
function Get-Row([string]$html, [string]$code) {
    $m = [regex]::Match($html, "(?s)<tr>(?:(?!</tr>).)*TOK-$code(?:(?!</tr>).)*</tr>")
    Assert-True $m.Success "row for $code"
    $m.Value
}

try {
    New-Item -ItemType Directory -Force $repo, (Join-Path $channel 'tareas\TOK'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    Set-Content (Join-Path $channel 'tareas\TOK\ETAPA.md') @('# TOK · Etapa de prueba', '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', '| Estado | abierta |', '', '## Historial de decisiones', '', '- 2026-05-01 · Claude-Opus · Se abre') -Encoding utf8
    git -C $repo init -q | Out-Null

    $old = "Claude-Opus: total 1500000 = entrada 100 + cacheados 1400000 + salida 99900 (medido) $dot llamadas 10 $dot contexto medio 140000 $dot acierto 99.9%; Copilot: no disponible"
    Write-Card 'T01' $old
    $w1 = "$arrow Claude Sonnet 5.5 (delegado por Claude-Opus, exacto): total 250000 = entrada 50 + cacheados 200000 + salida 49950"
    $w2 = "$arrow gpt-5.5 (preview) (delegado por Claude-Opus, estimado por ventana): total 8000 = entrada 1000 + cacheados 3000 + salida 4000"
    Write-Card 'T02' ("Claude-Opus: total 1000000 = entrada 10 + cacheados 900000 + salida 99990 (medido); $w1; $w2; Total de la tarea: 1258000")
    Write-Card 'T03' "Codex: total 2000 = entrada 1000 + cacheados 500 + salida 500 (medido); $arrow gpt-6-luna (delegado por Codex): sin datos; $arrow gpt-6-luna (delegado por Codex, sin datos); Total de la tarea: 2000"

    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir | Out-Null
    $html = [IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html'))
    $arrowHtml = '&#8627;'

    # (a) old format: unchanged rendering, no worker/total markup.
    $a = Get-Row $html 'T01'
    Assert-True ($a -match 'Claude-Opus: <strong>1\.5 M</strong>') 'old: orchestrator total'
    Assert-True ($a -match 'Copilot: <span class="meta">no disponible</span>') 'old: Copilot entry kept'
    Assert-True ($a -notmatch 'token-worker' -and $a -notmatch 'Total de la tarea') 'old: no worker or total markup'

    # (b) orchestrator + exact worker + estimated worker + total.
    $b = Get-Row $html 'T02'
    Assert-True ($b -match 'Claude-Opus: <strong>1\.0 M</strong>') 'b: orchestrator'
    Assert-True ($b -match "token-worker[^>]*margin-left[^>]*>$arrowHtml Claude Sonnet 5\.5 <span class=""meta"">\(delegado por Claude-Opus\)</span>: <strong>250 k</strong> <span class=""meta"">\(exacto") 'b: exact worker indented'
    Assert-True ($b -match "token-worker[^>]*>$arrowHtml gpt-5\.5 \(preview\) .*?<strong>8 k</strong> <span class=""meta"">\(estimado") 'b: estimated worker, name with parentheses'
    Assert-True ([regex]::Matches($b, 'class="token-worker"').Count -eq 2) 'b: both workers kept'
    Assert-True ($b -match 'Total de la tarea: 1\.3 M') 'b: task total'
    Assert-True ($b.IndexOf('Claude-Opus:') -lt $b.IndexOf('Claude Sonnet 5.5') -and $b.IndexOf('gpt-5.5') -lt $b.IndexOf('Total de la tarea')) 'b: order orchestrator, workers, total'

    # (c) worker without data (both spellings), nothing dropped.
    $c = Get-Row $html 'T03'
    Assert-True ([regex]::Matches($c, 'class="token-worker"').Count -eq 2) 'c: both no-data workers kept'
    Assert-True ($c -match 'gpt-6-luna <span class="meta">\(delegado por Codex\)</span>: <span class="meta">sin datos</span>') 'c: sin datos tag'
    Assert-True ($c -match 'Codex: <strong>2 k</strong>' -and $c -match 'Total de la tarea: 2 k') 'c: orchestrator and total'

    # Worker tokens do not inflate the orchestrator's agent-card sum (1.5 M + 1.0 M = 2.5 M, never plus 258 k).
    if ($html -match 'Tokens en tareas</h4><p><strong>2\.5 M</strong>') { Write-Host 'agent card: orchestrator sum excludes workers' }
    Assert-True ($html -notmatch 'Tokens en tareas</h4><p><strong>2\.8 M</strong>') 'workers not double counted into the orchestrator'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T08 tokens PASS'
