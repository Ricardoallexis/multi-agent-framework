# Waits, without any AI model, until a condition holds and then runs an action (rule 36).
# Conditions (-Tipo): hora (-Valor 'HH:mm' or ISO date), archivo (path or wildcard that must exist),
#   reporte (an integration report OK newer than -Desde), entrada (a channel entry line in RESUMEN.md matching -Valor regex),
#   commit (git rev that must exist in -Repo), proceso (PID that must have exited), estado (-Valor 'ETAPA-Tnn=estado').
# Several pairs: -Tipo 'a,b' -Valor 'x;;y' (or arrays); all must hold. The only action is the fixed verb 'avisar':
# write a ready marker for an agent to consume. It never executes a supplied script or starts an application.
# -Tarea (ETAPA-Tnn) and -Agente say what waits and who is reactivated; the state is kept in orquestacion/esperas/activas/<id>.json
# (moved to .../terminadas when done) so Seguimiento and the GUI can show «En espera de activación». Every check and the outcome go to -Registro. Gives up after -MaximoHoras without running the action.
#
# SEG-T19: every check also writes an honest progress estimate, per condition and overall, with no AI model:
#   avance (0-100, or null when it cannot be estimated), estimado (ISO time the wait should end, or null) and base (Spanish text saying how
#   it was estimated; a percentage never goes without it).
#   - hora is exact (-Confianza carries the confidence of the time when it comes from a usage-limit reset: then the base says so instead of «exacta»).
#   - reporte (an integration round) has two phases. Until the round has started (patches applied, parches/ronda.txt, and the «Aplico sin commit» line
#     in RESUMEN.md) there is NO percentage. Then it is approximate: the median of the last 5 rounds read in RESUMEN.md, shown with the habitual band
#     (quartiles), capped at 95 %; past the median the time is dropped; past the 90th percentile the percentage stays frozen and the base says
#     «tarda más de lo habitual».
#   - estado, commit, entrada, proceso and archivo depend on an agent acting: null («sin estimación»).
#   - Overall: if ANY pending condition has no estimate, there is none; otherwise it is the one of the slowest pending condition.
#   - 100 % only exists with `met_at` (the time the condition was seen to hold); the -MaximoHoras deadline is the field `vence`, apart from progress.
# Robustness: every pass rewrites the heartbeat (ultima_comprobacion); an error in a check is logged and the state becomes `error` (with the
# message in `error`) instead of the process dying, and the next pass retries. The sweeper requires a heartbeat older
# than 2 intervals AND a missing process before marking a watcher lost.
#   -Probar does ONE check, prints the state as JSON and writes nothing (no state file, no log, no action): it is how the estimates are tested.
#   -Inicio sets when the wait began (default: now), for relaunching a wait that was already running.
[CmdletBinding(DefaultParameterSetName='Watch')]
param(
    [Parameter(Mandatory,ParameterSetName='Watch')][string[]]$Tipo,
    [Parameter(Mandatory,ParameterSetName='Watch')][string[]]$Valor,
    [Parameter(Mandatory,ParameterSetName='Watch')][ValidateSet('avisar')][string]$Accion,
    [Parameter(Mandatory,ParameterSetName='Watch')][string]$Motivo,
    [Parameter(Mandatory,ParameterSetName='Sweep')][switch]$Barrido,
    [datetime]$Desde = (Get-Date),
    [ValidateRange(1,120)][int]$IntervaloSegundos = 120,
    [ValidateRange(0.001,216)][double]$MaximoHoras = 12,
    [string]$Repo = '',
    [string]$Registro = '',
    [string]$Tarea = '',
    [string]$Agente = '',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$')][string]$Retencion = 'channel',
    [datetime]$Inicio = (Get-Date),
    [string]$Confianza = '',
    [switch]$Probar
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$channel = $PSScriptRoot
$scriptStart = Get-Date
$stateDir = Join-Path $channel 'orquestacion' 'esperas' 'activas'
# A path typed by a caller may use '\' (Windows habit); on Linux/macOS .NET would take it as part of a file name, so it becomes '/'.
function ConvertTo-NativePath([string]$p) { if ([IO.Path]::DirectorySeparatorChar -eq '/') { $p.Replace('\', '/') } else { $p } }
function Write-AtomicState([string]$path, [string]$json) {
    $temp = $path + '.' + [guid]::NewGuid().ToString('N') + '.tmp'
    try {
        [IO.File]::WriteAllText($temp, $json, [Text.UTF8Encoding]::new($false))
        for ($attempt = 0; ; $attempt++) {
            try { [IO.File]::Move($temp, $path, $true); break }
            catch [IO.IOException] {
                if ($attempt -ge 3) { throw }
                Start-Sleep -Milliseconds (50 * [math]::Pow(2, $attempt))
            }
        }
    } finally { if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) } }
}
if ($Barrido) {
    foreach ($file in Get-ChildItem -LiteralPath $stateDir -Filter '*.json' -ErrorAction SilentlyContinue) {
        try {
            $raw = [IO.File]::ReadAllText($file.FullName)
            $state = $raw | ConvertFrom-Json
            if ($state.estado -notin 'vigilando','error') { continue }
            $stale = ((Get-Date) - [datetime]$state.ultima_comprobacion).TotalSeconds -gt 2 * $state.intervalo_segundos
            if ($stale -and -not (Get-Process -Id $state.pid -ErrorAction SilentlyContinue) -and
                    [IO.File]::ReadAllText($file.FullName) -ceq $raw) {
                $state.estado = 'perdida'
                Write-AtomicState $file.FullName ($state | ConvertTo-Json -Depth 5)
                "PERDIDA: $($state.id)"
            }
        } catch { Write-Warning 'Estado ilegible; no se infiere permiso para relanzar.' }
    }
    return
}
# With pwsh -File, lists arrive as one string: types are split on ',' and values on ';;'.
$Tipo = @($Tipo | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$Valor = @($Valor | ForEach-Object { $_ -split ';;' } | Where-Object { $_ -ne '' })
foreach ($x in $Tipo) { if ($x -notin 'hora', 'archivo', 'reporte', 'entrada', 'commit', 'proceso', 'estado') { throw "Tipo desconocido: $x" } }
if ($Tipo.Count -ne $Valor.Count) { throw 'Cada -Tipo necesita su -Valor.' }
if (-not $Tipo.Count) { throw 'Se necesita al menos una condición.' }
if ('commit' -in $Tipo -and (-not $Repo -or -not (Test-Path -LiteralPath $Repo -PathType Container))) {
    throw 'La condición commit exige -Repo válido.'
}
if (-not $Probar -and ($Tarea -notmatch '^[A-Z][A-Z0-9]*-T[0-9]{2}$' -or $Agente -notmatch '^[A-Za-z]+(-[A-Za-z0-9]+)?$')) {
    throw 'Avisar exige una -Tarea y un -Agente válidos.'
}
function Get-TaskWait {
    $stage, $number = $Tarea -split '-'
    $cards = @(Get-ChildItem -LiteralPath (Join-Path $channel 'tareas' $stage) -Filter "$number-*.md" -ErrorAction SilentlyContinue)
    if ($cards.Count -ne 1) { throw 'No hay una ficha única para la espera.' }
    $text = [IO.File]::ReadAllText($cards[0].FullName)
    $status = [regex]::Match($text, '(?m)^\| Estado \| ([^|]+) \|').Groups[1].Value.Trim()
    $owner = [regex]::Match($text, '(?m)^\| Agente \| ([^|]+) \|').Groups[1].Value.Trim()
    $wait = [regex]::Match($text, '(?m)^\| Espera \| ([^|]+) \|').Groups[1].Value.Trim()
    if ($status -notin 'en curso','pendiente','disponible' -or ($owner -ne $Agente -and $owner -notin '—','-') -or $wait -in '', '—', '-') {
        throw 'La tarea ya no está retenida para ese agente.'
    }
    # The retained identity includes ownership and state, not only the question.
    @{ estado=$status; agente=$owner; espera=$wait } | ConvertTo-Json -Compress
}
$originalWait = if ($Probar) { '' } else { Get-TaskWait }
if (-not $Probar) {
    if (-not $Registro) { $Registro = Join-Path $channel 'orquestacion' 'esperas' ((Get-Date -Format 'yyyyMMdd-HHmmss') + '.log') }
    $logRoot = [IO.Path]::GetFullPath((Join-Path $channel 'orquestacion' 'esperas')).TrimEnd('\','/') + [IO.Path]::DirectorySeparatorChar
    $Registro = [IO.Path]::GetFullPath((ConvertTo-NativePath $Registro))
    $pathCase = if ([IO.Path]::DirectorySeparatorChar -eq '\') { [StringComparison]::OrdinalIgnoreCase } else { [StringComparison]::Ordinal }
    if (-not $Registro.StartsWith($logRoot, $pathCase) -or [IO.Path]::GetExtension($Registro) -ne '.log') {
        throw 'Registro debe ser un archivo .log dentro de orquestacion/esperas.'
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -LiteralPath $Registro) | Out-Null
}
function Log([string]$m) {
    if (-not $Probar) {
        try { "$(Get-Date -Format s) $m" | Add-Content -LiteralPath $Registro -Encoding utf8 }
        catch { Write-Warning 'No se pudo escribir el registro; se conserva la vigilancia sin ejecutar scripts.' }
    }
}
$identity = [Text.Encoding]::UTF8.GetBytes("$Tarea/$Retencion")
$waitId = 'ESP-' + [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($identity)).Substring(0,24).ToLowerInvariant()
if (-not $Probar) { New-Item -ItemType Directory -Force -Path $stateDir | Out-Null }
$stateFile = Join-Path $stateDir "$waitId.json"
$lease = $null
if (-not $Probar) {
    if (Test-Path -LiteralPath (Join-Path $channel 'orquestacion' 'esperas' 'terminadas' "$waitId.json")) { throw 'Esta retención ya terminó; no se reinicia automáticamente.' }
    $lease = [IO.File]::Open(($stateFile + '.lease'), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::Read)
}
$started = $Inicio
$deadline = $started.AddHours($MaximoHoras)

# --- SEG-T19: honest progress ---------------------------------------------------------------------------------------------------------------
$script:NoEstimate = 'sin estimación (depende de que un agente actúe)'
$script:MetAt = @{}   # condition index -> when it was first seen to hold
$script:RecentRounds = 5
function Get-HoraTarget([string]$v) { if ($v -match '^\d{1,2}:\d{2}$') { [datetime]::ParseExact($v, 'H:mm', [cultureinfo]::InvariantCulture) } else { [datetime]$v } }
# The time a finished integration report was written: its name carries it (yyyy-MM-dd_HHmmss_fff_ or the old yyyy-MM-dd_HHmm_).
function Get-ReportEnd([string]$name, [datetime]$fallback) {
    if ($name -match '^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})(\d{2})_(\d{3})_') {
        return [datetime]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3], [int]$Matches[4], [int]$Matches[5], [int]$Matches[6], [int]$Matches[7])
    }
    if ($name -match '^(\d{4})-(\d{2})-(\d{2})_(\d{2})(\d{2})_') {
        return [datetime]::new([int]$Matches[1], [int]$Matches[2], [int]$Matches[3], [int]$Matches[4], [int]$Matches[5], 0)
    }
    $fallback
}
# Integration rounds read in RESUMEN.md, in order: a round starts at its first «Aplico sin commit» line and ends at the «Pruebas de integracion: OK»
# line (the exact end is the time in the name of the report that line points to). A failed test or a commit closes the round without a duration.
# Returns the durations (seconds) of the past OK rounds, oldest first, and the start of the round still open (or $null).
function Get-RoundHistory {
    $spans = [System.Collections.Generic.List[double]]::new()
    $open = $null
    $file = Join-Path $channel 'RESUMEN.md'
    if (Test-Path -LiteralPath $file -PathType Leaf) {
        foreach ($line in [IO.File]::ReadAllLines($file, [Text.Encoding]::UTF8)) {
            if ($line -notmatch '^- (?<t>\d{4}-\d{2}-\d{2} \d{2}:\d{2}) - ') { continue }
            $at = [datetime]::ParseExact($Matches['t'], 'yyyy-MM-dd HH:mm', [cultureinfo]::InvariantCulture)
            if ($line -match 'Aplico sin commit') {
                if ($null -eq $open) { $open = $at }
            } elseif ($line -match 'Pruebas de integracion: (?<r>OK|FALLO)') {
                if ($Matches['r'] -eq 'OK' -and $null -ne $open) {
                    $end = if ($line -match 'pruebas/integracion/(?<f>\S+_integracion\.md)') { Get-ReportEnd $Matches['f'] $at } else { $at }
                    $seconds = ($end - $open).TotalSeconds
                    if ($seconds -gt 0 -and $seconds -le 4 * 3600) { $spans.Add($seconds) }
                }
                $open = $null
            } elseif ($line -match ' - Commit [0-9a-f]{7,40}\b') {
                $open = $null
            }
        }
    }
    [pscustomobject]@{ Spans = @($spans); Open = $open }
}
# Percentile (0..1) of a list, with linear interpolation between the two nearest values.
function Get-Percentile([double[]]$values, [double]$p) {
    $sorted = @($values | Sort-Object)
    $h = ($sorted.Count - 1) * $p
    $low = [int][Math]::Floor($h); $high = [int][Math]::Ceiling($h)
    $sorted[$low] + ($h - $low) * ($sorted[$high] - $sorted[$low])
}
# One condition: cumplida, met_at, avance (0-100 or $null), estimado (ISO or $null), base (how it was estimated) and confianza (hora only).
function Get-ConditionProgress([int]$index, [string]$t, [string]$v, [bool]$met, [datetime]$now) {
    $confidence = if ($t -eq 'hora' -and $Confianza) { $Confianza } else { $null }
    $make = { param($avance, $estimado, $base, $metAt = $null) [ordered]@{ tipo = $t; valor = $v; cumplida = $met; met_at = $metAt; avance = $avance; estimado = $estimado; base = $base; confianza = $confidence } }
    if ($met) {
        # 100 % only with the moment it was seen to hold.
        if (-not $script:MetAt.ContainsKey($index)) { $script:MetAt[$index] = $now }
        $at = $script:MetAt[$index].ToString('s')
        return & $make 100 $at 'cumplida' $at
    }
    $script:MetAt.Remove($index)
    switch ($t) {
        'hora' {
            $when = Get-HoraTarget $v
            $total = ($when - $started).TotalSeconds
            $pct = if ($total -gt 0) { [int][Math]::Max(0, [Math]::Min(99, [Math]::Floor(100 * ($now - $started).TotalSeconds / $total))) } else { 99 }
            $base = if ($confidence) { "la hora indicada ($($when.ToString('HH:mm'))) viene de un límite; confianza del restablecimiento: $confidence" } else { "exacta: la hora indicada ($($when.ToString('HH:mm')))" }
            return & $make $pct $when.ToString('s') $base
        }
        'reporte' {
            # Phase 1: no percentage until there is evidence that the integration started (a round open in parches/ronda.txt and in RESUMEN.md).
            $roundFile = Join-Path $channel 'parches' 'ronda.txt'
            $inRound = (Test-Path -LiteralPath $roundFile -PathType Leaf) -and @(Get-Content -LiteralPath $roundFile -Encoding utf8 | Where-Object { $_.Trim() }).Count -gt 0
            if (-not $inRound) { return & $make $null $null 'sin estimación (la integración aún no empezó: no hay ronda en curso)' }
            $history = Get-RoundHistory
            if ($null -eq $history.Open) { return & $make $null $null 'sin estimación (no consta cuándo empezó la ronda en RESUMEN.md)' }
            if ($history.Spans.Count -lt 3) { return & $make $null $null "sin estimación (solo $($history.Spans.Count) rondas anteriores medibles; hacen falta 3)" }
            # Phase 2: the median of the last rounds, with their habitual band (quartiles) and the 90th percentile.
            $recent = @($history.Spans | Select-Object -Last $script:RecentRounds)
            $median = Get-Percentile $recent 0.5
            $band = "lo habitual: entre $([int][Math]::Round((Get-Percentile $recent 0.25) / 60)) y $([int][Math]::Round((Get-Percentile $recent 0.75) / 60)) min"
            $p90 = Get-Percentile $recent 0.9
            $elapsed = ($now - $history.Open).TotalSeconds
            $how = "aproximada: ronda iniciada a las $($history.Open.ToString('HH:mm')); mediana de las últimas $($recent.Count) rondas $([int][Math]::Round($median / 60)) min ($band)"
            if ($elapsed -ge $p90) { return & $make 95 $null "$how; tarda más de lo habitual (pasó el percentil 90, $([int][Math]::Round($p90 / 60)) min): avance detenido en 95 % hasta que haya reporte" }
            if ($elapsed -ge $median) { return & $make 95 $null "$how; ya supera la mediana, tope 95 % hasta que haya reporte" }
            $pct = [int][Math]::Min(95, [Math]::Floor(100 * $elapsed / $median))
            return & $make $pct $history.Open.AddSeconds($median).ToString('s') $how
        }
        default { return & $make $null $null $script:NoEstimate }
    }
}
# The state object: the wait's fields plus the progress of each condition and the overall one. If ANY pending condition has no estimate there is
# none; otherwise it is the one of the slowest pending condition. In `error` the progress is not computed (it may be what failed).
function Get-StateObject([string]$status, [datetime]$next, [bool[]]$met, [string]$problem = '') {
    $now = Get-Date
    $avance = $null; $estimado = $null; $base = ''; $metAt = $null; $confidence = $null
    if ($status -eq 'error') {
        $conds = @(for ($i = 0; $i -lt $Tipo.Count; $i++) { [ordered]@{ tipo = $Tipo[$i]; valor = $Valor[$i]; cumplida = $false; met_at = $null; avance = $null; estimado = $null; base = 'sin estimación (la última comprobación falló)'; confianza = $null } })
        $base = 'sin estimación (la última comprobación falló; se reintenta)'
    } else {
        $conds = @(for ($i = 0; $i -lt $Tipo.Count; $i++) { Get-ConditionProgress $i $Tipo[$i] $Valor[$i] $met[$i] $now })
        $pending = @($conds | Where-Object { -not $_.cumplida })
        if ($status -eq 'vencida') {
            $base = 'vencida: se agotó el tiempo máximo sin cumplirse'
        } elseif (-not $pending.Count) {
            $metAt = (@($conds | ForEach-Object { [datetime]$_.met_at }) | Sort-Object -Descending | Select-Object -First 1).ToString('s')
            $avance = 100; $estimado = $metAt; $base = 'condiciones cumplidas'
        } else {
            $unknown = @($pending | Where-Object { $null -eq $_.avance })
            if ($unknown.Count -eq 1) {
                $base = $unknown[0].base
            } elseif ($unknown.Count -gt 1) {
                $base = "sin estimación: no se pueden estimar $(($unknown | ForEach-Object { $_.tipo }) -join ', ')"
            } else {
                $overdue = @($pending | Where-Object { $null -eq $_.estimado })
                $slow = if ($overdue.Count) { $overdue[0] } else { @($pending | Sort-Object { [datetime]$_.estimado } -Descending)[0] }
                $avance = $slow.avance; $estimado = $slow.estimado; $confidence = $slow.confianza
                $base = if ($pending.Count -gt 1) { "la condición más lenta ($($slow.tipo)): $($slow.base)" } else { $slow.base }
            }
        }
    }
    [ordered]@{ id = $waitId; motivo = $Motivo; tarea = $Tarea; agente = $Agente; estado = $status; condiciones = $conds
        avance = $avance; estimado = $estimado; base = $base; confianza = $confidence; met_at = $metAt; vence = $deadline.ToString('s')
        error = $(if ($problem) { $(if ($problem.Length -gt 200) { $problem.Substring(0, 200) } else { $problem }) } else { $null })
        inicio = $started.ToString('s'); ultima_comprobacion = $now.ToString('s'); proxima_comprobacion = $next.ToString('s'); pid = $PID; registro = $Registro
        accion = $Accion; retencion = $Retencion; intervalo_segundos = $IntervaloSegundos }
}
function Save-State([string]$status, [datetime]$next, [bool[]]$met, [string]$problem = '') {
    $obj = Get-StateObject $status $next $met $problem
    Write-AtomicState $stateFile ($obj | ConvertTo-Json -Depth 5)
}
function Close-State([string]$status, [bool[]]$met) {
    Save-State $status (Get-Date) $met
    $doneDir = Join-Path $channel 'orquestacion' 'esperas' 'terminadas'
    New-Item -ItemType Directory -Force -Path $doneDir | Out-Null
    Move-Item -LiteralPath $stateFile -Destination (Join-Path $doneDir "$waitId.json") -Force
    # Keep the lease file as a tombstone: a second process cannot reset this hold.
    if ($lease) { $lease.Dispose() }
}
function Test-One([string]$t, [string]$v) {
    switch ($t) {
        'hora' { return (Get-Date) -ge (Get-HoraTarget $v) }
        'archivo' { return [bool](Get-ChildItem -Path (ConvertTo-NativePath $v) -ErrorAction SilentlyContinue | Select-Object -First 1) }
        'reporte' {
            $dir = Join-Path $channel 'pruebas' 'integracion'
            $new = Get-ChildItem -LiteralPath $dir -Filter '*_integracion.md' -File | Where-Object { $_.LastWriteTime -gt $Desde }
            foreach ($f in $new) { if (Select-String -LiteralPath $f.FullName -Pattern 'Resultado global:\s*\*\*OK\*\*' -Quiet) { return $true } }
            return $false
        }
        'entrada' { return [bool](Select-String -LiteralPath (Join-Path $channel 'RESUMEN.md') -Pattern $v -Quiet) }
        'commit' { & git -C $Repo rev-parse --verify --quiet "$v^{commit}" *> $null; return $LASTEXITCODE -eq 0 }
        'proceso' { return -not (Get-Process -Id ([int]$v) -ErrorAction SilentlyContinue) }
        'estado' {
            if ($v -notmatch '^([A-Z][A-Z0-9]*)-(T\d+)=(.+)$') { throw "estado espera 'ETAPA-Tnn=estado': $v" }
            $card = Get-ChildItem -LiteralPath (Join-Path $channel 'tareas' $Matches[1]) -Filter "$($Matches[2])-*.md" | Select-Object -First 1
            if (-not $card) { return $false }
            return [bool](Select-String -LiteralPath $card.FullName -Pattern ('^\| Estado \| ' + [regex]::Escape($Matches[3]) + ' \|') -Quiet)
        }
    }
}
# Every condition is checked on each pass (no early exit) so that the state can say which are already met.
function Test-All { [bool[]]@(for ($i = 0; $i -lt $Tipo.Count; $i++) { [bool](Test-One $Tipo[$i] $Valor[$i]) }) }

if ($Probar) {
    $met = Test-All
    $status = if ($met -contains $false) { 'vigilando' } else { 'cumplida' }
    Get-StateObject $status (Get-Date).AddSeconds($IntervaloSegundos) $met | ConvertTo-Json -Depth 5
    exit 0
}

Log "inicio · motivo: $Motivo · condiciones: $(( 0..($Tipo.Count-1) | ForEach-Object { "$($Tipo[$_])=$($Valor[$_])" }) -join '; ') · acción: $Accion"
$lastMet = [bool[]]@(for ($i = 0; $i -lt $Tipo.Count; $i++) { $false })
while ((Get-Date) -lt $deadline) {
    try {
        $met = Test-All
        $lastMet = $met
        if (-not ($met -contains $false)) {
            # A shared exclusive lock with channel integration closes the check/action race.
            . (Join-Path $channel 'bloqueo.ps1')
            Enter-ChannelLock 'vigilante avisar' 1
            try {
                $round = Join-Path $channel 'parches' 'ronda.txt'
                if ((Test-Path -LiteralPath $round) -and [IO.File]::ReadAllText($round).Trim()) { throw 'integration_active' }
                $met = Test-All  # observations from before the lock are not authority to act
                if ((Get-Date) -ge $deadline) { Close-State 'vencida' $met; exit 2 }
                if ($met -contains $false) { throw 'conditions_changed' }
                try { $currentWait = Get-TaskWait } catch {
                    Close-State 'detenida' $met
                    exit 3
                }
                if ($currentWait -cne $originalWait) {
                    Close-State 'detenida' $met
                    Log 'la retención cambió; no se avisa'
                    exit 3
                }
                $readyDir = Join-Path $channel 'orquestacion' 'esperas' 'listas'
                New-Item -ItemType Directory -Force -Path $readyDir | Out-Null
                $ready = @{ tarea=$Tarea; agente=$Agente; retencion=$Retencion; accion='avisar'; desde=(Get-Date -Format s) }
                if ((Get-Date) -ge $deadline) { Close-State 'vencida' $met; exit 2 }
                Close-State 'cumplida' $met
                # Commit the tombstone before publishing, then publish once. A failed
                # publication needs operator attention; it must not be repeated.
                $noticeFile = [IO.File]::Open((Join-Path $readyDir "$waitId.json"), [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
                try {
                    $noticeBytes = [Text.Encoding]::UTF8.GetBytes(($ready | ConvertTo-Json))
                    $noticeFile.Write($noticeBytes, 0, $noticeBytes.Length)
                } finally { $noticeFile.Dispose() }
                Log 'condiciones cumplidas · aviso listo; el agente debe retomarlo'
            } finally { Exit-ChannelLock }
            "CUMPLIDA: $Motivo"
            exit 0
        }
        Save-State 'vigilando' ((Get-Date).AddSeconds($IntervaloSegundos)) $met
    } catch {
        if (Test-Path -LiteralPath (Join-Path $channel 'orquestacion' 'esperas' 'terminadas' "$waitId.json")) {
            Log 'error después del cierre; no se repite el aviso'
            $closedFile = Join-Path $channel 'orquestacion' 'esperas' 'terminadas' "$waitId.json"
            try {
                $closed = [IO.File]::ReadAllText($closedFile) | ConvertFrom-Json
                $closed.estado = 'atencion'
                $closed.error = 'aviso_no_confirmado; revisar manualmente, no repetir'
                Write-AtomicState $closedFile ($closed | ConvertTo-Json -Depth 5)
            } catch { Log 'no se pudo anotar atención en el estado cerrado' }
            exit 3
        }
        # An error in a check must not kill the watcher silently: it is logged, the state says `error` (heartbeat included) and the next pass retries.
        Log "error en la comprobación: $($_.Exception.Message)"
        try {
            if ($_.Exception.Message -in 'integration_active','conditions_changed') { Save-State 'vigilando' ((Get-Date).AddSeconds($IntervaloSegundos)) $lastMet }
            else { Save-State 'error' ((Get-Date).AddSeconds($IntervaloSegundos)) $lastMet $_.Exception.Message }
        }
        catch { Log "no se pudo escribir el estado: $($_.Exception.Message)" }
    }
    Start-Sleep -Seconds $IntervaloSegundos
}
try { $final = Test-All } catch { $final = $lastMet }
try { Close-State 'vencida' $final } catch { Log "no se pudo cerrar el estado: $($_.Exception.Message)" }
Log 'tiempo máximo agotado; no se ejecutó la acción'
"VENCIDA: $Motivo"
exit 2
