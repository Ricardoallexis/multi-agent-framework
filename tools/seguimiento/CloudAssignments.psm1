# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0

Set-StrictMode -Version Latest
$script:Utf8 = [System.Text.UTF8Encoding]::new($false, $true)
$script:Transitions = @{
    preparing_transfer = @('ready_to_send', 'preparation_failed', 'cancelled')
    ready_to_send = @('sent_to_cloud', 'human_review', 'cancelled')
    sent_to_cloud = @('in_execution', 'waiting_response', 'human_review', 'cancelled')
    in_execution = @('waiting_response', 'human_review', 'cancelled')
    waiting_response = @('in_execution', 'human_review', 'cancelled')
    human_review = @('integrating_result', 'completed', 'cancelled')
    integrating_result = @('human_review', 'completed', 'cancelled')
    preparation_failed = @()
    completed = @()
    cancelled = @()
}
$script:StateLabels = @{
    preparing_transfer = 'Preparando transferencia'
    ready_to_send = 'Lista para enviar · no enviada'
    sent_to_cloud = 'Enviada · reportado por operador'
    in_execution = 'Ejecución declarada · sin señal en vivo'
    waiting_response = 'Esperando respuesta · reportado por operador'
    human_review = 'Revisión humana'
    integrating_result = 'Integrando resultado · reportado por operador'
    completed = 'Completada · confirmación manual'
    cancelled = 'Cancelada · confirmación manual'
    preparation_failed = 'Falló la preparación'
}

function Get-CloudAssignmentsPath([string]$SystemRoot) {
    if (-not $SystemRoot) { throw 'SystemRoot is required.' }
    Join-Path $SystemRoot 'asignaciones-cloud.json'
}

function Get-CloudSafeText([object]$Value, [string]$Name, [int]$MaxLength, [bool]$Required = $true) {
    if ($Value -isnot [string]) { throw "$Name must be text." }
    $text = $Value.Trim()
    if (($Required -and -not $text) -or $text.Length -gt $MaxLength -or
        $text -match '[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]') {
        throw "$Name is empty, too long, or contains forbidden control characters."
    }
    [regex]::Replace($text, '(?i)(?:[A-Z]:\\|\\\\)[^\s`"<>|]+|/(?:Users|home|mnt)/[^\s`"<>|]+|\.local[/\\][^\s`"<>|]+', '[local path omitted]')
}

function ConvertTo-CloudTimestampText([object]$Value, [string]$Name) {
    try {
        if ($Value -is [DateTimeOffset]) {
            $timestamp = $Value
        } elseif ($Value -is [DateTime]) {
            $timestamp = [DateTimeOffset]$Value
        } elseif ($Value -is [string]) {
            $timestamp = [DateTimeOffset]::Parse($Value, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None)
        } else {
            throw 'Timestamp must be an ISO-8601 date.'
        }
        $timestamp.ToUniversalTime().ToString('o')
    } catch {
        throw "$Name is not a valid timestamp."
    }
}

function Read-CloudAssignmentsUnlocked([string]$StorePath) {
    if (-not (Test-Path -LiteralPath $StorePath -PathType Leaf)) { return @() }
    try {
        $raw = [System.IO.File]::ReadAllText([System.IO.Path]::GetFullPath($StorePath), $script:Utf8)
        $data = ConvertFrom-Json -InputObject $raw -ErrorAction Stop
    } catch {
        throw "Cloud assignment store is unavailable or malformed; refusing to treat it as empty. $($_.Exception.Message)"
    }
    if ($data.schema_version -ne 1 -or $data.assignments -isnot [array]) {
        throw 'Cloud assignment store has an unsupported or invalid schema.'
    }
    $seen = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
    foreach ($assignment in $data.assignments) {
        if ($assignment -isnot [System.Management.Automation.PSCustomObject] -or
            $assignment.id -notmatch '^CLOUD-[0-9a-f]{32}$' -or -not $seen.Add([string]$assignment.id) -or
            $assignment.request_id -notmatch '^[0-9a-f]{32}$' -or
            $assignment.task_id -notmatch '^[A-Za-z0-9]+-T\d+$' -or
            $assignment.state -notin $script:Transitions.Keys -or
            $assignment.origin_agent -isnot [string] -or $assignment.destination_agent -isnot [string] -or
            $assignment.follow_up_agent -isnot [string] -or $assignment.title -isnot [string] -or
            $assignment.goal -isnot [string] -or $assignment.base_commit -notmatch '^[0-9a-f]{40}$' -or
            $assignment.package_name -isnot [string] -or $assignment.files -isnot [array] -or
            $assignment.package_sha256 -isnot [string] -or
            ($assignment.package_sha256 -and $assignment.package_sha256 -notmatch '^[0-9a-f]{64}$') -or
            $assignment.context_summary -isnot [string] -or
            $assignment.history -isnot [array] -or -not $assignment.history.Count) {
            throw 'Cloud assignment store contains an invalid entry.'
        }
        foreach ($file in $assignment.files) {
            if ($file -isnot [System.Management.Automation.PSCustomObject] -or
                $file.path -isnot [string] -or $file.path -notmatch '^[^\\/:]+(?:/[^\\/:]+)*$' -or
                $file.path -match '(^|/)\.\.(/|$)|[\x00-\x1f\x7f]' -or
                $file.sha256 -notmatch '^[0-9a-f]{64}$') {
                throw "Cloud assignment $($assignment.id) contains an invalid file reference."
            }
        }
        $updatedState = $null
        foreach ($event in $assignment.history) {
            if ($event -isnot [System.Management.Automation.PSCustomObject] -or
                $event.state -notin $script:Transitions.Keys -or $event.actor -isnot [string] -or
                $event.detail -isnot [string]) {
                throw "Cloud assignment $($assignment.id) contains an invalid history event."
            }
            $event.occurred_utc = ConvertTo-CloudTimestampText $event.occurred_utc 'History timestamp'
            if (($null -eq $updatedState -and $event.state -cne 'preparing_transfer') -or
                ($null -ne $updatedState -and $event.state -notin $script:Transitions[$updatedState])) {
                throw "Cloud assignment $($assignment.id) contains an invalid state history."
            }
            $updatedState = [string]$event.state
        }
        $assignment.created_utc = ConvertTo-CloudTimestampText $assignment.created_utc 'Created timestamp'
        $assignment.updated_utc = ConvertTo-CloudTimestampText $assignment.updated_utc 'Updated timestamp'
        if ($updatedState -cne $assignment.state) {
            throw "Cloud assignment $($assignment.id) state does not match its history."
        }
    }
    @($data.assignments)
}

function Enter-CloudAssignmentsLock([string]$Path) {
    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $bytes = $script:Utf8.GetBytes("pid=$PID")
        $stream.Write($bytes, 0, $bytes.Length)
        $stream.Dispose()
    } catch [System.IO.IOException] {
        throw 'Cloud assignments are being edited by another process; retry later. A stale lock requires explicit user recovery.'
    }
}

function Save-CloudAssignmentsUnlocked([string]$StorePath, [object[]]$Assignments) {
    $path = [System.IO.Path]::GetFullPath($StorePath)
    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $path)) | Out-Null
    $data = [ordered]@{
        schema_version = 1
        assignments = @($Assignments)
    }
    $temp = "$path.$PID.$([guid]::NewGuid().ToString('N')).tmp"
    try {
        [System.IO.File]::WriteAllText($temp, (($data | ConvertTo-Json -Depth 12) + "`n"), $script:Utf8)
        Move-Item -LiteralPath $temp -Destination $path -Force
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    }
}

function Invoke-CloudAssignmentMutation([string]$StorePath, [scriptblock]$Mutation) {
    $path = [System.IO.Path]::GetFullPath($StorePath)
    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $path)) | Out-Null
    $lockPath = "$path.lock"
    Enter-CloudAssignmentsLock $lockPath
    try {
        $items = [System.Collections.Generic.List[object]]::new()
        foreach ($item in (Read-CloudAssignmentsUnlocked $path)) { $items.Add($item) }
        $result = & $Mutation $items
        Save-CloudAssignmentsUnlocked $path $items.ToArray()
        $result
    } finally {
        Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue
    }
}

function Get-CloudAssignments {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$StorePath)
    Read-CloudAssignmentsUnlocked $StorePath
}

function Get-CloudAssignmentByRequestId {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$StorePath, [Parameter(Mandatory)][string]$RequestId)
    @(Read-CloudAssignmentsUnlocked $StorePath | Where-Object { $_.request_id -ceq $RequestId } | Select-Object -First 1)
}

function New-CloudAssignment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$StorePath,
        [Parameter(Mandatory)][string]$TaskId,
        [Parameter(Mandatory)][string]$RequestId,
        [Parameter(Mandatory)][string]$OriginAgent,
        [Parameter(Mandatory)][string]$DestinationAgent,
        [Parameter(Mandatory)][string]$Title,
        [Parameter(Mandatory)][string]$Goal,
        [Parameter(Mandatory)][string]$BaseCommit,
        [Parameter(Mandatory)][string]$PackageName,
        [Parameter(Mandatory)][object[]]$Files,
        [Parameter(Mandatory)][string]$Detail
    )
    if ($TaskId -notmatch '^[A-Za-z0-9]+-T\d+$' -or $RequestId -notmatch '^[0-9a-f]{32}$' -or
        $BaseCommit -notmatch '^[0-9a-f]{40}$') {
        throw 'Task ID, request ID, or base commit is invalid.'
    }
    if ($PackageName -match '[\\/:]' -or $PackageName -match '[\x00-\x1f\x7f]') { throw 'PackageName must be a file name only.' }
    $entry = [pscustomobject]@{
        id = "CLOUD-$RequestId"
        task_id = $TaskId
        request_id = $RequestId
        title = Get-CloudSafeText $Title 'Title' 240
        goal = Get-CloudSafeText $Goal 'Goal' 6000
        origin_agent = Get-CloudSafeText $OriginAgent 'OriginAgent' 80
        destination_agent = Get-CloudSafeText $DestinationAgent 'DestinationAgent' 80
        follow_up_agent = Get-CloudSafeText $OriginAgent 'FollowUpAgent' 80
        base_commit = $BaseCommit.ToLowerInvariant()
        package_name = Get-CloudSafeText $PackageName 'PackageName' 240
        package_sha256 = ''
        context_summary = 'TASK.md (objetivo, criterios, dependencias y reglas de retorno), manifest.json y archivos fuente permitidos con sus hashes; no incluye historial completo del canal.'
        files = @($Files | ForEach-Object {
            if ($_.Path -notmatch '^[^\\/:]+(?:/[^\\/:]+)*$' -or $_.Path -match '(^|/)\.\.(/|$)' -or $_.Sha256 -notmatch '^[0-9a-f]{64}$') {
                throw 'A package file reference is invalid.'
            }
            [pscustomobject]@{ path = [string]$_.Path; sha256 = [string]$_.Sha256 }
        })
        state = 'preparing_transfer'
        created_utc = [DateTime]::UtcNow.ToString('o')
        updated_utc = [DateTime]::UtcNow.ToString('o')
        history = @([pscustomobject]@{
            state = 'preparing_transfer'
            actor = Get-CloudSafeText $OriginAgent 'OriginAgent' 80
            occurred_utc = [DateTime]::UtcNow.ToString('o')
            detail = Get-CloudSafeText $Detail 'Detail' 1000
        })
    }
    if (-not $entry.files.Count) { throw 'An assignment must include at least one package file.' }
    Invoke-CloudAssignmentMutation $StorePath {
        param($items)
        if (@($items | Where-Object { $_.request_id -ceq $RequestId }).Count) {
            throw "A cloud assignment already exists for request $RequestId."
        }
        $items.Add($entry)
        $entry
    }
}

function Set-CloudAssignmentState {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$StorePath,
        [Parameter(Mandatory)][string]$RequestId,
        [Parameter(Mandatory)][ValidateSet('preparing_transfer', 'ready_to_send', 'sent_to_cloud', 'in_execution', 'waiting_response', 'human_review', 'integrating_result', 'completed', 'cancelled', 'preparation_failed')][string]$State,
        [Parameter(Mandatory)][string]$Actor,
        [Parameter(Mandatory)][string]$Detail,
        [string]$PackageSha256 = ''
    )
    $safeActor = Get-CloudSafeText $Actor 'Actor' 80
    $safeDetail = Get-CloudSafeText $Detail 'Detail' 1000
    if ($PackageSha256 -and ($State -ne 'ready_to_send' -or $PackageSha256 -notmatch '^[0-9a-f]{64}$')) {
        throw 'PackageSha256 is valid only for ready_to_send and must be a 64-digit SHA-256.'
    }
    Invoke-CloudAssignmentMutation $StorePath {
        param($items)
        $item = $items | Where-Object { $_.request_id -ceq $RequestId } | Select-Object -First 1
        if (-not $item) { throw "Cloud assignment not found for request $RequestId." }
        if ($State -notin $script:Transitions[[string]$item.state]) {
            throw "Invalid cloud assignment transition: $($item.state) -> $State."
        }
        $item.state = $State
        if ($PackageSha256) { $item.package_sha256 = $PackageSha256.ToLowerInvariant() }
        $item.updated_utc = [DateTime]::UtcNow.ToString('o')
        $item.history = @($item.history) + @([pscustomobject]@{
            state = $State
            actor = $safeActor
            occurred_utc = [DateTime]::UtcNow.ToString('o')
            detail = $safeDetail
        })
        $item
    }
}

function Get-CloudAssignmentNextStates([string]$State) {
    if (-not $script:Transitions.ContainsKey($State)) { throw "Unknown cloud assignment state: $State." }
    @($script:Transitions[$State])
}

function Get-CloudAssignmentStateLabel([string]$State) {
    if (-not $script:StateLabels.ContainsKey($State)) { throw "Unknown cloud assignment state: $State." }
    $script:StateLabels[$State]
}

Export-ModuleMember -Function Get-CloudAssignmentsPath, Get-CloudAssignments, Get-CloudAssignmentByRequestId, New-CloudAssignment, Set-CloudAssignmentState, Get-CloudAssignmentNextStates, Get-CloudAssignmentStateLabel
