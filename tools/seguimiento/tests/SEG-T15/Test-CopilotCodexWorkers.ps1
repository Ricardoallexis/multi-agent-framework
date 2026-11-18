# SEG-T15: Copilot sessions (copilot:<id>) and Codex threads (codex:<id>) are workers like the Claude subagents:
# activity state from the tail of their transcript, running-test detection, and "Copilot · modelo" / "Codex · modelo"
# in the agents tab and the Trabajaron column. Synthetic folders only; the content of the sessions is never printed.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t15-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$subRoot = Join-Path $temp 'projects'
$copilotRoot = Join-Path $temp 'copilot'
$codexRoot = Join-Path $temp 'codex'
$codexDay = Join-Path $codexRoot 'sessions\2026\10\11'
$now = [datetime]'2026-10-11T12:00:00'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Utc([datetime]$local) { $local.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ss.fffZ') }
function Json($value) { $value | ConvertTo-Json -Compress -Depth 8 }
function Set-Age([string]$path, [int]$minutesAgo) { (Get-Item $path).LastWriteTime = $now.AddMinutes(-$minutesAgo) }

# --- Copilot: ~/.copilot/session-state/<id>/events.jsonl, records {type, data, id, timestamp, parentId} ---
function CopRec([string]$type, [int]$minutesAgo, $data) { Json @{ type = $type; data = $data; id = [guid]::NewGuid().ToString(); timestamp = (Utc $now.AddMinutes(-$minutesAgo)); parentId = $null } }
# $tool starts at $minutesAgo + 2 and (with -Answered) completes at $minutesAgo; without a command the last record is a message.
function New-CopilotSession([string]$id, [int]$minutesAgo, [string]$model = 'gpt-5.6-luna', [string]$tool = '', $arguments = $null, [switch]$Answered, [switch]$NoModelInTail, [int]$FillerLines = 0) {
    $dir = Join-Path $copilotRoot "session-state\$id"
    New-Item -ItemType Directory -Force $dir | Out-Null
    $lines = @()
    $lines += CopRec 'session.start' ($minutesAgo + 40) @{ sessionId = $id; selectedModel = $model; copilotVersion = '1.0' }
    $lines += CopRec 'user.message' ($minutesAgo + 30) @{ content = 'SECRET-CONTENT'; messageId = 'm1' }
    for ($i = 0; $i -lt $FillerLines; $i++) { $lines += CopRec 'hook.start' ($minutesAgo + 20) @{ hookInvocationId = "h$i"; hookType = 'x'; input = ('x' * 2000) } }
    if ($tool) {
        $start = @{ toolCallId = "tc_$id"; toolName = $tool; arguments = $arguments; turnId = 't1' }
        if (-not $NoModelInTail) { $start.model = $model }
        $lines += CopRec 'tool.execution_start' ($minutesAgo + 2) $start
        if ($Answered) {
            $done = @{ toolCallId = "tc_$id"; success = $true; result = @{ content = 'SECRET-CONTENT' } }
            if (-not $NoModelInTail) { $done.model = $model }
            $lines += CopRec 'tool.execution_complete' $minutesAgo $done
        }
    } else {
        $message = @{ messageId = 'm2'; content = 'SECRET-CONTENT' }
        if (-not $NoModelInTail) { $message.model = $model }
        $lines += CopRec 'assistant.message' $minutesAgo $message
    }
    $path = Join-Path $dir 'events.jsonl'
    Set-Content $path $lines -Encoding utf8
    Set-Age $path $minutesAgo
}

# --- Codex: ~/.codex/sessions/YYYY/MM/DD/rollout-*-<id>.jsonl, records {timestamp, type, payload} ---
function CxRec([string]$type, [int]$minutesAgo, $payload) { Json @{ timestamp = (Utc $now.AddMinutes(-$minutesAgo)); type = $type; payload = $payload } }
function New-CodexThread([string]$id, [int]$minutesAgo, [string]$model = 'gpt-6-luna', [string]$call = '', [string]$callName = 'exec', [string]$callText = '', [switch]$Answered, [switch]$ModelInHeadOnly, [int]$FillerLines = 0) {
    New-Item -ItemType Directory -Force $codexDay | Out-Null
    $lines = @()
    $lines += CxRec 'session_meta' ($minutesAgo + 40) @{ id = $id; model_provider = 'openai'; cwd = 'C:\x' }
    if ($ModelInHeadOnly) { $lines += CxRec 'turn_context' ($minutesAgo + 39) @{ model = $model; effort = 'high' } }
    $lines += CxRec 'response_item' ($minutesAgo + 30) @{ type = 'message'; role = 'user'; content = 'SECRET-CONTENT' }
    for ($i = 0; $i -lt $FillerLines; $i++) { $lines += CxRec 'response_item' ($minutesAgo + 20) @{ type = 'function_call_output'; call_id = "old$i"; output = ('x' * 2000) } }
    if (-not $ModelInHeadOnly) { $lines += CxRec 'turn_context' ($minutesAgo + 5) @{ model = $model; effort = 'high' } }
    if ($call) {
        $payload = @{ type = $call; name = $callName; call_id = "call_$id" }
        if ($call -eq 'custom_tool_call') { $payload.input = $callText } else { $payload.arguments = $callText }
        $lines += CxRec 'response_item' ($minutesAgo + 2) $payload
        if ($Answered) { $lines += CxRec 'response_item' $minutesAgo @{ type = "$($call)_output"; call_id = "call_$id"; output = 'SECRET-CONTENT' } }
    } else {
        $lines += CxRec 'response_item' $minutesAgo @{ type = 'message'; role = 'assistant'; content = 'SECRET-CONTENT' }
    }
    $path = Join-Path $codexDay "rollout-2026-10-11T10-00-00-$id.jsonl"
    Set-Content $path $lines -Encoding utf8
    Set-Age $path $minutesAgo
}

function Write-Card([string]$code, [string]$state, [string[]]$rows, [string]$tokens = '') {
    $lines = @("# AAA-$code Tarea $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Depende de | - |', '| Parche | ninguno |', '| Agente | Claude |')
    if ($tokens) { $lines += "| Tokens | $tokens |" }
    $lines += @('', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', '| 2026-10-11 10:00 | Claude-Opus | creada | x |') + $rows
    Set-Content (Join-Path $channel "tareas\AAA\$code-x.md") $lines -Encoding utf8
}
function Deleg([string]$time, [string]$worker, [string]$ref, [string]$piece, [string]$level = 'T2, esfuerzo medio') { "| 2026-10-11 $time | Claude-Opus | delegada | $worker ($level): $piece [ref: $ref] |" }
function Get-Row([string]$html, [string]$code) {
    $m = [regex]::Match($html, "(?s)<tr><td><a [^>]*>AAA-$code Tarea $code</a>.*?</tr>")
    Assert-True $m.Success "row of $code present"
    $m.Value
}
function Get-Cell([string]$row) { ([regex]::Matches($row, '(?s)<td>(.*?)</td>') | ForEach-Object { $_.Groups[1].Value })[7] }

$cp = @{
    work = 'a1111111-0000-4000-8000-000000000001'; test = 'a1111111-0000-4000-8000-000000000002'; done = 'a1111111-0000-4000-8000-000000000003'
    stop = 'a1111111-0000-4000-8000-000000000004'; msg = 'a1111111-0000-4000-8000-000000000005'; head = 'a1111111-0000-4000-8000-000000000006'
    gone = 'a1111111-0000-4000-8000-0000000000ff'; upper = 'a1111111-0000-4000-8000-000000000007'
}
$cx = @{
    work = '01a10000-0000-7000-8000-000000000001'; test = '01a10000-0000-7000-8000-000000000002'; done = '01a10000-0000-7000-8000-000000000003'
    stop = '01a10000-0000-7000-8000-000000000004'; msg = '01a10000-0000-7000-8000-000000000005'; head = '01a10000-0000-7000-8000-000000000006'
    gone = '01a10000-0000-7000-8000-0000000000ff'; shell = '01a10000-0000-7000-8000-000000000007'
}

try {
    New-Item -ItemType Directory -Force $repo, $subRoot, $copilotRoot, $codexRoot, (Join-Path $channel 'tareas\AAA'), (Join-Path $channel 'orquestacion\AAA'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    git -C $repo init -q | Out-Null
    Set-Content (Join-Path $channel 'tareas\AAA\ETAPA.md') @('# AAA · Etapa de prueba', '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', '| Estado | abierta |', '', '## Fases de tareas', '', '| Fase | Tareas | Descripción |', '|---|---|---|', '| Fase 1 | T01, T02, T03, T04, T05, T06, T07, T08, T09, T10, T11, T12, T13, T14, T15 | x |', '', '## Historial de decisiones', '', '- 2026-10-11 · Claude-Opus · Se abre la etapa') -Encoding utf8

    # Copilot sessions
    New-CopilotSession $cp.work 3
    New-CopilotSession $cp.test 20 -tool 'powershell' -arguments @{ command = 'python -m pytest tests -q SECRET-CMD'; description = 'SECRET-CONTENT' }
    New-CopilotSession $cp.done 20 -tool 'powershell' -arguments @{ command = 'python -m pytest tests -q' } -Answered
    New-CopilotSession $cp.stop 90
    New-CopilotSession $cp.msg 20 -tool 'send_session_message' -arguments @{ message = 'ejecuta pytest SECRET-CONTENT'; session_id = 'x' }
    New-CopilotSession $cp.head 3 -model 'claude-sonnet-5.5' -NoModelInTail -FillerLines 220
    New-CopilotSession $cp.upper 3 -model 'gpt-5.6-luna'
    # Codex threads
    New-CodexThread $cx.work 3 -call 'function_call' -callName 'shell_command' -callText '{"command":"git status"}' -Answered
    New-CodexThread $cx.test 20 -call 'custom_tool_call' -callName 'exec' -callText 'await run("python -m pytest tests -q SECRET-CMD")'
    New-CodexThread $cx.done 20 -call 'custom_tool_call' -callName 'exec' -callText 'await run("python -m pytest tests -q")' -Answered
    New-CodexThread $cx.stop 90
    New-CodexThread $cx.msg 20 -call 'function_call' -callName 'send_message' -callText '{"target":"x","message":"corre pytest SECRET-CONTENT"}'
    New-CodexThread $cx.head 3 -model 'gpt-6-sol' -ModelInHeadOnly -FillerLines 220
    New-CodexThread $cx.shell 20 -call 'function_call' -callName 'shell_command' -callText '{"command":"pwsh .\\probar.ps1 -Todo"}'

    # Cards: Copilot (T01..T06), Codex (T07..T12), mixed/others (T13..T15)
    Write-Card 'T01' 'en curso' @((Deleg '11:00' 'GPT worker' "copilot:$($cp.work)" 'pieza uno'))
    Write-Card 'T02' 'en curso' @((Deleg '11:30' 'GPT worker' "copilot:$($cp.test)" 'pieza dos'))
    Write-Card 'T03' 'en curso' @((Deleg '11:00' 'GPT worker' "copilot:$($cp.done)" 'pieza tres'), (Deleg '11:05' 'GPT worker B' "copilot:$($cp.stop)" 'pieza cuatro'))
    Write-Card 'T04' 'en curso' @((Deleg '11:00' 'GPT worker' "copilot:$($cp.msg)" 'pieza cinco'))
    Write-Card 'T05' 'en curso' @((Deleg '11:00' 'GPT worker' "copilot:$($cp.head)" 'pieza seis'))
    Write-Card 'T06' 'en curso' @((Deleg '11:00' 'Copilot GPT-5.2' "copilot:$($cp.gone)" 'pieza siete'), (Deleg '11:10' 'GPT-5.2' "copilot:$($cp.gone)" 'pieza ocho' 'T1'))
    Write-Card 'T07' 'en curso' @((Deleg '11:00' 'Codex worker' "codex:$($cx.work)" 'pieza nueve')) 'Claude-Opus: total 5 = entrada 1 + cacheados 3 + salida 1 (medido); ↳ Codex worker (delegado por Claude-Opus, exacto por ventana, con 3 hilos hijos): total 485 = entrada 310 + cacheados 130 + salida 45; Total de la tarea: 490'
    Write-Card 'T08' 'en curso' @((Deleg '11:30' 'Codex worker' "codex:$($cx.test)" 'pieza diez')) 'Claude-Opus: total 5 = entrada 1 + cacheados 3 + salida 1 (medido); ↳ Codex worker (delegado por Claude-Opus, sin datos)'

    Write-Card 'T09' 'en curso' @((Deleg '11:00' 'Codex worker' "codex:$($cx.done)" 'pieza once'), (Deleg '11:05' 'Codex worker B' "codex:$($cx.stop)" 'pieza doce'))
    Write-Card 'T10' 'en curso' @((Deleg '11:00' 'Codex worker' "codex:$($cx.msg)" 'pieza trece'), (Deleg '11:10' 'Codex worker B' "codex:$($cx.shell)" 'pieza catorce'))
    Write-Card 'T11' 'en curso' @((Deleg '11:00' 'Codex worker' "codex:$($cx.head)" 'pieza quince'), (Deleg '11:10' 'GPT' "codex:$($cx.gone)" 'pieza dieciséis' 'T1'))
    # T12: returned delegation of a Copilot worker still shows app and model in the Trabajaron column.
    Write-Card 'T12' 'en curso' @((Deleg '11:00' 'GPT worker' "copilot:$($cp.work)" 'pieza devuelta'), '| 2026-10-11 11:30 | Claude-Opus | devuelta | GPT worker: listo |')
    # T13: reference written in upper case (kept lower case by registrar-delegacion.ps1, but a hand-written card must still work).
    Write-Card 'T13' 'en curso' @((Deleg '11:00' 'GPT worker' "COPILOT:$($cp.upper.ToUpperInvariant())" 'pieza mayúsculas'))
    # T14: terminada task, not an activity line; T15: worker only in ejecuciones.jsonl with a Codex reference.
    Write-Card 'T14' 'terminada' @((Deleg '11:00' 'GPT worker' "copilot:$($cp.work)" 'pieza terminada'))
    Write-Card 'T15' 'en curso' @()
    Set-Content (Join-Path $channel 'orquestacion\AAA\ejecuciones.jsonl') @(
        (Json @{ task = 'AAA-T15'; worker = 'Codex GPT'; requested_model = 'gpt-6'; requested_effort = 'high'; reason = 'auditoría estática'; status = 'delegated'; recorded_at = ([datetimeoffset][datetime]'2026-10-11T08:00:00').ToString('o'); orchestrator = 'Claude-Opus'; reference = "codex:$($cx.work)" }),
        (Json @{ task = 'AAA-T15'; worker = 'Codex GPT'; reason = 'PASS'; status = 'reviewed_complete'; recorded_at = ([datetimeoffset][datetime]'2026-10-11T08:45:00').ToString('o'); orchestrator = 'Claude-Opus' })) -Encoding utf8

    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots $subRoot -CopilotRoot $copilotRoot -CodexRoot $codexRoot -NowOverride $now | Out-Null
    $html = [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))

    # Copilot: activity states and app · model
    Assert-True ($html -match 'T01: Copilot · gpt-5\.6-luna \(T2\) desde 11:00 · Trabajando · pieza uno') 'T01 Copilot working, app · model'
    Assert-True ($html -match 'T02: Copilot · gpt-5\.6-luna \(T2\) desde 11:30 · Ejecutando prueba · desde 11:38 · pieza dos') 'T02 Copilot running a test (start 11:38 = unanswered tool.execution_start)'
    Assert-True ($html -match 'T03: Copilot · gpt-5\.6-luna \(T2\) desde 11:00 · Sin actividad reciente · última hace 20 min · pieza tres') 'T03 answered tool is not a running test'
    Assert-True ($html -match 'T03: Copilot · gpt-5\.6-luna \(T2\) desde 11:05 · Probablemente detenido · pieza cuatro') 'T03 Copilot stopped'
    Assert-True ($html -match 'T04: Copilot · gpt-5\.6-luna \(T2\) desde 11:00 · Sin actividad reciente · última hace 20 min · pieza cinco') 'T04 a non-shell tool mentioning pytest is not a test'
    Assert-True ($html -match 'T05: Copilot · claude-sonnet-5\.5 \(T2\) desde 11:00 · Trabajando · pieza seis') 'T05 model from the head when the tail has none'
    Assert-True ($html -match 'T06: Copilot GPT-5\.2 \(T2\) desde 11:00 · sin señal de actividad · pieza siete') 'T06 missing session: worker already named Copilot'
    Assert-True ($html -match 'T06: Copilot · GPT-5\.2 \(T1\) desde 11:10 · sin señal de actividad · pieza ocho') 'T06 missing session: app prefix added'
    # Codex
    Assert-True ($html -match 'T07: Codex · gpt-6-luna \(T2\) desde 11:00 · Trabajando · pieza nueve') 'T07 Codex working, app · model'
    Assert-True ($html -match 'T08: Codex · gpt-6-luna \(T2\) desde 11:30 · Ejecutando prueba · desde 11:38 · pieza diez') 'T08 Codex running a test'
    Assert-True ($html -match 'T09: Codex · gpt-6-luna \(T2\) desde 11:00 · Sin actividad reciente · última hace 20 min · pieza once') 'T09 answered call is not a running test'
    Assert-True ($html -match 'T09: Codex · gpt-6-luna \(T2\) desde 11:05 · Probablemente detenido · pieza doce') 'T09 Codex stopped'
    Assert-True ($html -match 'T10: Codex · gpt-6-luna \(T2\) desde 11:00 · Sin actividad reciente · última hace 20 min · pieza trece') 'T10 send_message mentioning pytest is not a test'
    Assert-True ($html -match 'T10: Codex · gpt-6-luna \(T2\) desde 11:10 · Ejecutando prueba · desde 11:38 · pieza catorce') 'T10 probar.ps1 through shell_command'
    Assert-True ($html -match 'T11: Codex · gpt-6-sol \(T2\) desde 11:00 · Trabajando · pieza quince') 'T11 model from the head of the rollout'
    Assert-True ($html -match 'T11: Codex · GPT \(T1\) desde 11:10 · sin señal de actividad · pieza dieciséis') 'T11 missing thread: app prefix added'
    Assert-True ($html -match 'T13: Copilot · gpt-5\.6-luna \(T2\) desde 11:00 · Trabajando · pieza mayúsculas') 'T13 upper case reference'
    Assert-True ($html -notmatch 'T12: [^<]*pieza devuelta') 'T12 returned delegation is not an activity line'
    Assert-True ($html -notmatch 'T14: [^<]*pieza terminada') 'T14 delivered task has no activity line'

    # Trabajaron column: app · model in the worker label, tokens/activity lines untouched
    $c1 = Get-Cell (Get-Row $html 'T01')
    Assert-True ($c1 -match '<summary>Copilot · gpt-5\.6-luna \(T2, esfuerzo medio\)') 'T01 Trabajaron label: Copilot · model (level, effort)'
    Assert-True ($c1 -match 'desde 11:00 · en curso · Trabajando') 'T01 activity in the Trabajaron cell'
    Assert-True ($c1 -notmatch 'GPT worker') 'T01 the registered name is replaced by app · model'
    $c7 = Get-Cell (Get-Row $html 'T07')
    Assert-True ($c7 -match '<summary>Codex · gpt-6-luna \(T2, esfuerzo medio\)') 'T07 Trabajaron label: Codex · model'
    Assert-True ($c7 -match 'Tokens: 485 · entrada 310 · salida 45 · exacto por ventana') 'T07 exact tokens with child threads shown'
    Assert-True ((Get-Cell (Get-Row $html 'T08')) -match 'Tokens: sin datos') 'T08 tokens sin datos'
    $c12 = Get-Cell (Get-Row $html 'T12')
    Assert-True ($c12 -match '<summary>Copilot · gpt-5\.6-luna \(T2, esfuerzo medio\)') 'T12 returned delegation keeps app · model'
    Assert-True ($c12 -match 'desde 11:00 hasta 11:30' -and $c12 -match 'Resultado: listo') 'T12 times and result'
    $c14 = Get-Cell (Get-Row $html 'T14')
    Assert-True ($c14 -match '<summary>Copilot · gpt-5\.6-luna \(T2, esfuerzo medio\)' -and $c14 -match 'sin devolver') 'T14 listed as worked, not active'
    $c15 = Get-Cell (Get-Row $html 'T15')
    Assert-True ($c15 -match '<summary>Codex · gpt-6-luna \(gpt-6, esfuerzo high\)') 'T15 worker only in ejecuciones.jsonl: Codex · model'
    $c6 = Get-Cell (Get-Row $html 'T06')
    Assert-True ($c6 -match '<summary>Copilot GPT-5\.2 \(T2, esfuerzo medio\)') 'T06 label without data keeps the worker name'

    # Nothing of the sessions is printed
    Assert-True ($html -notmatch 'SECRET-CONTENT' -and $html -notmatch 'SECRET-CMD') 'session content never printed'
    Assert-True ($html -notmatch '\[ref:') 'reference kept out of the page'
    Assert-True ($html -notmatch [regex]::Escape($cp.work) -and $html -notmatch [regex]::Escape($cx.work)) 'ids not printed'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T15 Copilot and Codex workers PASS'
