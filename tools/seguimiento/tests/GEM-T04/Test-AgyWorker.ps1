# GEM-T04 (viewer part, REGLAS 33): Antigravity (agy) is one more entry of the table of applications ($script:WorkerApps). A worker with the
# reference agy:<conversation_id> appears in the card of its own application (Antigravity), whichever application orchestrates it: "trabajando
# para <orquestador> en <tarea>", its real activity (from the modification time of conversations\<id>.db, else of the json that the orchestrator
# saves in orquestacion\herramientas\agy-<n>.json) and its registered name, which already carries the model. Synthetic folders only; the content of
# the conversations is never printed (nor read: only times and the conversation id of the saved json).
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$temp = Join-Path ([IO.Path]::GetTempPath()) ('gem-t04-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'
$subRoot = Join-Path $temp 'projects'
$copilotRoot = Join-Path $temp 'copilot'
$codexRoot = Join-Path $temp 'codex'
$agyRoot = Join-Path $temp 'agy'
$agyConversations = Join-Path $agyRoot 'conversations'
$tools = Join-Path $channel 'orquestacion\herramientas'
$now = [datetime]'2026-11-18T12:00:00'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Set-Age([string]$path, [int]$minutesAgo) { (Get-Item $path).LastWriteTime = $now.AddMinutes(-$minutesAgo) }

# --- agy: <root>\conversations\<conversation_id>.db (SQLite, binary: synthetic bytes with a marker that must never be printed) ---
function New-AgyConversation([string]$id, [int]$minutesAgo) {
    New-Item -ItemType Directory -Force $agyConversations | Out-Null
    $path = Join-Path $agyConversations "$id.db"
    [IO.File]::WriteAllBytes($path, [Text.Encoding]::UTF8.GetBytes("SQLite format 3`0`0SECRET-CONTENT`0`0" + ('x' * 6000) + "`nSECRET-CONTENT"))
    Set-Age $path $minutesAgo
}
# --- the json the orchestrator saves from a run: {conversation_id, status, response, duration_seconds, num_turns, usage{...}} ---
function New-AgyRunJson([string]$name, [string]$id, [int]$minutesAgo) {
    New-Item -ItemType Directory -Force $tools | Out-Null
    $path = Join-Path $tools $name
    @{ conversation_id = $id; status = 'SUCCESS'; response = 'SECRET-CONTENT'; duration_seconds = 9.4; num_turns = 1; usage = @{ input_tokens = 1; output_tokens = 1; total_tokens = 2 } } | ConvertTo-Json -Compress | Set-Content $path -Encoding utf8
    Set-Age $path $minutesAgo
}

function Write-Card([string]$code, [string]$state, [string]$owner, [string[]]$rows) {
    $lines = @("# AAA-$code · Tarea de prueba $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", '| Depende de | - |', '| Parche | ninguno |', "| Agente | $owner |",
        '', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', "| 2026-11-18 10:00 | $owner | creada | x |") + $rows
    Set-Content (Join-Path $channel "tareas\AAA\$code-x.md") $lines -Encoding utf8
}
function Deleg([string]$orchestrator, [string]$time, [string]$worker, [string]$ref, [string]$piece, [string]$level = 'T2, esfuerzo medio') {
    $tail = if ($ref) { " [ref: $ref]" } else { '' }
    "| 2026-11-18 $time | $orchestrator | delegada | $worker ($level): $piece$tail |"
}
function Run-Viewer {
    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -SubagentRoots $subRoot -CopilotRoot $copilotRoot -CodexRoot $codexRoot -AgyRoot $agyRoot -NowOverride $now | Out-Null
    $html = [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))
    $agents = $html.Substring($html.IndexOf('<div class="tab" id="agentes">'))
    $agents.Substring(0, $agents.IndexOf('cloud-assignment-group'))
}
function Get-AppCard([string]$agents, [string]$application) {
    $m = [regex]::Match($agents, "(?s)<section class=""panel agent-card"">\s*<div class=""card-head""><h2>$application</h2>.*?</section>")
    Assert-True $m.Success "card of $application present"
    $m.Value
}
function Get-WorkerBlocks([string]$card) { @([regex]::Matches($card, '(?s)<div class="agent-task agent-worker">.*?</div>') | ForEach-Object Value) }
function Get-WorkerBlock([string]$card, [string]$piece) {
    $found = @(Get-WorkerBlocks $card | Where-Object { $_ -match [regex]::Escape($piece) })
    Assert-True ($found.Count -eq 1) "exactly one worker block for '$piece' in the card (found $($found.Count))"
    $found[0]
}
function Get-CardStatus([string]$card) { [regex]::Match($card, '(?s)<div class="card-head"><h2>[^<]+</h2><span class="state ([^"]+)">([^<]*)</span>') }

$ag = @{ work = 'c3333333-0000-4000-8000-000000000001'; quiet = 'c3333333-0000-4000-8000-000000000002'; stop = 'c3333333-0000-4000-8000-000000000003'
    json = 'c3333333-0000-4000-8000-000000000004'; none = 'c3333333-0000-4000-8000-000000000005'; codexOwner = 'c3333333-0000-4000-8000-000000000006' }

try {
    New-Item -ItemType Directory -Force $repo, $subRoot, $copilotRoot, $codexRoot, (Join-Path $channel 'tareas\AAA'), (Join-Path $channel 'orquestacion\AAA'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    Set-Content (Join-Path $channel 'AGENTES.md') @('| Agente | Modelo | Aplicación | Estado | Último | Cursor | Sesión |', '|---|---|---|---|---|---|---|',
        '| Claude-Opus | Opus | Claude | ACTIVO | x | c | s1 |', '| Codex | gpt | Codex | ACTIVO | x | c | s2 |',
        '| Antigravity-Flash | gemini-3.8-flash | Antigravity | ACTIVO | x | c | s3 |', '| Antigravity-Opus | claude-opus-5-5 | Antigravity | ACTIVO | x | c | s4 |') -Encoding utf8
    git -C $repo init -q | Out-Null
    Set-Content (Join-Path $channel 'tareas\AAA\ETAPA.md') @('# AAA · Etapa de prueba', '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', '| Estado | abierta |', '', '## Fases de tareas', '', '| Fase | Tareas | Descripción |', '|---|---|---|', '| Fase 1 | T01, T02 | x |', '', '## Historial de decisiones', '', '- 2026-11-18 · Claude-Opus · Se abre la etapa') -Encoding utf8

    New-AgyConversation $ag.work 3        # trabajando
    New-AgyConversation $ag.quiet 20      # sin actividad reciente
    New-AgyConversation $ag.stop 90       # probablemente detenido
    New-AgyRunJson 'agy-01.json' $ag.json 25   # no .db: the saved json of the run is the source (ended 25 minutes ago)
    New-AgyRunJson 'agy-02.json' $ag.none 1    # a json of another conversation: does not answer for the ids below

    # T01: Claude-Opus orchestrates four agy workers (db, quiet db, stopped db, json only) and one whose reference finds nothing.
    Write-Card 'T01' 'en curso' 'Claude-Opus' @(
        (Deleg 'Claude-Opus' '11:00' 'Antigravity-Flash' "agy:$($ag.work)" 'pieza uno'),
        (Deleg 'Claude-Opus' '11:05' 'Antigravity-Sonnet' "agy:$($ag.quiet)" 'pieza dos'),
        (Deleg 'Claude-Opus' '11:10' 'Antigravity-Argon' "agy:$($ag.stop)" 'pieza tres'),
        (Deleg 'Claude-Opus' '11:15' 'Antigravity-Hydrogen' "agy:$($ag.json)" 'pieza cuatro'),
        (Deleg 'Claude-Opus' '11:20' 'Antigravity-GPTOSS' "agy:c3333333-0000-4000-8000-0000000000ff" 'pieza cinco'),
        (Deleg 'Claude-Opus' '11:25' 'Antigravity-Opus' '' 'pieza seis sin referencia'))
    # T02: Codex orchestrates an agy worker (another orchestrator, same application card).
    Write-Card 'T02' 'en curso' 'Codex' @((Deleg 'Codex' '11:30' 'Antigravity-Flash' "agy:$($ag.codexOwner)" 'pieza siete'))
    New-AgyConversation $ag.codexOwner 2

    $agents = Run-Viewer
    $apps = [regex]::Matches($agents, '<section class="panel agent-card">\s*<div class="card-head"><h2>([^<]+)</h2>') | ForEach-Object { $_.Groups[1].Value }
    Assert-True ((@($apps) -join ',') -eq 'Claude,Codex,Antigravity') "one card per application, got: $(@($apps) -join ',')"
    $agy = Get-AppCard $agents 'Antigravity'
    $claude = Get-AppCard $agents 'Claude'
    $codex = Get-AppCard $agents 'Codex'

    $status = Get-CardStatus $agy
    Assert-True ($status.Groups[1].Value -eq 's-en-curso') 'the Antigravity card is active'
    Assert-True ($agy -notmatch '<span class="state s-idle">sin actividad') 'the Antigravity card does not say sin actividad while it has active workers'

    # db of a conversation: its modification time is the activity; the registered name stays (no "Antigravity ·" prefix, the model is not on disk).
    $w1 = Get-WorkerBlock $agy 'pieza uno'
    Assert-True ($w1 -match '<strong>Antigravity-Flash \(T2\)</strong>') 'T01 agy worker shows its registered name and level'
    Assert-True ($w1 -match 'Activo · trabajando para Claude-Opus en AAA-T01') 'T01 agy worker: trabajando para <orquestador> en <tarea>'
    Assert-True ($w1 -match 'Actividad: Trabajando · delegado desde 11:00 · pieza uno') 'T01 agy worker: real activity from the conversation file'
    Assert-True ($w1 -match 'Tarea: AAA-T01 · Tarea de prueba T01') 'T01 agy worker: task'
    $w2 = Get-WorkerBlock $agy 'pieza dos'
    Assert-True ($w2 -match 'Antigravity-Sonnet' -and $w2 -match 'Actividad: Sin actividad reciente · última hace 20 min') 'quiet conversation (20 min)'
    $w3 = Get-WorkerBlock $agy 'pieza tres'
    Assert-True ($w3 -match 'Probablemente detenido · delegado por Claude-Opus en AAA-T01' -and $w3 -notmatch 'trabajando para') 'stopped conversation (90 min) is listed, not counted as active'
    # without the .db: the json that the orchestrator saved from the run
    $w4 = Get-WorkerBlock $agy 'pieza cuatro'
    Assert-True ($w4 -match 'Antigravity-Hydrogen' -and $w4 -match 'Actividad: Sin actividad reciente · última hace 25 min') 'json of the run as the source when the conversation file is missing'
    # nothing on disk: no activity signal, still listed
    $w5 = Get-WorkerBlock $agy 'pieza cinco'
    Assert-True ($w5 -match 'Actividad: sin señal de actividad') 'a reference without any file has no activity signal'
    # no reference: the application comes from the name
    $w6 = Get-WorkerBlock $agy 'pieza seis sin referencia'
    Assert-True ($w6 -match '<strong>Antigravity-Opus \(T2\)</strong>' -and $w6 -match 'trabajando para Claude-Opus en AAA-T01' -and $w6 -match 'Actividad: sin señal de actividad') 'no reference: application from the worker name'
    # another orchestrator
    $w7 = Get-WorkerBlock $agy 'pieza siete'
    Assert-True ($w7 -match 'trabajando para Codex en AAA-T02' -and $w7 -match 'Actividad: Trabajando') 'agy worker delegated by Codex is in the Antigravity card'
    Assert-True (@(Get-WorkerBlocks $agy).Count -eq 7) 'seven agy workers in the Antigravity card'

    # The orchestrators keep their delegations and none of the workers leaks into another application's card.
    Assert-True ($claude -match 'Antigravity-Flash \(T2\) desde 11:00 · Trabajando · pieza uno') 'Claude-Opus card still lists the agy delegation'
    Assert-True ($codex -match 'Antigravity-Flash \(T2\) desde 11:30 · Trabajando · pieza siete') 'Codex card still lists the agy delegation'
    Assert-True (@(Get-WorkerBlocks $claude).Count -eq 0 -and @(Get-WorkerBlocks $codex).Count -eq 0) 'agy workers are not placed in the Claude or Codex cards'

    # Nothing of the conversations is printed: no content, no ids, no file locations.
    $html = [System.Net.WebUtility]::HtmlDecode([IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html')))
    Assert-True ($html -notmatch 'SECRET-CONTENT') 'conversation and run content never printed'
    $blocks = (Get-WorkerBlocks $agy) -join "`n" -replace '<[^>]+>', ' '
    Assert-True ($blocks -ne '' -and $blocks -notmatch '\[ref:' -and $blocks -notmatch 'agy:' -and $blocks -notmatch 'c3333333') 'worker blocks keep the reference and the ids out of the page'
    Assert-True ($html -notmatch 'antigravity-cli|conversations\\|\.db\b|agy-01\.json') 'file locations not printed'

    # --- A returned worker and a finished task are no longer active ---
    Write-Card 'T01' 'terminada' 'Claude-Opus' @((Deleg 'Claude-Opus' '11:00' 'Antigravity-Flash' "agy:$($ag.work)" 'pieza nueve'))
    Remove-Item (Join-Path $channel 'tareas\AAA\T02-x.md')
    $agents = Run-Viewer
    $agy = Get-AppCard $agents 'Antigravity'
    Assert-True ($agy -notmatch 'pieza nueve' -and (Get-CardStatus $agy).Groups[2].Value -eq 'sin actividad') 'a delivered task has no active agy worker: the card says sin actividad'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($env:SEG_KEEP) { Write-Host $temp } elseif ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'GEM-T04 agy worker PASS'
