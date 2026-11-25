# GEM-T06 (viewer part): the Antigravity card shows the same metrics as the other applications.
#  1. Plan usage: Format-PlanUsage finds the `antigravity` app in the `multiagent usage-report plans` report (synthetic channel cache).
#  2. Tokens per worker, task, and totals: from the card's Tokens field when `tokens-tarea.ps1` wrote it, or from run outputs
#     (orquestacion\herramientas\agy-*.json / agy-*.ndjson), linked by the agy:<conversation_id> reference. One run per output name.
#  3. The model observed: init.model of the ndjson run.
# Synthetic folders only; the response of a run is never printed.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
$temp = Join-Path ([IO.Path]::GetTempPath()) ('gem-t06-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$agyRoot = Join-Path $temp 'agy'
$tools = Join-Path $channel 'orquestacion\herramientas'
$now = [datetime]'2026-11-18T12:00:00'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Json($value) { $value | ConvertTo-Json -Compress -Depth 8 }
function Utc([datetime]$local) { $local.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') }
function Write-Card([string]$code, [string]$state, [string]$owner, [string[]]$rows, [string]$tokens = '') {
    $lines = @("# AAA-$code · Tarea de prueba $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Depende de | - |', '| Parche | ninguno |', "| Agente | $owner |")
    if ($tokens) { $lines += "| Tokens | $tokens |" }
    $lines += @('', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', "| 2026-11-18 10:00 | $owner | creada | x |") + $rows
    Set-Content (Join-Path $channel "tareas\AAA\$code-x.md") $lines -Encoding utf8
}
function Deleg([string]$orchestrator, [string]$time, [string]$worker, [string]$ref, [string]$piece) {
    "| 2026-11-18 $time | $orchestrator | delegada | $worker (T2, esfuerzo medio): $piece [ref: $ref] |"
}
function Run-Viewer {
    & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots (Join-Path $temp 'p') -CopilotRoot (Join-Path $temp 'c') -CodexRoot (Join-Path $temp 'x') -AgyRoot $agyRoot -NowOverride $now | Out-Null
    $html = [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))
    $agents = $html.Substring($html.IndexOf('<div class="tab" id="agentes">'))
    [pscustomobject]@{ Html = $html; Agents = $agents.Substring(0, $agents.IndexOf('cloud-assignment-group')) }
}
function Get-AppCard([string]$agents, [string]$application) {
    $m = [regex]::Match($agents, "(?s)<section class=""panel agent-card"">\s*<div class=""card-head""><h2>$application</h2>.*?</section>")
    Assert-True $m.Success "card of $application present"
    $m.Value
}
function Get-WorkerBlock([string]$card, [string]$piece) {
    $found = @([regex]::Matches($card, '(?s)<div class="agent-task agent-worker">.*?</div>') | ForEach-Object Value | Where-Object { $_ -match [regex]::Escape($piece) })
    Assert-True ($found.Count -eq 1) "exactly one worker block for '$piece' (found $($found.Count))"
    $found[0]
}
function Get-PlanBlock([string]$card) { [regex]::Match($card, '(?s)<div class="plan-usage">.*?</div>').Value }

$ag = @{ flash = 'd4444444-0000-4000-8000-000000000001'; sonnet = 'd4444444-0000-4000-8000-000000000002'; json = 'd4444444-0000-4000-8000-000000000003'; missing = 'd4444444-0000-4000-8000-000000000004'; mixed = 'd4444444-0000-4000-8000-000000000005' }
$locks = @()

try {
    New-Item -ItemType Directory -Force $repo, (Join-Path $agyRoot 'conversations'), $tools, (Join-Path $channel 'contexto'), (Join-Path $channel 'tareas\AAA'), (Join-Path $channel 'orquestacion\AAA'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    Set-Content (Join-Path $channel 'AGENTES.md') @('| Agente | Modelo | Aplicación | Estado | Último | Cursor | Sesión |', '|---|---|---|---|---|---|---|',
        '| Claude-Opus | Opus | Claude | ACTIVO | x | c | s1 |', '| Codex | gpt | Codex | ACTIVO | x | c | s2 |',
        '| Antigravity-Flash | gemini-3.8-flash | Antigravity | ACTIVO | x | c | s3 |', '| Antigravity-Sonnet | claude-sonnet-5-5 | Antigravity | ACTIVO | x | c | s4 |',
        '| Antigravity-Null | synthetic | Antigravity | ACTIVO | x | c | s5 |') -Encoding utf8
    git -C $repo init -q | Out-Null
    Set-Content (Join-Path $channel 'tareas\AAA\ETAPA.md') @('# AAA · Etapa de prueba', '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', '| Estado | abierta |', '', '## Historial de decisiones', '', '- 2026-11-18 · Claude-Opus · Se abre la etapa') -Encoding utf8

    # --- 1. plan usage: a synthetic report (the cache of the channel, fresh) with codex and antigravity, same shape ---
    $window = { param($name, $label, $percent, $hours) @{ window = $name; label = $label; used_percent = $percent; resets_at = (Utc $now.AddHours($hours)); elapsed_percent = 40 } }
    $report = @{ apps = @(
        @{ app = 'codex'; plan = 'ChatGPT Plus'; windows = @((& $window 'five_hour' '5 h' 20 2)); source = @{ kind = 'codex rate limits'; observed_at = (Utc $now.AddMinutes(-3)) } },
        @{ app = 'antigravity'; plan = 'Google AI Pro'; windows = @((& $window 'five_hour' '5 h' 12.5 3), (& $window 'weekly' 'Semana' 50 90), ((& $window 'weekly' 'Semana' 7 90) + @{ group = 'Claude and GPT Models' })); source = @{ kind = 'agy /usage'; observed_at = (Utc $now.AddMinutes(-5)) } }) }
    Set-Content (Join-Path $channel 'contexto\usage-plans-cache.json') (Json @{ attempted_at = (Utc $now.AddSeconds(-30)); report = $report }) -Encoding utf8

    # --- 2./3. runs: ndjson with init.model and result.usage (+ the same run as json: counted once), json only, and a run without usage ---
    $nd = @(
        (Json @{ event = 'init'; conversation_id = $ag.flash; init = @{ model = 'gemini-3.8-flash-low'; cwd = 'C:\x'; tools = @(); permission_mode = 'plan' } }),
        (Json @{ event = 'step_update'; step_update = @{ text = 'SECRET-CONTENT' } }),
        (Json @{ event = 'result'; result = @{ conversation_id = $ag.flash; status = 'SUCCESS'; response = 'SECRET-CONTENT'; duration_seconds = 9.4; num_turns = 1
            usage = @{ input_tokens = 11475; output_tokens = 766; thinking_tokens = 661; cache_read_tokens = 8592; total_tokens = 12241 } } }))
    Set-Content (Join-Path $tools 'agy-10.ndjson') $nd -Encoding utf8
    Set-Content (Join-Path $tools 'agy-10.json') (Json @{ conversation_id = $ag.flash; status = 'SUCCESS'; response = 'SECRET-CONTENT'; usage = @{ input_tokens = 11475; output_tokens = 766; thinking_tokens = 661; cache_read_tokens = 8592; total_tokens = 12241 } }) -Encoding utf8
    Set-Content (Join-Path $tools 'agy-11.json') (Json @{ conversation_id = $ag.json; status = 'SUCCESS'; response = 'SECRET-CONTENT'; usage = @{ input_tokens = 2000; output_tokens = 500; thinking_tokens = 0; cache_read_tokens = 1000; total_tokens = 2500 } }) -Encoding utf8
    Set-Content (Join-Path $tools 'agy-12.json') (Json @{ conversation_id = $ag.sonnet; status = 'SUCCESS'; response = 'SECRET-CONTENT'; usage = @{ input_tokens = 9; output_tokens = 9; cache_read_tokens = 0; total_tokens = 18 } }) -Encoding utf8
    Set-Content (Join-Path $tools 'agy-13.json') (Json @{ conversation_id = $ag.missing; usage = @{ input_tokens = 30; output_tokens = 10; cache_read_tokens = 5; total_tokens = 40 } }) -Encoding utf8
    Set-Content (Join-Path $tools 'agy-14.ndjson') @((Json @{ event = 'init'; conversation_id = $ag.mixed; init = @{ model = 'synthetic' } }), (Json @{ event = 'result'; result = @{ conversation_id = $ag.mixed; usage = @{ input_tokens = 20; output_tokens = 10; cache_read_tokens = 0; total_tokens = 30 } } })) -Encoding utf8
    foreach ($name in @('agy-10.ndjson', 'agy-13.json', 'agy-14.ndjson')) {
        $locks += [IO.File]::Open((Join-Path $tools $name), [IO.FileMode]::Open, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
    }
    # activity: the conversation file of the first run was written 3 minutes ago
    $db = Join-Path $agyRoot "conversations\$($ag.flash).db"
    [IO.File]::WriteAllBytes($db, [Text.Encoding]::UTF8.GetBytes('SQLite format 3 SECRET-CONTENT'))
    (Get-Item $db).LastWriteTime = $now.AddMinutes(-3)

    # T01: tokens only from the runs (no Tokens field). T02: the card's field wins over the run. T03: json-only run, no model.
    Write-Card 'T01' 'en curso' 'Claude-Opus' @((Deleg 'Claude-Opus' '11:00' 'Antigravity-Flash' "agy:$($ag.flash)" 'pieza uno'))
    Write-Card 'T02' 'en curso' 'Claude-Opus' @((Deleg 'Claude-Opus' '11:10' 'Antigravity-Sonnet' "agy:$($ag.sonnet)" 'pieza dos')) 'Claude-Opus: total 5 = entrada 1 + cacheados 3 + salida 1 (medido); ↳ Antigravity-Sonnet (delegado por Claude-Opus, exacto por ventana): total 500 = entrada 100 + cacheados 300 + salida 100; Total de la tarea: 505'
    Write-Card 'T03' 'en curso' 'Claude-Opus' @((Deleg 'Claude-Opus' '11:20' 'Antigravity-Argon' "agy:$($ag.json)" 'pieza tres'))
    Write-Card 'T04' 'en curso' 'Claude-Opus' @((Deleg 'Claude-Opus' '11:25' 'Antigravity-Null' "agy:$($ag.missing)" 'pieza cuatro')) '↳ Antigravity-Null (delegado por Claude-Opus, sin datos): sin datos; Total de la tarea: 5'
    Write-Card 'T05' 'en curso' 'Claude-Opus' @((Deleg 'Claude-Opus' '11:30' 'Antigravity-Mixed' "agy:$($ag.mixed)" 'pieza cinco')) 'Claude-Opus: total 5 = entrada 3 + cacheados 1 + salida 1 (medido); Total de la tarea: 5'

    $run = Run-Viewer
    $agy = Get-AppCard $run.Agents 'Antigravity'
    $codex = Get-AppCard $run.Agents 'Codex'

    # 1. plan usage, painted like the other apps
    $plan = Get-PlanBlock $agy
    Assert-True ($plan -match 'Plan usage · Google AI Pro') 'plan name in the Antigravity card'
    Assert-True ($plan -match '5 h</strong>: 12\.5 % used' -and $plan -match 'Semana</strong>: 50 % used') 'plan windows with used percent'
    Assert-True ($plan -match 'Claude and GPT Models · Semana</strong>: 7 % used') 'a window of another agy model group carries the group name'
    Assert-True ($plan -match 'reset: \d\d/\d\d \d\d:\d\d' -and $plan -match 'Pace: ') 'reset and pace like the other apps'
    Assert-True ($plan -match 'Source: agy /usage · Freshness: 5 min ago') 'source and freshness'
    $codexPlan = Get-PlanBlock $codex
    Assert-True ($codexPlan -match 'Plan usage · ChatGPT Plus' -and $codexPlan -match '20 % used') 'the Codex card has its plan usage (same renderer)'
    # A report without Antigravity: no data, not a failure.
    $reportNone = @{ apps = @($report.apps[0]) }
    Set-Content (Join-Path $channel 'contexto\usage-plans-cache.json') (Json @{ attempted_at = (Utc $now.AddSeconds(-30)); report = $reportNone }) -Encoding utf8
    $none = Run-Viewer
    $planNone = Get-PlanBlock (Get-AppCard $none.Agents 'Antigravity')
    Assert-True ($planNone -match 'Plan usage' -and $planNone -match 'No data') 'without Antigravity in the report, the card says No data'
    Set-Content (Join-Path $channel 'contexto\usage-plans-cache.json') (Json @{ attempted_at = (Utc $now.AddSeconds(-30)); report = $report }) -Encoding utf8

    # 2. Tokens per worker (from the runs): agy's input excludes the cache, so total = input + cached + output.
    $w1 = Get-WorkerBlock $agy 'pieza uno'
    Assert-True ($w1 -match 'Tokens: 21 k · input 11 k · output 766 · measured in this run') "T01 worker tokens from the run (ndjson and json of the same run counted once), got: $($w1 -replace '<[^>]+>', ' ')"
    # 3. model observed in init.model
    Assert-True ($w1 -match '<strong>Antigravity · gemini-3\.8-flash-low \(T2\)</strong>') 'the model observed (init.model) names the worker'
    Assert-True ($w1 -match 'Activity: Working') 'activity from the conversation file'
    # per task: the Tokens column of the task row and its total
    $taskRow = [regex]::Match($run.Html, '(?s)<tr><td><a [^>]*>AAA-T01.*?</tr>').Value
    Assert-True ($taskRow -match 'Task total: 21 k' -and $taskRow -match 'Antigravity-Flash' -and $taskRow -match '21 k') 'T01 task tokens and total from the runs'
    Assert-True ($taskRow -match 'Input 11475 · cached 8592 · output 766') 'T01 token detail (title attribute)'
    # the card's field wins when it has the worker
    $w2 = Get-WorkerBlock $agy 'pieza dos'
    Assert-True ($w2 -match 'Tokens: 500 · input 100 · output 100 · exact per window') "T02 worker tokens from the card field, got: $($w2 -replace '<[^>]+>', ' ')"
    $row2 = [regex]::Match($run.Html, '(?s)<tr><td><a [^>]*>AAA-T02.*?</tr>').Value
    Assert-True ($row2 -match 'Task total: 505' -and $row2 -notmatch 'measured in this run') 'T02 task total from the field, no duplicate from the run'
    $row4 = [regex]::Match($run.Html, '(?s)<tr><td><a [^>]*>AAA-T04.*?</tr>').Value
    Assert-True ($row4 -match 'Task total: 50' -and $row4 -match 'Antigravity-Null.*<strong>45</strong>') 'a no-data card entry does not hide a locked agy json run'
    $w4 = Get-WorkerBlock $agy 'pieza cuatro'
    Assert-True ($w4 -match 'Tokens: 45 · input 30 · output 10 · measured in this run') 'worker card falls back from no-data field to measured run'
    $row5 = [regex]::Match($run.Html, '(?s)<tr><td><a [^>]*>AAA-T05.*?</tr>').Value
    Assert-True ($row5 -match 'Task total: 35' -and $row5 -match 'Antigravity-Mixed.*<strong>30</strong>') 'task total includes the extra locked agy ndjson run'
    # json-only run: tokens, no model (the registered name stays)
    $w3 = Get-WorkerBlock $agy 'pieza tres'
    Assert-True ($w3 -match '<strong>Antigravity-Argon \(T2\)</strong>' -and $w3 -match 'Tokens: 4 k · input 2 k · output 500 · measured in this run') 'T03 json-only run: tokens and registered name'
    # per agent: the agent card sums the tokens of its tasks
    Assert-True ($agy -match '<h4>Task tokens</h4><p><strong>21 k</strong> across 1 task\(s\) · output 766') 'Antigravity-Flash agent card: tokens in tasks'
    Assert-True ($agy -match '<h4>Task tokens</h4><p><strong>500</strong> across 1 task\(s\)') 'Antigravity-Sonnet agent card: tokens from the field'

    # Nothing of the runs is printed
    Assert-True ($run.Html -notmatch 'SECRET-CONTENT') 'the response and the steps of the runs are never printed'
    Assert-True ($run.Html -notmatch 'agy-10|agy-11|herramientas\\') 'run file names are not printed'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    foreach ($lock in $locks) { $lock.Dispose() }
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'GEM-T06 agy metrics PASS'
