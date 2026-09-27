# Renders SEGUIMIENTO.md, the task board, and integration evidence as a friendly HTML page.
# The page reloads itself every 30 s, so a browser refresh always shows the last generated version.
# Completed stages are archived in the view only; source files are never moved or rewritten.
#
# Normal use: double-click Seguimiento.cmd. The page opens in its own Edge window (separate profile)
# and a hidden watcher regenerates it whenever SEGUIMIENTO.md or RESUMEN.md change. Closing that
# window ends the watcher. Only one watcher runs at a time. Nothing is installed.
#
#   ver-seguimiento.ps1              regenerate, open the window, watch until it is closed
#   ver-seguimiento.ps1 -NoAbrir     regenerate only
#   ver-seguimiento.ps1 -NoAbrir -Vigilar   regenerate on changes without a window (until stopped)

param(
    [Parameter(Mandatory)][string]$ChannelPath,
    [Parameter(Mandatory)][string]$RepoPath,
    [switch]$NoAbrir,
    [switch]$Vigilar
)

$ErrorActionPreference = 'Stop'
$channel = (Resolve-Path -LiteralPath $ChannelPath).Path
$repoRoot = (Resolve-Path -LiteralPath $RepoPath).Path
$source = Join-Path $channel 'SEGUIMIENTO.md'
$summary = Join-Path $channel 'RESUMEN.md'
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
function Test-TaskPatchRequired([string]$patch) {
    $value = $patch.Trim()
    if (-not $value) { return $false }
    $value -notmatch '(?i)^\s*(?:—|–|-|ninguno|none|no aplica)(?:\s|$|\()'
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
        $taskCommit = Get-CommitToken $task.Commit
        if (-not $taskCommit) {
            return [pscustomobject]@{ Status = 'unknown'; Reason = "La tarea $($task.Id) no tiene un commit válido."; Commit = $closeCommit; Report = ''; Stage = $stageName }
        }
        $resolvedTaskCommit = Resolve-IntegrationCommit $taskCommit
        if (-not $resolvedTaskCommit -or $resolvedTaskCommit -ne $resolvedCloseCommit) {
            return [pscustomobject]@{ Status = 'unknown'; Reason = "El commit de $($task.Id) no coincide con el commit de cierre."; Commit = $closeCommit; Report = ''; Stage = $stageName }
        }
    }

    $reportText = Get-Field $stageText 'Reporte de integración'
    $reportId = if ($reportText -match '(\d{4}-\d{2}-\d{2}_\d{4})') { $Matches[1] }
        elseif ((Get-Field $stageText 'Commit de cierre') -match '(?i)integraci[oó]n[^0-9]*(\d{4}-\d{2}-\d{2}_\d{4})') { $Matches[1] }
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

function Get-AgentBoardData {
    $registered = @('Claude', 'Codex', 'Copilot')
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
            }
            $tasks.Add($task)
            if ($state -in @('en curso', 'pendiente') -and $agent -notin $registered) {
                $issues++
                Write-Warning "La ficha de tarea $($file.Name) no identifica un agente registrado para su asignación activa."
            }

            $historyText = Get-TaskSection $text 'Historial'
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
            @"
<article class="panel idea-card">
  <h3>$(Encode $item.Title)</h3>
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
$button
<p id="ideas-status" class="meta" role="status" aria-live="polite"></p>
$contents
"@
}

# realinear\ESTADO.md (written by realinear.ps1): one row per agent with its realignment state.
function Get-AlignmentHtml([string]$name) {
    $path = Join-Path $channel 'realinear\ESTADO.md'
    $row = if (Test-Path -LiteralPath $path) {
        Get-Content -LiteralPath $path -Encoding utf8 | Where-Object { $_ -match "^\|\s*$name\s*\|" } | Select-Object -First 1
    }
    if (-not $row) { return '<p class="meta">Realineación: sin revisar todavía (paso 4 de Flujo.cmd).</p>' }
    $cells = @($row.Trim('|').Split('|') | ForEach-Object { $_.Trim() })
    $pending = $cells[1] -eq 'PENDIENTE'
    $class = if ($pending) { 's-pendiente' } else { 's-integrada' }
    $detail = if ($pending -and $cells[3]) { " — $(Encode $cells[3]). Debe realinearse antes de tomar otra tarea (regla 18)." } else { '' }
    "<p class=""meta""><span class=""state $class"">Realineación: $(Encode $cells[1])</span> $(Encode $cells[2]) · revisado $(Encode $cells[4])$detail</p>"
}

function Build-Page {
$markdown = Get-Content -Path $source -Raw -Encoding utf8
$body = (ConvertFrom-Markdown -InputObject $markdown).Html
$agentBoard = Get-AgentBoardData
$readsText = if (Test-Path -LiteralPath $integrationReads -PathType Leaf) {
    try { Get-Content -LiteralPath $integrationReads -Raw -Encoding utf8 -ErrorAction Stop }
    catch {
        Write-Warning 'No se pudo leer el registro de resultados de integración; no se verificará ningún cierre.'
        ''
    }
} else { '' }

# Progress cards: every stage row that states "done/total hitos = NN%".
$progressRecords = foreach ($line in ($markdown -split "`r?`n")) {
    if ($line -match '^\|\s*\*\*(G\d+[A-Za-z0-9]*)[^|]*\|\s*([^|]*)\|\s*([^|]*)\|\s*([^|]*)\|') {
        $gate = $Matches[1]; $goal = $Matches[2].Trim(); $state = $Matches[3].Trim() -replace '\*', ''
        $progress = $Matches[4]
        if ($progress -match '(\d+)\s*/\s*(\d+)\s*hitos\s*=\s*(\d+)%') {
            $done = [int]$Matches[1]; $total = [int]$Matches[2]; $pct = [int]$Matches[3]
            $code = if ($progress -match '(\d+)/(\d+) entregas publicadas, (\d+)/(\d+) revisadas') {
                "Codigo: $($Matches[1])/$($Matches[2]) entregas publicadas, $($Matches[3])/$($Matches[4]) revisadas"
            } else { '' }
            [pscustomobject]@{ Stage = $gate; Goal = $goal; State = $state; Done = $done; Total = $total; Percent = $pct; Code = $code }
        }
    }
}

# These legacy milestones were explicitly confirmed by the user as verified and present
# in the public repository. New stages remain local until publication is confirmed.
$publicVerifiedEvidence = @{
    G1 = [pscustomobject]@{ Commit = 'e887bf3'; Report = '2026-09-26_1935_integracion.md' }
    G2 = [pscustomobject]@{ Commit = '27b3e1e'; Report = '2026-09-26_2020_integracion.md' }
    G3 = [pscustomobject]@{ Commit = '2682b3e'; Report = '2026-09-26_2031_integracion.md' }
}

$boards = ''
$archiveBoards = ''
$publicVerifiedBoards = [System.Collections.Generic.List[string]]::new()
$uncertainBoards = ''
$archiveLinks = [System.Collections.Generic.List[string]]::new()
$stageIndex = @{}
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
            $record = [pscustomobject]@{
                Id = "$($stage.Name)-$taskId"
                Code = $taskId
                Title = $name
                State = $state
                Commit = Get-Field $text 'Commit'
                Dependencies = Get-Field $text 'Depende de'
                Patch = Get-Field $text 'Parche'
                Agent = Get-Field $text 'Agente'
                Preferred = Get-Field $text 'Preferente'
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
            $taskRecords.Add($record)
        }
        $tasksByCode = @{}
        foreach ($record in $taskRecords) { $tasksByCode[$record.Code] = $record }
        foreach ($record in $taskRecords) {
            $dependencyCodes = @([regex]::Matches($record.Dependencies, '(?i)\bT\d+\b') | ForEach-Object { $_.Value.ToUpperInvariant() } | Select-Object -Unique)
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
                if (Test-TaskPatchRequired $record.Patch) {
                    "<span class=""state s-pendiente"">Requiere integración · pendiente</span>$date<div class=""meta"">Parche: $(Inline $record.Patch)</div>"
                } elseif ($record.Patch.Trim()) {
                    "<span class=""state s-idle"">Sin parche que integrar</span>$date"
                } else {
                    "<span class=""state s-unknown"">Integración indeterminada · parche no registrado</span>$date"
                }
            } elseif ($record.State -eq 'integrada') {
                $commit = Get-CommitToken $record.Commit
                $commitLabel = if ($commit) { "<div class=""meta"">Commit <code>$(Encode $commit)</code></div>" } else { '<div class="meta">Commit no registrado</div>' }
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
            $rowHtml = "<tr><td><a href=""$($record.Href)"">$(Encode $record.Title)</a>$($record.LeftHtml)</td>" +
            "<td><span class=""state $stateClass"">$(Encode $record.State)</span></td>" +
            "<td>$(Inline $record.Agent)</td><td>$(Inline $record.Preferred)</td>" +
            "<td>$dependencyHtml</td><td>$integrationHtml</td><td>$(Inline $record.Patch)</td><td>$(Encode $record.Workers)</td></tr>"
            $taskRows.Add([pscustomobject]@{ Id = $record.Code; State = $record.State; Patch = $record.Patch; Html = $rowHtml })
        }
        $closure = if ($stageReadFailed) {
            [pscustomobject]@{ Status = 'unknown'; Reason = 'La ficha de etapa falta o no se pudo leer.'; Commit = ''; Report = ''; Stage = $stage.Name }
        } else {
            Get-StageClosure $stage.Name $stageText @($taskRecords) $taskReadFailed $readsText
        }
        $claimsClosed = -not $stageReadFailed -and (Get-Field $stageText 'Estado').Trim().ToLowerInvariant() -in @('cerrada', 'cerrado', 'closed', 'complete', 'completed')
        $closure | Add-Member -NotePropertyName ClaimsClosed -NotePropertyValue $claimsClosed
        $stageIndex[$stage.Name.ToLowerInvariant()] = $closure
        $pct = if ($total) { [int](100 * $done / $total) } else { 0 }
        $tableHeader = '<table><tr><th>Tarea</th><th>Estado</th><th>Agente</th><th>Preferente</th><th>Dependencias y estado</th><th>Integración</th><th>Parche</th><th>Trabajaron</th></tr>'
        $phaseDefinitions = @(Get-StagePhases $stageText)
        $taskGroups = ''
        if ($phaseDefinitions.Count) {
            $assignedTasks = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
            $phaseSections = [System.Collections.Generic.List[string]]::new()
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
                $phaseBlocked = @($phaseTaskRows | Where-Object { $_.State -eq 'requiere cambios' }).Count
                $phaseBadge = if (-not $phaseTaskRows.Count) { '' }
                    elseif ($phaseBlocked) { '<span class="state s-requiere-cambios">Con cambios requeridos</span>' }
                    elseif ($phaseIntegrated -eq $phaseTaskRows.Count) { '<span class="state s-integrada">Fase completa</span>' }
                    elseif ($phaseDone -eq $phaseTaskRows.Count) { '<span class="state s-pendiente">Fase terminada · falta integrar</span>' }
                    else { '<span class="state s-en-curso">En progreso</span>' }
                $phaseContent = if ($phaseTaskRows.Count) {
                    "$tableHeader$($phaseTaskRows.Html -join "`n")</table>"
                } else {
                    '<p class="meta">No hay fichas de tarea que coincidan con esta fase.</p>'
                }
                $phaseSections.Add(@"
<section class="board-phase">
  <div class="card-head"><h3>$(Encode $phase.Label) $phaseBadge</h3><span class="meta">$($phaseTaskRows.Count) $phaseTaskLabel · $phaseDone terminadas o integradas ($phaseIntegrated sin integración pendiente)</span></div>
  $(if ($phase.Description) { "<p class=""meta"">$(Encode $phase.Description)</p>" })
  $phaseContent
</section>
"@)
            }
            $unassignedRows = @($taskRows | Where-Object { -not $assignedTasks.Contains($_.Id) })
            if ($unassignedRows.Count) {
                $phaseSections.Add(@"
<section class="board-phase">
  <h3>Sin fase asignada</h3>
  $tableHeader$($unassignedRows.Html -join "`n")</table>
</section>
"@)
            }
            $taskGroups = $phaseSections -join "`n"
        } else {
            $taskGroups = "$tableHeader$($taskRows.Html -join "`n")</table>"
        }
        $boardHtml = @"
<section class="panel board">
  <div class="card-head"><h2>$(Encode $stageTitle)</h2><span class="pct">$done/$total</span></div>
  <div class="bar"><div class="fill" style="width:$pct%"></div></div>
  <p class="meta">Autorizada: $(Encode $authorized) &middot; terminadas o integradas: $done de $total</p>
  <p class="meta">Integración: un parche entregado queda pendiente hasta que la ficha registre integrada; sin parche no implica integración pendiente. El commit y la fecha se muestran solo si constan en la ficha y su historial.</p>
  <p class="meta">Cierre: $(if ($closure.Status -eq 'complete') { 'verificado' } elseif ($closure.Status -eq 'active') { 'etapa activa' } else { 'no verificado — ' + (Encode $closure.Reason) })</p>
  $taskGroups
</section>
"@
        if ($closure.Status -eq 'complete') {
            $stageHref = ([uri]$stageFile).AbsoluteUri
            $reportHref = ([uri](Join-Path $integrationDir $closure.Report)).AbsoluteUri
            $stageAnchor = "history-$($stage.Name.ToLowerInvariant())"
            $archiveLinks.Add("<div class=""history-summary""><strong>$(Encode $stageTitle)</strong><span class=""state s-integrada"">Cerrada · local</span><div class=""meta"">Commit <code>$(Encode $closure.Commit)</code> &middot; <a href=""#$stageAnchor"">Historial</a> &middot; <a href=""$stageHref"">Ficha de etapa</a></div></div>")
            $archiveBoards += @"
<section class="panel archive-stage" id="$stageAnchor">
  <div class="card-head"><h2>$(Encode $stageTitle)</h2><span class="state s-integrada">Cierre verificado localmente · publicación no confirmada</span></div>
  <p class="meta">Commit de cierre: <code>$(Encode $closure.Commit)</code> &middot; <a href="$stageHref">Ficha de etapa</a> &middot; <a href="$reportHref">Reporte de integración OK</a></p>
  <details><summary>Ver tareas integradas ($total)</summary>$boardHtml</details>
</section>
"@
        } elseif ($closure.Status -eq 'unknown') {
            if ($closure.ClaimsClosed) {
                $stageHref = ([uri]$stageFile).AbsoluteUri
                $reason = Encode $closure.Reason
                $uncertainBoards += @"
<div class="history-summary"><strong>$(Encode $stageTitle)</strong><span class="state s-unknown">Cierre no verificado</span><div class="meta">$reason &middot; <a href="$stageHref">Ficha de etapa</a> &middot; <a href="#tareas">Ver tareas activas</a></div></div>
"@
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
if (-not $archiveBoards) { $archiveBoards = '<section class="panel">No hay cierres verificados localmente.</section>' }
if (-not $publicVerifiedBoards.Count) { $publicVerifiedBoards.Add('<section class="panel">No hay otras etapas con publicación pública confirmada.</section>') }
if (-not $uncertainBoards) { $uncertainBoards = '<section class="panel">No hay cierres pendientes de verificación.</section>' }

$cards = [System.Collections.Generic.List[string]]::new()
$reportedClosedNotVerified = [System.Collections.Generic.List[string]]::new()
foreach ($record in $progressRecords) {
    $stageKey = $record.Stage.ToLowerInvariant()
    $closure = if ($publicVerifiedEvidence.ContainsKey($record.Stage)) {
        [pscustomobject]@{
            Status = 'public-verified'
            Commit = $publicVerifiedEvidence[$record.Stage].Commit
            Report = $publicVerifiedEvidence[$record.Stage].Report
        }
    } else { $stageIndex[$stageKey] }
    $reportedClosed = $record.State -match '^(?i)cerrad[oa]\b'
    if ($reportedClosed -and $closure -and $closure.Status -in @('complete', 'public-verified')) {
        if ($closure.Status -eq 'public-verified') {
            $reportPath = Join-Path $integrationDir $closure.Report
            $report = if (Test-Path -LiteralPath $reportPath -PathType Leaf) {
                "<a href=""$([uri]$reportPath)"">Reporte registrado: $(Encode $closure.Report)</a>"
            } else {
                "Reporte registrado: $(Encode $closure.Report) (no disponible localmente)"
            }
            $stageTitle = "$(Encode $record.Stage) · $(Encode $record.Goal)"
            $publicVerifiedBoards.Add(@"
<section class="panel archive-stage">
  <div class="card-head"><h2>$stageTitle</h2><span class="state s-integrada">Verificada y publicada</span></div>
  <p class="meta">Verificación y presencia en el repositorio público confirmadas por el usuario. Commit <code>$(Encode $closure.Commit)</code> &middot; $report</p>
</section>
"@)
            $archiveLinks.Add("<div class=""history-summary""><strong>$stageTitle</strong><span class=""state s-integrada"">Verificada · pública</span><div class=""meta"">Commit <code>$(Encode $closure.Commit)</code> &middot; <a href=""#historico"">Historial</a></div></div>")
        }
        continue
    }
    $cardHtml = @"
<div class="card">
  <div class="card-head"><span class="gate">$(Encode $record.Stage)</span><span class="pct">$($record.Percent)%</span></div>
  <div class="bar"><div class="fill" style="width:$($record.Percent)%"></div></div>
  <div class="meta">$($record.Done) de $($record.Total) hitos &middot; $(Encode $record.State)</div>
  <div class="goal">$(Encode $record.Goal)</div>
  $(if ($record.Code) { "<div class=""meta"">$(Encode $record.Code)</div>" })
</div>
"@
    if ($reportedClosed) {
        $reason = if ($closure) { $closure.Reason } else { 'No existe una ficha ETAPA.md y un conjunto de tareas que permita verificar el cierre.' }
        $reportedCommit = if ($record.State -match '(?i)\bcommit\s+([0-9a-f]{7,40})\b') { "<span>Commit reportado <code>$(Encode $Matches[1])</code></span>" } else { '' }
        $reportedClosedNotVerified.Add(@"
<div class="history-summary"><strong>$(Encode $record.Stage) · $(Encode $record.Goal)</strong><span class="state s-unknown">Cierre no verificado</span><div class="meta">$reportedCommit $(Encode $reason) &middot; <a href="#detalle">Consultar seguimiento detallado</a></div></div>
"@)
        continue
    }
    $cards.Add($cardHtml)
}

$stop = if ($markdown -match '(?s)### Punto de parada actual\s*(.+?)\s*$') { $Matches[1].Trim() -replace '\*\*', '' } else { '' }

$todo = ''
if (Test-Path $summary) {
    $summaryText = Get-Content -Path $summary -Raw -Encoding utf8
    if ($summaryText -match '(?s)## Qu. tienes que hacer ahora\s*(.+?)\s*## Bit') {
        $todoMarkdown = ($Matches[1] -split "`r?`n" | Where-Object { $_ -notmatch '^_Esta secci' }) -join "`n"
        $todo = (ConvertFrom-Markdown -InputObject $todoMarkdown).Html
    }
}

# ROADMAP_VISTA.md: "## Etapas y features" and "## Roadmap completo" become their own tabs.
$features = ''; $fullRoadmap = ''
if (Test-Path $roadmap) {
    $roadmapText = Get-Content -Path $roadmap -Raw -Encoding utf8
    if ($roadmapText -match '(?sm)## Etapas y features\s*(.+?)\s*(?=^## |\z)') {
        $features = (ConvertFrom-Markdown -InputObject $Matches[1]).Html
    }
    if ($roadmapText -match '(?sm)## Roadmap completo\s*(.+?)\s*(?=^## |\z)') {
        $fullRoadmap = (ConvertFrom-Markdown -InputObject $Matches[1]).Html
    }
}

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
$agentCards = foreach ($name in @('Claude', 'Codex', 'Copilot')) {
    if (-not $agentBoard.Available) {
        $status = 'Estado no disponible'
        $statusClass = 's-unknown'
        $current = '<p class="meta">No se pudo consultar el tablero de tareas.</p>'
    } else {
        $active = @($agentBoard.Tasks | Where-Object { $_.Agent -ieq $name -and $_.State -in @('en curso', 'pendiente') })
        if ($active) {
            $status = if (@($active | Where-Object State -eq 'en curso').Count) { 'Asignada · En curso' } else { 'Asignada · En espera' }
            $statusClass = if ($status -eq 'Asignada · En curso') { 's-en-curso' } else { 's-pendiente' }
            $current = foreach ($task in $active) {
                $taskState = if ($task.State -eq 'en curso') { 'En curso' } else { 'Pendiente' }
                $context = if ($task.Missing) { "<p class=""agent-context"">Qué falta: $(Encode $task.Missing)</p>" } else { '' }
                "<div class=""agent-task""><strong>$(Encode $task.Id) · $(Encode $task.Title)</strong><span class=""state $(if ($task.State -eq 'en curso') { 's-en-curso' } else { 's-pendiente' })"">Asignada · $(Encode $taskState)</span>$context</div>"
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
    @"
<section class="panel agent-card">
  <div class="card-head"><h2>$(Encode $name)</h2><span class="state $statusClass">$(Encode $status)</span></div>
  <div class="agent-current"><h3>Tarea actual según el tablero</h3>$current</div>
  <div class="agent-current"><h3>Seguimiento de asignaciones cloud</h3>$cloudContext</div>
  $(Get-AlignmentHtml $name)
  $lastActivity
</section>
"@
}
$agentNotice = if (-not $agentBoard.Available) {
    'La fuente del tablero no está disponible; no se infiere que los agentes estén inactivos.'
} elseif ($agentBoard.Partial) {
    "Vista parcial: se omitieron $($agentBoard.Issues) fichas o filas de historial con formato no válido o no accesibles."
} else { '' }
$agentsView = @"
<p class="meta">Fuente: tablero e historiales de tareas locales; las asignaciones cloud aparecen en una sección separada y son registros manuales. Esto no informa presencia de procesos ni actividad en vivo.</p>
$(if ($agentNotice) { "<div class=""stop"">$(Encode $agentNotice)</div>" })
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

$updated = (Get-Item $source).LastWriteTime.ToString('yyyy-MM-dd HH:mm')
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
.s-idle { background:var(--accent-soft); color:var(--muted); }
.s-unknown { background:var(--warn-bg); border:1px solid var(--warn-line); }
.agent-grid { display:grid; grid-template-columns:repeat(auto-fit, minmax(300px, 1fr)); gap:12px; }
.agent-card h2 { margin:0; font-size:18px; }
.agent-card h3 { margin:14px 0 6px; font-size:14px; }
.agent-current .meta, .agent-last .meta { color:var(--muted); font-size:13px; margin:4px 0; }
.agent-task { border-left:3px solid var(--accent); padding:4px 0 6px 10px; margin:6px 0; }
.agent-task .state { margin-left:6px; }
.agent-context { color:var(--muted); font-size:13px; margin:4px 0 0; }
.agent-last { border-top:1px solid var(--line); margin-top:12px; padding-top:10px; font-size:13px; }
.agent-last ul { margin:4px 0 0; padding-left:20px; }
.idea-group { margin:16px 0; }
.idea-group h2 { font-size:18px; border-bottom:1px solid var(--line); padding-bottom:5px; }
.idea-card { margin:8px 0; }
.idea-card h3 { margin:0 0 8px; font-size:16px; }
.cloud-panel h2 { margin-top:0; }
</style>
</head>
<body>
<header>
  <h1>Seguimiento del proyecto &mdash; Multi-Agent Framework</h1>
  <p>Generado desde SEGUIMIENTO.md (modificado $updated) y ROADMAP_VISTA.md a las $(Get-Date -Format 'HH:mm:ss'). Se recarga sola cada 30 s mientras la ventana de Seguimiento.cmd siga abierta.</p>
</header>
<nav>
  <button data-tab="resumen">Resumen</button>
  <button data-tab="tareas">Tareas</button>
  <button data-tab="agentes">Agentes</button>
  <button data-tab="ideas">Ideas</button>
  <button data-tab="cloud">Agente cloud</button>
  <button data-tab="historico">Etapas completadas</button>
  <button data-tab="etapas">Etapas y features</button>
  <button data-tab="roadmap">Roadmap completo</button>
  <button data-tab="detalle">Seguimiento detallado</button>
  $(if ($flowListener) { "<button id=""run-flow"" type=""button"" aria-label=""Abrir el ciclo global de integración"">Abrir ciclo de integración</button>" } else { '<button id="run-flow" type="button" disabled title="El puente local no está activo; inicia el visor con Seguimiento.cmd">Abrir ciclo de integración</button>' })
</nav>
<p id="flow-status" class="meta" role="status" aria-live="polite" style="max-width:1100px;margin:6px auto;padding:0 16px"></p>
<main>
  <div class="tab" id="resumen">
    $(if ($cards.Count) { "<section class=""cards"">$($cards -join "`n")</section>" })
    $(if ($archiveLinks.Count) { "<section class=""panel todo""><h2>Etapas completadas</h2>$($archiveLinks -join "`n")</section>" })
    $(if ($reportedClosedNotVerified.Count) { "<section class=""history-warning""><h2>Cierres pendientes de verificación</h2><p class=""meta"">No se archivan como completos porque falta evidencia canónica suficiente. Consulta el <a href=""#historico"">histórico y sus motivos</a>.</p>$($reportedClosedNotVerified -join "`n")</section>" })
    $(if ($stop) { "<div class=""stop""><strong>Punto de parada actual</strong>$(Encode $stop)</div>" })
    $(if ($todo) { "<section class=""panel todo""><h2>Que tienes que hacer ahora</h2>$todo</section>" })
  </div>
  <div class="tab" id="tareas">$($boards -join "`n")</div>
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
    <p class="meta">Archivo de visualización de solo lectura. Los cierres locales se derivan de ETAPA.md, fichas de tareas, commits Git y reportes; la publicación pública requiere confirmación explícita. No mueve ni modifica las fuentes.</p>
    <section class="panel todo"><h2>Verificadas y presentes en el repositorio público</h2>$($publicVerifiedBoards -join "`n")</section>
    <section class="panel todo"><h2>Cerradas e integradas localmente; publicación no confirmada</h2>$(if ($archiveBoards) { $archiveBoards } else { '<section class="panel">No hay cierres verificados localmente.</section>' })</section>
    <section class="todo"><h2>Cierres pendientes de verificación</h2>$uncertainBoards</section>
    $(if ($reportedClosedNotVerified.Count) { "<section class=""history-warning""><h2>Etapas marcadas cerradas en el seguimiento, sin evidencia canónica completa</h2>$($reportedClosedNotVerified -join "`n")</section>" })
  </div>
  <div class="tab" id="etapas"><section class="panel doc features">$features</section></div>
  <div class="tab" id="roadmap"><section class="panel doc">$fullRoadmap</section></div>
  <div class="tab" id="detalle"><section class="panel doc">$body</section></div>
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
  show(location.hash.slice(1));
  setTimeout(() => location.reload(), 30000);
</script>
</body>
</html>
"@

Set-Content -Path $output -Value $html -Encoding utf8
}

function Get-Stamp {
    $files = @($source, $summary, $roadmap | Where-Object { Test-Path $_ } | Get-Item)
    $channelRegistry = Join-Path $channel 'CANAL.md'
    if (Test-Path -LiteralPath $channelRegistry -PathType Leaf) { $files += Get-Item -LiteralPath $channelRegistry }
    if (Test-Path $tasksDir) { $files += Get-ChildItem $tasksDir -Recurse -Filter '*.md' }
    $alignment = Join-Path $channel 'realinear\ESTADO.md'
    if (Test-Path -LiteralPath $alignment) { $files += Get-Item -LiteralPath $alignment }
    if (Test-Path -LiteralPath $integrationReads) { $files += Get-Item -LiteralPath $integrationReads }
    if (Test-Path -LiteralPath $integrationDir) {
        $files += Get-ChildItem -LiteralPath $integrationDir -Filter '*_integracion.md' -File -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $ideasStorePath -PathType Leaf) { $files += Get-Item -LiteralPath $ideasStorePath }
    if (Test-Path -LiteralPath $cloudAssignmentsPath -PathType Leaf) { $files += Get-Item -LiteralPath $cloudAssignmentsPath }
    ($files | ForEach-Object { "$($_.Name):$($_.LastWriteTimeUtc.Ticks)" }) -join '-'
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
