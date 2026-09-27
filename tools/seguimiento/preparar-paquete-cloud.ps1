# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0

[CmdletBinding()]
param(
    [string]$TaskId,
    [string]$Agent = 'Copilot',
    [Parameter(Mandatory)][string]$ChannelPath,
    [string]$RepoPath,
    [string]$OutputPath,
    [string]$CloudAgent = 'Agente cloud',
    [switch]$Interactive,
    [switch]$ConfirmReservation,
    [switch]$ReleaseReservation,
    [string]$RequestId
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression

$script:Utf8 = [System.Text.UTF8Encoding]::new($false, $true)
$script:CloudAssignmentsModulePath = Join-Path $PSScriptRoot 'CloudAssignments.psm1'
if (-not (Test-Path -LiteralPath $script:CloudAssignmentsModulePath -PathType Leaf)) {
    throw 'Cloud assignment tracking module is missing; refusing to prepare an untracked package.'
}
Import-Module $script:CloudAssignmentsModulePath -Force -ErrorAction Stop
$script:MaxFileBytes = 2MB
$script:MaxFiles = 40
$script:MaxExpandedBytes = 6MB

function Fail([string]$Message) { throw "Cloud package rejected: $Message" }

function Get-Sha256([byte[]]$Bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Quote-GitArgument([string]$Value) {
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    '"' + $escaped + '"'
}

function Invoke-Git([string]$WorkingDirectory, [string[]]$Arguments) {
    $globalConfig = [System.IO.Path]::GetTempFileName()
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = 'git'
    $start.WorkingDirectory = $WorkingDirectory
    $start.Arguments = ((@('-c', 'core.fsmonitor=false', '-c', 'core.quotepath=false') + $Arguments) |
        ForEach-Object { Quote-GitArgument $_ }) -join ' '
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardError = $true
    $start.RedirectStandardOutput = $true
    $start.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false, $true)
    $start.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false, $true)
    $start.EnvironmentVariables['GIT_CONFIG_NOSYSTEM'] = '1'
    $start.EnvironmentVariables['GIT_CONFIG_GLOBAL'] = $globalConfig
    $process = [System.Diagnostics.Process]::new()
    try {
        $process.StartInfo = $start
        if (-not $process.Start()) { Fail 'Could not start Git.' }
        $stdout = $process.StandardOutput.ReadToEnd()
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        [pscustomobject]@{ ExitCode = $process.ExitCode; Text = $stdout; Error = $stderr }
    } finally {
        $process.Dispose()
        if (Test-Path -LiteralPath $globalConfig) { Remove-Item -LiteralPath $globalConfig -Force }
    }
}

function Get-GitBlobBytes([string]$Repo, [string]$Commit, [string]$Path) {
    $globalConfig = [System.IO.Path]::GetTempFileName()
    $outputPath = [System.IO.Path]::GetTempFileName()
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = 'git'
    $start.WorkingDirectory = $Repo
    $start.Arguments = ((@('-c', 'core.fsmonitor=false', '-c', 'core.quotepath=false', '-C', $Repo, 'cat-file', 'blob', "$Commit`:$Path") |
        ForEach-Object { Quote-GitArgument $_ }) -join ' ')
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardError = $true
    $start.RedirectStandardOutput = $true
    $start.EnvironmentVariables['GIT_CONFIG_NOSYSTEM'] = '1'
    $start.EnvironmentVariables['GIT_CONFIG_GLOBAL'] = $globalConfig
    $process = [System.Diagnostics.Process]::new()
    try {
        $process.StartInfo = $start
        if (-not $process.Start()) { Fail "Could not read base Git blob for $Path." }
        $output = [System.IO.File]::Open($outputPath, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try { $process.StandardOutput.BaseStream.CopyTo($output) } finally { $output.Dispose() }
        $errorText = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { Fail "Could not read base Git blob for $Path`: $($errorText.Trim())" }
        ,([System.IO.File]::ReadAllBytes($outputPath))
    } finally {
        $process.Dispose()
        if (Test-Path -LiteralPath $globalConfig) { Remove-Item -LiteralPath $globalConfig -Force }
        if (Test-Path -LiteralPath $outputPath) { Remove-Item -LiteralPath $outputPath -Force }
    }
}

function Get-TaskFile([string]$Channel, [string]$Id) {
    if ($Id -notmatch '^(?<stage>[A-Za-z0-9]+)-(?<task>T\d+)$') { Fail 'TaskId must use <stage>-Txx form.' }
    $files = @(Get-ChildItem -LiteralPath (Join-Path $Channel "tareas\$($Matches.stage)") -File -Filter "$($Matches.task)-*.md" -ErrorAction SilentlyContinue)
    if ($files.Count -ne 1) { Fail "Expected one task card for $Id; found $($files.Count)." }
    $files[0].FullName
}

function Get-Field([string]$Text, [string]$Name) {
    $match = [regex]::Match($Text, "(?m)^\|\s*$([regex]::Escape($Name))\s*\|\s*(.*?)\s*\|\s*$")
    if (-not $match.Success) { Fail "Task card is missing its $Name field." }
    $match.Groups[1].Value.Trim()
}

function Get-AllowedPaths([string]$Text) {
    $field = Get-Field $Text 'Archivos'
    $tokens = @([regex]::Matches($field, '`([^`]+)`') | ForEach-Object { $_.Groups[1].Value })
    if (-not $tokens.Count) { Fail 'The task card has no literal backtick-delimited allowlist.' }
    $exact = [System.Collections.Generic.List[string]]::new()
    $prefixes = [System.Collections.Generic.List[string]]::new()
    foreach ($token in $tokens) {
        if ($token -match '[*?]' -or $token -match '(^|[\\/])\.\.([\\/]|$)') { Fail 'Allowlist entries must be literal paths without traversal or globs.' }
        $path = $token.Replace('\', '/')
        if ($path -match '(^|/)(?:\.local|\.git|tareas|parches|bandejas|realinear|reservas-paquete-cloud|validacion-importador-[^/]*)(?:/|$)' -or
            $path -match '(^|/)(?:LOG\.md|RESUMEN\.md|CANAL\.md)$' -or
            $path -match '(?i)(^|/)\.env(?:\.[^/]*)?$|(^|/)(?:id_rsa|[^/]*\.(?:pem|pfx|p12|key))$') {
            Fail "Private, channel, credential, or generated paths cannot be packaged: $path"
        }
        if ($path -match '(^/|^[A-Za-z]:|//|[\x00-\x1f\x7f<>:"|,])') { Fail "Allowlist path is not portable: $path" }
        if ($path.Length -gt 240) { Fail "Allowlist path exceeds the archive path limit: $path" }
        $parts = $path.TrimEnd('/').Split('/')
        if (@($parts | Where-Object { -not $_ -or $_ -eq '.' -or $_ -eq '..' }).Count -gt 0) { Fail "Invalid allowlist path: $path" }
        foreach ($part in $parts) {
            if ($part.EndsWith('.') -or $part.EndsWith(' ') -or $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
                Fail "Allowlist path contains a Windows-reserved filename: $path"
            }
        }
        if ($token.EndsWith('/') -or $token.EndsWith('\')) {
            $prefixes.Add(($path.TrimEnd('/') + '/'))
        } else {
            $exact.Add($path)
        }
    }
    [pscustomobject]@{ Exact = @($exact); Prefixes = @($prefixes) }
}

function Test-AllowedPath([string]$Path, $Allowlist) {
    if ($Allowlist.Exact -ccontains $Path) { return $true }
    foreach ($prefix in $Allowlist.Prefixes) {
        if ($Path.StartsWith($prefix, [StringComparison]::Ordinal)) { return $true }
    }
    $false
}

function Test-PrivateMaterial([string]$Text, [string]$Label) {
    if ($Text -match '(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|(?:gh[pousr]_[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|sk-(?:live|test)-[A-Za-z0-9]{16,})') {
        Fail "$Label contains a recognizable credential/private-key pattern; review or narrow the task allowlist."
    }
    if ($Text -match '(?i)(?:[A-Z]:\\Users\\[^\\\s`"<>]+|[A-Z]:\\Documents and Settings\\[^\\\s`"<>]+|/Users/[^/\s`"<>]+|/home/[^/\s`"<>]+|\\\\[^\\\s`"<>]+\\Users\\[^\\\s`"<>]+)') {
        Fail "$Label contains a user-specific absolute path; review or narrow the task allowlist."
    }
}

function Get-TaskCards([string]$Channel) {
    @(Get-ChildItem -LiteralPath (Join-Path $Channel 'tareas') -Recurse -File -Filter 'T*-*.md' |
        Where-Object { $_.Directory.Name -ne 'plantillas' })
}

function Get-CardAllowlist([string]$Path) {
    try {
        $allowlist = Get-AllowedPaths ([System.IO.File]::ReadAllText($Path))
        Add-Member -InputObject $allowlist -NotePropertyName Invalid -NotePropertyValue $false
        $allowlist
    }
    catch { [pscustomobject]@{ Exact = @(); Prefixes = @(); Invalid = $true } }
}

function Test-PathOverlap($Left, $Right) {
    foreach ($a in @($Left.Exact) + @($Left.Prefixes)) {
        foreach ($b in @($Right.Exact) + @($Right.Prefixes)) {
            $aPath = $a.TrimEnd('/')
            $bPath = $b.TrimEnd('/')
            if ($aPath -ceq $bPath -or $a.StartsWith($b, [StringComparison]::OrdinalIgnoreCase) -or $b.StartsWith($a, [StringComparison]::OrdinalIgnoreCase)) {
                return $true
            }
        }
    }
    $false
}

function Get-TaskIdentity([string]$Channel, [string]$Path) {
    $relative = [System.IO.Path]::GetRelativePath((Join-Path $Channel 'tareas'), $Path).Replace('\', '/')
    if ($relative -notmatch '^(?<stage>[^/]+)/(?<task>T\d+)-') { return $null }
    "$($Matches.stage)-$($Matches.task)"
}

function Test-Dependencies([string]$Channel, [string]$Text, [string]$Stage) {
    $dependencyField = Get-Field $Text 'Depende de'
    $ids = @([regex]::Matches($dependencyField, '(?:[A-Za-z0-9]+-)?T\d+') | ForEach-Object { $_.Value })
    foreach ($id in $ids) {
        $fullId = if ($id -match '-') { $id } else { "$Stage-$id" }
        $depPath = Get-TaskFile $Channel $fullId
        $depText = [System.IO.File]::ReadAllText($depPath)
        $depState = Get-Field $depText 'Estado'
        if ($depState -eq 'integrada') { continue }
        if ($depState -eq 'terminada') {
            $patch = Get-Field $depText 'Parche'
            if ($patch -in @('', '—', '-', 'n/a')) { continue }
            $patchName = [regex]::Match($patch, '(\d{3}-[A-Za-z][A-Za-z0-9-]*\.patch)')
            if ($patchName.Success -and (Test-Path -LiteralPath (Join-Path $Channel "parches\aplicados\$($patchName.Groups[1].Value)"))) { continue }
        }
        Fail "Dependency $fullId is $depState; it must be integrated or delivered with its patch already applied."
    }
}

function Get-TaskContent([string]$Text, [string]$Heading) {
    $pattern = "(?s)^## $([regex]::Escape($Heading))\s*\r?\n(.*?)(?=\r?\n## |\z)"
    $match = [regex]::Match($Text, $pattern, [System.Text.RegularExpressions.RegexOptions]::Multiline)
    if (-not $match.Success) { return '' }
    $value = $match.Groups[1].Value.Trim()
    $value = [regex]::Replace($value, '(?i)[A-Z]:\\[^\s`"<>|]+', '[local path omitted]')
    $value = [regex]::Replace($value, '(?i)(?:[A-Z]:\\|\\\\)[^\s`"<>|]+', '[local path omitted]')
    $value = [regex]::Replace($value, '(?i)/(?:Users|home|mnt)/[^\s`"<>|]+', '[local path omitted]')
    $value = [regex]::Replace($value, '(?i)\.local[/\\][^\s`"<>|]+', '[private channel path omitted]')
    $value
}

function Get-GitSnapshot([string]$Repo, $Allowlist) {
    $tracked = Invoke-Git $Repo @('-C', $Repo, 'ls-files', '-z')
    if ($tracked.ExitCode -ne 0) { Fail "Could not list tracked files: $($tracked.Error.Trim())" }
    $allPaths = @($tracked.Text.Split([char]0, [StringSplitOptions]::RemoveEmptyEntries))
    $selected = @($allPaths | Where-Object { Test-AllowedPath $_ $Allowlist } | Sort-Object -Unique)
    if (-not $selected.Count) { Fail 'The allowlist matched no tracked files.' }
    if ($selected.Count -gt $script:MaxFiles) { Fail "Allowlist includes more than $($script:MaxFiles) tracked files." }
    $statusPaths = @($Allowlist.Exact) + @($Allowlist.Prefixes | ForEach-Object { $_.TrimEnd('/') })
    foreach ($path in $statusPaths) {
        $status = Invoke-Git $Repo @('-C', $Repo, 'status', '--porcelain', '--untracked-files=all', '--', $path)
        if ($status.ExitCode -ne 0) { Fail "Could not check working-tree status for $path." }
        if ($status.Text.Trim()) { Fail "Allowed path has local changes or untracked files; refusing to package: $path" }
    }
    $head = Invoke-Git $Repo @('-C', $Repo, 'rev-parse', '--verify', 'HEAD^{commit}')
    if ($head.ExitCode -ne 0 -or $head.Text.Trim() -notmatch '^[0-9a-fA-F]{40}$') { Fail 'Could not resolve a full Git HEAD commit.' }
    $baseCommit = $head.Text.Trim().ToLowerInvariant()
    $files = [System.Collections.Generic.List[object]]::new()
    $total = 0L
    foreach ($path in $selected) {
        $entry = Invoke-Git $Repo @('-C', $Repo, 'ls-files', '--stage', '-z', '--', $path)
        if ($entry.ExitCode -ne 0) { Fail "Could not inspect Git entry for $path." }
        $metadata = ($entry.Text.Split([char]0, [StringSplitOptions]::RemoveEmptyEntries) | Select-Object -First 1)
        $metadataMatch = [regex]::Match([string]$metadata, '^(?<mode>\d{6}) (?<blob>[0-9a-f]{40}) 0\t')
        if (-not $metadataMatch.Success) { Fail "Could not parse tracked Git metadata for $path." }
        if ($metadataMatch.Groups['mode'].Value -notin @('100644', '100755')) { Fail "Only regular text files are allowed: $path" }
        $size = Invoke-Git $Repo @('-C', $Repo, 'cat-file', '-s', $metadataMatch.Groups['blob'].Value)
        if ($size.ExitCode -ne 0 -or $size.Text.Trim() -notmatch '^\d+$') { Fail "Could not read Git blob size for $path." }
        if ([long]$size.Text.Trim() -gt $script:MaxFileBytes) { Fail "Allowed file exceeds the 2 MiB limit: $path" }
        $absolute = Join-Path $Repo ($path.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $absolute -PathType Leaf)) { Fail "Tracked allowed file is missing: $path" }
        $ancestor = $Repo
        foreach ($segment in $path.Split('/')) {
            $ancestor = Join-Path $ancestor $segment
            if (Test-Path -LiteralPath $ancestor) {
                $attributes = [System.IO.File]::GetAttributes($ancestor)
                if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { Fail "Reparse-point source is not allowed: $path" }
            }
        }
        $bytes = Get-GitBlobBytes $Repo $baseCommit $path
        if ($bytes.Length -gt $script:MaxFileBytes) { Fail "Allowed file exceeds the 2 MiB limit: $path" }
        try { $text = $script:Utf8.GetString($bytes) } catch { Fail "Only UTF-8 text files are allowed: $path" }
        if ($text.Contains([char]0)) { Fail "Binary content is not allowed: $path" }
        Test-PrivateMaterial $text $path
        $total += $bytes.Length
        if ($total -gt $script:MaxExpandedBytes) { Fail 'Selected files exceed the total uncompressed size limit.' }
        $files.Add([pscustomobject]@{ Path = $path; Bytes = $bytes; Sha256 = Get-Sha256 $bytes; Size = $bytes.Length })
    }
    [pscustomobject]@{ BaseCommit = $baseCommit; Files = @($files); TotalBytes = $total }
}

function New-RequestDocument([string]$TaskId, [string]$RequestId, [string]$BaseCommit, [string]$Agent, [string]$Text, $Files, [string]$Channel) {
    $objective = Get-TaskContent $Text 'Objetivo'
    $criteria = Get-TaskContent $Text 'Criterio de terminado'
    if (-not $objective -or -not $criteria) { Fail 'Task card must contain Objetivo and Criterio de terminado.' }
    Test-PrivateMaterial $objective 'Task objective'
    Test-PrivateMaterial $criteria 'Task acceptance criteria'
    $allowed = @($Files | ForEach-Object { "- ``$($_.Path)`` (SHA-256 ``$($_.Sha256)``)" }) -join "`n"
    $dependencies = Get-Field $Text 'Depende de'
    $stagePath = Join-Path $Channel "tareas\$($TaskId.Split('-')[0])\ETAPA.md"
    $stageText = [System.IO.File]::ReadAllText($stagePath)
    if ($stageText -notmatch '(?im)^\|\s*Autorizada\s*\|\s*s[ií]\s*\|') { Fail 'Stage is not explicitly authorized in ETAPA.md.' }
    $taskMarkdown = @"
# Cloud task request

This ZIP is a scoped work request. It is not evidence of live process presence. The task is reserved in the local task board for the local operator while awaiting a cloud response.

- Task ID: ``$TaskId``
- Request ID: ``$RequestId``
- Base commit: ``$BaseCommit``
- Local operator attribution: ``$Agent`` (not a cloud identity claim)
- Declared dependencies: $dependencies

## Objective

$objective

## Acceptance criteria

$criteria

## Allowed files

Only edit files listed in the task card's allowlist. The supplied source snapshot is under ``files/``; its paths are repository-relative and its hashes are listed in ``manifest.json``. Local packaging excludes known private path/credential patterns, but cannot guarantee that arbitrary source text contains no secrets; the user must review the allowlist and snapshot before upload.

$allowed

## Return instructions

Return a new ZIP with ``manifest.json`` and either ``change.patch`` or only the changed files at their repository-relative paths. The response manifest must use schema_version 1 and include task_id ``$TaskId``, request_id ``$RequestId``, base_commit ``$BaseCommit``, agent, summary, files, and checks. Hashes and patch format must follow the request template. Include no unchanged context files. Do not include ``TASK.md``, scripts, provider credentials, channel data, local paths, or unrelated files. Do not contact or write to the local channel. Report accurately which checks you ran; checks are unverified until local review.

This package contains source text only. No script from the archive should be executed. The local user will validate and review the returned ZIP; successful validation only records a pending patch and does not apply, test, stage, commit, or publish it.
"@
    $taskBytes = $script:Utf8.GetBytes($taskMarkdown)
    if ($taskBytes.Length -gt 100KB) { Fail 'Generated TASK.md exceeds the 100 KiB context limit.' }
    [pscustomobject]@{
        TaskBytes = $taskBytes
        Manifest = [ordered]@{
            schema_version = 1
            kind = 'cloud-task-request'
            task_id = $TaskId
            request_id = $RequestId
            base_commit = $BaseCommit
            operator = $Agent
            created_utc = [DateTime]::UtcNow.ToString('o')
            files = @($Files | ForEach-Object { [ordered]@{ path = $_.Path; sha256 = $_.Sha256; size_bytes = $_.Size } })
        }
    }
}

function Add-ZipFile([System.IO.Compression.ZipArchive]$Zip, [string]$Name, [byte[]]$Bytes) {
    $entry = $Zip.CreateEntry($Name, [System.IO.Compression.CompressionLevel]::Optimal)
    $stream = $entry.Open()
    try { $stream.Write($Bytes, 0, $Bytes.Length) } finally { $stream.Dispose() }
}

function New-RequestZip([string]$Path, $Document, $Files) {
    $file = [System.IO.File]::Open($Path, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::ReadWrite, [System.IO.FileShare]::None)
    try {
        $zip = [System.IO.Compression.ZipArchive]::new($file, [System.IO.Compression.ZipArchiveMode]::Create, $false)
        try {
            Add-ZipFile $zip 'manifest.json' ($script:Utf8.GetBytes(($Document.Manifest | ConvertTo-Json -Depth 8)))
            Add-ZipFile $zip 'TASK.md' $Document.TaskBytes
            foreach ($item in $Files) { Add-ZipFile $zip "files/$($item.Path)" $item.Bytes }
        } finally { $zip.Dispose() }
    } finally { $file.Dispose() }
    $info = Get-Item -LiteralPath $Path
    if ($info.Length -gt 5MB) { Fail 'Generated request ZIP exceeds the 5 MiB compressed-size limit.' }
}

function Set-CardField([string]$Text, [string]$Name, [string]$Value) {
    $pattern = "(?m)^(\|\s*$([regex]::Escape($Name))\s*\|\s*).*?(\s*\|\s*)$"
    $matches = [regex]::Matches($Text, $pattern)
    if ($matches.Count -ne 1) { Fail "Expected exactly one $Name field in task card." }
    [regex]::Replace($Text, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{
        param($match)
        $match.Groups[1].Value + $Value + $match.Groups[2].Value
    }, 1)
}

function Test-TaskAvailable([string]$Text, [string]$AgentName) {
    if ((Get-Field $Text 'Estado') -cne 'disponible') { Fail 'Only a task explicitly marked disponible can be reserved.' }
    $assigned = Get-Field $Text 'Agente'
    if ($assigned -and $assigned -notin @('—', '-', 'n/a')) { Fail "Task already has an agent assignment: $assigned" }
    $prefer = Get-Field $Text 'Preferente'
    if ($prefer -and $prefer -notin @('—', '-', 'n/a', $AgentName)) {
        Write-Warning "Task preference is $prefer; the user selected registered operator $AgentName. The reservation will record $AgentName as responsible."
    }
}

function Test-NoActiveOverlap([string]$Channel, [string]$TaskPath, $Allowlist) {
    foreach ($card in Get-TaskCards $Channel) {
        if ($card.FullName -eq $TaskPath) { continue }
        $text = [System.IO.File]::ReadAllText($card.FullName)
        $state = [regex]::Match($text, '(?m)^\|\s*Estado\s*\|\s*(.*?)\s*\|\s*$')
        if (-not $state.Success -or $state.Groups[1].Value -notin @('en curso', 'pendiente')) { continue }
        $other = Get-CardAllowlist $card.FullName
        if ($other.Invalid -or (Test-PathOverlap $Allowlist $other)) {
            $id = Get-TaskIdentity $Channel $card.FullName
            Fail "Allowed paths overlap active task $id; no package was created."
        }
    }
}

function Test-Ready([string]$Channel, [string]$Repo, [string]$TaskPath, [string]$TaskText, [string]$TaskStage, [string]$AgentName) {
    Test-TaskAvailable $TaskText $AgentName
    Test-Dependencies $Channel $TaskText $TaskStage
    $allowlist = Get-AllowedPaths $TaskText
    Test-NoActiveOverlap $Channel $TaskPath $allowlist
    $snapshot = Get-GitSnapshot $Repo $allowlist
    [pscustomobject]@{ Allowlist = $allowlist; Snapshot = $snapshot }
}

function Get-ReservationPath([string]$Channel, [string]$Id) {
    Join-Path $Channel "reservas-paquete-cloud\$Id.json"
}

function Write-AtomicChannelFile([string]$Path, [string]$Text) {
    $temp = "$Path.$PID.tmp"
    try {
        [System.IO.File]::WriteAllText($temp, $Text, [System.Text.UTF8Encoding]::new($false))
        Move-Item -LiteralPath $temp -Destination $Path -Force
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    }
}

function Reserve-Task([string]$Channel, [string]$TaskPath, [string]$TaskIdValue, [string]$AgentName, [string]$RequestGuid,
    [string]$Repo, [string]$BaseCommit, $SnapshotFiles, [string]$OriginalText, [string]$OutputTemp, [string]$OutputFinal) {
    . (Join-Path $Channel 'bloqueo.ps1')
    $markerPath = Get-ReservationPath $Channel $TaskIdValue
    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $markerPath)) | Out-Null
    Enter-ChannelLock 'preparar-paquete-cloud.ps1 (reservar tarea)'
    $cardChanged = $false
    $markerWritten = $false
    $outputMoved = $false
    try {
        if (Test-Path -LiteralPath $markerPath) { Fail 'Task already has a cloud package reservation.' }
        $current = [System.IO.File]::ReadAllText($TaskPath)
        if ($current -cne $OriginalText) { Fail 'Task card changed while preparing the package; retry after review.' }
        $stage = $TaskIdValue.Split('-')[0]
        $ready = Test-Ready $Channel $Repo $TaskPath $current $stage $AgentName
        if ($ready.Snapshot.BaseCommit -cne $BaseCommit) { Fail 'Git HEAD changed while preparing the package; retry.' }
        if (Test-Path -LiteralPath $OutputFinal) { Fail 'Output ZIP already exists; choose a different path.' }
        $reserved = Set-CardField $current 'Estado' 'pendiente'
        $reserved = Set-CardField $reserved 'Agente' $AgentName
        $reserved = [regex]::Replace($reserved, '(?s)(## Dónde quedó\s*\r?\n).*?(?=\r?\n## Qué falta)', ('$1' + "Paquete cloud local preparado; request_id $RequestGuid; base $BaseCommit. Reservada por $AgentName mientras espera el ZIP de retorno."))
        $reserved = [regex]::Replace($reserved, '(?s)(## Qué falta\s*\r?\n).*?(?=\r?\n## Historial)', ('$1' + "Esperando ZIP de retorno. Importar con importar-paquete-nube.ps1 para $TaskIdValue y request_id $RequestGuid; la validación no aplica el parche."))
        $now = Get-Date -Format 'yyyy-MM-dd HH:mm'
        $reserved = $reserved.TrimEnd() + "`r`n| $now | $AgentName | pausada | Reserva cloud request_id $RequestGuid; esperando respuesta, sin afirmar presencia de proceso. |`r`n"
        $marker = [ordered]@{
            schema_version = 1
            task_id = $TaskIdValue
            request_id = $RequestGuid
            agent = $AgentName
            base_commit = $BaseCommit
            original_card = $current
            reserved_card = $reserved
            reserved_card_sha256 = Get-Sha256 ($script:Utf8.GetBytes($reserved))
            package_sha256 = Get-Sha256 ([System.IO.File]::ReadAllBytes($OutputTemp))
            package_name = [System.IO.Path]::GetFileName($OutputFinal)
            package_path = $OutputFinal
            files = @($SnapshotFiles | ForEach-Object { [ordered]@{ path = $_.Path; sha256 = $_.Sha256 } })
            created_utc = [DateTime]::UtcNow.ToString('o')
        }
        $markerStream = [System.IO.File]::Open($markerPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        $markerWritten = $true
        try {
            $markerBytes = $script:Utf8.GetBytes(($marker | ConvertTo-Json -Depth 8))
            $markerStream.Write($markerBytes, 0, $markerBytes.Length)
            $markerStream.Flush($true)
        } finally { $markerStream.Dispose() }
        Write-AtomicChannelFile $TaskPath $reserved
        $cardChanged = $true
        Move-Item -LiteralPath $OutputTemp -Destination $OutputFinal
        $outputMoved = $true
        $store = Get-CloudAssignmentsPath $Channel
        Set-CloudAssignmentState -StorePath $store -RequestId $RequestGuid -State ready_to_send `
            -Actor $AgentName -Detail 'El paquete ZIP quedó creado localmente y la reserva se registró. No se ha enviado al agente cloud.' `
            -PackageSha256 $marker.package_sha256 | Out-Null
    } catch {
        if ($cardChanged -and [System.IO.File]::ReadAllText($TaskPath) -ceq $reserved) {
            Write-AtomicChannelFile $TaskPath $OriginalText
        }
        if ($markerWritten -and (Test-Path -LiteralPath $markerPath)) { Remove-Item -LiteralPath $markerPath -Force }
        if ($outputMoved -and (Test-Path -LiteralPath $OutputFinal)) { Remove-Item -LiteralPath $OutputFinal -Force }
        throw
    } finally { Exit-ChannelLock }
    Write-Host "Reserved $TaskIdValue for operator $AgentName. Status is board-derived (pendiente), not live presence."
    Write-Host "Request ID: $RequestGuid"
    Write-Host "Package: $OutputFinal"
    Write-Host "Base: $BaseCommit"
}

function Release-Task([string]$Channel, [string]$TaskPath, [string]$TaskIdValue, [string]$AgentName, [string]$RequestGuid) {
    if ($RequestGuid -notmatch '^[0-9a-fA-F]{32}$') { Fail 'Release requires the 32-character request ID shown when the package was created.' }
    . (Join-Path $Channel 'bloqueo.ps1')
    $markerPath = Get-ReservationPath $Channel $TaskIdValue
    Enter-ChannelLock 'preparar-paquete-cloud.ps1 (liberar reserva)'
    try {
        if (-not (Test-Path -LiteralPath $markerPath)) { Fail 'No reservation exists for this task.' }
        $marker = [System.IO.File]::ReadAllText($markerPath) | ConvertFrom-Json -ErrorAction Stop
        if ($marker.task_id -cne $TaskIdValue -or $marker.request_id -cne $RequestGuid -or $marker.agent -cne $AgentName) {
            Fail 'Task, request ID, or responsible operator does not match the reservation.'
        }
        $current = [System.IO.File]::ReadAllText($TaskPath)
        if ((Get-Sha256 ($script:Utf8.GetBytes($current))) -cne $marker.reserved_card_sha256) {
            Fail 'Task card changed after reservation; refusing to overwrite it. Resolve the board manually.'
        }
        $restored = [string]$marker.original_card
        $originalState = Get-Field $restored 'Estado'
        $originalAgent = Get-Field $restored 'Agente'
        $restored = Set-CardField $restored 'Estado' $originalState
        $restored = Set-CardField $restored 'Agente' $originalAgent
        $date = Get-Date -Format 'yyyy-MM-dd HH:mm'
        $restored = $restored.TrimEnd() + "`r`n| $date | $AgentName | liberada | Reserva cloud $RequestGuid cancelada por el usuario; el paquete ya no es importable. |`r`n"
        Write-AtomicChannelFile $TaskPath $restored
        $assignmentPath = Get-CloudAssignmentsPath $Channel
        $assignment = @(Get-CloudAssignmentByRequestId -StorePath $assignmentPath -RequestId $RequestGuid)
        if ($assignment.Count) {
            Set-CloudAssignmentState -StorePath $assignmentPath -RequestId $RequestGuid -State cancelled `
                -Actor $AgentName -Detail 'El usuario canceló la reserva local; el ZIP no es importable.' | Out-Null
        }
        Remove-Item -LiteralPath $markerPath -Force
        Write-Host "Reservation cancelled for $TaskIdValue. The original task card was restored; no task was completed."
        $zipPath = [string]$marker.package_path
        if (Test-Path -LiteralPath $zipPath) {
            $zipHash = Get-Sha256 ([System.IO.File]::ReadAllBytes($zipPath))
            if ($zipHash -ceq $marker.package_sha256) {
                try {
                    Remove-Item -LiteralPath $zipPath -Force
                    Write-Host 'The unchanged generated ZIP was removed.'
                } catch {
                    Write-Warning "Reservation is cancelled but the unchanged ZIP could not be removed; its request_id is invalid and the importer will reject it. $($_.Exception.Message)"
                }
            } else {
                Write-Warning 'Reservation is cancelled; the generated ZIP was changed and was left in place. Its request_id is invalid.'
            }
        }
    } finally { Exit-ChannelLock }
}

function Invoke-Prepare {
    $channel = (Resolve-Path -LiteralPath $ChannelPath).Path
    if (-not $RepoPath) { $RepoPath = (Resolve-Path -LiteralPath (Join-Path $channel '..\..\..')).Path }
    else { $RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path }
    if ($TaskId -notmatch '^(?<stage>[A-Za-z0-9]+)-T\d+$') { Fail 'TaskId must use <stage>-Txx form.' }
    $stage = $Matches.stage
    $taskPath = Get-TaskFile $channel $TaskId
    if ($ReleaseReservation) {
        Release-Task $channel $taskPath $TaskId $Agent $RequestId
        return
    }
    if (-not $ConfirmReservation) { Fail 'Confirm reservation explicitly with -ConfirmReservation, or use the CMD to review and confirm.' }
    $registered = @([regex]::Matches([System.IO.File]::ReadAllText((Join-Path $channel 'CANAL.md')), '(?m)^## Para ([A-Za-z][A-Za-z0-9_-]*) \(sin leer\)$') | ForEach-Object { $_.Groups[1].Value })
    if ($Agent -notin $registered) { Fail 'Agent must be a currently registered channel operator.' }
    $original = [System.IO.File]::ReadAllText($taskPath)
    $ready = Test-Ready $channel $RepoPath $taskPath $original $stage $Agent
    $requestGuid = [guid]::NewGuid().ToString('N')
    if ($CloudAgent -notmatch '^[\p{L}\p{N}][\p{L}\p{N} ._-]{0,79}$') { Fail 'CloudAgent must be a short display name without control characters or paths.' }
    $outputDir = if ($OutputPath) { Split-Path -Parent ([System.IO.Path]::GetFullPath($OutputPath)) } else { Join-Path ([Environment]::GetFolderPath('UserProfile')) 'Downloads' }
    if (-not $outputDir) { $outputDir = (Get-Location).Path }
    [System.IO.Directory]::CreateDirectory($outputDir) | Out-Null
    $outputFinal = if ($OutputPath) { [System.IO.Path]::GetFullPath($OutputPath) } else {
        Join-Path $outputDir ("cloud-task-{0}-{1}.zip" -f $TaskId, (Get-Date -Format 'yyyyMMdd-HHmmss'))
    }
    $repoPrefix = [System.IO.Path]::GetFullPath($RepoPath).TrimEnd('\') + '\'
    if ($outputFinal.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)) { Fail 'Output ZIP must be outside the repository checkout.' }
    if (Test-Path -LiteralPath $outputFinal) { Fail 'Output ZIP already exists; choose a different path.' }
    $temp = Join-Path $outputDir ('.cloud-request-' + [guid]::NewGuid().ToString('N') + '.tmp')
    $assignmentPath = Get-CloudAssignmentsPath $channel
    $taskTitle = [regex]::Match($original, '(?m)^#\s+[^·\r\n]+·\s*(.+?)\s*$')
    if (-not $taskTitle.Success) { Fail 'Task card title is missing or malformed.' }
    $assignment = New-CloudAssignment -StorePath $assignmentPath -TaskId $TaskId -RequestId $requestGuid `
        -OriginAgent $Agent -DestinationAgent $CloudAgent -Title $taskTitle.Groups[1].Value `
        -Goal (Get-TaskContent $original 'Objetivo') -BaseCommit $ready.Snapshot.BaseCommit `
        -PackageName ([System.IO.Path]::GetFileName($outputFinal)) -Files $ready.Snapshot.Files `
        -Detail 'Inicio de preparación local confirmado por el operador; todavía no existe un paquete enviado.'
    try {
        try {
            $document = New-RequestDocument $TaskId $requestGuid $ready.Snapshot.BaseCommit $Agent $original $ready.Snapshot.Files $channel
            New-RequestZip $temp $document $ready.Snapshot.Files
            Reserve-Task $channel $taskPath $TaskId $Agent $requestGuid $RepoPath $ready.Snapshot.BaseCommit $ready.Snapshot.Files $original $temp $outputFinal
        } catch {
            $preparationError = $_.Exception.Message
            $currentAssignment = @(Get-CloudAssignmentByRequestId -StorePath $assignmentPath -RequestId $requestGuid)
            if ($currentAssignment.Count -and $currentAssignment[0].state -eq 'preparing_transfer') {
                try {
                    Set-CloudAssignmentState -StorePath $assignmentPath -RequestId $requestGuid -State preparation_failed `
                        -Actor $Agent -Detail 'La preparación o reserva local falló; el paquete no quedó disponible para envío.' | Out-Null
                } catch {
                    $trackingError = $_.Exception.Message
                    throw "Package preparation failed ($preparationError); assignment status could not be updated ($trackingError)."
                }
            }
            throw
        }
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    }
}

try {
    if ($Interactive) {
        Write-Host 'Preparacion local para trabajo cloud. No se conecta a ningun proveedor.'
        $TaskId = (Read-Host 'ID de tarea (etapa-Txx)').Trim()
        if (-not $TaskId) { throw 'TaskId is required.' }
        $AgentInput = (Read-Host "Agente local responsable [$Agent]").Trim()
        if ($AgentInput) { $Agent = $AgentInput }
        $CloudAgentInput = (Read-Host "Agente cloud destino [$CloudAgent]").Trim()
        if ($CloudAgentInput) { $CloudAgent = $CloudAgentInput }
        Write-Host "`n1. Preparar ZIP y reservar la tarea`n2. Cancelar una reserva intacta"
        switch ((Read-Host 'Seleccion').Trim()) {
            '1' {
                if ((Read-Host 'Confirma generar el paquete y reservar esta tarea? [s/N]').Trim() -notmatch '^(?i:s|si|sí)$') { return }
                $ConfirmReservation = $true
            }
            '2' {
                $RequestId = (Read-Host 'ID de solicitud mostrado al preparar el ZIP').Trim()
                if (-not $RequestId) { throw 'RequestId is required to release a reservation.' }
                if ((Read-Host 'Confirma cancelar esta reserva? [s/N]').Trim() -notmatch '^(?i:s|si|sí)$') { return }
                $ReleaseReservation = $true
            }
            default { throw 'Invalid selection.' }
        }
    }
    if (-not $TaskId) { Fail 'TaskId is required.' }
    if ($ReleaseReservation -and $ConfirmReservation) { Fail 'Choose either package preparation or reservation release.' }
    Invoke-Prepare
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
