# Renders the task board, integration evidence, agents, and roadmap as a friendly HTML page. Every
# tab is derived from live sources (task cards, RESUMEN.md, pending patches, integration reports,
# PROGRESS.md and docs/ROADMAP_STAGES.md of the repository), never from a hand-kept status file.
# The page reloads itself every 30 s, so a browser refresh always shows the last generated version.
# Completed stages are archived in the view only; source files are never moved or rewritten.
#
# Normal use: double-click Seguimiento.cmd. The page opens in its own Edge window (separate profile)
# and a hidden watcher regenerates it whenever one of its sources changes. Closing that
# window ends the watcher. Only one watcher runs at a time. Nothing is installed.
#
#   ver-seguimiento.ps1              regenerate, open the window, watch until it is closed
#   ver-seguimiento.ps1 -NoAbrir     regenerate only
#   ver-seguimiento.ps1 -NoAbrir -Vigilar   regenerate on changes without a window (until stopped)

param(
    [Parameter(Mandatory)][string]$ChannelPath,
    [Parameter(Mandatory)][string]$RepoPath,
    [switch]$NoAbrir,
    [switch]$Vigilar,
    [switch]$LeerApi,
    [string]$ApiBaseUrl = 'http://127.0.0.1:8000',
    [ValidateRange(1, 120)][int]$ApiTimeoutSeconds = 5,
    # SEG-T11: where subagent transcripts live, and the activity thresholds (minutes).
    [string[]]$SubagentRoots = @((Join-Path $HOME '.claude\projects')),
    # SEG-T15: roots of the local Copilot (session-state\<id>\events.jsonl) and Codex (sessions\**\rollout-*-<id>.jsonl) data.
    [string]$CopilotRoot = (Join-Path $HOME '.copilot'),
    [string]$CodexRoot = (Join-Path $HOME '.codex'),
    [ValidateRange(1, 1440)][int]$ActiveMinutes = 15,
    [ValidateRange(1, 10080)][int]$StoppedMinutes = 60,
    [Nullable[datetime]]$NowOverride = $null
)

$ErrorActionPreference = 'Stop'
$script:apiBaseUrl = $ApiBaseUrl.TrimEnd('/')
$script:apiTimeoutSeconds = $ApiTimeoutSeconds
$script:subagentRoots = @($SubagentRoots)
$script:copilotRoot = $CopilotRoot
$script:codexRoot = $CodexRoot
$script:activeMinutes = $ActiveMinutes
$script:stoppedMinutes = $StoppedMinutes
$script:nowOverride = $NowOverride
$channel = (Resolve-Path -LiteralPath $ChannelPath).Path
$repoRoot = (Resolve-Path -LiteralPath $RepoPath).Path
$summary = Join-Path $channel 'RESUMEN.md'
$pendingPatchesDir = Join-Path $channel 'parches\pendientes'
$repoProgress = Join-Path $repoRoot 'PROGRESS.md'
$repoStagePlan = Join-Path $repoRoot 'docs\ROADMAP_STAGES.md'
$roadmap = Join-Path $channel 'ROADMAP_VISTA.md'
$output = Join-Path $channel 'SEGUIMIENTO.html'

$tasksDir = Join-Path $channel 'tareas'
$integrationDir = Join-Path $channel 'pruebas\integracion'
$integrationReads = Join-Path $integrationDir 'LECTURAS.md'
$flowCmd = Join-Path (Split-Path $channel -Parent) 'Flujo.cmd'
$ideasManagerCmd = Join-Path $channel 'GestionSeguimiento.cmd'
$cloudCmd = Join-Path $channel 'PrepararPaqueteCloud.cmd'
$cloudTrackingCmd = Join-Path $channel 'GestionAsignacionesCloud.cmd'
$ideasModulePath = Join-Path $channel 'IdeasPrompts.psm1'
$ideasStorePath = Join-Path (Join-Path $channel 'ideas') 'prompts.json'
$cloudAssignmentsModulePath = Join-Path $PSScriptRoot 'CloudAssignments.psm1'
$cloudAssignmentsPath = Join-Path $channel 'asignaciones-cloud.json'
$flowListener = $null
$flowToken = ''
$ideasToken = ''
$cloudToken = ''
$cloudTrackingToken = ''
$flowPort = 0
$ideasModuleAvailable = $false
$cloudAssignmentsModuleAvailable = $false
if (Test-Path -LiteralPath $ideasModulePath -PathType Leaf) {
    try {
        Import-Module $ideasModulePath -Force -ErrorAction Stop
        $ideasModuleAvailable = $true
    } catch {
        Write-Warning "La biblioteca de ideas no esta disponible; el resto del seguimiento continua. $($_.Exception.Message)"
    }
}
if (Test-Path -LiteralPath $cloudAssignmentsModulePath -PathType Leaf) {
    try {
        Import-Module $cloudAssignmentsModulePath -Force -ErrorAction Stop
        $cloudAssignmentsModuleAvailable = $true
    } catch {
        Write-Warning "El registro de asignaciones cloud no esta disponible; el resto del seguimiento continua. $($_.Exception.Message)"
    }
}

function Encode([string]$text) { [System.Net.WebUtility]::HtmlEncode($text) }
function Inline([string]$text) { (Encode $text) -replace '`([^`]*)`', '<code>$1</code>' }
function Get-ApiJson([string]$path) {
    $baseUri = [uri]::new($script:apiBaseUrl)
    if ($baseUri.Scheme -notin @('http', 'https') -or $baseUri.UserInfo -or $baseUri.Query -or $baseUri.Fragment) { throw 'ApiBaseUrl debe usar HTTP(S) y no incluir credenciales, query ni fragmento.' }
    $uri = [uri]::new($baseUri, $path)
    $response = Invoke-WebRequest -Method Get -Uri $uri -TimeoutSec $script:apiTimeoutSeconds -NoProxy -ErrorAction Stop
    $value = ConvertFrom-Json -InputObject $response.Content -AsHashtable -NoEnumerate -ErrorAction Stop
    return ,$value
}
function Get-ApiBoardHtml {
    $source = 'URL inválida'
    try {
        $displayUri = [uri]::new($script:apiBaseUrl)
        $source = Encode ($displayUri.GetLeftPart([System.UriPartial]::Authority) + $displayUri.AbsolutePath.TrimEnd('/'))
    } catch { }
    try {
        $groups = Get-ApiJson '/api/v1/task-groups'
        $tasks = Get-ApiJson '/api/v1/tasks'
        if ($groups -isnot [System.Array] -or $tasks -isnot [System.Array]) { throw 'Schema inválido: grupos y tareas deben ser listas JSON.' }
        foreach ($group in $groups) {
            if ($group -isnot [System.Collections.IDictionary] -or -not $group.id -or -not $group.title -or $null -eq $group.authorized -or $group.counts -isnot [System.Collections.IDictionary]) { throw 'Schema inválido en grupo: se requieren id, title, authorized y counts.' }
            foreach ($countName in $group.counts.Keys) {
                $countValue = $group.counts[$countName]
                if ($countValue -isnot [byte] -and $countValue -isnot [sbyte] -and $countValue -isnot [int16] -and $countValue -isnot [uint16] -and $countValue -isnot [int32] -and $countValue -isnot [uint32] -and $countValue -isnot [int64] -and $countValue -isnot [uint64]) { throw "Schema inválido: count '$countName' de grupo $($group.id) debe ser entero." }
                if ([long]$countValue -lt 0) { throw "Schema inválido: count '$countName' de grupo $($group.id) no puede ser negativo." }
            }
        }
        $groupIds = @($groups | ForEach-Object { [string]$_.id })
        foreach ($task in $tasks) {
            if ($task -isnot [System.Collections.IDictionary] -or -not $task.id -or -not $task.group_id -or -not $task.title -or -not $task.state -or -not $task.status) { throw 'Schema inválido en tarea: se requieren id, group_id, title, state y status.' }
            if ($task.group_id -notin $groupIds) { throw "Schema inválido: la tarea $($task.id) referencia un grupo ausente." }
        }
        foreach ($group in $groups) {
            $groupTasks = @($tasks | Where-Object { $_.group_id -eq $group.id })
            $actual = @{}; foreach ($task in $groupTasks) { $key = [string]$task.status; $actual[$key] = 1 + [int]$actual[$key] }
            $keys = @($actual.Keys) + @($group.counts.Keys) | Select-Object -Unique
            foreach ($key in $keys) {
                if ([long]$actual[$key] -ne [long]$group.counts[$key]) { throw "Conteo API inconsistente para grupo $($group.id), estado $key." }
            }
        }
        $events = [System.Collections.Generic.List[object]]::new()
        $after = 0
        do {
            $page = Get-ApiJson "/api/v1/team-events?after=$after&limit=1000"
            if ($page -isnot [System.Array]) { throw 'Schema inválido: eventos debe ser una lista JSON.' }
            foreach ($event in $page) {
                if ($event -isnot [System.Collections.IDictionary] -or $null -eq $event.seq -or -not $event.type -or -not $event.actor -or -not $event.created_at -or $null -eq $event.payload) { throw 'Schema inválido en evento: faltan seq, type, actor, created_at o payload.' }
                if ([long]$event.seq -le $after) { throw 'Schema inválido: secuencia de eventos no avanza.' }
                $after = [long]$event.seq
                $events.Add($event)
            }
        } while ($page.Count -eq 1000)

        $groupRows = foreach ($group in $groups) {
            $tasksInGroup = @($tasks | Where-Object { $_.group_id -eq $group.id })
            $taskRows = foreach ($task in $tasksInGroup) {
                $tokens = if ($null -ne $task.tokens) { Encode ([string]$task.tokens) } else { '<span class="meta">Desconocidos · API no provee tokens</span>' }
                $cost = if ($null -ne $task.cost) { Encode ([string]$task.cost) } else { '<span class="meta">Desconocido · API no provee costo</span>' }
                $assignee = if ($null -ne $task.assignee) { Encode ([string]$task.assignee) } else { '<span class="meta">Sin dato</span>' }
                $dependencyData = if ($null -ne $task.depends_on) { Encode (ConvertTo-Json -InputObject $task.depends_on -Compress -Depth 5) } else { '<span class="meta">Desconocido · API no provee dependencias</span>' }
                $blockerData = if ($null -ne $task.blockers) { Encode (ConvertTo-Json -InputObject $task.blockers -Compress -Depth 5) } else { '<span class="meta">Desconocido · API no provee bloqueos</span>' }
                $attempt = if ($null -ne $task.attempt) { Encode ([string]$task.attempt) } else { '<span class="meta">Desconocido</span>' }
                $status = Encode ([string]$task.status)
                "<tr><td><strong>$(Encode ([string]$task.title))</strong><div class=""meta"">$(Encode ([string]$task.id))</div></td><td><code>$status</code><div class=""meta"">Estado core: $(Encode ([string]$task.state))</div></td><td>$assignee</td><td>$dependencyData<div class=""meta"">Bloqueos: $blockerData</div></td><td>$attempt</td><td>$tokens</td><td>$cost</td></tr>"
            }
            $authorization = if ($group.authorized) { 'autorizado por API' } else { 'no autorizado por API' }
            "<section class=""panel""><h2>$(Encode ([string]$group.title)) <span class=""meta"">$(Encode ([string]$group.id)) · $authorization</span></h2><p class=""meta"">$(($tasksInGroup.Count)) tareas devueltas por API · conteos de estado verificados contra la lista de tareas.</p><table><tr><th>Tarea</th><th>Estado</th><th>Responsable</th><th>Dependencias y bloqueos (API)</th><th>Attempts core</th><th>Tokens</th><th>Costo</th></tr>$($taskRows -join "`n")</table></section>"
        }
        $eventRows = foreach ($event in @($events | Sort-Object seq -Descending | Select-Object -First 100)) {
            "<tr><td>$(Encode ([string]$event.seq))</td><td><code>$(Encode ([string]$event.type))</code></td><td>$(Encode ([string]$event.group_id))</td><td>$(Encode ([string]$event.task_id))</td><td>$(Encode ([string]$event.actor))</td><td>$(Encode ([string]$event.created_at))</td><td><code>$(Encode (ConvertTo-Json -InputObject $event.payload -Compress -Depth 8))</code></td></tr>"
        }
        $empty = if (-not $groups.Count -and -not $tasks.Count -and -not $events.Count) { '<p class="meta">API confirmó grupos, tareas y eventos vacíos.</p>' } else { '' }
        return "<p class=""meta"">Fuente: API de solo lectura <code>$source</code>. Estado core literal; `accepted` no se interpreta como integrada. Git y reportes de integración se leen aparte desde el checkout/canal locales.</p>$empty$($groupRows -join "`n")<section class=""panel""><h2>Eventos core <span class=""meta"">$($events.Count) leídos; se muestran los últimos 100</span></h2><table><tr><th>Seq</th><th>Evento</th><th>Grupo</th><th>Tarea</th><th>Actor</th><th>Fecha API</th><th>Payload</th></tr>$($eventRows -join "`n")</table></section>"
    } catch {
        $detail = Encode ($_.Exception.Message + $(if ($_.Exception.InnerException) { ' (' + $_.Exception.InnerException.Message + ')' }))
        return "<section class=""stop""><strong>API desconocida o no disponible</strong><p>No se pudo validar el snapshot; no se presenta como tablero vacío. Fuente configurada: <code>$source</code>.</p><p class=""meta"">$detail</p></section>"
    }
}
function Get-Field([string]$text, [string]$name) {
    if ($text -match "(?m)^\|\s*$name\s*\|\s*(.*?)\s*\|\s*$") { $Matches[1] } else { '' }
}
function Get-StagePhases([string]$text) {
    if ($text -notmatch '(?s)## Fases de tareas\s*(.+?)(?=\r?\n## |\z)') { return @() }
    $phases = foreach ($line in ($Matches[1] -split "`r?`n")) {
        if ($line -notmatch '^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|$') { continue }
        $label = $Matches[1].Trim()
        $taskList = $Matches[2].Trim()
        $description = $Matches[3].Trim()
        if ($label -eq 'Fase' -or $label -match '^:?-{2,}:?$') { continue }
        $taskIds = @([regex]::Matches($taskList, '(?i)\bT\d+\b') | ForEach-Object { $_.Value.ToUpperInvariant() } | Select-Object -Unique)
        if (-not $taskIds) { continue }
        [pscustomobject]@{ Label = $label; TaskIds = $taskIds; Description = $description }
    }
    @($phases)
}
function Get-CommitToken([string]$value) {
    if ($value -match '(?i)`([0-9a-f]{7,40})`') { $Matches[1].ToLowerInvariant() }
    elseif ($value -match '^\s*([0-9a-f]{7,40})(?:\s|$)') { $Matches[1].ToLowerInvariant() }
    else { '' }
}
# "Tokens" field of a task card: "Claude: total N = entrada a + cacheados b + salida c (medido); Copilot: no disponible".
# SEG-T08: also "↳ Modelo (delegado por Orquestador, exacto|estimado por ventana): total N = ..." / "(..., sin datos)"
# worker entries (name may hold spaces, dots, parentheses) and a final "Total de la tarea: N". Kind: agent | worker | total.
function Get-TokenEntries([string]$value) {
    foreach ($part in ($value -split ';')) {
        if ($part -match '^\s*↳\s*(.+)\s\(delegado por\s+([^,)]+?)\s*(?:,\s*([^)]*?))?\s*\)\s*(?::\s*(.*?))?\s*$') {
            $name = $Matches[1].Trim(); $orch = $Matches[2].Trim(); $kind = "$($Matches[3])".Trim(); $rest = "$($Matches[4])"
            # SEG-T15: "exacto por ventana" (a Copilot / Codex worker measured inside its delegation window) keeps its full tag.
            $tag = if ($kind -match '(?i)estimad') { 'estimado' } elseif ($kind -match '(?i)exact[a-z]*\s+por\s+ventana') { 'exacto por ventana' } elseif ($kind -match '(?i)exact') { 'exacto' } elseif ($kind -match '(?i)sin datos' -or $rest -match '(?i)sin datos') { 'sin datos' } else { $kind }
            if ($rest -match '(?i)total\s+(\d+)\s*=\s*entrada\s+(\d+)\s*\+\s*cacheados\s+(\d+)\s*\+\s*salida\s+(\d+)') {
                [pscustomobject]@{ Kind = 'worker'; Agent = $name; Orchestrator = $orch; Total = [long]$Matches[1]; Input = [long]$Matches[2]; Cached = [long]$Matches[3]; Output = [long]$Matches[4]; Source = $tag }
            } else {
                if (-not $tag) { $tag = 'sin datos' }
                [pscustomobject]@{ Kind = 'worker'; Agent = $name; Orchestrator = $orch; Total = $null; Input = $null; Cached = $null; Output = $null; Source = $tag }
            }
        } elseif ($part -match '^\s*Total de la tarea:\s*(\d+)\s*$') {
            [pscustomobject]@{ Kind = 'total'; Agent = $null; Orchestrator = $null; Total = [long]$Matches[1]; Input = $null; Cached = $null; Output = $null; Source = '' }
        } elseif ($part -match '^\s*([A-Za-z0-9-]+):\s*total\s+(\d+)\s*=\s*entrada\s+(\d+)\s*\+\s*cacheados\s+(\d+)\s*\+\s*salida\s+(\d+)\s*\(([^)]+)\)') {
            [pscustomobject]@{ Kind = 'agent'; Agent = $Matches[1]; Orchestrator = $null; Total = [long]$Matches[2]; Input = [long]$Matches[3]; Cached = [long]$Matches[4]; Output = [long]$Matches[5]; Source = $Matches[6].Trim() }
        } elseif ($part -match '^\s*([A-Za-z0-9-]+):\s*(.+?)\s*$') {
            [pscustomobject]@{ Kind = 'agent'; Agent = $Matches[1]; Orchestrator = $null; Total = $null; Input = $null; Cached = $null; Output = $null; Source = $Matches[2] }
        }
    }
}
function Format-TokenCount([long]$value) {
    if ($value -ge 1000000) { '{0:0.0} M' -f ($value / 1000000) }
    elseif ($value -ge 1000) { '{0:0} k' -f ($value / 1000) }
    else { "$value" }
}
function Get-TokensHtml([string]$value) {
    $entries = @(Get-TokenEntries $value)
    if (-not $entries) { return '<span class="meta">Sin registro</span>' }
    $items = foreach ($entry in $entries) {
        if ($entry.Kind -eq 'total') {
            "<div class=""token-total""><strong>Total de la tarea: $(Format-TokenCount $entry.Total)</strong></div>"
        } elseif ($entry.Kind -eq 'worker') {
            $style = 'margin-left:1em'
            if ($null -eq $entry.Total) {
                "<div class=""token-worker"" style=""$style"">&#8627; $(Encode $entry.Agent) <span class=""meta"">(delegado por $(Encode $entry.Orchestrator))</span>: <span class=""meta"">$(Encode $entry.Source)</span></div>"
            } else {
                $detail = "Entrada $($entry.Input) · cacheados $($entry.Cached) · salida $($entry.Output) · $($entry.Source)"
                "<div class=""token-worker"" style=""$style"" title=""$(Encode $detail)"">&#8627; $(Encode $entry.Agent) <span class=""meta"">(delegado por $(Encode $entry.Orchestrator))</span>: <strong>$(Format-TokenCount $entry.Total)</strong> <span class=""meta"">($(Encode $entry.Source) · salida $(Format-TokenCount $entry.Output))</span></div>"
            }
        } elseif ($null -eq $entry.Total) {
            "<div>$(Encode $entry.Agent): <span class=""meta"">$(Encode $entry.Source)</span></div>"
        } else {
            $detail = "Entrada $($entry.Input) · cacheados $($entry.Cached) · salida $($entry.Output) · $($entry.Source)"
            "<div title=""$(Encode $detail)"">$(Encode $entry.Agent): <strong>$(Format-TokenCount $entry.Total)</strong> <span class=""meta"">(salida $(Format-TokenCount $entry.Output))</span></div>"
        }
    }
    $items -join ''
}
function Get-TaskSection([string]$text, [string]$heading) {
    $pattern = "(?s)##\s*$([regex]::Escape($heading))\s*(.+?)(?=\r?\n##\s|\z)"
    if ($text -match $pattern) { $Matches[1].Trim() } else { '' }
}
function Get-TaskEventDate([string]$history, [string]$action) {
    $date = ''
    foreach ($line in ($history -split "`r?`n")) {
        if ($line -match ("^\|\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)\s*\|\s*[^|]+\|\s*" + [regex]::Escape($action) + "\s*\|")) {
            $date = $Matches[1].Trim()
        }
    }
    $date
}
# SEG-T04: dates are local time; a date-only value counts as 00:00. Anything unparseable gives $null.
function ConvertTo-ActivityDate([string]$date, [string]$hour = '', [string]$minute = '') {
    try {
        $value = [datetime]::ParseExact($date, 'yyyy-MM-dd', [cultureinfo]::InvariantCulture)
        if ($hour -ne '' -and $minute -ne '') {
            if ([int]$hour -gt 23 -or [int]$minute -gt 59) { return $null }
            $value = $value.AddHours([int]$hour).AddMinutes([int]$minute)
        }
        return $value
    } catch { return $null }
}
# Rows of a "## Historial" table: | AAAA-MM-DD [HH:mm] | agente | acción | detalle |
function Get-HistoryRows([string]$history) {
    foreach ($line in ($history -split "`r?`n")) {
        if ($line -notmatch '^\|\s*(\d{4}-\d{2}-\d{2})(?:\s+(\d{2}):(\d{2}))?\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*(.*?)\s*\|?\s*$') { continue }
        $when = ConvertTo-ActivityDate $Matches[1] $Matches[2] $Matches[3]
        if (-not $when) { continue }
        [pscustomobject]@{ Date = $when; Agent = $Matches[4]; Action = $Matches[5]; Detail = $Matches[6] }
    }
}
# Lines of the "## Historial de decisiones" section of ETAPA.md: - AAAA-MM-DD [HH:mm] · Agente · texto
function Get-StageHistoryEntries([string]$stageText) {
    foreach ($line in ((Get-TaskSection $stageText 'Historial de decisiones') -split "`r?`n")) {
        if ($line -notmatch '^\s*[-*]\s*(\d{4}-\d{2}-\d{2})(?:[ T](\d{2}):(\d{2}))?\s*·\s*([^·]*?)\s*·\s*(.+?)\s*$') { continue }
        $when = ConvertTo-ActivityDate $Matches[1] $Matches[2] $Matches[3]
        if (-not $when) { continue }
        [pscustomobject]@{ Date = $when; Agent = $Matches[4]; Text = $Matches[5] }
    }
}
function Format-ActivityDate($date) { ([datetime]$date).ToString('yyyy-MM-dd HH:mm', [cultureinfo]::InvariantCulture) }
function Format-TimeAgo($date) {
    $span = (Get-Date) - ([datetime]$date)
    if ($span.TotalMinutes -lt 0) { return 'hace 0 minutos' }
    if ($span.TotalMinutes -lt 60) { $n = [int][math]::Floor($span.TotalMinutes); return "hace $n $(if ($n -eq 1) { 'minuto' } else { 'minutos' })" }
    if ($span.TotalHours -lt 24) { $n = [int][math]::Floor($span.TotalHours); return "hace $n $(if ($n -eq 1) { 'hora' } else { 'horas' })" }
    $n = [int][math]::Floor($span.TotalDays)
    "hace $n $(if ($n -eq 1) { 'día' } else { 'días' })"
}
function Get-ClosureLabelHtml($date, [bool]$fromCard) {
    if (-not $date) { return '<span class="meta">fecha de cierre desconocida</span>' }
    $source = if ($fromCard) { ' &middot; según la ficha' } else { '' }
    "Cerrada el $(Encode (Format-ActivityDate $date)) ($(Encode (Format-TimeAgo $date)))$source"
}
# Committer date (local time) of a commit of the repository, or $null.
function Get-CommitDate([string]$commit) {
    if ($commit -notmatch '^(?i)[0-9a-f]{7,40}$') { return $null }
    $raw = git -C $repoRoot log -1 --format=%cI $commit 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
    $parsed = [datetimeoffset]::MinValue
    if ([datetimeoffset]::TryParse(([string]$raw).Trim(), [cultureinfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$parsed)) { return $parsed.LocalDateTime }
    $null
}
# Entries with a Date property, newest first; entries without a date go last (stable order).
function Sort-NewestFirst([object[]]$entries) {
    @($entries | Sort-Object @{ Expression = { if ($_.Date) { $_.Date } else { [datetime]::MinValue } }; Descending = $true })
}
function Test-TaskPatchRequired([string]$patch) {
    $value = $patch.Trim()
    if (-not $value) { return $false }
    $value -notmatch '(?i)^\s*(?:—|–|-|ninguno|none|no aplica)(?:\s|$|\()'
}
# "T01, T03" / "T02-T08, T10" / "-" -> T01, T03 ... (same stage). Same expansion as Expand-TaskDeps in
# sistema\tareas.ps1, which tomar-tarea.ps1 uses; the range accepts a hyphen or an en dash.
function Expand-DependencyCodes([string]$value) {
    $dash = [string][char]0x2013
    $codes = foreach ($part in ($value -split ',')) {
        if ($part -match "T(\d+)\s*[$dash-]\s*T(\d+)") {
            foreach ($n in [int]$Matches[1]..[int]$Matches[2]) { 'T{0:D2}' -f $n }
        } elseif ($part -match 'T(\d+)') {
            'T{0:D2}' -f [int]$Matches[1]
        }
    }
    @($codes | Select-Object -Unique)
}
# Rule 27, the same decision as Get-DependencyBlock in sistema\tareas.ps1: $null when the dependency
# releases its dependents under the stage mode (probada, integrada or terminada), otherwise why it does not.
function Get-DependencyWait($dependency, [string]$mode) {
    if ($dependency.State -eq 'integrada') { return $null }
    if ($dependency.State -ne 'terminada') { return [string]$dependency.State }
    $hasPatch = [regex]::Matches([string]$dependency.Patch, '\d{3}-[^`\s,()]+\.patch').Count -gt 0
    if (-not $hasPatch -or $mode -eq 'terminada') { return $null }
    if ($mode -eq 'integrada') { return 'terminada, falta integrarla' }
    if ($dependency.Verdict -eq 'probada') { return $null }
    if ($dependency.Verdict -eq 'prueba fallida') { return 'terminada, prueba fallida' }
    'terminada, sin pruebas aprobadas'
}
function Safe-Snippet([string]$text) {
    $value = $text -replace '(?i)\b[A-Z]:\\[^\s<>""|]+', '[ruta omitida]'
    $value = $value -replace '\\\\[^\s<>""|]+', '[ruta omitida]'
    $value = $value -replace '(?i)\bfile:///[^)\s]+', '[enlace local omitido]'
    $value = $value -replace '\[([^\]]+)\]\([^)]+\)', '$1'
    $value = $value -replace '\*\*|__|[`*#]', ''
    $value = ($value -split "`r?`n" | ForEach-Object { $_.Trim() -replace '^\s*[-*]\s*', '' }) -join ' '
    $value = $value -replace '\s+', ' '
    if ($value.Length -gt 220) { $value = $value.Substring(0, 217) + '...' }
    $value.Trim()
}
function Resolve-IntegrationCommit([string]$commit) {
    if ($commit -notmatch '^(?i)[0-9a-f]{7,40}$') { return '' }
    $resolved = git -C $repoRoot rev-parse --verify "$commit^{commit}" 2>$null
    if ($LASTEXITCODE -ne 0 -or $resolved -notmatch '^(?i)[0-9a-f]{40}$') { return '' }
    $resolved.Trim().ToLowerInvariant()
}
function Get-StageClosure([string]$stageName, [string]$stageText, [object[]]$tasks, [bool]$taskReadFailed, [string]$readsText) {
    $state = (Get-Field $stageText 'Estado').Trim().ToLowerInvariant()
    if ($state -notin @('cerrada', 'cerrado', 'closed', 'complete', 'completed')) {
        if ($state -in @('abierta', 'abierto', 'activa', 'activo', 'en curso', 'open', 'active')) {
            return [pscustomobject]@{ Status = 'active'; Reason = ''; Commit = ''; Report = ''; Stage = $stageName }
        }
        return [pscustomobject]@{ Status = 'unknown'; Reason = 'El estado de cierre no está definido o no es reconocible.'; Commit = ''; Report = ''; Stage = $stageName }
    }

    $closeCommit = Get-CommitToken (Get-Field $stageText 'Commit de cierre')
    if (-not $closeCommit) {
        return [pscustomobject]@{ Status = 'unknown'; Reason = 'La ficha no contiene un hash válido de commit de cierre.'; Commit = ''; Report = ''; Stage = $stageName }
    }
    $resolvedCloseCommit = Resolve-IntegrationCommit $closeCommit
    if (-not $resolvedCloseCommit) {
        return [pscustomobject]@{ Status = 'unknown'; Reason = 'El commit de cierre no se pudo confirmar en el repositorio actual.'; Commit = $closeCommit; Report = ''; Stage = $stageName }
    }
    if ($taskReadFailed -or -not $tasks.Count) {
        return [pscustomobject]@{ Status = 'unknown'; Reason = 'No se pudieron verificar todas las fichas de tarea o la etapa no tiene tareas.'; Commit = $closeCommit; Report = ''; Stage = $stageName }
    }
    foreach ($task in $tasks) {
        if ($task.State -ne 'integrada') {
            return [pscustomobject]@{ Status = 'unknown'; Reason = "La tarea $($task.Id) no está integrada."; Commit = $closeCommit; Report = ''; Stage = $stageName }
        }
        # A task that states it has no patch (for example an analysis delivered in the channel)
        # has nothing to integrate; an empty Parche field is not enough.
        if ($task.Patch.Trim() -and -not (Test-TaskPatchRequired $task.Patch)) { continue }
        $taskCommit = Get-CommitToken $task.Commit
        if (-not $taskCommit) {
            return [pscustomobject]@{ Status = 'unknown'; Reason = "La tarea $($task.Id) no tiene un commit válido."; Commit = $closeCommit; Report = ''; Stage = $stageName }
        }
        # A stage is usually integrated over several commits: each task commit must be contained
        # in the closing commit (the same commit or an earlier one in its history).
        $resolvedTaskCommit = Resolve-IntegrationCommit $taskCommit
        $contained = $false
        if ($resolvedTaskCommit) {
            git -C $repoRoot merge-base --is-ancestor $resolvedTaskCommit $resolvedCloseCommit 2>$null
            $contained = $LASTEXITCODE -eq 0
        }
        if (-not $contained) {
            return [pscustomobject]@{ Status = 'unknown'; Reason = "El commit de $($task.Id) no está contenido en el commit de cierre."; Commit = $closeCommit; Report = ''; Stage = $stageName }
        }
    }

    $reportText = Get-Field $stageText 'Reporte de integración'
    # SEG-T14: report ids are yyyy-MM-dd_HHmm (old) or yyyy-MM-dd_HHmmss_fff (probar.ps1 since ORQ-T37). Written inline on
    # purpose: verificar-etapas.ps1 loads only a fixed list of functions from this file, so this function needs no new helper.
    $reportId = if ($reportText -match '(\d{4}-\d{2}-\d{2}_\d{4}(?:\d{2}_\d{3})?)') { $Matches[1] }
        elseif ((Get-Field $stageText 'Commit de cierre') -match '(?i)integraci[oó]n[^0-9]*(\d{4}-\d{2}-\d{2}_\d{4}(?:\d{2}_\d{3})?)') { $Matches[1] }
        else { '' }
    if (-not $reportId) {
        return [pscustomobject]@{ Status = 'unknown'; Reason = 'La ficha no identifica un reporte de integración.'; Commit = $closeCommit; Report = ''; Stage = $stageName }
    }
    $reportName = "${reportId}_integracion.md"
    $reportPath = Join-Path $integrationDir $reportName
    if (-not (Test-Path -LiteralPath $reportPath -PathType Leaf)) {
        return [pscustomobject]@{ Status = 'unknown'; Reason = 'El reporte de integración indicado no existe.'; Commit = $closeCommit; Report = ''; Stage = $stageName }
    }
    $greenEvidence = $false
    foreach ($line in ($readsText -split "`r?`n")) {
        $columns = @($line.Trim().Trim('|').Split('|') | ForEach-Object { $_.Trim() })
        if ($columns.Count -ge 3 -and $columns[0] -eq $reportName -and $columns[1] -eq 'OK' -and $columns[2] -match '\d+\s+passed') {
            $greenEvidence = $true
            break
        }
    }
    if (-not $greenEvidence) {
        return [pscustomobject]@{ Status = 'unknown'; Reason = 'El registro del reporte no confirma un resultado OK con pruebas pasadas.'; Commit = $closeCommit; Report = $reportName; Stage = $stageName }
    }
    [pscustomobject]@{ Status = 'complete'; Reason = ''; Commit = $closeCommit; Report = $reportName; Stage = $stageName }
}

# SEG-T14: sortable time key of a report name in either format. yyyy-MM-dd_HHmm becomes yyyy-MM-dd_HHmm00_000 so old and
# new names compare correctly as plain strings (a name sorted on its own text would put _1200 after _011321_133).
# Returns '' when the text is not a report name.
function Get-ReportTimeKey([string]$name) {
    if ($name -match '^(\d{4}-\d{2}-\d{2})_(\d{4})(?:(\d{2})_(\d{3}))?_integracion\.md$') {
        $seconds = if ($Matches[3]) { $Matches[3] } else { '00' }
        $millis = if ($Matches[4]) { $Matches[4] } else { '000' }
        return "$($Matches[1])_$($Matches[2])${seconds}_$millis"
    }
    ''
}

# SEG-T13: integration round in progress. parches\ronda.txt lists the patches applied since the last integration commit
# (aplicar.ps1 appends, the commit clears it). Its write time is the last application; the tests of the round are "en curso"
# until an integration report (pruebas\integracion\*_integracion.md) is newer than that.
function Get-IntegrationRound {
    $none = [pscustomobject]@{ Active = $false; Patches = @(); Since = $null; TestsRunning = $false }
    $file = Join-Path $channel 'parches\ronda.txt'
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return $none }
    try {
        $patches = @(Get-Content -LiteralPath $file -Encoding utf8 -ErrorAction Stop | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        $since = (Get-Item -LiteralPath $file -ErrorAction Stop).LastWriteTime
    } catch { return $none }
    if (-not $patches.Count) { return $none }
    $reportAfter = $false
    if (Test-Path -LiteralPath $integrationDir -PathType Container) {
        $reportAfter = @(Get-ChildItem -LiteralPath $integrationDir -Filter '*_integracion.md' -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt $since }).Count -gt 0
    }
    [pscustomobject]@{ Active = $true; Patches = $patches; Since = $since; TestsRunning = -not $reportAfter }
}
# SEG-T13: one label for phases and stages, first match wins. $kind is 'Fase' or 'Etapa'; $rows are the tasks (State, Patch,
# WaitingFor, Integrating); $closure is, for a stage, 'cerrada' (verified closure), 'sin verificar' (the card says closed
# but it is not verified) or ''. Returns Text and Class, or $null when there is nothing to say (no tasks).
function Get-StatusLabel([string]$kind, [object[]]$rows, [string]$closure = '', $since = $null) {
    $rows = @($rows | Where-Object { $_ })
    $make = { param($text, $class) [pscustomobject]@{ Text = $text; Class = $class } }
    if ($closure -eq 'cerrada') { return & $make "$kind cerrada" 's-integrada' }
    if ($closure -eq 'sin verificar') { return & $make 'cierre no verificado' 's-unknown' }
    if (-not $rows.Count) { return $null }
    # Settled = integrated, or finished with no patch to integrate (analysis deliverables).
    $settled = @($rows | Where-Object { $_.State -eq 'integrada' -or ($_.State -eq 'terminada' -and -not (Test-TaskPatchRequired $_.Patch)) }).Count
    $done = @($rows | Where-Object { $_.State -in 'terminada', 'integrada' }).Count
    if (@($rows | Where-Object { $_.State -eq 'requiere cambios' }).Count) { return & $make 'Con cambios requeridos' 's-requiere-cambios' }
    if (@($rows | Where-Object { $_.Integrating }).Count) {
        $from = if ($since) { ([datetime]$since).ToString('HH:mm', [cultureinfo]::InvariantCulture) } else { '' }
        return & $make "Integrando · pruebas$(if ($from) { " desde $from" })" 's-en-curso'
    }
    if ($settled -eq $rows.Count) {
        if ($kind -eq 'Fase') { return & $make 'Fase completa' 's-integrada' }
        return & $make 'Etapa terminada · falta cerrar' 's-pendiente'
    }
    if ($done -eq $rows.Count) { return & $make "$kind terminada · falta integrar" 's-pendiente' }
    if (@($rows | Where-Object { $_.State -eq 'en curso' }).Count) { return & $make 'En progreso' 's-en-curso' }
    if ($done) { return & $make "Avance parcial · $done de $($rows.Count)" 's-pendiente' }
    # SEG-T10: takeable = disponible/liberada/reabierta with no dependency wait (reuses WaitingFor).
    if (@($rows | Where-Object { $_.State -in 'disponible', 'liberada', 'reabierta' -and -not $_.WaitingFor.Count }).Count) { return & $make 'Disponible' 's-disponible' }
    & $make 'En espera de dependencias' 's-idle'
}

# Agent registry of the channel (AGENTES.md, rule 28): name, application and state. Without the file
# (a checkout without the private channel) the three founding names are assumed active.
function Get-RegisteredAgents {
    $path = Join-Path $channel 'AGENTES.md'
    $rows = @()
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        foreach ($line in [IO.File]::ReadAllLines($path)) {
            if ($line -match '^\|\s*([A-Za-z0-9-]+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(ACTIVO|PAUSADO)\s*\|(?:\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|)?') {
                $rows += [pscustomobject]@{ Name = $Matches[1]; Application = ($Matches[1] -split '-', 2)[0]; Model = $Matches[2]; State = $Matches[4]; Cursor = $Matches[6]; Session = $Matches[7]; Base = '' }
            }
        }
    }
    if (-not $rows) { $rows = @('Claude', 'Codex', 'Copilot') | ForEach-Object { [pscustomobject]@{ Name = $_; Application = $_; Model = ''; State = 'ACTIVO'; Cursor = ''; Session = ''; Base = '' } } }
    # Instances of the same model (rule 28, P-303: base name + digit, e.g. Claude-Opus2) group under their active base.
    foreach ($row in $rows) {
        if ($row.Name -match '^(.+?)\d+$' -and @($rows | Where-Object { $_.Name -eq $Matches[1] -and $_.State -eq 'ACTIVO' })) { $row.Base = $Matches[1] }
    }
    $rows
}

# SEG-T17 (REGLAS 33): ONE table of the applications whose workers Seguimiento follows. A worker belongs to its own application,
# whatever application orchestrates it. A new application is added by ONE entry here, with no per-agent special cases:
#   Key            short lower-case id (the Kind of a parsed reference)
#   Application    name of its card in the Agentes tab (the Aplicación of its agents in AGENTES.md)
#   RefPattern     regex of its worker reference as registrar-delegacion.ps1 writes it ("[ref: ...]" and the JSONL "reference")
#   RefId          scriptblock: reference -> id used to find its activity file
#   BarePattern    a reference without a known prefix that matches this regex belongs here (Claude subagent ids); $null otherwise
#   NamePattern    regex of its name inside a worker name ("Copilot gpt-6-luna"), for delegations that carry no reference
#   ShowAppPrefix  show "App · modelo" in place of the registered worker name (its model is read from the activity file)
#   TailBytes      bytes read from the end of its activity file
#   FindFile       scriptblock: parsed reference {Kind, Id} -> activity file (FileInfo) or $null
#   ReadModel      scriptblock: file -> model name ('' when unknown); $null when the worker name already carries the model
#   PendingTest    scriptblock: parsed records, last activity time -> start time of a running test, or $null
# Only times, status and model names are read from the activity files, never their content.
$script:WorkerApps = @(
    [pscustomobject]@{ Key = 'claude'; Application = 'Claude'; RefPattern = 'agent-[A-Za-z0-9]+'; RefId = { param($reference) $reference }; BarePattern = '[A-Za-z0-9._-]+'
        NamePattern = '(?i)\bclaude\b'; ShowAppPrefix = $false; TailBytes = 65536
        FindFile = { param($ref) Find-SubagentFile $ref.Id }
        ReadModel = $null
        PendingTest = { param($records, $last) Get-ClaudePendingTest $records $last } }
    [pscustomobject]@{ Key = 'copilot'; Application = 'Copilot'; RefPattern = 'copilot:[0-9A-Fa-f][0-9A-Fa-f-]{7,63}'; RefId = { param($reference) ($reference -split ':', 2)[1].ToLowerInvariant() }; BarePattern = $null
        NamePattern = '(?i)\bcopilot\b'; ShowAppPrefix = $true; TailBytes = 262144
        FindFile = { param($ref)
            $path = Join-Path $script:copilotRoot "session-state\$($ref.Id)\events.jsonl"
            if (Test-Path -LiteralPath $path -PathType Leaf) { Get-Item -LiteralPath $path } }
        ReadModel = { param($file) Read-TranscriptModel $file.FullName { param($rec) Get-StringField $rec.data @('model', 'newModel', 'selectedModel', 'currentModel') } @('*"selectedModel"*', '*"model":*') { param($rec) Get-StringField $rec.data @('selectedModel', 'model') } }
        PendingTest = { param($records, $last) Get-CopilotPendingTest $records $last } }
    [pscustomobject]@{ Key = 'codex'; Application = 'Codex'; RefPattern = 'codex:[0-9A-Fa-f][0-9A-Fa-f-]{7,63}'; RefId = { param($reference) ($reference -split ':', 2)[1].ToLowerInvariant() }; BarePattern = $null
        NamePattern = '(?i)\bcodex\b'; ShowAppPrefix = $true; TailBytes = 262144
        FindFile = { param($ref)
            foreach ($sub in 'sessions', 'archived_sessions') {
                $dir = Join-Path $script:codexRoot $sub
                if (-not (Test-Path -LiteralPath $dir -PathType Container)) { continue }
                $found = Get-ChildItem -LiteralPath $dir -Recurse -File -Filter "rollout-*-$($ref.Id).jsonl" -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($found) { return $found }
            } }
        ReadModel = { param($file) Read-TranscriptModel $file.FullName { param($rec) if ($rec.type -eq 'turn_context') { Get-StringField $rec.payload @('model') } } @('*"type":"turn_context"*') { param($rec) Get-StringField $rec.payload @('model') } }
        PendingTest = { param($records, $last) Get-CodexPendingTest $records $last } }
)
# Any application's reference, for the "[ref: ...]" tail of a delegation row.
$script:WorkerRefPattern = '(?:' + (($script:WorkerApps | ForEach-Object { $_.RefPattern }) -join '|') + ')'
function Get-WorkerApp([string]$key) { $script:WorkerApps | Where-Object { $_.Key -eq $key } | Select-Object -First 1 }
# Application (table entry) named inside a worker or agent name: the one mentioned first ("Copilot Claude Sonnet" is Copilot).
function Get-WorkerAppByName([string]$name) {
    $best = $null; $bestIndex = [int]::MaxValue
    foreach ($app in $script:WorkerApps) {
        $m = [regex]::Match($name, $app.NamePattern)
        if ($m.Success -and $m.Index -lt $bestIndex) { $best = $app; $bestIndex = $m.Index }
    }
    $best
}
# Application (table entry) of a delegated worker: from its reference (agent- / copilot: / codex: / ...), else from its name.
function Get-WorkerEntryApp($entry) {
    $reference = Get-EntryReference $entry
    if ($reference) {
        $ref = Get-WorkerReference $reference
        if ($ref) { $app = Get-WorkerApp $ref.Kind; if ($app) { return $app } }
    }
    Get-WorkerAppByName ([string]$entry.Worker)
}
# Application name of an agent that orchestrates: its registered Aplicación, else the application named in it, else its first word.
function Get-AgentApplication([string]$name, [object[]]$registered = @()) {
    $row = @($registered | Where-Object { $_.Name -ieq $name }) | Select-Object -First 1
    if ($row -and $row.Application) { return $row.Application }
    $app = Get-WorkerAppByName $name
    if ($app) { return $app.Application }
    ($name -split '-', 2)[0]
}

# SEG-T05: delegations of a card ("delegada" / "devuelta" rows of its Historial, see registrar-delegacion.ps1).
# Returns Rows (every delegated worker, oldest first, deduplicated by worker) and Active (delegada without a later
# devuelta of the same worker). Defensive: rows that do not match are skipped.
# SEG-T12: also All (every delegation, with Label, Integration, Until and Result) for the Trabajaron column.
function Get-CardDelegations([string]$history, [string]$state = 'en curso') {
    $rows = [System.Collections.Generic.List[object]]::new()
    $active = [System.Collections.Generic.List[object]]::new()
    $all = [System.Collections.Generic.List[object]]::new()
    $orchestrator = ''
    foreach ($row in @(Get-HistoryRows $history | Sort-Object Date)) {
        if ($row.Action -in 'tomada', 'reasignada') {
            # SEG-T11: another agent took the task over; delegations of the previous orchestrator are over.
            for ($k = $active.Count - 1; $k -ge 0; $k--) { if ($active[$k].Orchestrator -ine $row.Agent) { $active.RemoveAt($k) } }
            continue
        }
        if ($row.Action -notin 'delegada', 'devuelta') { continue }
        if ($row.Detail -notmatch '^(?<w>[^(:]+?)(?:\s*\((?<p>[^)]*)\))?\s*:\s*(?<rest>.*)$') { continue }
        $worker = $Matches['w'].Trim(); $paren = $Matches['p']; $rest = $Matches['rest'].Trim()
        $reference = ''
        # SEG-T15: also copilot:<id de sesión> and codex:<id de hilo> (registrar-delegacion.ps1 -Referencia).
        # SEG-T17: the accepted references come from the table of applications ($script:WorkerApps).
        $referenceTail = '\s*\[ref:\s*(' + $script:WorkerRefPattern + ')\s*\]\s*$'
        if ($rest -match $referenceTail) {
            $reference = $Matches[1]; $rest = ($rest -replace $referenceTail, '').Trim()
        }
        if (-not $worker) { continue }
        if ($row.Action -eq 'delegada') {
            # SEG-T12: "etiqueta x" (label) and "integración" (marker of -Integracion) share the parenthesis with the level and effort.
            $label = if ($paren -match '(?i)etiqueta\s+([^,;)]+)') { $Matches[1].Trim() } else { '' }
            $integration = [bool]($paren -match '(?i)(?:^|,)\s*integraci[oó]n\s*(?:,|$)')
            $bare = $paren -replace '(?i)etiqueta\s+[^,;)]+', ''
            $level = if ($bare -match '\b(T[1-4])\b') { $Matches[1] } else { '' }
            $effort = if ($bare -match 'esfuerzo\s+([^,;)\s]+)') { $Matches[1] } else { '' }
            if (-not $orchestrator) { $orchestrator = $row.Agent }
            $entry = [pscustomobject]@{ Worker = $worker; Level = $level; Effort = $effort; Since = $row.Date; Piece = $rest; Orchestrator = $row.Agent; Reference = $reference
                Label = $label; Integration = $integration; Until = $null; Result = ''; IsActive = $false }
            $active.Add($entry)
            $all.Add($entry)
            if (-not @($rows | Where-Object { $_.Worker -ieq $worker })) { $rows.Add($entry) }
        } else {
            $i = 0
            while ($i -lt $active.Count -and $active[$i].Worker -ine $worker) { $i++ }
            if ($i -lt $active.Count) { $active[$i].Until = $row.Date; $active[$i].Result = $rest; $active.RemoveAt($i) }
        }
    }
    if ($state -ne 'en curso') {
        # SEG-T12: integration delegations (registrar-delegacion.ps1 -Integracion) stay active on a delivered task until devuelta.
        $keep = @(if ($state -eq 'terminada') { $active | Where-Object { $_.Integration } })
        $active.Clear()
        foreach ($kept in $keep) { $active.Add($kept) }
    }
    foreach ($live in $active) { $live.IsActive = $true }
    [pscustomobject]@{ Rows = @($rows); Active = @($active); Orchestrator = $orchestrator; All = @($all) }
}
# JSONL lines of <channel>\orquestacion\<ETAPA>\ejecuciones.jsonl, grouped by task id (upper case). Read once.
$script:ExecutionLines = $null
function Get-ExecutionLines([string]$taskId) {
    if ($null -eq $script:ExecutionLines) {
        $script:ExecutionLines = @{}
        $dir = Join-Path $channel 'orquestacion'
        $files = @()
        if (Test-Path -LiteralPath $dir -PathType Container) {
            try { $files = @(Get-ChildItem -LiteralPath $dir -Filter 'ejecuciones.jsonl' -File -Recurse -ErrorAction Stop) } catch { $files = @() }
        }
        foreach ($file in $files) {
            try { $lines = [IO.File]::ReadAllLines($file.FullName) } catch { continue }
            foreach ($line in $lines) {
                if (-not $line.Trim()) { continue }
                try { $item = $line | ConvertFrom-Json -ErrorAction Stop } catch { continue }
                # Activity rows (orquestacion\ACTIVIDADES) carry "activity", not "task": check the properties first so StrictMode callers do not fail.
                if (-not $item -or -not $item.PSObject.Properties['task'] -or -not $item.PSObject.Properties['worker'] -or -not $item.task -or -not $item.worker) { continue }
                $key = ([string]$item.task).Trim().ToUpperInvariant()
                if (-not $script:ExecutionLines.ContainsKey($key)) { $script:ExecutionLines[$key] = [System.Collections.Generic.List[object]]::new() }
                $script:ExecutionLines[$key].Add($item)
            }
        }
    }
    $key = $taskId.Trim().ToUpperInvariant()
    if ($script:ExecutionLines.ContainsKey($key)) { @($script:ExecutionLines[$key]) } else { @() }
}
function Format-WorkerLabel($entry) {
    $detail = @($entry.Level; if ($entry.Effort) { "esfuerzo $($entry.Effort)" }) | Where-Object { $_ }
    $name = Get-WorkerDisplayName $entry.Worker (Get-EntryReference $entry)
    if ($detail) { "$name ($($detail -join ', '))" } else { $name }
}
# SEG-T11: activity status of a delegated worker from the tail of its subagent transcript. Only times and
# the status are exposed, never the content.
$script:SubagentFileCache = @{}
function Find-SubagentFile([string]$reference) {
    if ($script:SubagentFileCache.ContainsKey($reference)) { return $script:SubagentFileCache[$reference] }
    $found = $null
    foreach ($root in $script:subagentRoots) {
        if (-not $root -or -not (Test-Path -LiteralPath $root -PathType Container)) { continue }
        try { $found = Get-ChildItem -Path (Join-Path $root "*\*\subagents\$reference.jsonl") -File -ErrorAction Stop | Select-Object -First 1 } catch { $found = $null }
        if ($found) { break }
    }
    $script:SubagentFileCache[$reference] = $found
    $found
}
function ConvertTo-LocalStamp($value) {
    if ($value -is [datetime]) { if ($value.Kind -eq 'Local') { return $value } return [datetime]::SpecifyKind($value, 'Utc').ToLocalTime() }
    try { ([datetimeoffset]::Parse([string]$value, [cultureinfo]::InvariantCulture, 'AssumeUniversal')).LocalDateTime } catch { $null }
}
# SEG-T15: Copilot sessions and Codex threads are workers like the Claude subagents. A reference is agent-<id> (Claude),
# copilot:<id de sesión> or codex:<id de hilo>. Only times, the status and the model name are read, never the content.
# SEG-T17: the application, its id pattern and its id come from the table of applications ($script:WorkerApps).
function Get-WorkerReference([string]$reference) {
    foreach ($app in $script:WorkerApps) {
        if ($reference -match "^(?:$($app.RefPattern))$") { return [pscustomobject]@{ Kind = $app.Key; Id = (& $app.RefId $reference) } }
    }
    foreach ($app in $script:WorkerApps) {
        if ($app.BarePattern -and $reference -match "^(?:$($app.BarePattern))$") { return [pscustomobject]@{ Kind = $app.Key; Id = $reference } }
    }
    $null
}
function Get-EntryReference($entry) {
    if ($entry -and $entry.PSObject.Properties['Reference'] -and $null -ne $entry.Reference) { [string]$entry.Reference } else { '' }
}
$script:WorkerFileCache = @{}
$script:WorkerModelCache = @{}
function Find-WorkerFile($ref) {
    $app = Get-WorkerApp $ref.Kind
    if (-not $app) { return $null }
    $key = "$($ref.Kind):$($ref.Id)"
    if ($script:WorkerFileCache.ContainsKey($key)) { return $script:WorkerFileCache[$key] }
    $found = $null
    try { $found = & $app.FindFile $ref } catch { $found = $null }
    $script:WorkerFileCache[$key] = $found
    $found
}
# Parsed JSON records of the last $maxBytes of a transcript (the first, possibly cut, line is dropped).
function Read-TailRecords([string]$path, [int]$maxBytes) {
    $stream = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    try {
        $length = $stream.Length
        $take = [int][Math]::Min($maxBytes, $length)
        $null = $stream.Seek(-$take, 'End')
        $buffer = New-Object byte[] $take
        $read = $stream.Read($buffer, 0, $take)
        $text = [Text.Encoding]::UTF8.GetString($buffer, 0, $read)
    } finally { $stream.Dispose() }
    $lines = @($text -split "`r?`n" | Where-Object { $_.Trim() })
    if ($take -lt $length -and $lines.Count) { $lines = @($lines | Select-Object -Skip 1) }
    $records = foreach ($line in $lines) { try { $line | ConvertFrom-Json -ErrorAction Stop } catch { } }
    @($records)
}
# Lines from the start of a transcript, stopping at $maxLines lines or about $maxChars characters.
function Read-HeadLines([string]$path, [int]$maxLines, [int]$maxChars) {
    $stream = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    try {
        $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::UTF8)
        $chars = 0
        for ($i = 0; $i -lt $maxLines -and $chars -lt $maxChars; $i++) {
            $line = $reader.ReadLine()
            if ($null -eq $line) { break }
            $chars += $line.Length
            $line
        }
    } finally { $stream.Dispose() }
}
function Get-StringField($object, [string[]]$names) {
    foreach ($name in $names) {
        if ($null -eq $object) { return '' }
        $property = $object.PSObject.Properties[$name]
        if ($property -and $property.Value -is [string] -and $property.Value.Trim()) { return $property.Value.Trim() }
    }
    ''
}
# Model named in a transcript: the last one in the records of its tail ($FromTail: record -> model), else the first in the lines of
# its head that contain one of $HeadMarkers (-like patterns; $FromHead: record -> model). Only the model name is read.
function Read-TranscriptModel([string]$path, [scriptblock]$FromTail, [string[]]$HeadMarkers, [scriptblock]$FromHead) {
    $model = ''
    $records = @(Read-TailRecords $path 262144)
    for ($i = $records.Count - 1; $i -ge 0 -and -not $model; $i--) { $model = & $FromTail $records[$i] }
    if (-not $model) {
        foreach ($line in Read-HeadLines $path 300 2000000) {
            $wanted = $false
            foreach ($marker in $HeadMarkers) { if ($line -like $marker) { $wanted = $true; break } }
            if (-not $wanted) { continue }
            try { $rec = $line | ConvertFrom-Json -ErrorAction Stop } catch { continue }
            $model = & $FromHead $rec
            if ($model) { break }
        }
    }
    [string]$model
}
# Model of a worker whose application reads it from its activity file (table column ReadModel); '' otherwise.
function Get-WorkerModel($ref) {
    $key = "$($ref.Kind):$($ref.Id)"
    if ($script:WorkerModelCache.ContainsKey($key)) { return $script:WorkerModelCache[$key] }
    $model = ''
    $app = Get-WorkerApp $ref.Kind
    $file = if ($app -and $app.ReadModel) { Find-WorkerFile $ref } else { $null }
    if ($file) {
        try { $model = [string](& $app.ReadModel $file) } catch { $model = '' }
    }
    $script:WorkerModelCache[$key] = $model
    $model
}
# "Copilot · <modelo>" / "Codex · <modelo>" for the applications that show their prefix; any other worker keeps its registered name.
function Get-WorkerDisplayName([string]$worker, [string]$reference) {
    $ref = if ($reference) { Get-WorkerReference $reference } else { $null }
    $entry = if ($ref) { Get-WorkerApp $ref.Kind } else { $null }
    if (-not $entry -or -not $entry.ShowAppPrefix) { return $worker }
    $app = $entry.Application
    $model = Get-WorkerModel $ref
    if ($model) { return "$app · $model" }
    if (-not $worker) { return $app }
    if ($worker -match [regex]::Escape($app)) { return $worker }
    "$app · $worker"
}
# Caches live for one page generation only (the watcher regenerates inside the same process).
function Reset-WorkerCaches {
    $script:SubagentFileCache = @{}
    $script:WorkerFileCache = @{}
    $script:WorkerModelCache = @{}
    $script:ExecutionLines = $null
    $script:ActivityBoard = $null   # SEG-T18
    $script:WaitBoard = $null       # SEG-T19
}
$script:TestCommandPattern = 'pytest|probar\.ps1|integrar-sin-ventanas\.ps1'
# Running test of a Claude subagent: its last tool_use without a tool_result whose command is a test run. Returns its start time.
function Get-ClaudePendingTest($records, [datetime]$last) {
    $resolved = @{}
    $pending = $null
    foreach ($rec in $records) {
        $content = $null
        try { $content = $rec.message.content } catch { }
        foreach ($block in @($content)) {
            if ($block -isnot [pscustomobject]) { continue }
            if ($block.type -eq 'tool_result' -and $block.tool_use_id) { $resolved[[string]$block.tool_use_id] = $true }
            elseif ($block.type -eq 'tool_use') {
                $cmd = ''
                try { $cmd = ($block.input | ConvertTo-Json -Depth 4 -Compress) } catch { }
                if ($rec.type -eq 'assistant') { $pending = [pscustomobject]@{ Id = [string]$block.id; Cmd = $cmd; Time = $null; Stamp = $rec.timestamp } }
            }
        }
    }
    if ($pending -and -not $resolved.ContainsKey($pending.Id) -and $pending.Cmd -match $script:TestCommandPattern) {
        $since = $last
        if ($pending.Stamp) { $t = ConvertTo-LocalStamp $pending.Stamp; if ($t) { $since = $t } }
        return $since
    }
    $null
}
# Copilot: tool.execution_start of a shell tool without its tool.execution_complete, running a test command.
function Get-CopilotPendingTest($records, [datetime]$last) {
    $resolved = @{}
    foreach ($rec in $records) {
        if ($rec.type -eq 'tool.execution_complete' -and $rec.data -and $rec.data.toolCallId) { $resolved[[string]$rec.data.toolCallId] = $true }
    }
    $since = $null
    foreach ($rec in $records) {
        if ($rec.type -ne 'tool.execution_start' -or -not $rec.data) { continue }
        $id = [string]$rec.data.toolCallId
        if (-not $id -or $resolved.ContainsKey($id)) { continue }
        if ([string]$rec.data.toolName -notmatch '(?i)^(powershell|pwsh|bash|sh|zsh|cmd|shell|run_in_terminal)$') { continue }
        $command = ''
        try { $command = [string]$rec.data.arguments.command } catch { }
        if ($command -notmatch $script:TestCommandPattern) { continue }
        $t = if ($rec.timestamp) { ConvertTo-LocalStamp $rec.timestamp } else { $null }
        if (-not $t) { $t = $last }
        if (-not $since -or $t -lt $since) { $since = $t }
    }
    $since
}
# Codex: a function_call / custom_tool_call / local_shell_call of an exec tool without its *_output, running a test command.
function Get-CodexPendingTest($records, [datetime]$last) {
    $resolved = @{}
    foreach ($rec in $records) {
        if ($rec.type -ne 'response_item' -or -not $rec.payload) { continue }
        if ([string]$rec.payload.type -like '*_output' -and $rec.payload.call_id) { $resolved[[string]$rec.payload.call_id] = $true }
    }
    $since = $null
    foreach ($rec in $records) {
        if ($rec.type -ne 'response_item' -or -not $rec.payload) { continue }
        $payload = $rec.payload
        $kind = [string]$payload.type
        if ($kind -notin 'function_call', 'custom_tool_call', 'local_shell_call') { continue }
        $id = [string]$payload.call_id
        if (-not $id -or $resolved.ContainsKey($id)) { continue }
        if ($kind -ne 'local_shell_call' -and [string]$payload.name -notmatch '(?i)(exec|shell|bash|powershell)') { continue }
        $text = ''
        try {
            if ($payload.arguments) { $text = [string]$payload.arguments }
            elseif ($payload.input) { $text = [string]$payload.input }
            elseif ($payload.action) { $text = ($payload.action | ConvertTo-Json -Depth 4 -Compress) }
        } catch { }
        if ($text -notmatch $script:TestCommandPattern) { continue }
        $t = if ($rec.timestamp) { ConvertTo-LocalStamp $rec.timestamp } else { $null }
        if (-not $t) { $t = $last }
        if (-not $since -or $t -lt $since) { $since = $t }
    }
    $since
}
function Get-WorkerActivity($entry) {
    $reference = Get-EntryReference $entry
    if (-not $reference) { return 'sin señal de actividad' }
    $ref = Get-WorkerReference $reference
    if (-not $ref) { return 'sin señal de actividad' }
    $app = Get-WorkerApp $ref.Kind
    if (-not $app) { return 'sin señal de actividad' }
    $file = Find-WorkerFile $ref
    if (-not $file) { return 'sin señal de actividad' }
    $now = if ($null -ne $script:nowOverride) { [datetime]$script:nowOverride } else { Get-Date }
    $last = $file.LastWriteTime
    $running = $null
    try {
        $records = @(Read-TailRecords $file.FullName $app.TailBytes)
        if ($records.Count) {
            $stamp = $records[-1].timestamp
            if ($stamp) { $t = ConvertTo-LocalStamp $stamp; if ($t -and $t -gt $last) { $last = $t } }
            # SEG-T17: the running-test detector of the worker's application (table of applications).
            $running = & $app.PendingTest $records $last
        }
    } catch { }
    $minutes = [Math]::Max(0, [int][Math]::Floor(($now - $last).TotalMinutes))
    if ($minutes -gt $script:stoppedMinutes) { return 'Probablemente detenido' }
    if ($running) { return "Ejecutando prueba · desde $($running.ToString('HH:mm', [cultureinfo]::InvariantCulture))" }
    if ($minutes -lt $script:activeMinutes) { return 'Trabajando' }
    "Sin actividad reciente · última hace $minutes min"
}
function Format-ActiveDelegation($entry) {
    $level = if ($entry.Level) { " ($($entry.Level))" } else { '' }
    $piece = if ($entry.Piece) { " · $($entry.Piece)" } else { '' }
    $activity = Get-WorkerActivity $entry
    $name = Get-WorkerDisplayName $entry.Worker (Get-EntryReference $entry)
    "$name$level desde $($entry.Since.ToString('HH:mm', [cultureinfo]::InvariantCulture)) · $activity$piece"
}
# Merged delegation summary of a task: orchestrator and distinct workers (card rows win over JSONL lines).
function Get-DelegationSummary([string]$taskId, $cardDelegations, [string]$cardAgent) {
    $workers = [System.Collections.Generic.List[string]]::new()
    $details = [System.Collections.Generic.List[object]]::new()   # SEG-T12: Details[i] belongs to Workers[i]
    $seen = @{}
    $orchestrator = $cardDelegations.Orchestrator
    foreach ($row in $cardDelegations.Rows) {
        $workers.Add((Format-WorkerLabel $row))
        $details.Add([pscustomobject]@{ Worker = $row.Worker; Items = @($cardDelegations.All | Where-Object { $_.Worker -ieq $row.Worker }) })
        $seen[$row.Worker.ToLowerInvariant()] = $true
    }
    foreach ($item in @(Get-ExecutionLines $taskId)) {
        $worker = ([string]$item.worker).Trim()
        if (-not $worker -or $seen.ContainsKey($worker.ToLowerInvariant())) { continue }
        $seen[$worker.ToLowerInvariant()] = $true
        $model = if ($item.PSObject.Properties['requested_model']) { ([string]$item.requested_model).Trim() } else { '' }
        $effort = if ($item.PSObject.Properties['requested_effort']) { ([string]$item.requested_effort).Trim() } else { '' }
        $itemOrchestrator = if ($item.PSObject.Properties['orchestrator']) { ([string]$item.orchestrator).Trim() } else { '' }
        # SEG-T15: a Copilot session / Codex thread reference shows the app and the model it really ran.
        $jsonReference = [string](@(Get-ExecutionLines $taskId | Where-Object { ([string]$_.worker).Trim() -ieq $worker -and $_.PSObject.Properties['reference'] -and $_.reference } | ForEach-Object { [string]$_.reference } | Select-Object -Last 1))
        $shown = Get-WorkerDisplayName $worker $jsonReference
        if ($itemOrchestrator) {
            if (-not $orchestrator) { $orchestrator = $itemOrchestrator }
            $detail = @($model; if ($effort) { "esfuerzo $effort" }) | Where-Object { $_ }
            $label = if ($detail) { "$shown ($($detail -join ', '))" } else { $shown }
        } else {
            # Older Codex lines: no orchestrator, the requested model is the useful name.
            $label = if ($shown -ne $worker) { $shown } elseif ($model) { "$model ($worker)" } else { $worker }
        }
        $workers.Add($label)
        $details.Add([pscustomobject]@{ Worker = $worker; Items = @(Get-WorkItemsFromLines @(Get-ExecutionLines $taskId | Where-Object { ([string]$_.worker).Trim() -ieq $worker })) })
    }
    if (-not $workers.Count) { return $null }
    [pscustomobject]@{ Orchestrator = if ($orchestrator) { $orchestrator } else { $cardAgent }; Workers = @($workers); Details = @($details) }
}

# SEG-T12: what each delegated worker did in a task, for the Trabajaron column. Only the card history, ejecuciones.jsonl and
# the times of a transcript (SEG-T11 activity) are used; the content of the subagents is never read.
# Short tag of a delegation: the registered label, "integración", or the first words of the piece.
function Get-WorkTag($item) {
    if ($item.Label) { return $item.Label }
    if ($item.Integration) { return 'integración' }
    $words = @(([string]$item.Piece) -split '\s+' | Where-Object { $_ })
    if (-not $words.Count) { return 'sin pieza' }
    $tag = ''
    $taken = 0
    foreach ($word in ($words | Select-Object -First 3)) {
        $next = if ($tag) { "$tag $word" } else { $word }
        if ($tag -and $next.Length -gt 24) { break }
        $tag = $next; $taken++
    }
    if ($tag.Length -gt 24) { $tag = $tag.Substring(0, 24) }
    $tag = $tag.TrimEnd(':', ';', ',', '.', ' ')
    if ($taken -lt $words.Count) { $tag += '…' }
    $tag
}
# HH:mm for today, the full date otherwise.
function Format-WorkTime($value) {
    $now = if ($null -ne $script:nowOverride) { [datetime]$script:nowOverride } else { Get-Date }
    $when = [datetime]$value
    if ($when.Date -eq $now.Date) { $when.ToString('HH:mm', [cultureinfo]::InvariantCulture) } else { $when.ToString('yyyy-MM-dd HH:mm', [cultureinfo]::InvariantCulture) }
}
# Delegations of one worker from its ejecuciones.jsonl lines (no card rows): "delegated" opens one, any other status closes the oldest.
function Get-WorkItemsFromLines([object[]]$lines) {
    $items = [System.Collections.Generic.List[object]]::new()
    $open = [System.Collections.Generic.List[object]]::new()
    $text = { param($line, $name) if ($line.PSObject.Properties[$name] -and $null -ne $line.$name) { ([string]$line.$name).Trim() } else { '' } }
    foreach ($line in $lines) {
        $status = & $text $line 'status'
        $reason = & $text $line 'reason'
        $stamp = if ($line.PSObject.Properties['recorded_at']) { ConvertTo-LocalStamp $line.recorded_at } else { $null }
        if ($status -eq 'delegated') {
            $item = [pscustomobject]@{ Worker = (& $text $line 'worker'); Level = ''; Effort = ''; Since = $stamp; Piece = $reason; Orchestrator = (& $text $line 'orchestrator'); Reference = (& $text $line 'reference')
                Label = (& $text $line 'label'); Integration = [bool]($line.PSObject.Properties['integration'] -and $line.integration -eq $true); Until = $null; Result = ''; IsActive = $false }
            $items.Add($item); $open.Add($item)
        } elseif ($open.Count) {
            $open[0].Until = $stamp; $open[0].Result = $(if ($reason) { $reason } else { $status }); $open.RemoveAt(0)
        } else {
            $items.Add([pscustomobject]@{ Worker = (& $text $line 'worker'); Level = ''; Effort = ''; Since = $stamp; Piece = ''; Orchestrator = (& $text $line 'orchestrator'); Reference = ''
                Label = (& $text $line 'label'); Integration = $false; Until = $stamp; Result = $(if ($reason) { $reason } else { $status }); IsActive = $false })
        }
    }
    @($items)
}
# "Tokens: ..." of a worker entry of the Tokens field (HTML-encoded): per-window total, input and output, or why there is no figure.
function Get-WorkerTokenText($token) {
    if ($null -eq $token.Total) { "Tokens: $(Encode $token.Source)" }
    else { "Tokens: $(Format-TokenCount $token.Total) · entrada $(Format-TokenCount $token.Input) · salida $(Format-TokenCount $token.Output) · $(Encode $token.Source)" }
}
# One <details> per delegated worker: the summary is the worker label with the short tag(s) of what it worked on; the body lists
# every delegation of that worker in the task (piece, result, from/until, activity while active) and its tokens (the "↳" entry
# of the Tokens field). Workers without items keep the plain line.
function Get-WorkerListHtml($delegation, [string]$tokens) {
    $tokenEntries = @(Get-TokenEntries $tokens | Where-Object { $_.Kind -eq 'worker' })
    $out = for ($i = 0; $i -lt @($delegation.Workers).Count; $i++) {
        $label = @($delegation.Workers)[$i]
        $detail = if ($i -lt @($delegation.Details).Count) { @($delegation.Details)[$i] } else { $null }
        $items = if ($detail) { @($detail.Items) } else { @() }
        if (-not $items.Count) { "<div class=""meta"">$(Encode $label)</div>"; continue }
        $tags = @($items | ForEach-Object { Get-WorkTag $_ } | Select-Object -Unique | Select-Object -First 3)
        $chips = ($tags | ForEach-Object { " <span class=""state s-idle seg-tag"">$(Encode $_)</span>" }) -join ''
        $lines = foreach ($item in @($items | Sort-Object @{ Expression = { if ($_.Since) { $_.Since } else { [datetime]::MinValue } } })) {
            $span = if ($item.Since) { "desde $(Format-WorkTime $item.Since)" } else { 'hora no registrada' }
            if ($item.Until) { $span += " hasta $(Format-WorkTime $item.Until)" }
            elseif ($item.IsActive) { $span += " · en curso · $(Get-WorkerActivity $item)" }
            else { $span += ' · sin devolver' }
            $piece = if ($item.Piece) { "<div>Pieza: $(Encode $item.Piece)</div>" } else { '' }
            $result = if ($item.Result) { "<div>Resultado: $(Encode $item.Result)</div>" } else { '' }
            "<li><strong>$(Encode (Get-WorkTag $item))</strong> · $(Encode $span)$piece$result</li>"
        }
        $token = $tokenEntries | Where-Object { $_.Agent -ieq $detail.Worker } | Select-Object -First 1
        $tokenLine = if ($token) { "<div>$(Get-WorkerTokenText $token)</div>" } else { '' }
        "<div class=""meta""><details class=""seg-worker""><summary>$(Encode $label)$chips</summary><ul class=""seg-worker-items"">$($lines -join '')</ul>$tokenLine</details></div>"
    }
    @($out) -join ''
}

# SEG-T17 (REGLAS 33): active delegations whose worker belongs to another application than the agent that delegated it (Claude
# orchestrating a Copilot or Codex worker, Codex orchestrating a Claude worker, ...). Each one is listed in the Agentes card of ITS
# application, besides its line in the delegations of the orchestrator. The application comes from the worker reference, else from its
# name. A worker of the orchestrator's own application stays in the orchestrator's block of that same card. Only times, the status
# and model names are read (Get-WorkerActivity, Get-WorkerDisplayName), never content.
function Get-ForeignWorkers($board, [object[]]$registered) {
    foreach ($task in @($board.Tasks)) {
        foreach ($entry in @($task.ActiveDelegations)) {
            $app = Get-WorkerEntryApp $entry
            if (-not $app) { continue }
            $orchestrator = if ($entry.Orchestrator) { [string]$entry.Orchestrator } else { [string]$task.Agent }
            if ((Get-AgentApplication $orchestrator $registered) -ieq $app.Application) { continue }
            $activity = Get-WorkerActivity $entry
            $token = @(Get-TokenEntries $task.Tokens | Where-Object { $_.Kind -eq 'worker' -and $_.Agent -ieq $entry.Worker }) | Select-Object -First 1
            [pscustomobject]@{ Application = $app.Application; Task = $task; Entry = $entry; Orchestrator = $orchestrator
                Activity = $activity; Stopped = ($activity -eq 'Probablemente detenido'); Token = $token }
        }
    }
}
# One worker block of an application card: model and level, "trabajando para <orquestador> en <tarea>", real activity, tokens.
function Format-ForeignWorkerHtml($item) {
    $entry = $item.Entry
    $level = if ($entry.Level) { " ($($entry.Level))" } else { '' }
    $name = Get-WorkerDisplayName $entry.Worker (Get-EntryReference $entry)
    $badge = if ($item.Stopped) { "Probablemente detenido · delegado por $($item.Orchestrator) en $($item.Task.Id)" } else { "Activo · trabajando para $($item.Orchestrator) en $($item.Task.Id)" }
    $badgeClass = if ($item.Stopped) { 's-unknown' } else { 's-en-curso' }
    $since = $entry.Since.ToString('HH:mm', [cultureinfo]::InvariantCulture)
    $kind = if ($entry.Integration) { ' · integración' } else { '' }
    $piece = if ($entry.Piece) { " · $($entry.Piece)" } else { '' }
    $tokens = if ($item.Token) { "<p class=""agent-context"">$(Get-WorkerTokenText $item.Token)</p>" } else { '' }
    "<div class=""agent-task agent-worker""><strong>$(Encode $name)$(Encode $level)</strong><span class=""state $badgeClass"">$(Encode $badge)</span>" +
    "<p class=""agent-context"">Tarea: $(Encode $item.Task.Id) · $(Encode $item.Task.Title)$(Encode $kind)</p>" +
    "<p class=""agent-context"">Actividad: $(Encode $item.Activity) · delegado desde $since$(Encode $piece)</p>$tokens</div>"
}

# SEG-T18: activities outside tasks (audits, proposals, research, reviews, integrations, maintenance, scheduled sessions). They hang off no task
# card: registrar-delegacion.ps1 -Actividad writes them to <channel>\orquestacion\ACTIVIDADES\ejecuciones.jsonl, one line per event
# (opened | delegated | returned | closed; the readable index ACTIVIDADES.md next to it is not read here). The events are folded into
# activities; each worker becomes an entry shaped like a task delegation, so Get-WorkerActivity, Get-WorkerDisplayName and the table of
# applications ($script:WorkerApps) serve it unchanged. Only the registered text (title, type, piece, result) and times, status and model
# names of the worker transcripts are shown, never the content of the sessions.
$script:ActivityBoard = $null
function Get-ActivityBoard {
    if ($null -ne $script:ActivityBoard) { return $script:ActivityBoard }
    $path = Join-Path $channel 'orquestacion\ACTIVIDADES\ejecuciones.jsonl'
    $available = $true
    $lines = @()
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        try { $lines = @([IO.File]::ReadAllLines($path)) } catch { $available = $false; Write-Warning 'No se pudo leer el registro de actividades fuera de tareas.' }
    }
    $byId = [ordered]@{}
    foreach ($line in $lines) {
        if (-not $line.Trim()) { continue }
        try { $event = $line | ConvertFrom-Json -ErrorAction Stop } catch { continue }
        if ($event -isnot [pscustomobject]) { continue }
        $id = Get-StringField $event @('activity')
        $kind = Get-StringField $event @('event')
        if (-not $id -or -not $kind) { continue }
        $stamp = if ($event.PSObject.Properties['recorded_at']) { ConvertTo-LocalStamp $event.recorded_at } else { $null }
        if ($kind -eq 'opened') {
            if ($byId.Contains($id)) { continue }
            $title = Get-StringField $event @('title')
            $byId[$id] = [pscustomobject]@{ Id = $id; Title = $(if ($title) { $title } else { $id }); Type = (Get-StringField $event @('type')); Request = (Get-StringField $event @('request'))
                Orchestrator = (Get-StringField $event @('orchestrator')); Start = $stamp; End = $null; Status = 'en curso'; Result = ''; Workers = [System.Collections.Generic.List[object]]::new() }
            continue
        }
        if (-not $byId.Contains($id)) { continue }
        $activity = $byId[$id]
        if ($kind -eq 'delegated') {
            $worker = Get-StringField $event @('worker')
            if (-not $worker) { continue }
            $orchestrator = Get-StringField $event @('orchestrator')
            $activity.Workers.Add([pscustomobject]@{ Worker = $worker; Level = (Get-StringField $event @('level')); Effort = (Get-StringField $event @('requested_effort')); Since = $stamp
                Piece = (Get-StringField $event @('reason')); Orchestrator = $(if ($orchestrator) { $orchestrator } else { $activity.Orchestrator }); Reference = (Get-StringField $event @('reference'))
                Label = (Get-StringField $event @('label')); Integration = $false; Until = $null; Result = ''; IsActive = $true; Tokens = $null; Status = $null; ActivityId = $id })
        } elseif ($kind -eq 'returned') {
            $worker = Get-StringField $event @('worker')
            $open = @($activity.Workers | Where-Object { $_.IsActive -and $_.Worker -ieq $worker }) | Select-Object -First 1
            if ($open) {
                $open.IsActive = $false; $open.Until = $stamp; $open.Result = (Get-StringField $event @('reason'))
                if ($event.PSObject.Properties['usage_tokens']) { $open.Tokens = $event.usage_tokens }
            }
        } elseif ($kind -eq 'closed') {
            $activity.End = $stamp; $activity.Status = 'terminada'; $activity.Result = (Get-StringField $event @('reason'))
        }
    }
    $script:ActivityBoard = [pscustomobject]@{ Available = $available; Activities = @($byId.Values) }
    $script:ActivityBoard
}
# Real activity of an activity worker (Trabajando / Ejecutando prueba / Sin actividad reciente / Probablemente detenido / sin señal), read once.
function Get-ActivityWorkerStatus($entry) {
    if ($null -eq $entry.Status) { $entry.Status = Get-WorkerActivity $entry }
    $entry.Status
}
# Tokens recorded for a worker: a total (number) or an object { total, input, output }. '' when none was recorded.
function Get-ActivityTokenText($value) {
    if ($null -eq $value) { return '' }
    $number = { param($source, $name) $p = $source.PSObject.Properties[$name]; if ($p -and $null -ne $p.Value -and "$($p.Value)" -match '^\d+$') { [long]$p.Value } else { $null } }
    if ($value -is [pscustomobject]) {
        $total = & $number $value 'total'; $in = & $number $value 'input'; $out = & $number $value 'output'
    } elseif ("$value" -match '^\d+$') { $total = [long]"$value"; $in = $null; $out = $null } else { return '' }
    if ($null -eq $total) { return '' }
    $text = "Tokens: $(Format-TokenCount $total)"
    if ($null -ne $in) { $text += " · entrada $(Format-TokenCount $in)" }
    if ($null -ne $out) { $text += " · salida $(Format-TokenCount $out)" }
    "$text · registrado"
}
# Chip class of a real-activity text.
function Get-ActivityStatusClass([string]$status) {
    if ($status -eq 'Probablemente detenido') { 's-unknown' }
    elseif ($status -like 'Sin actividad reciente*') { 's-pendiente' }
    elseif ($status -like 'sin señal*') { 's-idle' }
    else { 's-en-curso' }
}
function Format-ActivitySince($value) { if ($value) { Format-WorkTime $value } else { 'hora no registrada' } }
# Active workers of the activities en curso, for the card of THEIR application (REGLAS 33), whichever application orchestrates them.
function Get-ActivityWorkers($board) {
    foreach ($activity in @($board.Activities | Where-Object { $_.Status -eq 'en curso' })) {
        foreach ($entry in @($activity.Workers | Where-Object IsActive)) {
            $app = Get-WorkerEntryApp $entry
            if (-not $app) { continue }
            $status = Get-ActivityWorkerStatus $entry
            [pscustomobject]@{ Application = $app.Application; Activity = $activity; Entry = $entry; Orchestrator = [string]$entry.Orchestrator
                Status = $status; Stopped = ($status -eq 'Probablemente detenido') }
        }
    }
}
# One worker block of an application card: model and level, "trabajando para <orquestador> en <actividad>", real activity, tokens.
function Format-ActivityWorkerHtml($item) {
    $entry = $item.Entry
    $activity = $item.Activity
    $name = Format-WorkerLabel $entry
    $badge = if ($item.Stopped) { "Probablemente detenido · delegado por $($item.Orchestrator) en $($activity.Title)" } else { "Activo · trabajando para $($item.Orchestrator) en $($activity.Title)" }
    $badgeClass = if ($item.Stopped) { 's-unknown' } else { 's-en-curso' }
    $piece = if ($entry.Piece) { " · $($entry.Piece)" } else { '' }
    $kind = if ($activity.Type) { " · $($activity.Type)" } else { '' }
    $tokenText = Get-ActivityTokenText $entry.Tokens
    $tokens = if ($tokenText) { "<p class=""agent-context"">$(Encode $tokenText)</p>" } else { '' }
    "<div class=""agent-task agent-worker""><strong>$(Encode $name)</strong><span class=""state $badgeClass"">$(Encode $badge)</span>" +
    "<p class=""agent-context"">Fuera de tareas: $(Encode $activity.Id)$(Encode $kind) · $(Encode $activity.Title)</p>" +
    "<p class=""agent-context"">Actividad: $(Encode $item.Status) · delegado desde $(Encode (Format-ActivitySince $entry.Since))$(Encode $piece)</p>$tokens</div>"
}
# A worker line of the «Fuera de tareas» section: name, application, tag, real activity (active) or result (returned), tokens.
function Format-ActivityWorkerLine($entry, [string]$application) {
    $appChip = if ($application) { " <span class=""state s-idle"">$(Encode $application)</span>" } else { '' }
    $tagChip = if ($entry.Label) { " <span class=""state s-idle seg-tag"">$(Encode $entry.Label)</span>" } else { '' }
    $piece = if ($entry.Piece) { " · $(Encode $entry.Piece)" } else { '' }
    $tokenText = Get-ActivityTokenText $entry.Tokens
    $tokens = if ($tokenText) { " · $(Encode $tokenText)" } else { '' }
    if ($entry.IsActive) {
        $status = Get-ActivityWorkerStatus $entry
        $state = " <span class=""state $(Get-ActivityStatusClass $status)"">$(Encode $status)</span>"
        $when = "delegado desde $(Encode (Format-ActivitySince $entry.Since))"
        $tail = ''
    } else {
        $state = ' <span class="state s-integrada">Devuelto</span>'
        $when = "desde $(Encode (Format-ActivitySince $entry.Since))$(if ($entry.Until) { " hasta $(Encode (Format-WorkTime $entry.Until))" })"
        $tail = if ($entry.Result) { "<div class=""agent-context"">Resultado: $(Encode $entry.Result)</div>" } else { '' }
    }
    "<div class=""act-worker""><strong>$(Encode (Format-WorkerLabel $entry))</strong>$appChip$tagChip$state<span class=""meta""> · $when$piece$tokens</span>$tail</div>"
}
# The «Fuera de tareas» section of the Agentes tab: each activity en curso (type, title, orchestrator, workers), the finished ones folded.
function Get-ActivitiesHtml($board) {
    $active = @($board.Activities | Where-Object { $_.Status -eq 'en curso' } | Sort-Object @{ Expression = { if ($_.Start) { $_.Start } else { [datetime]::MinValue } }; Descending = $true })
    $finished = @($board.Activities | Where-Object { $_.Status -ne 'en curso' } | Sort-Object @{ Expression = { if ($_.End) { $_.End } elseif ($_.Start) { $_.Start } else { [datetime]::MinValue } }; Descending = $true })
    $count = if ($active.Count) { "$($active.Count) en curso" } else { 'sin actividades en curso' }
    $countClass = if ($active.Count) { 's-en-curso' } else { 's-idle' }
    $notice = if (-not $board.Available) { '<div class="stop"><strong>Actividades no disponibles</strong>No se pudo leer el registro de actividades fuera de tareas; no se presenta como una lista vacía.</div>' } else { '' }
    $blocks = foreach ($activity in $active) {
        $workers = @($activity.Workers)
        $liveStates = @($workers | Where-Object IsActive | ForEach-Object { Get-ActivityWorkerStatus $_ })
        $stopped = $liveStates.Count -gt 0 -and @($liveStates | Where-Object { $_ -ne 'Probablemente detenido' }).Count -eq 0
        $badge = if ($stopped) { '<span class="state s-unknown">Probablemente detenida</span>' } else { '<span class="state s-en-curso">En curso</span>' }
        $type = if ($activity.Type) { "<span class=""state s-idle"">$(Encode $activity.Type)</span> " } else { '' }
        $since = if ($activity.Start) { " · desde $(Encode (Format-WorkTime $activity.Start))" } else { '' }
        $request = if ($activity.Request) { "<p class=""agent-context"">Pedido: $(Encode $activity.Request)</p>" } else { '' }
        $lines = foreach ($entry in $workers) {
            $app = Get-WorkerEntryApp $entry
            Format-ActivityWorkerLine $entry $(if ($app) { $app.Application } else { '' })
        }
        $body = if ($workers.Count) { $lines -join '' } else { '<div class="agent-context">Sin trabajadores delegados: lo hace el propio orquestador.</div>' }
        "<div class=""agent-task act-item""><strong>$(Encode $activity.Id) · $(Encode $activity.Title)</strong> $type$badge" +
        "<p class=""agent-context"">Orquesta: $(Encode $activity.Orchestrator)$since</p>$request$body</div>"
    }
    $activeHtml = if ($active.Count) { $blocks -join "`n" } else { '<p class="meta">No hay actividades en curso fuera de tareas.</p>' }
    $finishedHtml = ''
    if ($finished.Count) {
        $items = foreach ($activity in ($finished | Select-Object -First 30)) {
            $span = "$(Encode (Format-ActivitySince $activity.Start))$(if ($activity.End) { " → $(Encode (Format-WorkTime $activity.End))" })"
            $names = @($activity.Workers | ForEach-Object {
                $token = Get-ActivityTokenText $_.Tokens
                if ($token) { "$(Format-WorkerLabel $_) ($($token -replace ' · registrado$', ''))" } else { Format-WorkerLabel $_ }
            })
            $workersText = if ($names.Count) { " · trabajaron: $(Encode ($names -join ', '))" } else { '' }
            $result = if ($activity.Result) { " · Resultado: $(Encode $activity.Result)" } else { '' }
            "<li><strong>$(Encode $activity.Id)</strong> · $(Encode $activity.Type) · $(Encode $activity.Title) · $(Encode $activity.Orchestrator) · $span$workersText$result</li>"
        }
        $more = if ($finished.Count -gt 30) { "<li class=""meta"">y $($finished.Count - 30) actividades terminadas más en ACTIVIDADES.md</li>" } else { '' }
        $finishedHtml = "<details class=""act-finished""><summary>Terminadas ($($finished.Count))</summary><ul>$($items -join '')$more</ul></details>"
    }
    "<section class=""panel activities-group"" id=""fuera-de-tareas""><div class=""card-head""><h2>Fuera de tareas</h2><span class=""state $countClass"">$count</span></div>" +
    '<p class="meta">Trabajo que no cuelga de una ficha (auditorías, propuestas, investigaciones, revisiones, integraciones, mantenimiento, sesiones programadas), registrado con <code>registrar-delegacion.ps1 -Actividad</code>. Cada trabajador figura aquí y también en el área de su aplicación (regla 33). Solo se leen horas, estado y modelo de sus sesiones, nunca su contenido.</p>' +
    "$notice$activeHtml$finishedHtml$(Get-WaitersHtml (Get-WaitBoard))</section>"
}

# SEG-T19 (REGLAS 36): «En espera de activación». An agent that only waits for something external leaves its task pendiente ("esperando: ...") and
# ends its run; esperar-condicion.ps1 (no AI model) watches the condition and relaunches it. That watcher keeps one state file per wait in
# <channel>\orquestacion\esperas\activas\<id>.json (moved to ...\terminadas when it is cumplida or vencida): id, motivo, tarea, agente, estado,
# condiciones (tipo, valor, cumplida, avance, estimado, base), inicio, ultima_comprobacion, proxima_comprobacion, pid, and the overall avance /
# estimado / base. A task or an agent that would read «sin actividad» but has a live watcher (tarea / agente) or a pendiente card that says
# «esperando: ...» reads «En espera de activación», with the motivo, condition, who it reactivates, since when, the next check and the honest
# progress («NN % · hacia las HH:MM · <base>» or «sin estimación»). Only registered text, times, status and numbers are shown, never content.
$script:WaitBoard = $null
$script:WaitLabel = 'En espera de activación'
function ConvertTo-WaitTime($value) {
    if ($null -eq $value -or "$value" -eq '') { return $null }
    if ($value -is [datetime]) { if ($value.Kind -eq 'Utc') { return $value.ToLocalTime() } return $value }
    $parsed = [datetimeoffset]::MinValue
    if ([datetimeoffset]::TryParse([string]$value, [cultureinfo]::InvariantCulture, [Globalization.DateTimeStyles]::AssumeLocal, [ref]$parsed)) { return $parsed.LocalDateTime }
    $null
}
# A percentage from the state file: a number from 0 to 100, else $null (never guessed).
function ConvertTo-WaitPercent($value) {
    if ($null -eq $value -or $value -is [bool]) { return $null }
    if ($value -is [string]) { if ($value.Trim() -notmatch '^\d+(\.\d+)?$') { return $null } }
    elseif ($value -isnot [ValueType]) { return $null }
    try { $number = [double]$value } catch { return $null }
    if ($number -lt 0 -or $number -gt 100) { return $null }
    [int][Math]::Round($number)
}
function Get-WaitProperty($object, [string]$name) {
    if ($null -eq $object) { return $null }
    $property = $object.PSObject.Properties[$name]
    if ($property) { $property.Value } else { $null }
}
# One state file as a wait. $null when it cannot be read (a watcher may be rewriting it: one retry).
# Status: cumplida / vencida (finished); otherwise detenido (its process is gone), perdido (its heartbeat is older than 3 times its interval),
# error (its last check failed; it retries) or vigilando. A percentage of 100 is only believed with its `met_at`.
function Read-WaitFile([string]$path, [bool]$finished) {
    $json = $null
    foreach ($attempt in 1..2) {
        try { $json = [IO.File]::ReadAllText($path) | ConvertFrom-Json -ErrorAction Stop; break } catch { if ($attempt -eq 1) { Start-Sleep -Milliseconds 150 } }
    }
    if ($json -isnot [pscustomobject]) { return $null }
    $id = Get-StringField $json @('id')
    if (-not $id) { $id = [IO.Path]::GetFileNameWithoutExtension($path) }
    $percent = { param($source) $p = ConvertTo-WaitPercent (Get-WaitProperty $source 'avance'); if ($p -eq 100 -and -not (ConvertTo-WaitTime (Get-WaitProperty $source 'met_at'))) { $null } else { $p } }
    $conditions = @(foreach ($item in @(Get-WaitProperty $json 'condiciones')) {
        if ($item -isnot [pscustomobject]) { continue }
        $type = Get-StringField $item @('tipo')
        $value = Get-WaitProperty $item 'valor'
        # The value of an entrada (a pattern over channel text) or archivo (a path) is not shown: it could carry content.
        [pscustomobject]@{ Type = $type; Value = $(if ($null -ne $value -and $type -notin 'entrada', 'archivo') { Safe-Snippet ([string]$value) } else { '' })
            Met = ((Get-WaitProperty $item 'cumplida') -eq $true); Progress = (& $percent $item)
            Estimated = (ConvertTo-WaitTime (Get-WaitProperty $item 'estimado')); Base = (Safe-Snippet (Get-StringField $item @('base'))) }
    })
    $status = (Get-StringField $json @('estado')).ToLowerInvariant()
    $isFinished = $finished -or $status -in 'cumplida', 'vencida'
    $now = if ($null -ne $script:nowOverride) { [datetime]$script:nowOverride } else { Get-Date }
    $lastCheck = ConvertTo-WaitTime (Get-WaitProperty $json 'ultima_comprobacion')
    $nextCheck = ConvertTo-WaitTime (Get-WaitProperty $json 'proxima_comprobacion')
    $state = if ($isFinished) { if ($status -in 'cumplida', 'vencida') { $status } else { 'terminada' } } else { 'vigilando' }
    if (-not $isFinished) {
        $processId = Get-WaitProperty $json 'pid'
        $interval = if ($lastCheck -and $nextCheck) { ($nextCheck - $lastCheck).TotalSeconds } else { 0 }
        if ($null -ne $processId -and "$processId" -match '^\d+$' -and -not (Get-Process -Id ([int]"$processId") -ErrorAction SilentlyContinue)) {
            # A state file whose watcher process is gone (killed, rebooted) will never reactivate anyone.
            $state = 'detenido'
        } elseif ($interval -gt 0 -and ($now - $lastCheck).TotalSeconds -gt 3 * $interval) {
            $state = 'perdido'
        } elseif ($status -eq 'error') {
            $state = 'error'
        }
    }
    [pscustomobject]@{ Id = $id; Reason = (Safe-Snippet (Get-StringField $json @('motivo'))); Task = (Get-StringField $json @('tarea')); Agent = (Get-StringField $json @('agente'))
        Status = $state; Conditions = $conditions; Start = (ConvertTo-WaitTime (Get-WaitProperty $json 'inicio')); LastCheck = $lastCheck; NextCheck = $nextCheck
        Deadline = (ConvertTo-WaitTime (Get-WaitProperty $json 'vence')); Progress = (& $percent $json)
        Estimated = (ConvertTo-WaitTime (Get-WaitProperty $json 'estimado')); Base = (Safe-Snippet (Get-StringField $json @('base'))); Source = 'vigilante' }
}
# The waits of the channel: Active (live watchers), Problem (error, perdido or detenido: shown in the list but they promise no reactivation) and
# Finished (cumplida / vencida), read once.
function Get-WaitBoard {
    if ($null -ne $script:WaitBoard) { return $script:WaitBoard }
    $available = $true
    $read = @{}
    $root = Join-Path $channel 'orquestacion\esperas'
    foreach ($kind in 'activas', 'terminadas') {
        $dir = Join-Path $root $kind
        if (-not (Test-Path -LiteralPath $dir -PathType Container)) { continue }
        try { $files = @(Get-ChildItem -LiteralPath $dir -Filter '*.json' -File -ErrorAction Stop | Sort-Object LastWriteTime -Descending) }
        catch { $available = $false; Write-Warning 'No se pudo leer la carpeta de esperas de activación.'; continue }
        if ($kind -eq 'terminadas') { $files = @($files | Select-Object -First 20) }
        foreach ($file in $files) {
            $wait = Read-WaitFile $file.FullName ($kind -eq 'terminadas')
            # A watcher finishing moves its file while the folders are read: the finished copy wins.
            if ($wait -and (-not $read.ContainsKey($wait.Id) -or $kind -eq 'terminadas')) { $read[$wait.Id] = $wait }
        }
    }
    $all = @($read.Values)
    $script:WaitBoard = [pscustomobject]@{ Available = $available
        Active = @($all | Where-Object { $_.Status -eq 'vigilando' }); Problem = @($all | Where-Object { $_.Status -in 'error', 'perdido', 'detenido' })
        Finished = @($all | Where-Object { $_.Status -in 'cumplida', 'vencida', 'terminada' } | Sort-Object @{ Expression = { if ($_.LastCheck) { $_.LastCheck } else { [datetime]::MinValue } }; Descending = $true }) }
    $script:WaitBoard
}
# The wait a pendiente card declares itself («esperando: ...» in «Qué falta», rule 36), for when no live watcher is registered for it.
function Get-CardWait([string]$taskId, [string]$state, [string]$agent, [string]$missing) {
    if ($state -ne 'pendiente' -or $missing -notmatch '(?i)esperando\s*:\s*(?<what>.+)$') { return $null }
    [pscustomobject]@{ Id = ''; Reason = (Limit-Text $Matches['what'].Trim() 200); Task = $taskId; Agent = $agent; Status = 'vigilando'; Conditions = @(); Start = $null; LastCheck = $null
        NextCheck = $null; Progress = $null; Estimated = $null; Base = 'sin vigilante activo registrado'; Source = 'ficha' }
}
# The waits of a task: its live watchers (tarea), else the one its own card declares.
function Get-TaskWaits($board, [string]$taskId, [string]$state, [string]$agent, [string]$missing) {
    $list = @($board.Active | Where-Object { $_.Task -and $_.Task -ieq $taskId })
    if (-not $list.Count) { $card = Get-CardWait $taskId $state $agent $missing; if ($card) { $list = @($card) } }
    $list
}
# The waits of an agent: the live watchers that reactivate it (agente) plus those of its active tasks.
function Get-AgentWaits($board, [string]$name, [object[]]$tasks) {
    $list = [System.Collections.Generic.List[object]]::new()
    $seen = @{}
    $candidates = @($board.Active | Where-Object { $_.Agent -and $_.Agent -ieq $name })
    foreach ($task in $tasks) { $candidates += @(Get-TaskWaits $board $task.Id $task.State $task.Agent $task.Missing) }
    foreach ($wait in $candidates) {
        $key = if ($wait.Id) { $wait.Id } else { "ficha:$($wait.Task)" }
        if (-not $seen.ContainsKey($key)) { $seen[$key] = $true; $list.Add($wait) }
    }
    @($list)
}
# «NN % · hacia las HH:MM · <base>», or «sin estimación» (with its base when there is one). A percentage never goes without its base.
function Format-WaitProgress($progress, $estimated, [string]$base) {
    if ($null -eq $progress -or -not $base) {
        if (-not $base) { return 'sin estimación' }
        if ($base -like 'sin estimación*') { return $base }
        return "sin estimación · $base"
    }
    $when = if ($estimated) {
        $now = if ($null -ne $script:nowOverride) { [datetime]$script:nowOverride } else { Get-Date }
        if ($estimated.Date -eq $now.Date) { "hacia las $($estimated.ToString('HH:mm', [cultureinfo]::InvariantCulture))" } else { "hacia el $($estimated.ToString('yyyy-MM-dd HH:mm', [cultureinfo]::InvariantCulture))" }
    } else { 'sin hora estimada' }
    "$progress % · $when · $base"
}
function Format-WaitCondition($condition) {
    $text = if ($condition.Value) { "$($condition.Type) $($condition.Value)" } else { $condition.Type }
    "$text ($(if ($condition.Met) { 'cumplida' } else { 'pendiente' }))"
}
function Format-WaitChip { "<span class=""state s-espera"">$(Encode $script:WaitLabel)</span>" }
# The detail of one wait: motivo, condition, who it reactivates, since when, last and next check, the progress, and apart from it the watcher's
# time limit. -PerCondition adds one line per condition.
function Format-WaitDetailHtml($wait, [switch]$PerCondition) {
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add("Motivo: $(Encode $(if ($wait.Reason) { $wait.Reason } else { 'no indicado' }))")
    if ($wait.Conditions.Count) { $lines.Add("Condición: $(Encode (($wait.Conditions | ForEach-Object { Format-WaitCondition $_ }) -join '; '))") }
    $who = if ($wait.Agent) { "Reactiva a: $(Encode $wait.Agent)" } else { 'Reactiva a: agente no indicado' }
    $times = @()
    if ($wait.Start) { $times += "desde $(Encode (Format-WorkTime $wait.Start))" }
    if ($wait.LastCheck) { $times += "última revisión $(Encode (Format-WorkTime $wait.LastCheck))" }
    if ($wait.NextCheck) { $times += "próxima revisión $(Encode (Format-WorkTime $wait.NextCheck))" }
    $lines.Add((@($who) + $times) -join ' · ')
    $lines.Add("Avance: $(Encode (Format-WaitProgress $wait.Progress $wait.Estimated $wait.Base))")
    if ($wait.Deadline) { $lines.Add("Tiempo máximo del vigilante: hasta $(Encode (Format-WorkTime $wait.Deadline)) (no es avance)") }
    $html = ($lines | ForEach-Object { "<div class=""meta"">$_</div>" }) -join ''
    if ($PerCondition -and $wait.Conditions.Count -gt 1) {
        $items = foreach ($condition in $wait.Conditions) { "<li>$(Encode (Format-WaitCondition $condition)): $(Encode (Format-WaitProgress $condition.Progress $condition.Estimated $condition.Base))</li>" }
        $html += "<ul class=""wait-conditions"">$($items -join '')</ul>"
    }
    "<div class=""wait-detail"">$html</div>"
}
# The label and the detail of every wait of a task or an agent.
function Get-WaitCellHtml([object[]]$waits) { "$(Format-WaitChip)$((@($waits) | ForEach-Object { Format-WaitDetailHtml $_ }) -join '')" }
# The «Vigilantes» list of the «Fuera de tareas» section: live watchers, the ones with a problem (detenido, perdido, con error: they promise no
# reactivation) and the finished ones collapsed. Never the message of an error, the action script or what a condition matched.
function Get-WaitersHtml($board) {
    $active = @($board.Active | Sort-Object @{ Expression = { if ($_.Start) { $_.Start } else { [datetime]::MinValue } }; Descending = $true })
    $problems = @($board.Problem | Sort-Object @{ Expression = { if ($_.Start) { $_.Start } else { [datetime]::MinValue } }; Descending = $true })
    if ($board.Available -and -not ($active.Count + $problems.Count + $board.Finished.Count)) { return '' }
    $notice = if (-not $board.Available) { '<div class="stop"><strong>Vigilantes no disponibles</strong>No se pudo leer la carpeta de esperas de activación; no se presenta como una lista vacía.</div>' } else { '' }
    $count = if ($active.Count) { "$($active.Count) $(if ($active.Count -eq 1) { 'activo' } else { 'activos' })" } else { 'ninguno activo' }
    if ($problems.Count) { $count += " · $($problems.Count) con problemas" }
    $countClass = if ($active.Count) { 's-espera' } elseif ($problems.Count) { 's-unknown' } else { 's-idle' }
    $head = { param($wait) $task = if ($wait.Task) { " · $(Encode $wait.Task)" } else { '' }; "<strong>$(Encode $wait.Id)$task</strong>" }
    $activeHtml = foreach ($wait in $active) { "<div class=""agent-task wait-item"">$(& $head $wait) $(Format-WaitChip)$(Format-WaitDetailHtml $wait -PerCondition)</div>" }
    $problemHtml = foreach ($wait in $problems) {
        $last = if ($wait.LastCheck) { Encode (Format-WorkTime $wait.LastCheck) } else { 'hora no registrada' }
        $chip, $chipClass, $text = switch ($wait.Status) {
            'detenido' { 'Vigilante detenido', 's-unknown', "El proceso del vigilante ya no existe: esta espera no se reactivará sola. Última revisión $last." }
            'perdido' { 'Vigilante perdido', 's-unknown', "No da señales de vida: su última revisión fue a las $last, hace más de 3 intervalos. Puede estar colgado." }
            default { 'Vigilante con error', 's-requiere-cambios', "Su última comprobación falló (última revisión $last); reintenta en la próxima$(if ($wait.NextCheck) { " ($(Encode (Format-WorkTime $wait.NextCheck)))" })." }
        }
        "<div class=""agent-task wait-item"">$(& $head $wait) <span class=""state $chipClass"">$chip</span>" +
        "<div class=""meta"">Motivo: $(Encode $wait.Reason)</div><div class=""meta"">$text</div></div>"
    }
    $finishedHtml = ''
    if ($board.Finished.Count) {
        $items = foreach ($wait in $board.Finished) {
            $span = "$(if ($wait.Start) { Encode (Format-WorkTime $wait.Start) } else { 'hora no registrada' })$(if ($wait.LastCheck) { " → $(Encode (Format-WorkTime $wait.LastCheck))" })"
            $target = @(if ($wait.Task) { "tarea $(Encode $wait.Task)" }; if ($wait.Agent) { "reactivó a $(Encode $wait.Agent)" }) -join ' · '
            "<li><strong>$(Encode $wait.Id)</strong> · $(Encode $wait.Status)$(if ($target) { " · $target" }) · $span · $(Encode $wait.Reason)</li>"
        }
        $finishedHtml = "<details class=""wait-finished""><summary>Terminados ($($board.Finished.Count))</summary><ul>$($items -join '')</ul></details>"
    }
    $empty = if (-not $active.Count -and -not $problems.Count) { '<p class="meta">No hay vigilantes activos.</p>' } else { '' }
    "<div class=""wait-group"" id=""vigilantes""><div class=""card-head""><h3>Vigilantes</h3><span class=""state $countClass"">$count</span></div>" +
    '<p class="meta">Esperas por condición (regla 36): un vigilante sin modelo de IA comprueba la condición y relanza al agente cuando se cumple. Solo se muestran horas, estado y números.</p>' +
    "$notice$empty$($activeHtml -join '')$($problemHtml -join '')$finishedHtml</div>"
}

function Get-AgentBoardData {
    $registered = @((Get-RegisteredAgents).Name)
    $tasks = [System.Collections.Generic.List[object]]::new()
    $history = [System.Collections.Generic.List[object]]::new()
    $issues = 0
    if (-not (Test-Path -LiteralPath $tasksDir -PathType Container)) {
        return [pscustomobject]@{ Available = $false; Partial = $false; Issues = 0; Tasks = @(); History = @() }
    }

    try {
        $stages = @(Get-ChildItem -LiteralPath $tasksDir -Directory -ErrorAction Stop)
    } catch {
        Write-Warning 'No se pudo enumerar el tablero de tareas; el estado de agentes no está disponible.'
        return [pscustomobject]@{ Available = $false; Partial = $false; Issues = 1; Tasks = @(); History = @() }
    }

    foreach ($stage in $stages) {
        try {
            $files = @(Get-ChildItem -LiteralPath $stage.FullName -Filter 'T*.md' -File -ErrorAction Stop | Sort-Object Name)
        } catch {
            $issues++
            Write-Warning 'No se pudieron enumerar fichas de una etapa; se omitieron.'
            continue
        }
        foreach ($file in $files) {
            try {
                $text = Get-Content -LiteralPath $file.FullName -Raw -Encoding utf8 -ErrorAction Stop
            } catch {
                $issues++
                Write-Warning "No se pudo leer la ficha de tarea $($file.Name); se omitió."
                continue
            }
            $state = (Get-Field $text 'Estado').Trim().ToLowerInvariant()
            if ($state -notin @('disponible', 'en curso', 'pendiente', 'liberada', 'terminada', 'requiere cambios', 'integrada', 'reabierta')) {
                $issues++
                Write-Warning "La ficha de tarea $($file.Name) no tiene un estado válido; se omitió."
                continue
            }
            $id = if ($text -match '(?m)^#\s+(G\d+[A-Za-z0-9]*-T\d+)\s*·') {
                $Matches[1]
            } elseif ($file.BaseName -match '^(T\d+)') {
                "$($stage.Name)-$($Matches[1])"
            } else {
                $file.BaseName
            }
            $title = if ($text -match '(?m)^#\s+[^·\r\n]+·\s*(.+)$') { $Matches[1].Trim() } else { $file.BaseName }
            $title = Safe-Snippet $title
            $agent = (Get-Field $text 'Agente').Trim()
            $task = [pscustomobject]@{
                Id = $id
                Title = $title
                State = $state
                Agent = $agent
                Missing = Safe-Snippet (Get-TaskSection $text 'Qué falta')
                Tokens = Get-Field $text 'Tokens'
            }
            $tasks.Add($task)
            if ($state -in @('en curso', 'pendiente') -and $agent -notin $registered) {
                $issues++
                Write-Warning "La ficha de tarea $($file.Name) no identifica un agente registrado para su asignación activa."
            }

            $historyText = Get-TaskSection $text 'Historial'
            $task | Add-Member -NotePropertyName ActiveDelegations -NotePropertyValue @((Get-CardDelegations $historyText $state).Active)
            $badHistory = $false
            $historyLineNumber = 0
            foreach ($line in ($historyText -split "`r?`n")) {
                $historyLineNumber++
                if ($line -match '^\|\s*Fecha\s*\|' -or $line -match '^\|\s*:?-{2,}') { continue }
                if ($line -notmatch '^\|\s*(\d{4}-\d{2}-\d{2}(?:\s+\d{2}:\d{2})?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|') {
                    if ($line -match '^\|.*\|') { $badHistory = $true }
                    continue
                }
                $dateText = $Matches[1]
                $historyAgent = $Matches[2].Trim()
                $action = $Matches[3].Trim()
                $detail = $Matches[4].Trim()
                $format = if ($dateText -match '\s') { 'yyyy-MM-dd HH:mm' } else { 'yyyy-MM-dd' }
                try {
                    $recorded = [datetime]::ParseExact($dateText, $format, [Globalization.CultureInfo]::InvariantCulture)
                } catch [System.FormatException] {
                    $badHistory = $true
                    continue
                }
                if ($historyAgent -notin $registered -or $action -eq 'creada') { continue }
                $hasTime = $dateText -match '\s'
                $history.Add([pscustomobject]@{
                    Agent = $historyAgent
                    TaskId = $id
                    Title = $title
                    Date = $recorded.ToString('yyyy-MM-dd')
                    Recorded = $recorded
                    HasTime = $hasTime
                    Action = $action
                    Detail = Safe-Snippet $detail
                    Sequence = $historyLineNumber
                })
            }
            if ($badHistory) {
                $issues++
                Write-Warning "El historial de la ficha $($file.Name) contiene fechas no válidas; se omitieron esas filas."
            }
        }
    }
    [pscustomobject]@{
        Available = $true
        Partial = $issues -gt 0
        Issues = $issues
        Tasks = @($tasks)
        History = @($history)
    }
}

function Get-IdeasHtml {
    if (-not $script:ideasModuleAvailable) {
        return '<section class="panel"><strong>Biblioteca de ideas no disponible.</strong><p class="meta">El gestor no pudo cargarse.</p></section>'
    }
    try {
        $prompts = @(Get-IdeasPrompts -StorePath $script:ideasStorePath)
        $registeredTargets = @(Get-IdeasPromptTargets -SystemRoot $channel)
    } catch {
        Write-Warning "No se pudo cargar la biblioteca de ideas: $($_.Exception.Message)"
        return '<section class="stop"><strong>Ideas no disponibles</strong>No se pudo leer la biblioteca o el registro de agentes; no se presenta como una lista vacía.</section>'
    }
    $targets = $registeredTargets + @($prompts | ForEach-Object Audience | Where-Object { $_ -notin $registeredTargets } | Select-Object -Unique)
    $groups = foreach ($audience in $targets) {
        $items = @($prompts | Where-Object Audience -CEQ $audience)
        if (-not $items.Count) { continue }
        $audienceLabel = switch -CaseSensitive ($audience) {
            'Compartida' { 'Abierta · cualquier agente' }
            'Agente cloud' { 'Agente cloud · marcado como destino (no enviado)' }
            default {
                if ($audience -notin $registeredTargets) {
                    "$audience · agente no registrado actualmente"
                } else { "Agente local · dirigido a $audience" }
            }
        }
        $cards = foreach ($item in $items) {
            $statusLabel, $statusClass = switch ($item.Status) {
                'pendiente_revision' { @('Sin revisar', 's-unknown') }
                'revisada_pendiente' { @('Revisada · pendiente', 's-pendiente') }
                'completada' { @('Completada', 's-integrada') }
                default { @('Estado no reconocido', 's-unknown') }
            }
            $reviewMeta = if ($item.StatusActor -and $item.StatusUpdatedUtc) {
                $updated = [DateTimeOffset]::Parse(
                    $item.StatusUpdatedUtc,
                    [System.Globalization.CultureInfo]::InvariantCulture,
                    [System.Globalization.DateTimeStyles]::RoundtripKind
                ).ToLocalTime().ToString('yyyy-MM-dd HH:mm')
                "<p class=""meta"">Estado registrado por $(Encode $item.StatusActor) · $(Encode $updated)</p>"
            } else { '' }
            $reviewNote = if ($item.StatusNote) {
                "<p class=""idea-review-note"">$(Encode $item.StatusNote)</p>"
            } else { '' }
            @"
<article class="panel idea-card">
  <div class="idea-title"><h3>$(Encode $item.Title)</h3><span class="state $statusClass">$statusLabel</span></div>
  $reviewNote
  $reviewMeta
</article>
"@
        }
        "<section class=""idea-group""><h2>$(Encode $audienceLabel)</h2>$($cards -join "`n")</section>"
    }
    $contents = if ($groups) { $groups -join "`n" } else { '<section class="panel">Todavía no hay ideas ni prompts.</section>' }
    $button = if ($script:flowListener -and $script:ideasToken) {
        '<button id="open-ideas-manager" class="action-button" type="button">Agregar, editar o eliminar ideas</button>'
    } else {
        '<button id="open-ideas-manager" class="action-button" type="button" disabled title="Abre el visualizador con Seguimiento.cmd">Agregar, editar o eliminar ideas</button>'
    }
    @"
<p class="meta">Cada prompt puede quedar abierto a cualquier agente, dirigido a un agente local registrado o marcado para un agente cloud. Esto solo indica el destinatario: no inicia trabajo, reserva archivos, notifica agentes ni envía contenido a la nube. La preparación cloud sigue separada y manual.</p>
<p class="meta idea-status-legend"><strong>Estado de ideas:</strong> Sin revisar = aún no evaluada; Revisada · pendiente = evaluada, con trabajo pendiente indicado debajo; Completada = la nota resume el resultado. La fecha es la del registro de este estado, no necesariamente la fecha en que se realizó el trabajo.</p>
$button
<p id="ideas-status" class="meta" role="status" aria-live="polite"></p>
$contents
"@
}

# realinear\ESTADO.md (written by realinear.ps1): one row per worktree. Worktrees belong to the application
# (rule 28), so a name like Claude-Fable falls back to the row of its application (Claude).
function Get-AlignmentHtml([string]$name, [string]$application = '') {
    $path = Join-Path $channel 'realinear\ESTADO.md'
    $lines = if (Test-Path -LiteralPath $path) { @(Get-Content -LiteralPath $path -Encoding utf8) } else { @() }
    $row = $null
    foreach ($key in @($name, $application) | Where-Object { $_ }) {
        $row = $lines | Where-Object { $_ -match "^\|\s*$([regex]::Escape($key))\s*\|" } | Select-Object -First 1
        if ($row) { break }
    }
    if (-not $row) { return '<p class="meta">Realineación: sin revisar todavía (paso 4 de Flujo.cmd).</p>' }
    $cells = @($row.Trim('|').Split('|') | ForEach-Object { $_.Trim() })
    $pending = $cells[1] -eq 'PENDIENTE'
    $class = if ($pending) { 's-pendiente' } else { 's-integrada' }
    $detail = if ($pending -and $cells[3]) { " — $(Encode $cells[3]). Debe realinearse antes de tomar otra tarea (regla 18)." } else { '' }
    "<p class=""meta""><span class=""state $class"">Realineación: $(Encode $cells[1])</span> $(Encode $cells[2]) · revisado $(Encode $cells[4])$detail</p>"
}

function Limit-Text([string]$text, [int]$max) {
    if ($text.Length -gt $max) { $text.Substring(0, $max - 3) + '...' } else { $text }
}
# SEG-T07 convention: a card or ETAPA.md line containing the literal marker "Pide al usuario:" (any
# case) is surfaced in Resumen > "Decisiones que te piden". Write it when an agent needs a decision.
function Get-PideAlUsuarioLines([string]$text) {
    @($text -split "`r?`n" | Where-Object { $_ -match '(?i)pide al usuario:' } | ForEach-Object { Limit-Text (Safe-Snippet $_) 200 })
}
function Format-TodoGroup([string]$heading, [object[]]$items) {
    if (-not $items.Count) { return '' }
    $sorted = @($items | Sort-Object @{ Expression = { if ($_.Date) { [datetime]$_.Date } else { [datetime]::MinValue } }; Descending = $true })
    $lis = foreach ($item in $sorted) {
        $label = "<a href=""$($item.Href)"">$(Encode $item.Title)</a>"
        $extra = if ($item.Text) { ' · ' + (Encode $item.Text) } else { '' }
        $date = if ($item.Date) { " <span class=""meta"">$(Encode (([datetime]$item.Date).ToString('yyyy-MM-dd HH:mm', [cultureinfo]::InvariantCulture)))</span>" } else { '' }
        "<li>$label$extra$date</li>"
    }
    "<h3>$(Encode $heading) <span class=""meta"">($($items.Count))</span></h3><ul>$($lis -join '')</ul>"
}
function Build-Page {
Reset-WorkerCaches   # SEG-T15: transcripts, models and execution lines are read again on every generation
$waitBoard = Get-WaitBoard   # SEG-T19: the watchers (esperas de activación) are read again too
$agentBoard = Get-AgentBoardData
$apiBoardHtml = if ($LeerApi) { Get-ApiBoardHtml } else { '' }
$readsText = if (Test-Path -LiteralPath $integrationReads -PathType Leaf) {
    try { Get-Content -LiteralPath $integrationReads -Raw -Encoding utf8 -ErrorAction Stop }
    catch {
        Write-Warning 'No se pudo leer el registro de resultados de integración; no se verificará ningún cierre.'
        ''
    }
} else { '' }

# Publication is derived, never hand-kept: a verified stage is public when its closing commit is
# contained in the public branch as this machine last saw it (the remote-tracking ref that push and
# fetch update; no network access here). <canal>\repositorio-publico.txt names the public checkout
# (line 1) and, optionally, the ref (line 2, default origin/main). Without it, stages stay "local".
$publicRepo = ''
$publicRef = 'origin/main'
$publicConfig = Join-Path $channel 'repositorio-publico.txt'
if (Test-Path -LiteralPath $publicConfig -PathType Leaf) {
    $publicLines = @(Get-Content -LiteralPath $publicConfig | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith('#') })
    if ($publicLines.Count -ge 1 -and (Test-Path -LiteralPath $publicLines[0] -PathType Container)) {
        $publicRepo = (Resolve-Path -LiteralPath $publicLines[0]).Path
    }
    if ($publicLines.Count -ge 2) { $publicRef = $publicLines[1] }
}
function Test-CommitPublished([string]$commit) {
    if (-not $publicRepo -or $commit -notmatch '^(?i)[0-9a-f]{7,40}$') { return $false }
    # Exit 1 = not contained; 128 = commit or ref unknown there (for example, rewritten hashes).
    git -C $publicRepo merge-base --is-ancestor $commit $publicRef 2>$null
    $LASTEXITCODE -eq 0
}

# These legacy milestones predate the task board and were explicitly confirmed by the user as
# verified and present in the public repository.
$publicVerifiedEvidence = [ordered]@{
    G1 = [pscustomobject]@{ Goal = 'Validar antes de ejecutar'; Commit = 'e887bf3'; Report = '2026-09-26_1935_integracion.md' }
    G2 = [pscustomobject]@{ Goal = 'Revisión humana a mitad del workflow'; Commit = '27b3e1e'; Report = '2026-09-26_2020_integracion.md' }
    G3 = [pscustomobject]@{ Goal = 'Un caso fuera de marketing (bundles)'; Commit = '2682b3e'; Report = '2026-09-26_2031_integracion.md' }
}
$activeCards = [System.Collections.Generic.List[object]]::new()
# SEG-T13: integration round in progress (parches\ronda.txt) and the notice shown when the page is generated during it.
$integrationRound = Get-IntegrationRound
$integrationNoticeHtml = if ($integrationRound.Active) {
    $roundNames = @($integrationRound.Patches | Select-Object -First 5) -join ', '
    if ($integrationRound.Patches.Count -gt 5) { $roundNames += " y $($integrationRound.Patches.Count - 5) más" }
    "<p class=""seg-integration-notice"" role=""status"">Generado durante una integración (ronda: $(Encode $roundNames)); los datos pueden cambiar al terminar.</p>"
} else { '' }

$boards = ''
$archiveBoardEntries = [System.Collections.Generic.List[object]]::new()
$publicEntries = [System.Collections.Generic.List[object]]::new()
$uncertainEntries = [System.Collections.Generic.List[object]]::new()
$archiveLinkEntries = [System.Collections.Generic.List[object]]::new()
$stageIndex = @{}
# SEG-T07: generated "Qué tienes que hacer ahora" items, filled while the board is read.
$todoReady = [System.Collections.Generic.List[object]]::new()
$todoAuth = [System.Collections.Generic.List[object]]::new()
$todoDecisions = [System.Collections.Generic.List[object]]::new()
$boardReadFailed = $false
if (Test-Path -LiteralPath $tasksDir -PathType Container) {
    $boardReadFailed = $false
    try {
        $stageDirectories = @(Get-ChildItem -LiteralPath $tasksDir -Directory -ErrorAction Stop | Sort-Object Name)
    } catch {
        Write-Warning 'No se pudieron enumerar las etapas del tablero.'
        $stageDirectories = @()
        $boardReadFailed = $true
    }
    foreach ($stage in $stageDirectories) {
        $stageFile = Join-Path $stage.FullName 'ETAPA.md'
        $stageText = ''
        $stageReadFailed = $false
        if (Test-Path -LiteralPath $stageFile -PathType Leaf) {
            try { $stageText = Get-Content -LiteralPath $stageFile -Raw -Encoding utf8 -ErrorAction Stop }
            catch {
                Write-Warning "No se pudo leer la ficha de etapa $($stage.Name)."
                $stageReadFailed = $true
                $boardReadFailed = $true
            }
        } else {
            $stageReadFailed = $true
        }
        $stageTitle = if ($stageText -match '(?m)^# (.+)$') { $Matches[1] } else { $stage.Name }
        $authorized = Get-Field $stageText 'Autorizada'
        $releaseMode = (Get-Field $stageText 'Liberar dependientes').Trim().ToLowerInvariant()
        if ($releaseMode -notin 'probada', 'integrada', 'terminada') { $releaseMode = 'probada' }
        $done = 0; $total = 0
        $taskReadFailed = $stageReadFailed
        try {
            $taskFiles = @(Get-ChildItem -LiteralPath $stage.FullName -Filter 'T*.md' -File -ErrorAction Stop | Sort-Object Name)
        } catch {
            Write-Warning "No se pudieron enumerar las fichas de la etapa $($stage.Name)."
            $taskFiles = @()
            $taskReadFailed = $true
            $boardReadFailed = $true
        }
        $taskRecords = [System.Collections.Generic.List[object]]::new()
        $taskRows = [System.Collections.Generic.List[object]]::new()
        foreach ($file in $taskFiles) {
            try { $text = Get-Content -LiteralPath $file.FullName -Raw -Encoding utf8 -ErrorAction Stop }
            catch {
                Write-Warning "No se pudo leer la ficha de tarea $($file.Name); se omitió de la tabla."
                $taskReadFailed = $true
                $boardReadFailed = $true
                continue
            }
            $name = if ($text -match '(?m)^# (.+)$') { $Matches[1] } else { $file.BaseName }
            $name = Safe-Snippet $name
            $state = (Get-Field $text 'Estado').Trim().ToLowerInvariant()
            $taskId = if ($file.BaseName -match '^(T\d+)') { $Matches[1].ToUpperInvariant() } else { $file.BaseName.ToUpperInvariant() }
            $history = Get-TaskSection $text 'Historial'
            $lastRow = @(Get-HistoryRows $history | Sort-Object Date) | Select-Object -Last 1
            $record = [pscustomobject]@{
                LastRow = $lastRow
                Id = "$($stage.Name)-$taskId"
                Code = $taskId
                Title = $name
                State = $state
                Commit = Get-Field $text 'Commit'
                Dependencies = Get-Field $text 'Depende de'
                Patch = Get-Field $text 'Parche'
                Agent = Get-Field $text 'Agente'
                Preferred = Get-Field $text 'Preferente'
                Tokens = Get-Field $text 'Tokens'
                # Rule 27: probar.ps1 writes "probada" / "prueba fallida" after a delivery; a new
                # delivery, reopening, or retake clears the verdict, so only the latest of these rows counts.
                Verdict = @($history -split "`r?`n" | ForEach-Object {
                    if ($_ -match '^\|\s*\d{4}-[^|]*\|\s*[^|]+?\s*\|\s*(terminada|probada|prueba fallida|reabierta|retomada|tomada)\s*\|') { $Matches[1] }
                } | Select-Object -Last 1) -replace '^(terminada|reabierta|retomada|tomada)$', ''
                Workers = @($history -split "`r?`n" | ForEach-Object {
                    # Same rule as tareas.ps1: authors took or finished the task.
                    if ($_ -match '^\|\s*\d{4}-[^|]*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|' -and $Matches[2].Trim() -in 'tomada', 'terminada') { $Matches[1] }
                } | Select-Object -Unique) -join ', '
                DeliveredDate = Get-TaskEventDate $history 'terminada'
                IntegratedDate = Get-TaskEventDate $history 'integrada'
                Href = ([uri]$file.FullName).AbsoluteUri
                Missing = ''
            }
            $total++
            if ($state -in 'terminada', 'integrada') { $done++ }
            $left = ''
            if (($state -in 'liberada', 'pendiente', 'reabierta', 'requiere cambios') -and $text -match '(?s)## Qu. falta\s*(.+?)\s*## ') {
                $left = '<div class="left">Falta: ' + (Inline $Matches[1]) + '</div>'
            }
            $record.Missing = if (($state -in 'liberada', 'pendiente', 'reabierta', 'requiere cambios') -and $text -match '(?s)## Qu. falta\s*(.+?)\s*## ') { Safe-Snippet $Matches[1] } else { '' }
            $record | Add-Member -NotePropertyName LeftHtml -NotePropertyValue $left
            $record | Add-Member -NotePropertyName PideLines -NotePropertyValue @(Get-PideAlUsuarioLines $text)
            $record | Add-Member -NotePropertyName FileDate -NotePropertyValue $file.LastWriteTime
            $cardDelegations = Get-CardDelegations $history $state
            $record | Add-Member -NotePropertyName ActiveDelegations -NotePropertyValue @($cardDelegations.Active)
            $record | Add-Member -NotePropertyName Delegation -NotePropertyValue (Get-DelegationSummary $record.Id $cardDelegations $record.Agent)
            $taskRecords.Add($record)
        }
        $tasksByCode = @{}
        foreach ($record in $taskRecords) { $tasksByCode[$record.Code] = $record }
        foreach ($record in $taskRecords) {
            $dependencyCodes = @(Expand-DependencyCodes $record.Dependencies)
            # A task that its card calls disponible, liberada or reabierta is not takeable while a
            # dependency has not released its dependents (rule 27); the card's state is not changed.
            $waitingFor = @()
            if ($record.State -in 'disponible', 'liberada', 'reabierta') {
                $waitingFor = @($dependencyCodes | ForEach-Object {
                    if (-not $tasksByCode.ContainsKey($_)) { "$_ (sin ficha)" }
                    elseif ($why = Get-DependencyWait $tasksByCode[$_] $releaseMode) { "$_ ($why)" }
                })
            }
            $record | Add-Member -NotePropertyName WaitingFor -NotePropertyValue $waitingFor -Force
            # SEG-T13: a delivered task whose patch is in the round being integrated, while the round's tests have no report yet.
            $integrating = $false
            if ($integrationRound.Active -and $integrationRound.TestsRunning -and $record.State -eq 'terminada') {
                $cardPatches = @([regex]::Matches([string]$record.Patch, '\d{3}-[^`\s,()]+\.patch') | ForEach-Object { $_.Value })
                $integrating = @($cardPatches | Where-Object { $integrationRound.Patches -contains $_ }).Count -gt 0
            }
            $record | Add-Member -NotePropertyName Integrating -NotePropertyValue $integrating -Force
            $dependencyHtml = if ($dependencyCodes.Count) {
                $items = foreach ($dependencyCode in $dependencyCodes) {
                    if (-not $tasksByCode.ContainsKey($dependencyCode)) {
                        "<div><code>$(Encode $dependencyCode)</code> <span class=""state s-unknown"">no verificable</span></div>"
                        continue
                    }
                    $dependency = $tasksByCode[$dependencyCode]
                    $dependencyClass = 's-' + ($dependency.State -replace ' ', '-')
                    $integrationNote = if ($dependency.State -eq 'terminada' -and (Test-TaskPatchRequired $dependency.Patch)) { ' · parche pendiente de integración' }
                        elseif ($dependency.State -eq 'terminada') { ' · sin parche que integrar' }
                        elseif ($dependency.State -eq 'integrada') { ' · integrada' }
                        else { '' }
                    "<div><code>$(Encode $dependencyCode)</code> <span class=""state $dependencyClass"">$(Encode $dependency.State)</span>$(Encode $integrationNote)</div>"
                }
                $items -join ''
            } else { '<span class="meta">Ninguna</span>' }

            $integrationHtml = if ($record.State -eq 'requiere cambios') {
                # A delivered task with a reported defect: its patch is held until the author fixes it.
                "<span class=""state s-requiere-cambios"">Bloqueada · requiere cambios</span><div class=""meta"">Parche en espera: $(Inline $record.Patch)</div>"
            } elseif ($record.State -eq 'terminada') {
                $date = if ($record.DeliveredDate) { "<div class=""meta"">Entrega registrada $(Encode $record.DeliveredDate)</div>" } else { '' }
                $verdict = switch ($record.Verdict) {
                    'probada' { '<div><span class="state s-integrada">Probada · libera dependientes</span></div>' }
                    'prueba fallida' { '<div><span class="state s-requiere-cambios">Prueba fallida · no libera dependientes</span></div>' }
                    default { '<div class="meta">Sin veredicto de prueba todavía (regla 27)</div>' }
                }
                if ($record.Integrating) {
                    "<span class=""state s-en-curso"">Integrando · pruebas desde $($integrationRound.Since.ToString('HH:mm', [cultureinfo]::InvariantCulture))</span>$date<div class=""meta"">Parche: $(Inline $record.Patch)</div>"
                } elseif (Test-TaskPatchRequired $record.Patch) {
                    "<span class=""state s-pendiente"">Requiere integración · pendiente</span>$verdict$date<div class=""meta"">Parche: $(Inline $record.Patch)</div>"
                } elseif ($record.Patch.Trim()) {
                    "<span class=""state s-idle"">Sin parche que integrar</span>$date"
                } else {
                    "<span class=""state s-unknown"">Integración indeterminada · parche no registrado</span>$date"
                }
            } elseif ($record.State -eq 'integrada') {
                $commit = Get-CommitToken $record.Commit
                $commitLabel = if ($commit) { "<div class=""meta"">Commit <code>$(Encode $commit)</code></div>" }
                    elseif ($record.Patch.Trim() -and -not (Test-TaskPatchRequired $record.Patch)) { '<div class="meta">Sin parche que integrar</div>' }
                    else { '<div class="meta">Commit no registrado</div>' }
                $date = if ($record.IntegratedDate) { "<div class=""meta"">Integrada $(Encode $record.IntegratedDate)</div>" } else { '<div class="meta">Fecha de integración no registrada</div>' }
                "<span class=""state s-integrada"">Integración registrada</span>$commitLabel$date"
            } else {
                $pendingDependencies = @($dependencyCodes | Where-Object {
                    $tasksByCode.ContainsKey($_) -and $tasksByCode[$_].State -eq 'terminada' -and (Test-TaskPatchRequired $tasksByCode[$_].Patch)
                })
                $gate = if ($pendingDependencies.Count) {
                    "Dependencia(s) con parche pendiente de integración: $(Encode ($pendingDependencies -join ', '))."
                } else { 'Sin integración propia pendiente.' }
                "<span class=""state s-unknown"">Aún no entregada</span><div class=""meta"">$gate</div>"
            }

            $stateClass = 's-' + ($record.State -replace ' ', '-')
            $stateCell = if ($record.WaitingFor.Count) {
                "<span class=""state s-bloqueada"">Bloqueada por dependencia</span><div class=""meta"">Espera: $(Encode ($record.WaitingFor -join '; '))</div><div class=""meta"">Estado en la ficha: $(Encode $record.State)</div>"
            } else { "<span class=""state $stateClass"">$(Encode $record.State)</span>" }
            # SEG-T19: a task with a live watcher (tarea), or a pendiente card that says «esperando: ...», reads «En espera de activación».
            $taskWaits = @(Get-TaskWaits $waitBoard $record.Id $record.State $record.Agent $record.Missing)
            if ($taskWaits.Count) {
                $stateCell = if ($record.WaitingFor.Count) { $stateCell + (Get-WaitCellHtml $taskWaits) }
                    else { (Get-WaitCellHtml $taskWaits) + "<div class=""meta"">Estado en la ficha: $(Encode $record.State)</div>" }
            }
            $lastActivityHtml =if ($record.LastRow) { "<div class=""meta"">Última actividad $(Encode (Format-ActivityDate $record.LastRow.Date))</div>" } else { '<div class="meta">Sin historial fechado</div>' }
            $delegationHtml = if ($record.Delegation) {
                "<div class=""meta"">orquestó $(Encode $record.Delegation.Orchestrator)</div>" + (Get-WorkerListHtml $record.Delegation $record.Tokens)
            } else { '' }
            $rowHtml = "<tr><td><a href=""$($record.Href)"">$(Encode $record.Title)</a>$lastActivityHtml$($record.LeftHtml)</td>" +
            "<td>$stateCell</td>" +
            "<td>$(Inline $record.Agent)</td><td>$(Inline $record.Preferred)</td>" +
            "<td>$dependencyHtml</td><td>$integrationHtml</td><td>$(Inline $record.Patch)</td><td>$(Encode $record.Workers)$delegationHtml</td><td>$(Get-TokensHtml $record.Tokens)</td></tr>"
            $taskRows.Add([pscustomobject]@{ Id = $record.Code; State = $record.State; Patch = $record.Patch; WaitingFor = @($record.WaitingFor); Integrating = [bool]$record.Integrating; Html = $rowHtml; Date = if ($record.LastRow) { $record.LastRow.Date } else { $null } })
        }
        # SEG-T05: tasks inside a stage follow their code (T01, T02, ...); each row still shows its own last activity.
        $taskRows = @($taskRows | Sort-Object @{ Expression = { if ($_.Id -match '^T(\d+)') { [int]$Matches[1] } else { [int]::MaxValue } } }, @{ Expression = 'Id' })
        # SEG-T07: pending items for the user coming from this stage and its cards.
        $stageClosedState = (Get-Field $stageText 'Estado').Trim().ToLowerInvariant() -in @('cerrada', 'cerrado', 'closed', 'complete', 'completed')
        if (-not $stageReadFailed -and -not $stageClosedState) {
            $stageFileDate = try { (Get-Item -LiteralPath $stageFile -ErrorAction Stop).LastWriteTime } catch { $null }
            $stageHref = ([uri]$stageFile).AbsoluteUri
            $stageAuthorized = $authorized.Trim() -match '^(?i)s[ií](?![\p{L}])'
            if ($authorized.Trim() -and -not $stageAuthorized) {
                $todoAuth.Add([pscustomobject]@{ Title = $stage.Name; Text = (Limit-Text (Safe-Snippet $authorized) 160); Href = $stageHref; Date = $stageFileDate })
            } elseif ($authorized -match '(?i)pendientes? de autorizaci[oó]n') {
                $fragment = if ($authorized -match '(?i)([^.;]{0,100}pendientes? de autorizaci[oó]n[^.;]{0,60})') { $Matches[1] } else { $authorized }
                $todoAuth.Add([pscustomobject]@{ Title = $stage.Name; Text = (Limit-Text (Safe-Snippet $fragment) 160); Href = $stageHref; Date = $stageFileDate })
            }
            if ($stageAuthorized) {
                foreach ($record in $taskRecords) {
                    if ($record.State -eq 'disponible' -and -not $record.WaitingFor.Count) {
                        $todoReady.Add([pscustomobject]@{ Title = "$(if ($record.Title -like "$($record.Id)*") { $record.Title -replace '^(\S+)\s+', '$1 · ' } else { "$($record.Id) · $($record.Title)" }) · di «trabaja en $($stage.Name)»"; Text = ''; Href = $record.Href; Date = if ($record.LastRow) { $record.LastRow.Date } else { $record.FileDate } })
                    }
                }
            }
            foreach ($pideLine in @(Get-PideAlUsuarioLines $stageText)) {
                $todoDecisions.Add([pscustomobject]@{ Title = $stage.Name; Text = $pideLine; Href = $stageHref; Date = $stageFileDate })
            }
            foreach ($record in $taskRecords) {
                if ($record.State -in 'terminada', 'integrada') { continue }
                foreach ($pideLine in @($record.PideLines)) {
                    $todoDecisions.Add([pscustomobject]@{ Title = $record.Id; Text = $pideLine; Href = $record.Href; Date = if ($record.LastRow) { $record.LastRow.Date } else { $record.FileDate } })
                }
            }
        }
        $closure = if ($stageReadFailed) {
            [pscustomobject]@{ Status = 'unknown'; Reason = 'La ficha de etapa falta o no se pudo leer.'; Commit = ''; Report = ''; Stage = $stage.Name }
        } else {
            Get-StageClosure $stage.Name $stageText @($taskRecords) $taskReadFailed $readsText
        }
        $claimsClosed = -not $stageReadFailed -and (Get-Field $stageText 'Estado').Trim().ToLowerInvariant() -in @('cerrada', 'cerrado', 'closed', 'complete', 'completed')
        $closure | Add-Member -NotePropertyName ClaimsClosed -NotePropertyValue $claimsClosed
        $stageIndex[$stage.Name.ToLowerInvariant()] = $closure
        # SEG-T04: last activity of the stage and closure date.
        $stageHistoryEntries = @(Get-StageHistoryEntries $stageText)
        $lastActivity = $null
        foreach ($record in $taskRecords) {
            if ($record.LastRow -and (-not $lastActivity -or $record.LastRow.Date -gt $lastActivity.Date)) {
                $lastActivity = [pscustomobject]@{ Date = $record.LastRow.Date; Text = "$($record.Id) $(Safe-Snippet $record.LastRow.Action) ($(Safe-Snippet $record.LastRow.Agent))" }
            }
        }
        foreach ($entry in $stageHistoryEntries) {
            if (-not $lastActivity -or $entry.Date -gt $lastActivity.Date) {
                $brief = Safe-Snippet $entry.Text
                if ($brief.Length -gt 80) { $brief = $brief.Substring(0, 77) + '...' }
                $lastActivity = [pscustomobject]@{ Date = $entry.Date; Text = "$brief ($(Safe-Snippet $entry.Agent))" }
            }
        }
        $cardCloseDate = @($stageHistoryEntries | Where-Object { $_.Text -match '(?i)cerrada' } | Sort-Object Date | Select-Object -Last 1 | ForEach-Object Date) | Select-Object -Last 1
        $closeDate = $null
        $closeFromCard = $false
        if ($closure.Status -eq 'complete') { $closeDate = Get-CommitDate $closure.Commit }
        if (-not $closeDate -and $cardCloseDate) { $closeDate = $cardCloseDate; $closeFromCard = $true }
        $closedLabel = Get-ClosureLabelHtml $closeDate $closeFromCard
        $lastActivityLine = if ($lastActivity) { "<div class=""meta"">Última actividad: $(Encode (Format-ActivityDate $lastActivity.Date)) · $(Encode $lastActivity.Text)</div>" } else { '<div class="meta">Última actividad: sin registro</div>' }
        $pct = if ($total) { [int](100 * $done / $total) } else { 0 }
        # Summary card for every stage that is not verified as closed, built from its task cards.
        if ($closure.Status -ne 'complete') {
            $count = { param($states) @($taskRecords | Where-Object { $_.State -in $states }).Count }
            $integrated = & $count @('integrada')
            $working = @($taskRecords | Where-Object { $_.State -eq 'en curso' } | ForEach-Object { "$($_.Code) ($($_.Agent))" })
            $waitingOnDependency = @($taskRecords | Where-Object { $_.WaitingFor.Count }).Count
            $blocked = @($taskRecords | Where-Object { $_.State -in 'pendiente', 'requiere cambios', 'reabierta' -and -not $_.WaitingFor.Count }).Count
            $available = @($taskRecords | Where-Object { $_.State -in 'disponible', 'liberada' -and -not $_.WaitingFor.Count }).Count
            $workingText = if ($working) { "En curso: $(Encode ($working -join ', '))" } else { 'Nadie trabajando ahora' }
            $delegatedLines = @($taskRecords | Where-Object { $_.State -eq 'en curso' } | ForEach-Object { foreach ($d in $_.ActiveDelegations) { "<div class=""meta"">$(Encode $_.Code): $(Encode (Format-ActiveDelegation $d))</div>" } })
            $workingText += ($delegatedLines -join '')
            # SEG-T12: integration activity (delegations registered with -Integracion on delivered tasks, e.g. Ejecutando prueba).
            $integrationLines = @($taskRecords | Where-Object { $_.State -eq 'terminada' } | ForEach-Object { foreach ($d in $_.ActiveDelegations) { "<div class=""meta"">$(Encode $_.Code): integración · $(Encode (Format-ActiveDelegation $d))</div>" } })
            $workingText += ($integrationLines -join '')
            $activeCards.Add([pscustomobject]@{ Date = if ($lastActivity) { $lastActivity.Date } else { $null }; Html = @"
<div class="card">
  <div class="card-head"><span class="gate">$(Encode $stage.Name)</span><span class="pct">$pct%</span></div>
  <div class="bar"><div class="fill" style="width:$pct%"></div></div>
  <div class="meta">$done de $total terminadas o integradas &middot; $integrated integradas &middot; $available disponibles$(if ($waitingOnDependency) { " &middot; $waitingOnDependency bloqueadas por dependencia" })$(if ($blocked) { " &middot; $blocked en espera o con cambios" })</div>
  <div class="goal">$(Encode ($stageTitle -replace '^\S+\s*·\s*', ''))</div>
  <div class="meta">$workingText</div>
  $lastActivityLine
</div>
"@ })
        }
        $tableHeader ='<table><tr><th>Tarea</th><th>Estado</th><th>Agente</th><th>Preferente</th><th>Dependencias y estado</th><th>Integración</th><th>Parche</th><th>Trabajaron</th><th>Tokens</th></tr>'
        $phaseDefinitions = @(Get-StagePhases $stageText)
        $taskGroups = ''
        if ($phaseDefinitions.Count) {
            $assignedTasks = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
            $phaseSections = [System.Collections.Generic.List[string]]::new()
            $currentPhaseFound = $false
            foreach ($phase in $phaseDefinitions) {
                $phaseTaskRows = @($taskRows | Where-Object { $phase.TaskIds -contains $_.Id })
                foreach ($phaseTask in $phaseTaskRows) { [void]$assignedTasks.Add($phaseTask.Id) }
                $phaseDone = @($phaseTaskRows | Where-Object { $_.State -in 'terminada', 'integrada' }).Count
                $phaseTaskLabel = if ($phaseTaskRows.Count -eq 1) { 'tarea' } else { 'tareas' }
                # Phase status: "terminada" is not "integrada" (rule 21 / #131). The badge says which one.
                # Settled = integrated, or finished with no patch to integrate (analysis deliverables).
                $phaseIntegrated = @($phaseTaskRows | Where-Object {
                    $_.State -eq 'integrada' -or ($_.State -eq 'terminada' -and -not (Test-TaskPatchRequired $_.Patch))
                }).Count
                # SEG-T13: the same label function as the stage header (Get-StatusLabel).
                $phaseLabel = Get-StatusLabel 'Fase' $phaseTaskRows '' $integrationRound.Since
                $phaseBadge = if ($phaseLabel) { "<span class=""state $($phaseLabel.Class)"">$(Encode $phaseLabel.Text)</span>" } else { '' }
                $phaseContent = if ($phaseTaskRows.Count) {
                    "$tableHeader$($phaseTaskRows.Html -join "`n")</table>"
                } else {
                    '<p class="meta">No hay fichas de tarea que coincidan con esta fase.</p>'
                }
                # SEG-T09: collapsible phase. Open only if it is the current one (first phase that is not complete).
                $phaseComplete = $phaseTaskRows.Count -gt 0 -and $phaseIntegrated -eq $phaseTaskRows.Count
                $phaseOpen = $false
                if (-not $phaseComplete -and -not $currentPhaseFound) { $phaseOpen = $true; $currentPhaseFound = $true }
                $phaseId = 'seg:' + $stage.Name + ':' + (($phase.Label.ToLowerInvariant() -replace '[^\p{L}\p{Nd}]+', '-').Trim('-'))
                $phaseSections.Add(@"
<details class="board-phase seg-phase" data-seg-id="$(Encode $phaseId)"$(if ($phaseOpen) { ' open' })>
  <summary><h3>$(Encode $phase.Label) $phaseBadge</h3><span class="meta">$phaseDone de $($phaseTaskRows.Count) terminadas o integradas</span></summary>
  <div class="seg-body">
  <p class="meta">$($phaseTaskRows.Count) $phaseTaskLabel · $phaseDone terminadas o integradas ($phaseIntegrated sin integración pendiente)</p>
  $(if ($phase.Description) { "<p class=""meta"">$(Encode $phase.Description)</p>" })
  $phaseContent
  </div>
</details>
"@)
            }
            $unassignedRows = @($taskRows | Where-Object { -not $assignedTasks.Contains($_.Id) })
            if ($unassignedRows.Count) {
                $phaseSections.Add(@"
<details class="board-phase seg-phase" data-seg-id="$(Encode ('seg:' + $stage.Name + ':sin-fase'))" open>
  <summary><h3>Sin fase asignada</h3><span class="meta">$(@($unassignedRows | Where-Object { $_.State -in 'terminada', 'integrada' }).Count) de $($unassignedRows.Count) terminadas o integradas</span></summary>
  <div class="seg-body">$tableHeader$($unassignedRows.Html -join "`n")</table></div>
</details>
"@)
            }
            $taskGroups = $phaseSections -join "`n"
        } else {
            $taskGroups = "$tableHeader$($taskRows.Html -join "`n")</table>"
        }
        # SEG-T09: the stage is a collapsible <details>; active stages open, completed ones closed.
        $stageIsComplete = $closure.Status -eq 'complete'
        $stageOpen = -not $stageIsComplete -and -not $claimsClosed
        $stageStateText = if ($stageIsComplete) { 'cerrada' } elseif ($claimsClosed) { 'cierre no verificado' } else { $sf = (Get-Field $stageText 'Estado').Trim(); if ($sf) { $sf } else { 'activa' } }
        # SEG-T13: the header says what the tasks are doing (same function as the phases); the card's Estado is a secondary datum.
        $stageLabel = Get-StatusLabel 'Etapa' $taskRows $(if ($stageIsComplete) { 'cerrada' } elseif ($claimsClosed) { 'sin verificar' } else { '' }) $integrationRound.Since
        $stageEstado = (Get-Field $stageText 'Estado').Trim().ToLowerInvariant()
        $stageStateHtml = if ($stageLabel) {
            "<span class=""state $($stageLabel.Class)"">$(Encode $stageLabel.Text)</span>" + $(if ($stageEstado -and -not $stageIsComplete -and -not $claimsClosed) { "<span class=""meta seg-estado"">etapa $(Encode $stageEstado)</span>" })
        } else { "<span class=""state s-$($stageStateText -replace '[^\p{L}\p{Nd}]+', '-')"">$(Encode $stageStateText)</span>" }
        $stateOrder = @('en curso', 'disponible', 'liberada', 'pendiente', 'requiere cambios', 'reabierta', 'terminada', 'integrada')
        $countParts = @($taskRecords | Group-Object State | Sort-Object @{ Expression = { $i = $stateOrder.IndexOf($_.Name); if ($i -lt 0) { 99 } else { $i } } } | ForEach-Object { "$($_.Count) $(Encode $_.Name)" })
        $stageCountsText = if ($countParts.Count) { $countParts -join ' &middot; ' } else { 'sin tareas' }
        $stageWorkers = foreach ($r in @($taskRecords | Where-Object { $_.State -eq 'en curso' })) {
            # SEG-T13: the agent in charge of the task is named besides the worker.
            if (@($r.ActiveDelegations).Count) { foreach ($d in $r.ActiveDelegations) { "$(Encode $r.Code)$(if ($r.Agent.Trim() -and $r.Agent.Trim() -ne '-') { " · a cargo de $(Encode $r.Agent.Trim())" }) · $(Encode (Format-ActiveDelegation $d))" } }
            elseif ($r.Agent.Trim() -and $r.Agent.Trim() -ne '-') { "trabajando: $(Encode $r.Agent.Trim()) en $(Encode $r.Code)" }
        }
        $stageWorkersHtml = if (@($stageWorkers).Count) { "<span class=""meta seg-workers"">$(@($stageWorkers) -join ' &middot; ')</span>" } else { '' }
        $stageActivityHtml = if ($stageIsComplete -or $claimsClosed) { $closedLabel }
            elseif ($lastActivity) { "Última actividad $(Encode (Format-ActivityDate $lastActivity.Date))" }
            else { 'Última actividad: sin registro' }
        $stageId = 'seg:' + $stage.Name
        $boardHtml = @"
<details class="panel board seg-stage" data-seg-id="$(Encode $stageId)"$(if ($stageOpen) { ' open' })>
  <summary><h2>$(Encode $stageTitle)</h2>$stageStateHtml<span class="pct">$done/$total</span>
    <span class="meta seg-counts">$stageCountsText</span><span class="meta seg-activity">$stageActivityHtml</span>$stageWorkersHtml</summary>
  <div class="seg-body">
  <div class="seg-toolbar"><button type="button" class="seg-btn" data-seg-action="expand" data-seg-scope="stage">Expandir todo</button> <button type="button" class="seg-btn" data-seg-action="collapse" data-seg-scope="stage">Compactar todo</button></div>
  <div class="bar"><div class="fill" style="width:$pct%"></div></div>
  <p class="meta">Autorizada: $(Encode $authorized) &middot; terminadas o integradas: $done de $total</p>
  <p class="meta">Integración: un parche entregado queda pendiente hasta que la ficha registre integrada; sin parche no implica integración pendiente. El commit y la fecha se muestran solo si constan en la ficha y su historial.</p>
  $(if (@($taskRecords | Where-Object { $_.WaitingFor.Count }).Count) { "<p class=""meta"">Bloqueada por dependencia: la ficha dice disponible, pero una dependencia todavía no libera a sus dependientes (regla 27, modo &laquo;$(Encode $releaseMode)&raquo;). Es lo mismo que comprueba tomar-tarea.ps1.</p>" })
  <p class="meta">Cierre: $(if ($closure.Status -eq 'complete') { 'verificado' } elseif ($closure.Status -eq 'active') { 'etapa activa' } else { 'no verificado — ' + (Encode $closure.Reason) })</p>
  $taskGroups
  </div>
</details>
"@
        if ($closure.Status -eq 'complete') {
            $stageHref = ([uri]$stageFile).AbsoluteUri
            $reportHref = ([uri](Join-Path $integrationDir $closure.Report)).AbsoluteUri
            $stageAnchor = "history-$($stage.Name.ToLowerInvariant())"
            if (Test-CommitPublished $closure.Commit) {
                $archiveLinkEntries.Add([pscustomobject]@{ Date = $closeDate; Html = "<div class=""history-summary""><strong>$(Encode $stageTitle)</strong><span class=""state s-integrada"">Verificada · pública</span><div class=""meta"">$closedLabel &middot; Commit <code>$(Encode $closure.Commit)</code> &middot; <a href=""#$stageAnchor"">Historial</a> &middot; <a href=""$stageHref"">Ficha de etapa</a></div></div>" })
                $publicEntries.Add([pscustomobject]@{ Date = $closeDate; Html = @"
<section class="panel archive-stage" id="$stageAnchor">
  <div class="card-head"><h2>$(Encode $stageTitle)</h2><span class="state s-integrada">Verificada y publicada</span></div>
  <p class="meta">$closedLabel &middot; Commit de cierre <code>$(Encode $closure.Commit)</code>, contenido en <code>$(Encode $publicRef)</code> del repositorio público &middot; <a href="$stageHref">Ficha de etapa</a> &middot; <a href="$reportHref">Reporte de integración OK</a></p>
  $boardHtml
</section>
"@ })
            } else {
                $archiveLinkEntries.Add([pscustomobject]@{ Date = $closeDate; Html = "<div class=""history-summary""><strong>$(Encode $stageTitle)</strong><span class=""state s-integrada"">Cerrada · local</span><div class=""meta"">$closedLabel &middot; Commit <code>$(Encode $closure.Commit)</code> &middot; <a href=""#$stageAnchor"">Historial</a> &middot; <a href=""$stageHref"">Ficha de etapa</a></div></div>" })
                $archiveBoardEntries.Add([pscustomobject]@{ Date = $closeDate; Html = @"
<section class="panel archive-stage" id="$stageAnchor">
  <div class="card-head"><h2>$(Encode $stageTitle)</h2><span class="state s-integrada">Cierre verificado localmente · publicación no confirmada</span></div>
  <p class="meta">$closedLabel &middot; Commit de cierre: <code>$(Encode $closure.Commit)</code> &middot; <a href="$stageHref">Ficha de etapa</a> &middot; <a href="$reportHref">Reporte de integración OK</a></p>
  $boardHtml
</section>
"@ })
            }
        } elseif ($closure.Status -eq 'unknown') {
            if ($closure.ClaimsClosed) {
                $stageHref = ([uri]$stageFile).AbsoluteUri
                $reason = Encode $closure.Reason
                $uncertainEntries.Add([pscustomobject]@{ Date = $closeDate; Html = @"
<div class="history-summary"><strong>$(Encode $stageTitle)</strong><span class="state s-unknown">Cierre no verificado</span><div class="meta">$closedLabel &middot; $reason &middot; <a href="$stageHref">Ficha de etapa</a> &middot; <a href="#tareas">Ver tareas activas</a></div></div>
"@ })
            }
            $boards += $boardHtml
        } else {
            $boards += $boardHtml
        }
    }
}
if (-not $boards) {
    $boards = if ($boardReadFailed) { '<section class="panel">No se pudieron leer todas las etapas del tablero.</section>' }
        elseif (Test-Path -LiteralPath $tasksDir) { '<section class="panel">Sin etapas activas en el tablero.</section>' }
        else { '<section class="panel">El tablero de tareas no está disponible.</section>' }
}
# SEG-T04: closed stages, newest closure first; stages without a date go last.
$archiveBoards = (Sort-NewestFirst $archiveBoardEntries | ForEach-Object Html) -join ''
$uncertainBoards = (Sort-NewestFirst $uncertainEntries | ForEach-Object Html) -join ''
if (-not $archiveBoards) { $archiveBoards = '<section class="panel">No hay cierres verificados localmente.</section>' }
if (-not $publicEntries.Count) { $publicEntries.Add([pscustomobject]@{ Date = $null; Html = '<section class="panel">No hay otras etapas con publicación pública confirmada.</section>' }) }
if (-not $uncertainBoards) { $uncertainBoards = '<section class="panel">No hay cierres pendientes de verificación.</section>' }

# Resumen: one live card per stage that is not verified as closed (built with the board above).
$cards = @(Sort-NewestFirst $activeCards | ForEach-Object Html)
$reportedClosedNotVerified = [System.Collections.Generic.List[string]]::new()
foreach ($gate in $publicVerifiedEvidence.Keys) {
    $evidence = $publicVerifiedEvidence[$gate]
    $reportPath = Join-Path $integrationDir $evidence.Report
    $report = if (Test-Path -LiteralPath $reportPath -PathType Leaf) {
        "<a href=""$([uri]$reportPath)"">Reporte registrado: $(Encode $evidence.Report)</a>"
    } else {
        "Reporte registrado: $(Encode $evidence.Report) (no disponible localmente)"
    }
    $stageTitle = "$(Encode $gate) · $(Encode $evidence.Goal)"
    $legacyDate = Get-CommitDate $evidence.Commit
    $legacyLabel = Get-ClosureLabelHtml $legacyDate $false
    $publicEntries.Add([pscustomobject]@{ Date = $legacyDate; Html = @"
<section class="panel archive-stage">
  <div class="card-head"><h2>$stageTitle</h2><span class="state s-integrada">Verificada y publicada</span></div>
  <p class="meta">$legacyLabel &middot; Verificación y presencia en el repositorio público confirmadas por el usuario. Commit <code>$(Encode $evidence.Commit)</code> &middot; $report</p>
</section>
"@ })
    $archiveLinkEntries.Add([pscustomobject]@{ Date = $legacyDate; Html = "<div class=""history-summary""><strong>$stageTitle</strong><span class=""state s-integrada"">Verificada · pública</span><div class=""meta"">$legacyLabel &middot; Commit <code>$(Encode $evidence.Commit)</code> &middot; <a href=""#historico"">Historial</a></div></div>" })
}
$publicVerifiedBoards = @(Sort-NewestFirst $publicEntries | ForEach-Object Html)
$archiveLinks = @(Sort-NewestFirst $archiveLinkEntries | ForEach-Object Html)

# Resumen: integration state from the channel and the repository, never from a hand-kept file.
$pendingPatches = @(Get-ChildItem -LiteralPath $pendingPatchesDir -Filter '*.patch' -File -ErrorAction SilentlyContinue | Sort-Object Name | ForEach-Object Name)
# SEG-T14: the latest reading by report time (both name formats); the later row wins a tie.
$lastReport = $null
$lastReportKey = ''
foreach ($readLine in ($readsText -split "`r?`n")) {
    if ($readLine -notmatch '^\|\s*(\d{4}-\d{2}-\d{2}_\d{4}(?:\d{2}_\d{3})?_integracion\.md)\s*\|') { continue }
    $readKey = Get-ReportTimeKey $Matches[1]
    if ($readKey -and [string]::CompareOrdinal($readKey, $lastReportKey) -ge 0) { $lastReport = $readLine; $lastReportKey = $readKey }
}
$lastReportHtml = if ($lastReport) {
    $columns = @($lastReport.Trim().Trim('|').Split('|') | ForEach-Object { $_.Trim() })
    $resultClass = if ($columns[1] -eq 'OK') { 's-integrada' } else { 's-requiere-cambios' }
    $passed = if ($columns[2] -match '(\d+\s+passed[^=]*?)(?:\s+in\s|\s*=|$)') { $Matches[1].Trim() } else { $columns[2] -replace '=+', '' }
    "<span class=""state $resultClass"">$(Encode $columns[1])</span> $(Encode ($columns[0] -replace '_integracion\.md$', '')) &middot; $(Encode $passed)"
} else { '<span class="meta">Sin reportes registrados.</span>' }
# ASCII separator from git, joined here, so the console encoding cannot garble it.
$lastCommit = try { ((git -C $repoRoot log -1 --format='%h|%s' 2>$null) -replace '^([0-9a-f]+)\|', '$1 · ') } catch { '' }
$integrationPanel = @"
<section class="panel todo"><h2>Integración</h2>
  <p><strong>Parches por aplicar:</strong> $(if ($pendingPatches) { "$($pendingPatches.Count) &middot; " + (($pendingPatches | ForEach-Object { "<code>$(Encode $_)</code>" }) -join ', ') } else { 'ninguno' })</p>
  <p><strong>Última prueba de integración:</strong> $lastReportHtml</p>
  <p><strong>Último commit de integración:</strong> $(if ($lastCommit) { "<code>$(Encode $lastCommit)</code>" } else { '<span class="meta">no disponible</span>' })</p>
</section>
"@

# SEG-T07: "Qué tienes que hacer ahora" is generated from live sources; the hand-written section of
# RESUMEN.md is kept below it as "Notas".
$todoProposals = [System.Collections.Generic.List[object]]::new()
$proposalsFile = Join-Path $channel 'PROPUESTAS.md'
if (Test-Path -LiteralPath $proposalsFile -PathType Leaf) {
    try {
        $proposalsHref = ([uri]$proposalsFile).AbsoluteUri
        foreach ($line in @(Get-Content -LiteralPath $proposalsFile -Encoding utf8 -ErrorAction Stop)) {
            if ($line -notmatch '^\|\s*(P-\d+)\s*\|([^|]*)\|([^|]*)\|') { continue }
            $proposalId = $Matches[1]
            $proposalTopic = $Matches[2]
            $proposalState = ($Matches[3].Trim().ToLowerInvariant() -replace '\s+', '_') -replace 'revisión', 'revision'
            if ($proposalState -notin 'propuesta', 'en_revision') { continue }
            $todoProposals.Add([pscustomobject]@{ Number = [int]($proposalId -replace '\D', ''); Title = $proposalId; Text = "$(Limit-Text (Safe-Snippet $proposalTopic) 120) · $proposalState"; Href = $proposalsHref; Date = $null })
        }
    } catch { Write-Warning 'No se pudo leer PROPUESTAS.md; se omiten las propuestas pendientes.' }
}
$todoPatches = @(Get-ChildItem -LiteralPath $pendingPatchesDir -Filter '*.patch' -File -ErrorAction SilentlyContinue | ForEach-Object {
    [pscustomobject]@{ Title = $_.Name; Text = ''; Href = ([uri]$_.FullName).AbsoluteUri; Date = $_.LastWriteTime }
})
$todoGroups = @(
    Format-TodoGroup 'Tareas esperando tu asignación' $todoReady.ToArray()
    Format-TodoGroup 'Pendiente de tu autorización' $todoAuth.ToArray()
    Format-TodoGroup 'Propuestas esperando tu decisión' @($todoProposals | Sort-Object Number -Descending)
    Format-TodoGroup 'Parches por integrar' $todoPatches
    Format-TodoGroup 'Decisiones que te piden' $todoDecisions.ToArray()
) | Where-Object { $_ }
$todoNotes = ''
if (Test-Path $summary) {
    $summaryText = Get-Content -Path $summary -Raw -Encoding utf8
    if ($summaryText -match '(?s)## Qu. tienes que hacer ahora\s*(.+?)\s*## Bit') {
        $todoMarkdown = ($Matches[1] -split "`r?`n" | Where-Object { $_ -notmatch '^_Esta secci' }) -join "`n"
        if ($todoMarkdown.Trim()) { $todoNotes = '<h3>Notas</h3>' + (ConvertFrom-Markdown -InputObject $todoMarkdown).Html }
    }
}
$todoBody = if (@($todoGroups).Count) { @($todoGroups) -join "`n" } else { '<p>Nada pendiente de tu parte.</p>' }
$todo = $todoBody + $todoNotes

# "Plan de etapas": the Spanish summary in ROADMAP_VISTA.md ("## Etapas y features").
# "Roadmap": generated from the repository itself (PROGRESS.md and docs/ROADMAP_STAGES.md), so it
# never goes stale; the Mermaid block is left out because the page does not render diagrams.
$features = ''; $fullRoadmap = ''
if (Test-Path $roadmap) {
    $roadmapText = Get-Content -Path $roadmap -Raw -Encoding utf8
    if ($roadmapText -match '(?sm)## Etapas y features\s*(.+?)\s*(?=^## |\z)') {
        $features = (ConvertFrom-Markdown -InputObject $Matches[1]).Html
    }
}
$roadmapParts = @()
if (Test-Path -LiteralPath $repoProgress -PathType Leaf) {
    $roadmapParts += (Get-Content -LiteralPath $repoProgress -Raw -Encoding utf8)
}
if (Test-Path -LiteralPath $repoStagePlan -PathType Leaf) {
    $planText = Get-Content -LiteralPath $repoStagePlan -Raw -Encoding utf8
    $roadmapParts += [regex]::Replace($planText, '(?s)```mermaid.*?```\s*', '')
}
$fullRoadmap = if ($roadmapParts) {
    '<p class="meta">Generado desde <code>PROGRESS.md</code> y <code>docs/ROADMAP_STAGES.md</code> del repositorio de integración (en inglés, como el repositorio público).</p>' +
        (ConvertFrom-Markdown -InputObject ($roadmapParts -join "`n`n---`n`n")).Html
} else { '<section class="panel">No se encontraron PROGRESS.md ni docs/ROADMAP_STAGES.md en el repositorio.</section>' }

$cloudAssignments = @()
$cloudAssignmentsError = ''
if ($cloudAssignmentsModuleAvailable) {
    try {
        $cloudAssignments = @(Get-CloudAssignments -StorePath $cloudAssignmentsPath)
    } catch {
        $cloudAssignmentsError = 'El registro no pudo leerse o contiene datos no validos; consulta la advertencia local.'
        Write-Warning "No se pudo leer el registro de asignaciones cloud. $($_.Exception.Message)"
    }
} else {
    $cloudAssignmentsError = 'El modulo de seguimiento cloud no esta disponible.'
}
$activeCloudAssignments = @($cloudAssignments | Where-Object {
    $_.state -notin @('completed', 'cancelled', 'preparation_failed')
})
$cloudAssignmentCards = foreach ($assignment in $activeCloudAssignments) {
    $stateClass = if ($assignment.state -in @('in_execution', 'integrating_result')) { 's-en-curso' }
        elseif ($assignment.state -eq 'human_review') { 's-pendiente' }
        else { 's-unknown' }
    $updatedAt = [DateTimeOffset]::Parse($assignment.updated_utc).ToLocalTime().ToString('yyyy-MM-dd HH:mm')
    $packageHash = if ($assignment.package_sha256) { " · SHA-256 <code>$(Encode $assignment.package_sha256)</code>" } else { '' }
    $fileRows = @($assignment.files | Sort-Object path | ForEach-Object { "<li><code>$(Encode $_.path)</code></li>" }) -join "`n"
    @"
<section class="panel cloud-assignment">
  <div class="card-head"><strong>$(Encode $assignment.task_id) · $(Encode $assignment.title)</strong><span class="state $stateClass">$(Encode (Get-CloudAssignmentStateLabel $assignment.state))</span></div>
  <p>$(Encode $assignment.goal)</p>
  <p class="meta">Origen: $(Encode $assignment.origin_agent) · destino declarado: $(Encode $assignment.destination_agent) · seguimiento: $(Encode $assignment.follow_up_agent)</p>
  <p class="meta">Paquete: <code>$(Encode $assignment.package_name)</code>$packageHash · última actualización registrada: $(Encode $updatedAt)</p>
  <p class="meta">Contenido registrado: $(Encode $assignment.context_summary)</p>
  <details><summary>Archivos incluidos ($( $assignment.files.Count ))</summary><ul>$fileRows</ul></details>
</section>
"@
}
$cloudAssignmentPanel = if ($cloudAssignmentsError) {
    "<section class=""stop""><strong>Seguimiento cloud no disponible</strong>$(Encode $cloudAssignmentsError). No se infiere que no existan asignaciones.</section>"
} elseif ($cloudAssignmentCards) {
    $cloudAssignmentCards -join "`n"
} else {
    '<section class="panel">No hay asignaciones cloud activas registradas.</section>'
}
$registeredAgents = @(Get-RegisteredAgents)
$agentCards = foreach ($agentRow in @($registeredAgents | Where-Object State -eq 'ACTIVO')) {
    $name = $agentRow.Name
    $agentWaits = @()
    $waiting = $false
    if (-not $agentBoard.Available) {
        $status = 'Estado no disponible'
        $statusClass = 's-unknown'
        $current = '<p class="meta">No se pudo consultar el tablero de tareas.</p>'
    } else {
        $active = @($agentBoard.Tasks | Where-Object { $_.Agent -ieq $name -and $_.State -in @('en curso', 'pendiente') })
        # SEG-T19: the live watchers that reactivate this agent, those of its tasks and the «esperando: ...» of its pendiente cards. It reads
        # «En espera de activación» unless it is working on a task en curso.
        $agentWaits = @(Get-AgentWaits $waitBoard $name $active)
        $waiting = $agentWaits.Count -gt 0 -and -not @($active | Where-Object State -eq 'en curso').Count
        if ($active) {
            $status = if (@($active | Where-Object State -eq 'en curso').Count) { 'Asignada · En curso' } else { 'Asignada · En espera' }
            $statusClass = if ($status -eq 'Asignada · En curso') { 's-en-curso' } else { 's-pendiente' }
            $current = foreach ($task in $active) {
                $taskState = if ($task.State -eq 'en curso') { 'En curso' } else { 'Pendiente' }
                $context = if ($task.Missing) { "<p class=""agent-context"">Qué falta: $(Encode $task.Missing)</p>" } else { '' }
                $delegated = (@($task.ActiveDelegations) | ForEach-Object { "<p class=""agent-context"">$(Encode (Format-ActiveDelegation $_))</p>" }) -join ''
                $waitChip = if (@(Get-TaskWaits $waitBoard $task.Id $task.State $task.Agent $task.Missing).Count) { Format-WaitChip } else { '' }
                "<div class=""agent-task""><strong>$(Encode $task.Id) · $(Encode $task.Title)</strong><span class=""state $(if ($task.State -eq 'en curso') { 's-en-curso' } else { 's-pendiente' })"">Asignada · $(Encode $taskState)</span>$waitChip$context$delegated</div>"
            }
            $current = $current -join "`n"
        } elseif ($agentBoard.Partial) {
            $status = 'Estado parcial'
            $statusClass = 's-unknown'
            $current = '<p class="meta">No hay una asignación activa legible; hay fichas omitidas.</p>'
        } else {
            $status = 'Idle · sin tarea activa'
            $statusClass = 's-idle'
            $current = '<p class="meta">No tiene una tarea asignada en curso o en espera según el tablero.</p>'
        }
    }
    # SEG-T19: the agent that only waits is not «sin actividad»: label and detail of every wait that will reactivate it.
    if ($waiting) { $status = $script:WaitLabel; $statusClass = 's-espera' }
    $waitsHtml = if ($agentWaits.Count) {
        "<div class=""agent-current agent-waiting""><h4>$(Encode $script:WaitLabel)</h4>$((@($agentWaits) | ForEach-Object { Format-WaitDetailHtml $_ }) -join '')</div>"
    } else { '' }
    $events = @($agentBoard.History | Where-Object Agent -ieq $name)
    $lastActivity = ''
    if ($events) {
        $latestDate = ($events | ForEach-Object Date | Sort-Object -Descending | Select-Object -First 1)
        $latestDay = @($events | Where-Object Date -eq $latestDate)
        $precise = @($latestDay | Where-Object HasTime)
        if ($precise) {
            $latestPrecise = $precise | Sort-Object Recorded -Descending | Select-Object -First 1
            $latestTicks = $latestPrecise.Recorded.Ticks
            $lastEvents = @($latestDay | Where-Object { $_.HasTime -and $_.Recorded.Ticks -eq $latestTicks })
            $untimed = @($latestDay | Where-Object { -not $_.HasTime } | Group-Object TaskId | ForEach-Object {
                $_.Group | Sort-Object Sequence -Descending | Select-Object -First 1
            })
            $lastEvents += $untimed
        } else {
            $lastEvents = @($latestDay | Group-Object TaskId | ForEach-Object {
                $_.Group | Sort-Object Sequence -Descending | Select-Object -First 1
            })
        }
        $hasUntimed = @($lastEvents | Where-Object { -not $_.HasTime }).Count -gt 0
        $timeNote = if ($hasUntimed -and $precise) {
            ' · ' + $latestPrecise.Recorded.ToString('HH:mm') + ' (hay acciones del día sin hora)'
        } elseif ($hasUntimed) {
            ' (sin hora; el orden intradía no consta)'
        } else {
            ' · ' + $lastEvents[0].Recorded.ToString('HH:mm')
        }
        $activityItems = foreach ($event in ($lastEvents | Select-Object -First 5)) {
            $detail = if ($event.Detail) { ' — ' + (Encode $event.Detail) } else { '' }
            "<li><strong>$(Encode $event.TaskId) · $(Encode $event.Title)</strong>: $(Encode $event.Action)$detail</li>"
        }
        $extra = if ($lastEvents.Count -gt 5) { "<li>y $($lastEvents.Count - 5) acciones más registradas ese día</li>" } else { '' }
        $activityLabel = if ($hasUntimed) { 'Actividad del día más reciente registrada' } else { 'Última actividad del historial' }
        $lastActivity = "<div class=""agent-last""><strong>${activityLabel}: $(Encode $latestDate)$timeNote</strong><ul>$($activityItems -join "`n")$extra</ul></div>"
    } else {
        $lastActivity = '<div class="agent-last"><strong>Última tarea/acción</strong><p class="meta">Sin actividad legible registrada en los historiales.</p></div>'
    }
    $cloudFollowups = @($activeCloudAssignments | Where-Object follow_up_agent -ieq $name)
    $cloudContext = if ($cloudAssignmentsError) {
        '<p class="meta">No se pudo consultar el registro cloud; no se infiere ausencia de asignaciones externas.</p>'
    } elseif ($cloudFollowups.Count) {
        $cloudRows = foreach ($assignment in $cloudFollowups) {
            "<div class=""agent-task""><strong>$(Encode $assignment.task_id) · $(Encode $assignment.title)</strong><span class=""state s-unknown"">Seguimiento cloud asignado · $(Encode (Get-CloudAssignmentStateLabel $assignment.state))</span><p class=""meta"">Destino declarado: $(Encode $assignment.destination_agent). Estado registrado por operador; no es presencia en vivo.</p></div>"
        }
        $cloudRows -join "`n"
    } else {
        '<p class="meta">Sin asignaciones cloud activas registradas para seguimiento.</p>'
    }
    # Tokens this agent used on board tasks (field "Tokens" of the cards; measured or reported).
    $tokenEntries = @($agentBoard.Tasks | ForEach-Object { Get-TokenEntries $_.Tokens } | Where-Object { $_.Kind -ne 'total' -and $_.Agent -ieq $name })
    $counted = @($tokenEntries | Where-Object { $null -ne $_.Total })
    $tokensHtml = if ($counted.Count) {
        $sum = ($counted | Measure-Object -Property Total -Sum).Sum
        $out = ($counted | Measure-Object -Property Output -Sum).Sum
        $missing = $tokenEntries.Count - $counted.Count
        $note = if ($missing) { " · $missing sin dato" } else { '' }
        "<p><strong>$(Format-TokenCount $sum)</strong> en $($counted.Count) tarea(s) · salida $(Format-TokenCount $out)$note</p><p class=""meta"">Incluye el contexto cacheado que se relee en cada llamada; la salida es lo que el agente generó.</p>"
    } elseif ($tokenEntries.Count) {
        '<p class="meta">Sin datos de uso disponibles para este agente.</p>'
    } else {
        '<p class="meta">Ninguna tarea tiene tokens registrados todavía.</p>'
    }
    $identity = @(
        if ($agentRow.Model) { "Modelo: $(Encode $agentRow.Model)" }
        if ($agentRow.Session) { "Última sesión: $(Encode $agentRow.Session)" }
    ) -join ' · '
    $identityHtml = if ($identity) { "<p class=""meta"">$identity</p>" } else { '' }
    $card = @"
<div class="agent-name">
  <div class="card-head"><h3>$(Encode $name)</h3><span class="state $statusClass">$(Encode $status)</span></div>
  $identityHtml
  <div class="agent-current"><h4>Tarea actual según el tablero</h4>$current</div>
  $waitsHtml
  <div class="agent-current"><h4>Tokens en tareas</h4>$tokensHtml</div>
  <div class="agent-current"><h4>Seguimiento de asignaciones cloud</h4>$cloudContext</div>
  $(Get-AlignmentHtml $name $agentRow.Application)
  $lastActivity
</div>
"@
    $isActive = -not $agentBoard.Available -or [bool]$active
    [pscustomobject]@{ Name = $name; Application = $agentRow.Application; Active = $isActive; Waiting = $waiting; Html = $card }
}
# SEG-T17 (REGLAS 33): delegated workers of another application than their orchestrator's, grouped under their own application.
$foreignWorkers = if ($agentBoard.Available) { @(Get-ForeignWorkers $agentBoard $registeredAgents) } else { @() }
# SEG-T18 (REGLAS 33): the active workers of the activities outside tasks are also listed in the card of their own application.
$activityBoard = Get-ActivityBoard
$activityWorkers = @(Get-ActivityWorkers $activityBoard)
$applications = [System.Collections.Generic.List[string]]::new()
foreach ($application in @($agentCards | ForEach-Object Application) + @($foreignWorkers | ForEach-Object Application) + @($activityWorkers | ForEach-Object Application)) {
    if ($application -and $application -inotin $applications) { $applications.Add($application) }
}
# SEG-T05: one card per application; only names with an active task are listed, the rest in one line.
# SEG-T17: also the active workers of that application, so an application with active workers never says "sin actividad".
$agentCards = foreach ($application in @($applications)) {
    $members = @($agentCards | Where-Object { $_.Application -ieq $application })
    # SEG-T19: an agent that only waits for a watcher is «en espera de activación», not active and not idle.
    $waitingMembers = @($members | Where-Object Waiting)
    $activeMembers = @($members | Where-Object { $_.Active -and -not $_.Waiting })
    $shownMembers = @($members | Where-Object { $_.Active -or $_.Waiting })
    $idleMembers = @($members | Where-Object { -not $_.Active -and -not $_.Waiting })
    $appWorkers = @($foreignWorkers | Where-Object { $_.Application -ieq $application } | Sort-Object @{ Expression = { $_.Task.Id } }, @{ Expression = { $_.Entry.Since } })
    $appActivityWorkers = @($activityWorkers | Where-Object { $_.Application -ieq $application } | Sort-Object @{ Expression = { $_.Activity.Id } }, @{ Expression = { $_.Entry.Since } })
    $liveWorkers = @(@($appWorkers) + @($appActivityWorkers) | Where-Object { -not $_.Stopped })
    $stoppedWorkers = @(@($appWorkers) + @($appActivityWorkers) | Where-Object { $_.Stopped })
    $statusParts = @()
    if ($activeMembers.Count) { $statusParts += "$($activeMembers.Count) con tarea activa" }
    if ($waitingMembers.Count) { $statusParts += "$($waitingMembers.Count) en espera de activación" }
    if ($liveWorkers.Count) { $statusParts += "$($liveWorkers.Count) $(if ($liveWorkers.Count -eq 1) { 'trabajador activo' } else { 'trabajadores activos' })" }
    if (-not $statusParts) { $statusParts += 'sin actividad' }
    if ($stoppedWorkers.Count) { $statusParts += "$($stoppedWorkers.Count) $(if ($stoppedWorkers.Count -eq 1) { 'trabajador probablemente detenido' } else { 'trabajadores probablemente detenidos' })" }
    $appStatus = $statusParts -join ' · '
    $appClass = if ($activeMembers.Count -or $liveWorkers.Count) { 's-en-curso' } elseif ($waitingMembers.Count) { 's-espera' } else { 's-idle' }
    $workersHtml = ''
    if ($appWorkers.Count) {
        $workersHtml = "<div class=""agent-name agent-workers""><h3>Trabajadores de otras aplicaciones</h3><p class=""meta"">Delegados por un agente de otra aplicación; también figuran como delegación en la tarjeta de quien los orquesta.</p>$(($appWorkers | ForEach-Object { Format-ForeignWorkerHtml $_ }) -join "`n")</div>"
    }
    if ($appActivityWorkers.Count) {
        $workersHtml += "<div class=""agent-name agent-workers agent-activity-workers""><h3>Trabajadores en actividades fuera de tareas</h3><p class=""meta"">Delegados en una actividad que no cuelga de una tarea; también figuran en la sección «Fuera de tareas».</p>$(($appActivityWorkers | ForEach-Object { Format-ActivityWorkerHtml $_ }) -join "`n")</div>"
    }
    $idleHtml = ''
    if ($idleMembers.Count) {
        $idleHtml = "<p class=""meta agent-idle"">sin actividad: $(Encode (($idleMembers | ForEach-Object Name) -join ', '))</p>" +
            (($idleMembers | ForEach-Object { "<details class=""agent-instance""><summary>Detalle de $(Encode $_.Name)</summary>$($_.Html)</details>" }) -join "`n")
    }
    @"
<section class="panel agent-card">
  <div class="card-head"><h2>$(Encode $application)</h2><span class="state $appClass">$(Encode $appStatus)</span></div>
  $(($shownMembers | ForEach-Object Html) -join "`n")
  $workersHtml
  $idleHtml
</section>
"@
}
# Retired names (PAUSADO, e.g. the generic Claude replaced by Claude-<Model>) stay folded, only if the board mentions them.
$retired = @($registeredAgents | Where-Object { $_.State -eq 'PAUSADO' -and @($agentBoard.History | Where-Object Agent -ieq $_.Name).Count })
if ($retired) {
    $retiredItems = foreach ($r in $retired) {
        $count = @($agentBoard.History | Where-Object Agent -ieq $r.Name).Count
        "<li><strong>$(Encode $r.Name)</strong> · $count acción(es) en el historial del tablero</li>"
    }
    $agentCards = @($agentCards) + "<details class=""panel""><summary>Nombres anteriores (PAUSADO)</summary><ul>$($retiredItems -join "`n")</ul><p class=""meta"">Sus entregas no se renombran; ya no leen ni registran en el canal.</p></details>"
}
$agentNotice = if (-not $agentBoard.Available) {
    'La fuente del tablero no está disponible; no se infiere que los agentes estén inactivos.'
} elseif ($agentBoard.Partial) {
    "Vista parcial: se omitieron $($agentBoard.Issues) fichas o filas de historial con formato no válido o no accesibles."
} else { '' }
$agentsView = @"
<p class="meta">Fuente: tablero e historiales de tareas locales; las asignaciones cloud aparecen en una sección separada y son registros manuales. Esto no informa presencia de procesos ni actividad en vivo.</p>
$(if ($agentNotice) { "<div class=""stop"">$(Encode $agentNotice)</div>" })
$(Get-ActivitiesHtml $activityBoard)
<div class="agent-grid">$($agentCards -join "`n")</div>
<section class="panel cloud-assignment-group"><h2>Asignaciones externas registradas</h2><p class="meta">Los estados cloud proceden de acciones de preparación/importación o declaraciones manuales del operador; no son telemetría del proveedor.</p>$($cloudAssignmentPanel)</section>
"@
$ideasView = Get-IdeasHtml
$cloudButton = if ($flowListener -and $cloudToken) {
    '<button id="prepare-cloud" class="action-button" type="button">Preparar paquete para agente cloud</button>'
} else {
    '<button id="prepare-cloud" class="action-button" type="button" disabled title="Abre el visualizador con Seguimiento.cmd">Preparar paquete para agente cloud</button>'
}
$cloudTrackingButton = if ($flowListener -and $cloudTrackingToken) {
    '<button id="manage-cloud-assignments" class="action-button" type="button">Actualizar seguimiento de asignaciones</button>'
} else {
    '<button id="manage-cloud-assignments" class="action-button" type="button" disabled title="Abre el visualizador con Seguimiento.cmd">Actualizar seguimiento de asignaciones</button>'
}

$html = @"
<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Seguimiento del proyecto</title>
<style>
:root { --bg:#f6f7f9; --panel:#ffffff; --text:#1d2330; --muted:#5d6675; --line:#dfe3ea;
        --accent:#2f6fdb; --accent-soft:#e7effc; --ok:#1f9d61; --warn-bg:#fff6e0; --warn-line:#f0c24b; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#14171c; --panel:#1c2027; --text:#e6e9ef; --muted:#9aa3b2; --line:#2e3440;
          --accent:#6ea0ff; --accent-soft:#223149; --ok:#3ecf8e; --warn-bg:#2d2615; --warn-line:#b8912d; }
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text);
       font:15px/1.55 "Segoe UI", system-ui, -apple-system, sans-serif; }
header { padding:24px 16px 8px; max-width:1100px; margin:0 auto; }
header h1 { margin:0; font-size:22px; }
header p { margin:4px 0 0; color:var(--muted); font-size:13px; }
main { max-width:1100px; margin:0 auto; padding:8px 16px 48px; }
.cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(260px, 1fr)); gap:12px; margin:12px 0 16px; }
.card, .panel { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
.card-head { display:flex; justify-content:space-between; align-items:baseline; }
.gate { font-weight:700; font-size:18px; }
.pct { font-weight:700; font-size:22px; color:var(--accent); }
.bar { height:10px; background:var(--accent-soft); border-radius:6px; overflow:hidden; margin:8px 0; }
.fill { height:100%; background:var(--accent); border-radius:6px; }
.card .meta { color:var(--muted); font-size:13px; }
.card .goal { font-size:13px; margin-top:4px; }
.stop { background:var(--warn-bg); border:1px solid var(--warn-line); border-radius:10px; padding:12px 16px; margin-bottom:16px; }
.stop strong { display:block; margin-bottom:4px; }
.todo { margin-bottom:16px; }
.todo h2 { margin-top:0; }
.doc h1 { display:none; }
.doc h2 { margin-top:28px; padding-bottom:4px; border-bottom:1px solid var(--line); font-size:19px; }
.doc h3 { font-size:16px; margin-top:20px; }
table { border-collapse:collapse; width:100%; display:block; overflow-x:auto; margin:8px 0 12px; font-size:14px; }
th, td { border:1px solid var(--line); padding:6px 10px; text-align:left; vertical-align:top; }
th { background:var(--accent-soft); }
code { background:var(--accent-soft); padding:1px 5px; border-radius:4px; font-size:13px; word-break:break-word; }
blockquote { margin:8px 0; padding:4px 12px; border-left:3px solid var(--accent); color:var(--muted); }
li input[type=checkbox] { margin-right:6px; }
a { color:var(--accent); }
nav { max-width:1100px; margin:0 auto; padding:4px 16px 0; display:flex; gap:6px; flex-wrap:wrap; }
nav button { font:inherit; font-size:14px; padding:7px 14px; border:1px solid var(--line); border-radius:8px;
             background:var(--panel); color:var(--text); cursor:pointer; }
nav button.active { background:var(--accent); border-color:var(--accent); color:#fff; }
.action-button { font:inherit; font-size:14px; padding:8px 14px; border:1px solid var(--accent); border-radius:8px;
                 background:var(--accent); color:#fff; cursor:pointer; }
.action-button:disabled { opacity:.6; cursor:not-allowed; }
.tab { display:none; }
.tab.active { display:block; }
.features h3 { margin:22px 0 4px; font-size:17px; border-left:4px solid var(--accent); padding-left:8px; }
.features h3:first-child { margin-top:0; }
.board { margin-bottom:16px; }
.board h2 { margin:0; font-size:18px; }
.board .meta { color:var(--muted); font-size:13px; margin:0 0 8px; }
.board .left { color:var(--muted); font-size:12px; margin-top:4px; }
.board-phase { border-top:1px solid var(--line); margin-top:14px; padding-top:10px; }
.board-phase:first-of-type { border-top:0; margin-top:0; padding-top:0; }
.board-phase h3 { margin:0; font-size:16px; }
.board-phase .card-head { gap:12px; flex-wrap:wrap; }
summary { cursor:pointer; }
.seg-stage > summary, .seg-phase > summary { display:flex; flex-wrap:wrap; align-items:baseline; gap:4px 12px; list-style:none; }
.seg-stage > summary::-webkit-details-marker, .seg-phase > summary::-webkit-details-marker { display:none; }
.seg-stage > summary::before, .seg-phase > summary::before { content:'\25B8'; color:var(--accent); }
.seg-stage[open] > summary::before, .seg-phase[open] > summary::before { content:'\25BE'; }
.seg-stage > summary .seg-counts, .seg-stage > summary .seg-activity, .seg-stage > summary .seg-workers { flex-basis:100%; margin:0; }
.seg-phase > summary h3 { display:inline; }
.seg-body { margin-top:8px; }
.seg-toolbar { margin:0 0 8px; }
.seg-btn { font:inherit; font-size:13px; cursor:pointer; padding:3px 10px; border:1px solid var(--line); border-radius:6px; background:transparent; color:var(--accent); }
.seg-integration-notice { max-width:1100px; margin:8px auto; padding:8px 14px; background:var(--warn-bg); border:1px solid var(--warn-line); border-radius:10px; font-size:13px; }
.seg-stage > summary .seg-estado { margin:0; }
.archive-stage { margin-bottom:14px; scroll-margin-top:12px; }
.archive-stage h2 { margin:0; font-size:18px; }
.archive-stage details { margin-top:8px; }
.archive-stage summary { color:var(--accent); cursor:pointer; }
.history-summary { display:grid; grid-template-columns:minmax(160px,1fr) auto; gap:4px 12px; padding:8px 0; border-bottom:1px solid var(--line); }
.history-summary .meta { grid-column:1/-1; color:var(--muted); font-size:13px; }
.history-summary:last-child { border-bottom:0; }
.history-warning { background:var(--warn-bg); border:1px solid var(--warn-line); border-radius:10px; padding:12px 16px; margin-bottom:14px; }
.history-warning h2 { font-size:16px; margin:0 0 6px; }
.state { display:inline-block; padding:1px 8px; border-radius:10px; font-size:12px; white-space:nowrap;
         background:var(--accent-soft); }
.s-en-curso { background:#2f6fdb; color:#fff; }
.s-pendiente, .s-liberada { background:var(--warn-bg); border:1px solid var(--warn-line); }
.s-reabierta { background:#d64545; color:#fff; }
.s-requiere-cambios { background:#f0a830; color:#2b1d00; }
.s-terminada { background:#bfe8d3; color:#10442b; }
.s-integrada { background:var(--ok); color:#fff; }
.s-disponible { background:var(--accent-soft); border:1px solid var(--accent); }
.s-idle { background:var(--accent-soft); color:var(--muted); }
.s-unknown { background:var(--warn-bg); border:1px solid var(--warn-line); }
.s-bloqueada { background:var(--accent-soft); color:var(--muted); border:1px dashed var(--warn-line); }
.s-espera { background:var(--accent-soft); border:1px dashed var(--accent); }
.wait-detail { margin:2px 0 6px; }
.wait-detail .meta { color:var(--muted); font-size:13px; margin:2px 0; }
.wait-conditions { margin:4px 0; padding-left:18px; font-size:13px; color:var(--muted); }
.wait-group { margin-top:14px; border-top:1px solid var(--line); padding-top:6px; }
.wait-group h3 { margin:6px 0; font-size:16px; }
.wait-finished { margin-top:10px; }
.wait-finished ul { margin:6px 0 0; padding-left:20px; font-size:13px; }
.wait-finished li { margin:3px 0; }
.agent-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:12px; }
.agent-card h2 { margin:0; font-size:18px; }
.agent-card h3 { margin:14px 0 6px; font-size:16px; }
.agent-card h4 { margin:10px 0 4px; font-size:14px; }
.agent-name { border-top:1px solid var(--line); margin-top:12px; padding-top:4px; }
.agent-current .meta, .agent-last .meta { color:var(--muted); font-size:13px; margin:4px 0; }
.agent-task { border-left:3px solid var(--accent); padding:4px 0 6px 10px; margin:6px 0; }
.agent-task .state { margin-left:6px; }
.agent-context { color:var(--muted); font-size:13px; margin:4px 0 0; }
.agent-last { border-top:1px solid var(--line); margin-top:12px; padding-top:10px; font-size:13px; }
.agent-last ul { margin:4px 0 0; padding-left:20px; }
.idea-group { margin:16px 0; }
.idea-group h2 { font-size:18px; border-bottom:1px solid var(--line); padding-bottom:5px; }
.idea-card { margin:8px 0; }
.idea-title { display:flex; justify-content:space-between; align-items:flex-start; gap:12px; }
.idea-card h3 { margin:0 0 8px; font-size:16px; }
.idea-review-note { margin:8px 0 0; }
.idea-card .meta { margin:6px 0 0; }
.cloud-panel h2 { margin-top:0; }
.seg-worker > summary { cursor:pointer; }
.seg-tag { margin-left:4px; font-size:11px; }
.seg-worker-items { margin:4px 0; padding-left:18px; }
.seg-worker-items li { margin:3px 0; }
.act-worker { margin:4px 0 4px 6px; }
.act-finished { margin-top:10px; }
.act-finished ul { margin:6px 0 0; padding-left:20px; font-size:13px; }
.act-finished li { margin:3px 0; }
</style>
</head>
<body>
<header>
  <h1>Seguimiento del proyecto &mdash; Multi-Agent Framework</h1>
  <p>Generado desde el tablero de tareas, el canal y el repositorio de integración a las $(Get-Date -Format 'HH:mm:ss'). Se recarga sola cada 30 s mientras la ventana de Seguimiento.cmd siga abierta.</p>
</header>
$integrationNoticeHtml
<nav>
  <button data-tab="resumen">Resumen</button>
  <button data-tab="tareas">Tareas</button>
  <button data-tab="agentes">Agentes</button>
  <button data-tab="ideas">Ideas</button>
  <button data-tab="cloud">Agente cloud</button>
  <button data-tab="historico">Etapas completadas</button>
  <button data-tab="etapas">Plan de etapas</button>
  <button data-tab="roadmap">Roadmap</button>
  $(if ($LeerApi) { '<button data-tab="api">API core</button>' })
  $(if ($flowListener) { "<button id=""run-flow"" type=""button"" aria-label=""Abrir el ciclo global de integración"">Abrir ciclo de integración</button>" } else { '<button id="run-flow" type="button" disabled title="El puente local no está activo; inicia el visor con Seguimiento.cmd">Abrir ciclo de integración</button>' })
</nav>
<p id="flow-status" class="meta" role="status" aria-live="polite" style="max-width:1100px;margin:6px auto;padding:0 16px"></p>
<main>
  <div class="tab" id="resumen">
    <section class="panel todo"><h2>Qué tienes que hacer ahora</h2>$todo</section>
    <h2>Etapas activas</h2>
    $(if ($cards.Count) { "<section class=""cards"">$($cards -join "`n")</section>" } else { '<section class="panel">No hay etapas activas en el tablero.</section>' })
    $integrationPanel
    $(if ($archiveLinks.Count) { "<section class=""panel todo""><h2>Etapas completadas</h2>$($archiveLinks -join "`n")</section>" })
  </div>
  <div class="tab" id="tareas"><div class="seg-toolbar"><button type="button" class="seg-btn" data-seg-action="expand" data-seg-scope="tab">Expandir todo</button> <button type="button" class="seg-btn" data-seg-action="collapse" data-seg-scope="tab">Compactar todo</button></div>$($boards -join "`n")</div>
  <div class="tab" id="agentes">$agentsView</div>
  <div class="tab" id="ideas">$ideasView</div>
  <div class="tab" id="cloud">
    <section class="panel cloud-panel">
      <h2>Preparación para agente cloud</h2>
      <p>Esta herramienta es independiente de la biblioteca de Ideas. Prepara localmente un paquete para subirlo manualmente a un agente cloud; este botón no envía archivos ni inicia una tarea.</p>
      $cloudButton
      <p id="cloud-status" class="meta" role="status" aria-live="polite"></p>
    </section>
    <section class="panel cloud-panel">
      <h2>Registro y seguimiento de asignaciones</h2>
      <p>La preparación crea un registro con la tarea, el operador, el destino declarado y los archivos relativos incluidos. El envío es manual; los estados posteriores se actualizan aquí como declaraciones del operador, sin confirmar actividad del proveedor.</p>
      $cloudTrackingButton
      <p id="cloud-tracking-status" class="meta" role="status" aria-live="polite"></p>
    </section>
    $cloudAssignmentPanel
  </div>
  <div class="tab" id="historico">
    <div class="seg-toolbar"><button type="button" class="seg-btn" data-seg-action="expand" data-seg-scope="tab">Expandir todo</button> <button type="button" class="seg-btn" data-seg-action="collapse" data-seg-scope="tab">Compactar todo</button></div>
    <p class="meta">Archivo de visualización de solo lectura. Los cierres se derivan de ETAPA.md, fichas de tareas, commits Git y reportes. Una etapa es pública cuando su commit de cierre está en $(if ($publicRepo) { "<code>$(Encode $publicRef)</code> del repositorio público (según el último push o fetch)" } else { 'la rama pública, pero falta <code>repositorio-publico.txt</code> en el canal' }). No mueve ni modifica las fuentes.</p>
    <section class="panel todo"><h2>Verificadas y presentes en el repositorio público</h2>$($publicVerifiedBoards -join "`n")</section>
    <section class="panel todo"><h2>Cerradas e integradas localmente; publicación no confirmada</h2>$(if ($archiveBoards) { $archiveBoards } else { '<section class="panel">No hay cierres verificados localmente.</section>' })</section>
    <section class="todo"><h2>Cierres pendientes de verificación</h2>$uncertainBoards</section>
    $(if ($reportedClosedNotVerified.Count) { "<section class=""history-warning""><h2>Etapas marcadas cerradas en el seguimiento, sin evidencia canónica completa</h2>$($reportedClosedNotVerified -join "`n")</section>" })
  </div>
  <div class="tab" id="etapas"><section class="panel doc features">$features</section></div>
  <div class="tab" id="roadmap"><section class="panel doc">$fullRoadmap</section></div>
  $(if ($LeerApi) { "<div class=""tab"" id=""api"">$apiBoardHtml</div>" })
</main>
<script>
  // The selected tab lives in the URL hash so the periodic reload keeps it.
  function show(name) {
    if (!document.getElementById(name)) name = 'resumen';
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.id === name));
    document.querySelectorAll('nav button').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
    history.replaceState(null, '', '#' + name);
  }
  document.querySelectorAll('nav button[data-tab]').forEach(b => b.addEventListener('click', () => show(b.dataset.tab)));
  const flowButton = document.getElementById('run-flow');
  if (flowButton && !flowButton.disabled) {
    flowButton.addEventListener('click', async () => {
      flowButton.disabled = true;
      const status = document.getElementById('flow-status');
      status.textContent = 'Abriendo Flujo.cmd en una ventana de consola...';
      try {
        const response = await fetch('http://127.0.0.1:$flowPort/$flowToken', { method: 'GET', mode: 'cors', cache: 'no-store' });
        if (!response.ok) throw new Error('El puente local rechazó la solicitud (' + response.status + ').');
        status.textContent = 'Flujo.cmd se abrió en una ventana aparte. Sigue allí sus confirmaciones; esta página no limita las tareas que seleccionas.';
      } catch (error) {
        status.textContent = 'No se pudo abrir Flujo.cmd. Abre Seguimiento.cmd de nuevo para restablecer el botón.';
        flowButton.disabled = false;
      }
    });
  }
  const ideasButton = document.getElementById('open-ideas-manager');
  if (ideasButton && !ideasButton.disabled) {
    ideasButton.addEventListener('click', async () => {
      ideasButton.disabled = true;
      const status = document.getElementById('ideas-status');
      status.textContent = 'Abriendo la biblioteca editable de prompts...';
      try {
        const response = await fetch('http://127.0.0.1:$flowPort/ideas/$ideasToken', { method: 'GET', mode: 'cors', cache: 'no-store' });
        if (!response.ok) throw new Error('El puente local rechazó la solicitud (' + response.status + ').');
        status.textContent = 'Gestor abierto en una consola aparte. Los prompts no se asignan ni ejecutan automáticamente.';
      } catch (error) {
        status.textContent = 'No se pudo abrir la biblioteca. Abre Seguimiento.cmd de nuevo para restablecer el botón.';
        ideasButton.disabled = false;
      }
    });
  }
  const cloudButton = document.getElementById('prepare-cloud');
  if (cloudButton && !cloudButton.disabled) {
    cloudButton.addEventListener('click', async () => {
      cloudButton.disabled = true;
      const status = document.getElementById('cloud-status');
      status.textContent = 'Abriendo el preparador local de paquetes cloud...';
      try {
        const response = await fetch('http://127.0.0.1:$flowPort/cloud/$cloudToken', { method: 'GET', mode: 'cors', cache: 'no-store' });
        if (!response.ok) throw new Error('El puente local rechazó la solicitud (' + response.status + ').');
        status.textContent = 'Preparador abierto. El paquete se crea localmente; subirlo al agente cloud es un paso manual e independiente.';
      } catch (error) {
        status.textContent = 'No se pudo abrir el preparador cloud. Abre Seguimiento.cmd de nuevo para restablecer el botón.';
        cloudButton.disabled = false;
      }
    });
  }
  const cloudTrackingButton = document.getElementById('manage-cloud-assignments');
  if (cloudTrackingButton && !cloudTrackingButton.disabled) {
    cloudTrackingButton.addEventListener('click', async () => {
      cloudTrackingButton.disabled = true;
      const status = document.getElementById('cloud-tracking-status');
      status.textContent = 'Abriendo el gestor de seguimiento cloud...';
      try {
        const response = await fetch('http://127.0.0.1:$flowPort/cloud-tracking/$cloudTrackingToken', { method: 'GET', mode: 'cors', cache: 'no-store' });
        if (!response.ok) throw new Error('El puente local rechazó la solicitud (' + response.status + ').');
        status.textContent = 'Gestor abierto. Cada cambio se registra como acción declarada por el operador.';
      } catch (error) {
        status.textContent = 'No se pudo abrir el seguimiento cloud. Abre Seguimiento.cmd de nuevo para restablecer el botón.';
        cloudTrackingButton.disabled = false;
      }
    });
  }
  // Local file links (task cards, stage sheets, reports) open outside this window so the
  // Edge --app window, which has no navigation bar, never leaves the tracker.
  document.querySelectorAll('a[href^="file:"]').forEach((link) => {
    link.target = '_blank';
    link.rel = 'noopener';
  });
  // SEG-T09: collapsible stages and phases; open/closed state is remembered per element in localStorage.
  function segLoad(key) { try { return window.localStorage.getItem(key); } catch (e) { return null; } }
  function segSave(key, open) { try { window.localStorage.setItem(key, open ? '1' : '0'); } catch (e) { /* storage unavailable: keep the initial state */ } }
  document.querySelectorAll('details[data-seg-id]').forEach((d) => {
    const saved = segLoad(d.dataset.segId);
    if (saved === '1') d.open = true; else if (saved === '0') d.open = false;
    d.addEventListener('toggle', () => segSave(d.dataset.segId, d.open));
  });
  document.querySelectorAll('button[data-seg-action]').forEach((b) => b.addEventListener('click', () => {
    const open = b.dataset.segAction === 'expand';
    const root = b.dataset.segScope === 'stage' ? b.closest('details.seg-stage') : b.closest('.tab');
    const selector = b.dataset.segScope === 'stage' ? 'details.seg-phase' : 'details[data-seg-id]';
    if (root) root.querySelectorAll(selector).forEach((d) => { d.open = open; });
  }));
  show(location.hash.slice(1));
  setTimeout(() => location.reload(), 30000);
</script>
</body>
</html>
"@

Set-Content -Path $output -Value $html -Encoding utf8
}

function Get-Stamp {
    $files = @($summary, $roadmap, $repoProgress, $repoStagePlan | Where-Object { Test-Path $_ } | Get-Item)
    # Pending patches and new integration commits change the Resumen tab.
    $files += @(Get-ChildItem -LiteralPath $pendingPatchesDir -Filter '*.patch' -File -ErrorAction SilentlyContinue)
    $headLog = Join-Path $repoRoot '.git\logs\HEAD'
    if (Test-Path -LiteralPath $headLog -PathType Leaf) { $files += Get-Item -LiteralPath $headLog }
    # A push or fetch moves the public tracking ref, which can publish a closed stage.
    if ($publicRepo) {
        $publicRefLog = Join-Path $publicRepo ".git\logs\refs\remotes\$($publicRef -replace '/', '\')"
        if (Test-Path -LiteralPath $publicRefLog -PathType Leaf) { $files += Get-Item -LiteralPath $publicRefLog }
    }
    # SEG-T13: an integration round (patches applied, tests running) changes the labels and the notice.
    $roundFile = Join-Path $channel 'parches\ronda.txt'
    if (Test-Path -LiteralPath $roundFile -PathType Leaf) { $files += Get-Item -LiteralPath $roundFile }
    $channelRegistry = Join-Path $channel 'CANAL.md'
    if (Test-Path -LiteralPath $channelRegistry -PathType Leaf) { $files += Get-Item -LiteralPath $channelRegistry }
    if (Test-Path $tasksDir) { $files += Get-ChildItem $tasksDir -Recurse -Filter '*.md' }
    $alignment = Join-Path $channel 'realinear\ESTADO.md'
    if (Test-Path -LiteralPath $alignment) { $files += Get-Item -LiteralPath $alignment }
    if (Test-Path -LiteralPath $integrationReads) { $files += Get-Item -LiteralPath $integrationReads }
    if (Test-Path -LiteralPath $integrationDir) {
        $files += Get-ChildItem -LiteralPath $integrationDir -Filter '*_integracion.md' -File -ErrorAction SilentlyContinue
    }
    # SEG-T18: the activities outside tasks live only in their JSONL.
    $activityLog = Join-Path $channel 'orquestacion\ACTIVIDADES\ejecuciones.jsonl'
    if (Test-Path -LiteralPath $activityLog -PathType Leaf) { $files += Get-Item -LiteralPath $activityLog }
    # SEG-T19: the watchers' state files (each check rewrites one, which moves the progress).
    $liveWaitFiles = @(Get-ChildItem -LiteralPath (Join-Path $channel 'orquestacion\esperas\activas') -Filter '*.json' -File -ErrorAction SilentlyContinue)
    $files += $liveWaitFiles
    $files += @(Get-ChildItem -LiteralPath (Join-Path $channel 'orquestacion\esperas\terminadas') -Filter '*.json' -File -ErrorAction SilentlyContinue)
    if (Test-Path -LiteralPath $ideasStorePath -PathType Leaf) { $files += Get-Item -LiteralPath $ideasStorePath }
    if (Test-Path -LiteralPath $cloudAssignmentsPath -PathType Leaf) { $files += Get-Item -LiteralPath $cloudAssignmentsPath }
    $stamp = ($files | ForEach-Object { "$($_.Name):$($_.LastWriteTimeUtc.Ticks)" }) -join '-'
    # A watcher that stops writing (lost, killed) leaves its file as it is, yet its label must change: while one is live the page is also
    # regenerated once a minute.
    if ($liveWaitFiles.Count) { $stamp += "-esperas:$([long][Math]::Floor([datetime]::UtcNow.Ticks / [timespan]::TicksPerMinute))" }
    $stamp
}

# The page opens in its own Edge app window with a separate profile, so its lifetime is observable.
$profileDir = Join-Path $env:LOCALAPPDATA 'canal-pareja-visor'
function Get-EdgePath {
    $path = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe' -ErrorAction SilentlyContinue).'(default)'
    if ($path -and (Test-Path $path)) { $path }
}
function Test-ViewerOpen {
    @(Get-CimInstance Win32_Process -Filter "Name='msedge.exe'" |
        Where-Object { $_.CommandLine -like "*$profileDir*" }).Count -gt 0
}

function Start-FlowListener {
    try {
        $script:flowListener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
        $script:flowListener.Start()
        $script:flowPort = ([System.Net.IPEndPoint]$script:flowListener.LocalEndpoint).Port
        $bytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        $script:flowToken = [Convert]::ToHexString($bytes).ToLowerInvariant()
        if (Test-Path -LiteralPath $script:ideasManagerCmd -PathType Leaf) {
            $ideasBytes = New-Object byte[] 32
            [System.Security.Cryptography.RandomNumberGenerator]::Fill($ideasBytes)
            $script:ideasToken = [Convert]::ToHexString($ideasBytes).ToLowerInvariant()
        }
        if (Test-Path -LiteralPath $script:cloudCmd -PathType Leaf) {
            $cloudBytes = New-Object byte[] 32
            [System.Security.Cryptography.RandomNumberGenerator]::Fill($cloudBytes)
            $script:cloudToken = [Convert]::ToHexString($cloudBytes).ToLowerInvariant()
        }
        if (Test-Path -LiteralPath $script:cloudTrackingCmd -PathType Leaf) {
            $cloudTrackingBytes = New-Object byte[] 32
            [System.Security.Cryptography.RandomNumberGenerator]::Fill($cloudTrackingBytes)
            $script:cloudTrackingToken = [Convert]::ToHexString($cloudTrackingBytes).ToLowerInvariant()
        }
        return $true
    } catch {
        if ($script:flowListener) {
            $script:flowListener.Stop()
            $script:flowListener = $null
        }
        $script:flowToken = ''
        $script:ideasToken = ''
        $script:cloudToken = ''
        $script:cloudTrackingToken = ''
        Write-Warning "No se pudieron habilitar los botones de acciones locales: $($_.Exception.Message)"
        return $false
    }
}

function Send-FlowResponse([System.Net.Sockets.NetworkStream]$Stream, [int]$Status, [string]$Reason, [string]$Body = '') {
    $bodyBytes = [System.Text.Encoding]::UTF8.GetBytes($Body)
    $headers = @(
        "HTTP/1.1 $Status $Reason",
        'Access-Control-Allow-Origin: null',
        'Access-Control-Allow-Methods: GET, OPTIONS',
        'Access-Control-Allow-Private-Network: true',
        'Vary: Origin',
        'Cache-Control: no-store',
        'Connection: close',
        "Content-Length: $($bodyBytes.Length)",
        'Content-Type: text/plain; charset=utf-8',
        '',
        ''
    ) -join "`r`n"
    $headerBytes = [System.Text.Encoding]::ASCII.GetBytes($headers)
    $Stream.Write($headerBytes, 0, $headerBytes.Length)
    if ($bodyBytes.Length) { $Stream.Write($bodyBytes, 0, $bodyBytes.Length) }
    $Stream.Flush()
}

function Invoke-FlowListenerRequest {
    if (-not $script:flowListener -or -not $script:flowListener.Pending()) { return }
    $client = $null
    try {
        $client = $script:flowListener.AcceptTcpClient()
        $remoteAddress = ([System.Net.IPEndPoint]$client.Client.RemoteEndPoint).Address
        if (-not [System.Net.IPAddress]::IsLoopback($remoteAddress)) { return }
        $stream = $client.GetStream()
        $stream.ReadTimeout = 2000
        $reader = [System.IO.StreamReader]::new($stream, [System.Text.Encoding]::ASCII, $false, 1024, $true)
        $requestLine = $reader.ReadLine()
        $headers = @{}
        $headerBytes = 0
        while ($true) {
            $line = $reader.ReadLine()
            if ($null -eq $line -or $line -eq '') { break }
            $headerBytes += $line.Length
            if ($headerBytes -gt 8192) { Send-FlowResponse $stream 431 'Request Header Fields Too Large'; return }
            if ($line -match '^([^:]+):\s*(.*)$') { $headers[$Matches[1].ToLowerInvariant()] = $Matches[2] }
        }
        if ($headers['origin'] -cne 'null') { Send-FlowResponse $stream 403 'Forbidden'; return }
        if ($requestLine -in @(
            'OPTIONS / HTTP/1.1',
            'OPTIONS / HTTP/1.0',
            "OPTIONS /$script:flowToken HTTP/1.1",
            "OPTIONS /$script:flowToken HTTP/1.0",
            "OPTIONS /ideas/$script:ideasToken HTTP/1.1",
            "OPTIONS /ideas/$script:ideasToken HTTP/1.0",
            "OPTIONS /cloud/$script:cloudToken HTTP/1.1",
            "OPTIONS /cloud/$script:cloudToken HTTP/1.0",
            "OPTIONS /cloud-tracking/$script:cloudTrackingToken HTTP/1.1",
            "OPTIONS /cloud-tracking/$script:cloudTrackingToken HTTP/1.0"
        )) {
            Send-FlowResponse $stream 204 'No Content'
            return
        }
        $isFlowRequest = $requestLine -in @("GET /$script:flowToken HTTP/1.1", "GET /$script:flowToken HTTP/1.0")
        $isIdeasRequest = $script:ideasToken -and $requestLine -in @(
            "GET /ideas/$script:ideasToken HTTP/1.1", "GET /ideas/$script:ideasToken HTTP/1.0"
        )
        $isCloudRequest = $script:cloudToken -and $requestLine -in @(
            "GET /cloud/$script:cloudToken HTTP/1.1", "GET /cloud/$script:cloudToken HTTP/1.0"
        )
        $isCloudTrackingRequest = $script:cloudTrackingToken -and $requestLine -in @(
            "GET /cloud-tracking/$script:cloudTrackingToken HTTP/1.1", "GET /cloud-tracking/$script:cloudTrackingToken HTTP/1.0"
        )
        if (-not $isFlowRequest -and -not $isIdeasRequest -and -not $isCloudRequest -and -not $isCloudTrackingRequest) {
            Send-FlowResponse $stream 404 'Not Found'
            return
        }
        if ($isIdeasRequest) {
            if (-not (Test-Path -LiteralPath $script:ideasManagerCmd -PathType Leaf)) {
                Send-FlowResponse $stream 500 'Ideas Manager Unavailable'
                return
            }
            $command = '"' + $script:ideasManagerCmd.Replace('"', '""') + '"'
            Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', $command) -WorkingDirectory $script:repoRoot -WindowStyle Normal
            Send-FlowResponse $stream 200 'OK' 'Ideas manager opened'
            return
        }
        if ($isCloudRequest) {
            if (-not (Test-Path -LiteralPath $script:cloudCmd -PathType Leaf)) {
                Send-FlowResponse $stream 500 'Cloud Package Preparer Unavailable'
                return
            }
            $command = '"' + $script:cloudCmd.Replace('"', '""') + '"'
            Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', $command) -WorkingDirectory $script:repoRoot -WindowStyle Normal
            Send-FlowResponse $stream 200 'OK' 'Cloud package preparer opened'
            return
        }
        if ($isCloudTrackingRequest) {
            if (-not (Test-Path -LiteralPath $script:cloudTrackingCmd -PathType Leaf)) {
                Send-FlowResponse $stream 500 'Cloud Assignment Manager Unavailable'
                return
            }
            $command = '"' + $script:cloudTrackingCmd.Replace('"', '""') + '"'
            Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', $command) -WorkingDirectory $script:repoRoot -WindowStyle Normal
            Send-FlowResponse $stream 200 'OK' 'Cloud assignment manager opened'
            return
        }
        if (-not (Test-Path -LiteralPath $script:flowCmd -PathType Leaf)) {
            Send-FlowResponse $stream 500 'Flow Launcher Unavailable'
            return
        }
        $command = '"' + $script:flowCmd.Replace('"', '""') + '"'
        Start-Process -FilePath $env:ComSpec -ArgumentList @('/c', $command) -WorkingDirectory $script:repoRoot -WindowStyle Normal
        Send-FlowResponse $stream 200 'OK' 'Flow started'
    } catch {
        Write-Warning "La solicitud para abrir Flujo.cmd falló: $($_.Exception.Message)"
        if ($client -and $client.Connected) {
            try { Send-FlowResponse $client.GetStream() 500 'Internal Server Error' } catch { }
        }
    } finally {
        if ($client) { $client.Dispose() }
    }
}

if ((-not $NoAbrir -and (Get-EdgePath)) -or $Vigilar) { $null = Start-FlowListener }
Build-Page
Write-Host "Visualizador generado: $output"

$edge = if (-not $NoAbrir) { Get-EdgePath }
if (-not $NoAbrir) {
    if ($edge) {
        Start-Process $edge -ArgumentList "--user-data-dir=`"$profileDir`"", '--no-first-run', "--app=$(([uri]$output).AbsoluteUri)"
    } else {
        Start-Process $output   # no Edge: default browser; the watcher then needs -Vigilar and runs until sign-out
    }
}

$untilClosed = [bool]$edge
if (-not ($untilClosed -or $Vigilar)) {
    if ($flowListener) { $flowListener.Stop(); $flowListener = $null }
    return
}
$mutex = [System.Threading.Mutex]::new($false, 'Local\canal-pareja-seguimiento')
if (-not $mutex.WaitOne(0)) {
    if ($flowListener) { $flowListener.Stop(); $flowListener = $null }
    return
}   # a watcher is already running for an open window
if ($untilClosed) { Start-Sleep -Seconds 5 }   # let the window start
$last = Get-Stamp
try {
    while (-not $untilClosed -or (Test-ViewerOpen)) {
        Invoke-FlowListenerRequest
        $stamp = Get-Stamp
        if ($stamp -ne $last) {
            try { Build-Page; $last = $stamp } catch { Write-Warning "No se pudo actualizar la página de seguimiento: $_" }
        }
        Start-Sleep -Milliseconds 250
    }
} finally {
    if ($flowListener) { $flowListener.Stop(); $flowListener = $null }
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
