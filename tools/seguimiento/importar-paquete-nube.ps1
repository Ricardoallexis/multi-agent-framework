# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0

[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PackagePath,
    [Parameter(Mandatory)][string]$TaskId,
    [string]$Agent,
    [string]$Authorization,
    [switch]$ValidateOnly,
    [switch]$ConfirmImport,
    [Parameter(Mandatory)][string]$ChannelPath,
    [string]$RepoPath
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$script:MaxArchiveBytes = 5MB
$script:MaxExpandedBytes = 6MB
$script:MaxEntryBytes = 2MB
$script:MaxManifestBytes = 100KB
$script:MaxEntries = 41
$script:MaxCompressionRatio = 100
$script:Utf8Strict = [System.Text.UTF8Encoding]::new($false, $true)
$script:CloudAssignmentsModulePath = Join-Path $PSScriptRoot 'CloudAssignments.psm1'
if (-not (Test-Path -LiteralPath $script:CloudAssignmentsModulePath -PathType Leaf)) {
    throw 'Cloud assignment tracking module is missing; refusing to import an untracked cloud response.'
}
Import-Module $script:CloudAssignmentsModulePath -Force -ErrorAction Stop

function Fail([string]$Message) {
    throw "Import rejected: $Message"
}

function Quote-GitArgument([string]$Value) {
    $escaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $escaped = [regex]::Replace($escaped, '(\\+)$', '$1$1')
    '"' + $escaped + '"'
}

function Invoke-GitProcess([string]$WorkingDirectory, [string[]]$Arguments, [string]$OutputFile = '') {
    $globalConfig = [System.IO.Path]::GetTempFileName()
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = 'git'
    $start.WorkingDirectory = $WorkingDirectory
    $safeArguments = @('-c', 'core.fsmonitor=false') + $Arguments
    $start.Arguments = ($safeArguments | ForEach-Object { Quote-GitArgument $_ }) -join ' '
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardError = $true
    $start.RedirectStandardOutput = $true
    $start.EnvironmentVariables['GIT_CONFIG_NOSYSTEM'] = '1'
    $start.EnvironmentVariables['GIT_CONFIG_GLOBAL'] = $globalConfig

    $process = [System.Diagnostics.Process]::new()
    try {
        $process.StartInfo = $start
        if (-not $process.Start()) { Fail 'Could not start git.' }
        if ($OutputFile) {
            $output = [System.IO.File]::Open($OutputFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
            try { $process.StandardOutput.BaseStream.CopyTo($output) } finally { $output.Dispose() }
        } else {
            $stdout = $process.StandardOutput
            $text = $stdout.ReadToEnd()
        }
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($OutputFile) { $text = '' }
        return [pscustomobject]@{ ExitCode = $process.ExitCode; Text = $text; Error = $stderr; OutputFile = $OutputFile }
    } finally {
        $process.Dispose()
        if (Test-Path -LiteralPath $globalConfig) { Remove-Item -LiteralPath $globalConfig -Force }
    }
}

function Get-Sha256([byte[]]$Bytes) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { [BitConverter]::ToString($sha.ComputeHash($Bytes)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Get-CanonicalTextSha256([byte[]]$Bytes, [string]$Label) {
    try { $text = $script:Utf8Strict.GetString($Bytes) } catch { Fail "$Label is not valid UTF-8 text." }
    if ($text.Contains([char]0)) { Fail "$Label contains a NUL byte; binary files are not supported." }
    $canonical = $text -replace "\r\n?", "`n"
    Get-Sha256 ($script:Utf8Strict.GetBytes($canonical))
}

function Read-BoundedStream([System.IO.Stream]$Stream, [long]$Limit, [string]$Label) {
    $memory = [System.IO.MemoryStream]::new()
    $buffer = New-Object byte[] 81920
    $total = 0L
    try {
        while (($read = $Stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $total += $read
            if ($total -gt $Limit) { Fail "$Label expands beyond its size limit." }
            $memory.Write($buffer, 0, $read)
        }
        ,$memory.ToArray()
    } finally { $memory.Dispose() }
}

function Get-SafeArchivePath([string]$Path) {
    if (-not $Path -or $Path.Length -gt 240 -or $Path -match '[\x00-\x1f\x7f]' -or $Path.Contains('\')) {
        Fail 'An archive path is empty, too long, or contains a control character or backslash.'
    }
    if ($Path.StartsWith('/') -or $Path -match '^[A-Za-z]:' -or $Path -match '[<>:"|?*]') {
        Fail "Absolute or non-portable archive path is not allowed: $Path"
    }
    if ($Path -match '(^|/)\.git(?:/|$)' -or $Path -match '(^|/)\.gitattributes$|(^|/)\.gitmodules$' -or $Path.Contains(',')) {
        Fail "Git metadata paths or comma characters are not allowed: $Path"
    }
    $parts = $Path.Split('/')
    if (@($parts | Where-Object { -not $_ -or $_ -eq '.' -or $_ -eq '..' }).Count -gt 0) {
        Fail "Archive path contains an empty, current, or parent segment: $Path"
    }
    foreach ($part in $parts) {
        if ($part.EndsWith('.') -or $part.EndsWith(' ') -or $part -match '^(?i:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$') {
            Fail "Archive path contains a Windows-reserved filename: $Path"
        }
    }
    if ($Path -ne ($parts -join '/')) { Fail "Non-canonical archive path: $Path" }
    $Path
}

function Get-ObjectField($Object, [string]$Name) {
    $property = $Object.PSObject.Properties[$Name]
    if (-not $property) { return $null }
    $property.Value
}

function Test-Hash([object]$Value, [bool]$Nullable, [string]$Label) {
    if ($null -eq $Value -and $Nullable) { return }
    if ($Value -isnot [string] -or $Value -notmatch '^[0-9a-fA-F]{64}$') {
        Fail "$Label must be a 64-digit SHA-256$(if ($Nullable) { ' or null' })."
    }
}

function Test-PlainText([object]$Value, [string]$Label, [int]$MaxLength, [bool]$Required = $false) {
    if ($Value -isnot [string] -or ($Required -and -not $Value.Trim())) { Fail "$Label must be a non-empty string." }
    if ($Value.Length -gt $MaxLength -or $Value -match '[\x00-\x1f\x7f]' -or $Value.Contains('|')) {
        Fail "$Label exceeds its length or contains forbidden control/table characters."
    }
}

function Get-TaskFile([string]$Channel, [string]$Id, [ref]$Stage, [ref]$Task) {
    if ($Id -notmatch '^(?<stage>[A-Za-z0-9]+)-(?<task>T\d+)$') { Fail 'TaskId must use <stage>-Txx form.' }
    $Stage.Value = $Matches.stage
    $Task.Value = $Matches.task
    $path = Join-Path $Channel "tareas\$($Stage.Value)\$($Task.Value)-*.md"
    $matches = @(Get-ChildItem -Path $path -File -ErrorAction SilentlyContinue)
    if ($matches.Count -ne 1) { Fail "Expected one board file for $Id; found $($matches.Count)." }
    $matches[0].FullName
}

function Get-AllowedPaths([string]$TaskText) {
    $line = [regex]::Match($TaskText, '(?m)^\|\s*Archivos\s*\|\s*(.*?)\s*\|\s*$')
    if (-not $line.Success) { Fail 'Task card has no Archivos field.' }
    $tokens = @([regex]::Matches($line.Groups[1].Value, '`([^`]+)`') | ForEach-Object { $_.Groups[1].Value })
    if (-not $tokens) { Fail 'Task card has no explicit backtick-delimited allowed paths.' }
    $paths = [System.Collections.Generic.List[string]]::new()
    $prefixes = [System.Collections.Generic.List[string]]::new()
    foreach ($token in $tokens) {
        if ($token -match '[*?]' -or $token -match '(^|[\\/])\.\.([\\/]|$)') { Fail 'Task card contains a non-literal allowlist path.' }
        $normalized = $token.Replace('\', '/')
        if ($normalized.EndsWith('/')) {
            $prefixes.Add((Get-SafeArchivePath $normalized.TrimEnd('/') ) + '/')
        } else {
            $paths.Add((Get-SafeArchivePath $normalized))
        }
    }
    [pscustomobject]@{ Exact = @($paths); Prefixes = @($prefixes) }
}

function Test-AllowedPath([string]$Path, $Allowlist) {
    if ($Allowlist.Exact -ccontains $Path) { return $true }
    foreach ($prefix in $Allowlist.Prefixes) {
        if ($Path.StartsWith($prefix, [StringComparison]::Ordinal)) { return $true }
    }
    $false
}

function Get-GitBlobBytes([string]$Repo, [string]$Commit, [string]$Path) {
    $result = Invoke-GitProcess $Repo @('-C', $Repo, 'cat-file', 'blob', "$Commit`:$Path") ([System.IO.Path]::GetTempFileName())
    $file = $result.OutputFile
    if ($result.ExitCode -ne 0) {
        if (Test-Path $file) { Remove-Item -LiteralPath $file -Force }
        return $null
    }
    try { ,([System.IO.File]::ReadAllBytes($file)) } finally { Remove-Item -LiteralPath $file -Force }
}

function Get-ArchiveEntries([string]$Path) {
    $info = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($info.Length -gt $script:MaxArchiveBytes) { Fail 'ZIP file exceeds the compressed-size limit.' }
    $zip = [System.IO.Compression.ZipFile]::OpenRead($info.FullName)
    try {
        if ($zip.Entries.Count -lt 1 -or $zip.Entries.Count -gt $script:MaxEntries) { Fail 'ZIP has an invalid number of entries.' }
        $entries = @{}
        $seen = [System.Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
        $totalExpanded = 0L
        foreach ($entry in $zip.Entries) {
            $name = Get-SafeArchivePath $entry.FullName
            if (-not $seen.Add($name)) { Fail "Duplicate archive path: $name" }
            $mode = ($entry.ExternalAttributes -shr 16) -band 0xF000
            if ($mode -eq 0xA000 -or ($entry.ExternalAttributes -band 0x400)) { Fail "Symlink or reparse-point entry is not allowed: $name" }
            if ($entry.FullName.EndsWith('/')) { Fail 'Directory entries are not allowed in the package.' }
            if ($entry.Length -gt $script:MaxEntryBytes) { Fail "Archive entry exceeds the per-file limit: $name" }
            $compressed = [Math]::Max(1L, $entry.CompressedLength)
            if ($entry.Length -gt 4096 -and ($entry.Length / $compressed) -gt $script:MaxCompressionRatio) {
                Fail "Archive entry compression ratio exceeds the limit: $name"
            }
            $totalExpanded += $entry.Length
            if ($totalExpanded -gt $script:MaxExpandedBytes) { Fail 'ZIP expands beyond the total-size limit.' }
            $stream = $entry.Open()
            try { $bytes = Read-BoundedStream $stream $script:MaxEntryBytes $name } finally { $stream.Dispose() }
            $totalExpanded += ($bytes.Length - $entry.Length)
            if ($totalExpanded -gt $script:MaxExpandedBytes) { Fail 'ZIP expands beyond the total-size limit.' }
            $entries[$name] = [pscustomobject]@{ Bytes = $bytes; Length = $entry.Length }
        }
        return $entries
    } finally { $zip.Dispose() }
}

function Get-PatchFiles([string]$PatchText) {
    if (($PatchText.Length -gt 0 -and $PatchText[0] -eq [char]0xFEFF) -or -not $PatchText.StartsWith('diff --git ', [StringComparison]::Ordinal)) {
        Fail 'Patch must start with a UTF-8 git diff header without a byte-order mark.'
    }
    if ($PatchText -match '(?m)^(GIT binary patch|Binary files )') { Fail 'Binary patches are not supported.' }
    $headers = [regex]::Matches($PatchText, '(?m)^diff --git (?:"a/(?<oldQuoted>[^"\\]*)"|a/(?<old>[^ \r\n]+)) (?:"b/(?<newQuoted>[^"\\]*)"|b/(?<new>[^ \r\n]+))\r?$')
    if ($headers.Count -eq 0) { Fail 'change.patch contains no supported git diff headers.' }
    $files = [System.Collections.Generic.List[object]]::new()
    for ($i = 0; $i -lt $headers.Count; $i++) {
        $oldPath = if ($headers[$i].Groups['oldQuoted'].Success) { $headers[$i].Groups['oldQuoted'].Value } else { $headers[$i].Groups['old'].Value }
        $newPath = if ($headers[$i].Groups['newQuoted'].Success) { $headers[$i].Groups['newQuoted'].Value } else { $headers[$i].Groups['new'].Value }
        $oldPath = Get-SafeArchivePath $oldPath
        $newPath = Get-SafeArchivePath $newPath
        if ($oldPath -cne $newPath) { Fail 'Renames and copies are not supported.' }
        $start = $headers[$i].Index
        $end = if ($i + 1 -lt $headers.Count) { $headers[$i + 1].Index } else { $PatchText.Length }
        $block = $PatchText.Substring($start, $end - $start)
        if ($block -match '(?m)^(?:rename|copy) (?:from|to) ' -or $block -match '(?m)^(?:old|new) mode ') {
            Fail 'Renames, copies, and mode-only changes are not supported.'
        }
        if ($block -match '(?m)^index [0-9a-f]+\.\.[0-9a-f]+ (?:120000|160000)\r?$' -or
            $block -match '(?m)^(?:new file mode|deleted file mode) (?:120000|160000)$') {
            Fail 'Symlinks and submodules are not supported.'
        }
        $action = if ($block -match '(?m)^new file mode (?:100644|100755)\r?$') { 'add' } elseif ($block -match '(?m)^deleted file mode (?:100644|100755)\r?$') { 'delete' } else { 'modify' }
        $oldHeader = [regex]::Matches($block, '(?m)^--- (.+?)\r?$')
        $newHeader = [regex]::Matches($block, '(?m)^\+\+\+ (.+?)\r?$')
        if ($oldHeader.Count -ne 1 -or $newHeader.Count -ne 1) { Fail 'Each patch section must contain exactly one old/new file header.' }
        $expectedOld = if ($action -eq 'add') { '/dev/null' } else { "a/$oldPath" }
        $expectedNew = if ($action -eq 'delete') { '/dev/null' } else { "b/$oldPath" }
        $actualOld = $oldHeader[0].Groups[1].Value
        $actualNew = $newHeader[0].Groups[1].Value
        if ($actualOld -match '^"a/(?<path>[^"\\]+)"$') { $actualOld = "a/$($Matches.path)" }
        if ($actualNew -match '^"b/(?<path>[^"\\]+)"$') { $actualNew = "b/$($Matches.path)" }
        if ($actualOld -cne $expectedOld -or $actualNew -cne $expectedNew) {
            Fail "Patch file headers do not match its diff header for $oldPath."
        }
        if ($block -match '(?m)^new file mode (?!100644\r?$|100755\r?$)|^deleted file mode (?!100644\r?$|100755\r?$)') {
            Fail 'Unsupported file mode in patch.'
        }
        $files.Add([pscustomobject]@{ Path = $oldPath; Action = $action })
    }
    @($files)
}

function Invoke-TempGitApply([string]$TempRoot, [string]$PatchPath, [switch]$Check) {
    $arguments = @('-C', $TempRoot, 'apply')
    if ($Check) { $arguments += '--check' }
    $arguments += @('--whitespace=nowarn', '--', $PatchPath)
    Invoke-GitProcess $TempRoot $arguments
}

function Invoke-TaskCardCompletion([string]$Channel, [string]$TaskPath, [string]$OriginalText, [string]$AgentName, [string]$PatchName, [string]$Summary, [string]$RequestId, [string]$TaskIdValue, [string]$BaseCommit) {
    . (Join-Path $Channel 'bloqueo.ps1')
    Enter-ChannelLock 'importar-paquete-nube.ps1 (actualizar ficha)'
    try {
        $current = [System.IO.File]::ReadAllText($TaskPath)
        if ($current -cne $OriginalText) { Fail 'Task card changed during import; patch is registered but task remains unchanged and held.' }
        $state = [regex]::Match($current, '(?m)^\|\s*Estado\s*\|\s*(.*?)\s*\|\s*$')
        $currentAgent = [regex]::Match($current, '(?m)^\|\s*Agente\s*\|\s*(.*?)\s*\|\s*$')
        if (-not $state.Success -or $state.Groups[1].Value -notin @('en curso', 'pendiente')) { Fail 'Task state changed; patch is registered but task remains held.' }
        if ($currentAgent.Success -and $currentAgent.Groups[1].Value -and $currentAgent.Groups[1].Value -cne $AgentName) {
            Fail 'Task assignment changed; patch is registered but task remains held.'
        }
        $reservationPath = Join-Path $Channel "reservas-paquete-cloud\$TaskIdValue.json"
        if ($RequestId) {
            if (-not (Test-Path -LiteralPath $reservationPath)) { Fail 'Cloud reservation disappeared during import; patch is registered but task remains held.' }
            try { $reservation = [System.IO.File]::ReadAllText($reservationPath) | ConvertFrom-Json -ErrorAction Stop }
            catch { Fail 'Cloud reservation file is malformed; patch is registered but task remains held.' }
            if ($reservation.task_id -cne $TaskIdValue -or $reservation.request_id -cne $RequestId -or
                $reservation.agent -cne $AgentName -or $reservation.base_commit -cne $BaseCommit -or
                $reservation.reserved_card_sha256 -cne (Get-Sha256 ($script:Utf8Strict.GetBytes($current)))) {
                Fail 'Cloud reservation changed during import; patch is registered but task remains held.'
            }
        }
        $updated = [regex]::Replace($current, '(?m)^(\|\s*Estado\s*\|\s*).*?(\s*\|\s*)$', '${1}terminada${2}', 1)
        $updated = [regex]::Replace($updated, '(?m)^(\|\s*Agente\s*\|\s*).*?(\s*\|\s*)$', ('${1}' + $AgentName + '${2}'), 1)
        $updated = [regex]::Replace($updated, '(?m)^(\|\s*Parche\s*\|\s*).*?(\s*\|\s*)$', ('${1}`' + $PatchName + '`' + '${2}'), 1)
        $detail = "Entrega cloud validada estructuralmente por el operador. $Summary"
        $updated = [regex]::Replace($updated, '(?s)(## Dónde quedó\s*\r?\n).*?(?=\r?\n## Qué falta)', ('$1' + $detail))
        $updated = [regex]::Replace($updated, '(?s)(## Qué falta\s*\r?\n).*?(?=\r?\n## Historial)', ('$1' + 'El usuario puede revisar y aplicar el parche por el flujo ordinario; checks del manifiesto no verificados.'))
        $date = Get-Date -Format 'yyyy-MM-dd HH:mm'
        $historyRows = "`r`n| $date | $AgentName | tomada | Trabajo recibido desde paquete cloud; atribución declarada por el operador. |`r`n| $date | $AgentName | terminada | Paquete validado y parche $PatchName registrado; checks declarados, no verificados. |"
        $updated = $updated.TrimEnd() + $historyRows + "`r`n"
        Write-ChannelFile $TaskPath $updated
        if ($RequestId) { Remove-Item -LiteralPath $reservationPath -Force }
    } finally { Exit-ChannelLock }
}

function Read-RegisteredAgents([string]$Channel) {
    $canal = [System.IO.File]::ReadAllText((Join-Path $Channel 'CANAL.md'))
    @([regex]::Matches($canal, '(?m)^## Para ([A-Za-z][A-Za-z0-9_-]*) \(sin leer\)$') | ForEach-Object { $_.Groups[1].Value })
}

function Invoke-Importer {
    $channel = (Resolve-Path -LiteralPath $ChannelPath).Path
    if (-not $RepoPath) { $RepoPath = (Resolve-Path (Join-Path $channel '..\..\..')).Path }
    else { $RepoPath = (Resolve-Path -LiteralPath $RepoPath).Path }
    $package = (Resolve-Path -LiteralPath $PackagePath).Path
    $stage = ''
    $task = ''
    $taskPath = Get-TaskFile $channel $TaskId ([ref]$stage) ([ref]$task)
    $taskOriginal = [System.IO.File]::ReadAllText($taskPath)
    $agent = $Agent
    if (-not $ValidateOnly) {
        if (-not $agent -or $agent -notin (Read-RegisteredAgents $channel)) { Fail 'Specify -Agent as a currently registered agent name.' }
        if ($Authorization -notmatch '^usuario (?:\d{4}-\d{2}-\d{2}:|#\d{3}\b).+') { Fail 'Specify -Authorization as the exact user authorization being exercised.' }
        Test-PlainText $Authorization 'Authorization' 300 $true
    }
    $state = [regex]::Match($taskOriginal, '(?m)^\|\s*Estado\s*\|\s*(.*?)\s*\|\s*$')
    $boardAgent = [regex]::Match($taskOriginal, '(?m)^\|\s*Agente\s*\|\s*(.*?)\s*\|\s*$')
    if (-not $state.Success -or $state.Groups[1].Value -notin @('en curso', 'pendiente')) { Fail 'Task must be en curso or pendiente before cloud delivery import.' }
    if ($boardAgent.Success -and $boardAgent.Groups[1].Value -and $Agent -and $boardAgent.Groups[1].Value -cne $Agent) {
        Fail 'The declared responsible agent does not match the task assignment.'
    }
    $allowlist = Get-AllowedPaths $taskOriginal
    $entries = Get-ArchiveEntries $package
    if (-not $entries.ContainsKey('manifest.json')) { Fail 'ZIP must contain a root manifest.json.' }
    try { $manifestText = $script:Utf8Strict.GetString($entries['manifest.json'].Bytes) } catch { Fail 'manifest.json is not valid UTF-8.' }
    if ($entries['manifest.json'].Bytes.Length -gt $script:MaxManifestBytes) { Fail 'manifest.json exceeds its size limit.' }
    try { $manifest = ConvertFrom-Json -InputObject $manifestText -ErrorAction Stop } catch { Fail "manifest.json is invalid JSON: $($_.Exception.Message)" }
    if ((Get-ObjectField $manifest 'schema_version') -ne 1) { Fail 'Only manifest schema_version 1 is supported.' }
    if ((Get-ObjectField $manifest 'task_id') -cne $TaskId) { Fail 'Manifest task_id does not match -TaskId.' }
    $baseCommit = Get-ObjectField $manifest 'base_commit'
    if ($baseCommit -isnot [string] -or $baseCommit -notmatch '^[0-9a-fA-F]{40}$') { Fail 'base_commit must be a full 40-digit commit SHA.' }
    $requestId = Get-ObjectField $manifest 'request_id'
    $reservationPath = Join-Path $channel "reservas-paquete-cloud\$TaskId.json"
    $hasReservation = Test-Path -LiteralPath $reservationPath
    if ($null -ne $requestId -and ($requestId -isnot [string] -or $requestId -notmatch '^[0-9a-fA-F]{32}$')) {
        Fail 'request_id, when present, must be the 32-character ID from a local task package.'
    }
    if ($hasReservation) {
        if ($requestId -isnot [string]) { Fail 'This task has a cloud reservation; manifest request_id is required.' }
        try { $reservation = [System.IO.File]::ReadAllText($reservationPath) | ConvertFrom-Json -ErrorAction Stop }
        catch { Fail 'Cloud reservation file is malformed.' }
        if ($reservation.task_id -cne $TaskId -or $reservation.request_id -cne $requestId -or
            $reservation.base_commit -cne $baseCommit -or $reservation.agent -cne $boardAgent.Groups[1].Value -or
            (Get-Sha256 ($script:Utf8Strict.GetBytes($taskOriginal))) -cne $reservation.reserved_card_sha256) {
            Fail 'Response task, request ID, base, or reserved task card does not match the local cloud reservation.'
        }
        if ($Agent -and $Agent -cne $reservation.agent) { Fail 'The operator does not match the reserved task owner.' }
    } elseif ($null -ne $requestId) {
        Fail 'No matching local reservation exists for this request_id; cancelled or orphaned requests cannot be imported.'
    }
    $baseCheck = Invoke-GitProcess $RepoPath @('-C', $RepoPath, 'cat-file', '-e', "$baseCommit`^{commit}")
    if ($baseCheck.ExitCode -ne 0) { Fail 'base_commit is not a commit available in the local repository.' }
    $declaredAgent = Get-ObjectField $manifest 'agent'
    if ($declaredAgent -isnot [string]) { Fail 'manifest agent must be a display-name string.' }
    Test-PlainText $declaredAgent 'agent' 80 $true
    $summary = Get-ObjectField $manifest 'summary'
    Test-PlainText $summary 'summary' 500 $true
    $checks = Get-ObjectField $manifest 'checks'
    $checksRun = @(Get-ObjectField $checks 'declared_run')
    $checksNotRun = @(Get-ObjectField $checks 'declared_not_run')
    foreach ($check in @($checksRun + $checksNotRun)) { Test-PlainText $check 'check' 120 $true }
    if ($checksRun.Count -gt 30 -or $checksNotRun.Count -gt 30) { Fail 'Too many declared checks.' }
    $manifestFiles = @(Get-ObjectField $manifest 'files')
    if ($manifestFiles.Count -lt 1 -or $manifestFiles.Count -gt 40) { Fail 'Manifest files must contain between 1 and 40 entries.' }
    $declared = @{}
    foreach ($file in $manifestFiles) {
        $path = Get-SafeArchivePath (Get-ObjectField $file 'path')
        if ($declared.ContainsKey($path)) { Fail "Duplicate manifest path: $path" }
        if (-not (Test-AllowedPath $path $allowlist)) { Fail "Task does not allow file: $path" }
        $action = Get-ObjectField $file 'action'
        if ($action -notin @('add', 'modify', 'delete')) { Fail "Invalid action for $path." }
        $before = Get-ObjectField $file 'sha256_before'
        $after = Get-ObjectField $file 'sha256_after'
        Test-Hash $before ($action -eq 'add') "$path sha256_before"
        Test-Hash $after ($action -eq 'delete') "$path sha256_after"
        if (($action -eq 'add' -and $null -ne $before) -or ($action -eq 'delete' -and $null -ne $after)) {
            Fail "Hash nullability does not match action for $path."
        }
        $blob = Get-GitBlobBytes $RepoPath $baseCommit $path
        if ($action -eq 'add') {
            if ($null -ne $blob) { Fail "File declared add already exists at base: $path" }
        } else {
            if ($null -eq $blob -or (Get-Sha256 $blob) -cne $before.ToLowerInvariant()) { Fail "sha256_before does not match base_commit for $path" }
        }
        $declared[$path] = [pscustomobject]@{ Path = $path; Action = $action; Before = $before; After = $after }
    }
    $patchBytes = $null
    $patchText = ''
    $patchMode = $entries.ContainsKey('change.patch')
    if ($patchMode) {
        if ($entries.Count -ne 2) { Fail 'Diff-first ZIP may contain only manifest.json and change.patch.' }
        $patchBytes = $entries['change.patch'].Bytes
        $patchHash = Get-ObjectField $manifest 'patch_sha256'
        Test-Hash $patchHash $false 'patch_sha256'
        if ((Get-Sha256 $patchBytes) -cne $patchHash.ToLowerInvariant()) { Fail 'patch_sha256 does not match change.patch.' }
        try { $patchText = $script:Utf8Strict.GetString($patchBytes) } catch { Fail 'change.patch must be UTF-8 text.' }
        $patchText = $patchText -replace "\r\n?", "`n"
        $patchBytes = $script:Utf8Strict.GetBytes($patchText)
        $patchFiles = @(Get-PatchFiles $patchText)
        if ($patchFiles.Count -ne $declared.Count) { Fail 'Manifest file list and change.patch file list differ.' }
        foreach ($changed in $patchFiles) {
            if (-not $declared.ContainsKey($changed.Path)) { Fail "change.patch touches undeclared file: $($changed.Path)" }
            if ($declared[$changed.Path].Action -cne $changed.Action) { Fail "Manifest action does not match patch for $($changed.Path)." }
        }
    } else {
        if ((Get-ObjectField $manifest 'patch_sha256') -notin @($null, '')) { Fail 'patch_sha256 must be absent when change.patch is absent.' }
        $packagedFileCount = @($declared.Values | Where-Object { $_.Action -ne 'delete' }).Count
        if ($entries.Count -ne ($packagedFileCount + 1)) { Fail 'Files-only ZIP must contain manifest.json and exactly the declared result files.' }
        foreach ($path in $declared.Keys) {
            if ($declared[$path].Action -eq 'delete') {
                if ($entries.ContainsKey($path)) { Fail "Deleted file must not be included: $path" }
            } else {
                if (-not $entries.ContainsKey($path)) { Fail "ZIP is missing declared file: $path" }
                if ((Get-CanonicalTextSha256 $entries[$path].Bytes $path) -cne $declared[$path].After.ToLowerInvariant()) { Fail "sha256_after does not match packaged file: $path" }
            }
        }
        foreach ($name in $entries.Keys) {
            if ($name -cne 'manifest.json' -and -not $declared.ContainsKey($name)) { Fail "Unexpected ZIP entry: $name" }
        }
    }

    $headResult = Invoke-GitProcess $RepoPath @('-C', $RepoPath, 'rev-parse', 'HEAD')
    if ($headResult.ExitCode -ne 0) { Fail 'Could not read integration HEAD.' }
    $head = $headResult.Text.Trim()
    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('cloud-import-' + [guid]::NewGuid().ToString('N'))
    [System.IO.Directory]::CreateDirectory($tempRoot) | Out-Null
    try {
        $init = Invoke-GitProcess $tempRoot @('-C', $tempRoot, 'init', '--quiet')
        if ($init.ExitCode -ne 0) { Fail "Could not initialize a temporary validation workspace: $($init.Error.Trim())" }
        $cleanPaths = @($declared.Keys)
        foreach ($path in $cleanPaths) {
            $statusResult = Invoke-GitProcess $RepoPath @('-C', $RepoPath, 'status', '--porcelain', '--', $path)
            if ($statusResult.ExitCode -ne 0) { Fail "Could not check local status for $path." }
            if ($statusResult.Text.Trim()) { Fail "Destination has local changes; refusing to inspect/apply package for $path." }
            $absolute = Join-Path $RepoPath ($path.Replace('/', '\'))
            $ancestor = $RepoPath
            foreach ($segment in $path.Split('/')) {
                $ancestor = Join-Path $ancestor $segment
                if (Test-Path -LiteralPath $ancestor) {
                    $attributes = [System.IO.File]::GetAttributes($ancestor)
                    if (($attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                        Fail "Destination path contains a symlink/reparse point: $path"
                    }
                }
            }
            if (Test-Path -LiteralPath $absolute) {
                $copy = Join-Path $tempRoot ($path.Replace('/', '\'))
                [System.IO.Directory]::CreateDirectory((Split-Path -Parent $copy)) | Out-Null
                [System.IO.File]::WriteAllBytes($copy, [System.IO.File]::ReadAllBytes($absolute))
            } elseif ($declared[$path].Action -ne 'add') {
                Fail "Destination file is absent from current checkout: $path"
            }
        }
        if ($patchMode) {
            $patchFile = Join-Path $tempRoot 'change.patch'
            [System.IO.File]::WriteAllBytes($patchFile, $patchBytes)
            $check = Invoke-TempGitApply $tempRoot $patchFile -Check
            if ($check.ExitCode -ne 0) { Fail "Patch does not apply cleanly to current integration: $($check.Error.Trim())" }
            $apply = Invoke-TempGitApply $tempRoot $patchFile
            if ($apply.ExitCode -ne 0) { Fail "Patch application to validation copy failed: $($apply.Error.Trim())" }
        } else {
            $existingPaths = [System.Collections.Generic.List[string]]::new()
            $newPaths = [System.Collections.Generic.List[string]]::new()
            foreach ($path in $declared.Keys) {
                if ($declared[$path].Action -eq 'add') { $newPaths.Add($path) } else { $existingPaths.Add($path) }
            }
            if ($existingPaths.Count) {
                $addArgs = @('-C', $tempRoot, 'add', '--') + @($existingPaths)
                $add = Invoke-GitProcess $tempRoot $addArgs
                if ($add.ExitCode -ne 0) { Fail "Could not create temporary base index: $($add.Error.Trim())" }
            }
            foreach ($path in $declared.Keys) {
                $target = Join-Path $tempRoot ($path.Replace('/', '\'))
                if ($declared[$path].Action -eq 'delete') {
                    Remove-Item -LiteralPath $target -Force
                } else {
                    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $target)) | Out-Null
                    [System.IO.File]::WriteAllBytes($target, $entries[$path].Bytes)
                }
            }
            if ($newPaths.Count) {
                $addArgs = @('-C', $tempRoot, 'add', '-N', '--') + @($newPaths)
                $add = Invoke-GitProcess $tempRoot $addArgs
                if ($add.ExitCode -ne 0) { Fail "Could not stage intent-to-add in temporary workspace: $($add.Error.Trim())" }
            }
            $diffFile = Join-Path $tempRoot 'generated.patch'
            $diffArgs = @('-C', $tempRoot, 'diff', '--binary', '--no-ext-diff', '--full-index', '--') + $cleanPaths
            $diff = Invoke-GitProcess $tempRoot $diffArgs $diffFile
            if ($diff.ExitCode -gt 1) { Fail "Could not generate diff from packaged files: $($diff.Error.Trim())" }
            $patchBytes = [System.IO.File]::ReadAllBytes($diffFile)
            if (-not $patchBytes.Length) { Fail 'Files-only package produces no changes.' }
            $patchText = $script:Utf8Strict.GetString($patchBytes)
            $patchFiles = @(Get-PatchFiles $patchText)
            if ($patchFiles.Count -ne $declared.Count) { Fail 'Generated diff does not cover every declared file.' }
            foreach ($changed in $patchFiles) {
                if (-not $declared.ContainsKey($changed.Path) -or $declared[$changed.Path].Action -cne $changed.Action) {
                    Fail "Generated diff has an unexpected file or action: $($changed.Path)"
                }
            }
            $resultRoot = Join-Path $tempRoot 'result'
            [System.IO.Directory]::CreateDirectory($resultRoot) | Out-Null
            $resultInit = Invoke-GitProcess $resultRoot @('-C', $resultRoot, 'init', '--quiet')
            if ($resultInit.ExitCode -ne 0) { Fail "Could not initialize isolated result workspace: $($resultInit.Error.Trim())" }
            foreach ($path in $cleanPaths) {
                $source = Join-Path $RepoPath ($path.Replace('/', '\'))
                if (Test-Path -LiteralPath $source) {
                    $destination = Join-Path $resultRoot ($path.Replace('/', '\'))
                    [System.IO.Directory]::CreateDirectory((Split-Path -Parent $destination)) | Out-Null
                    [System.IO.File]::WriteAllBytes($destination, [System.IO.File]::ReadAllBytes($source))
                }
            }
            $checkPatch = Join-Path $tempRoot 'generated.patch'
            $check = Invoke-GitProcess $resultRoot @('-C', $resultRoot, 'apply', '--check', '--whitespace=nowarn', '--', $checkPatch)
            if ($check.ExitCode -ne 0) { Fail "Generated patch failed validation: $($check.Error.Trim())" }
            $apply = Invoke-GitProcess $resultRoot @('-C', $resultRoot, 'apply', '--whitespace=nowarn', '--', $checkPatch)
            if ($apply.ExitCode -ne 0) { Fail "Could not apply generated patch to an isolated result copy: $($apply.Error.Trim())" }
        }
        if ($patchMode) { $resultRoot = $tempRoot }
        foreach ($path in $declared.Keys) {
            $resultPath = Join-Path $resultRoot ($path.Replace('/', '\'))
            if ($declared[$path].Action -eq 'delete') {
                if (Test-Path -LiteralPath $resultPath) { Fail "Deleted file still exists after validation: $path" }
            } else {
                if (-not (Test-Path -LiteralPath $resultPath -PathType Leaf)) { Fail "Patched file is missing after validation: $path" }
                $resultHash = Get-CanonicalTextSha256 ([System.IO.File]::ReadAllBytes($resultPath)) $path
                if ($resultHash -cne $declared[$path].After.ToLowerInvariant()) {
                    Fail "sha256_after does not match validated result for $path (expected $($declared[$path].After), got $resultHash)."
                }
            }
        }
        if ($baseCommit -cne $head) { Write-Host "Notice: package base is $baseCommit; current HEAD is $head. Hashes validated and patch applies cleanly." -ForegroundColor Yellow }
        Write-Host "`nValidated package for $TaskId" -ForegroundColor Green
        Write-Host "Summary (declared): $summary"
        Write-Host "Agent (declared, not authenticated): $declaredAgent"
        Write-Host "Agent attribution (operator): $(if ($Agent) { $Agent } else { 'not supplied (validate-only)' })"
        Write-Host "Base: $baseCommit; current HEAD: $head"
        Write-Host 'Files:'
        foreach ($path in $declared.Keys | Sort-Object) { Write-Host "  $($declared[$path].Action) $path" }
        Write-Host 'Checks declared by package (not verified):'
        foreach ($checkName in $checksRun) { Write-Host "  run: $checkName" }
        foreach ($checkName in $checksNotRun) { Write-Host "  not run: $checkName" }
        if ($ValidateOnly) { return }
        if ($declaredAgent -cne $Agent) {
            Write-Host "Warning: manifest agent '$declaredAgent' differs from operator attribution '$Agent'." -ForegroundColor Yellow
        }
        if ($patchMode) {
            $previewPath = Join-Path $tempRoot 'preview.patch'
            [System.IO.File]::WriteAllBytes($previewPath, $patchBytes)
        } else {
            $previewPath = Join-Path $tempRoot 'generated.patch'
        }
        $stat = Invoke-GitProcess $RepoPath @('-C', $RepoPath, 'apply', '--stat', '--', $previewPath)
        if ($stat.ExitCode -ne 0) { Fail "Could not display validated diff summary: $($stat.Error.Trim())" }
        Write-Host "`nDiff summary:`n$($stat.Text)"
        $safePreview = [regex]::Replace($patchText, '[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', {
            param($match)
            '\x{0:X2}' -f [int][char]$match.Value
        })
        Write-Host "`nValidated unified diff (untrusted text preview):`n$safePreview"
        Write-Host 'Checks above are manifest declarations only; no tests were run by the importer.'
        if (-not $ConfirmImport) {
            Write-Host "Not registered. Inspect this preview, then re-run with -ConfirmImport to record it; this will not apply the patch."
            return
        }
        $patchName = "{0}-{1}-{2}-cloud-import" -f $Agent.ToLowerInvariant(), $stage, $task
        $pending = Join-Path $channel 'parches\pendientes'
        $patchTemp = Join-Path $pending ("cloud-import-{0}.patch.tmp" -f $PID)
        $bodyTemp = Join-Path $tempRoot 'channel-entry.md'
        $patchStream = [System.IO.File]::Open($patchTemp, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None)
        try { $patchStream.Write($patchBytes, 0, $patchBytes.Length) } finally { $patchStream.Dispose() }
        $fileList = @($declared.Keys | Sort-Object | ForEach-Object { "`"$($_)`"" }) -join ', '
        $checkList = @($checksRun | ForEach-Object { "run: $($_)" }) + @($checksNotRun | ForEach-Object { "not run: $($_)" })
        $body = @(
            "- base_commit: ``$baseCommit`` (current HEAD ``$head``)",
            '- parche: {PARCHE}',
            "- archivos: $fileList",
            "- cambios de contrato/interfaz: paquete cloud para $TaskId validado estructuralmente; resumen declarado: `"$summary`"; agente manifestado: `"$declaredAgent`"; atribución confirmada por operador: `"$Agent`".",
            "- pruebas no ejecutadas: no ejecutadas por el importador. Checks declarados por el paquete, no verificados: $($checkList -join '; ')",
            "- pide a: Usuario — revisar y aplicar el parche mediante el flujo ordinario si la tarea/dependencias están listas; el importador no lo aplica."
        ) -join "`n"
        [System.IO.File]::WriteAllText($bodyTemp, $body, [System.Text.UTF8Encoding]::new($false))
        try {
            $registrationOutput = & (Join-Path $channel 'registrar-entrada.ps1') `
                -Autor $Agent -Tipo entrega -Titulo "Entrega cloud para $TaskId" -Para Todos `
                -Autorizacion $Authorization -CuerpoArchivo $bodyTemp `
                -Resumen "Paquete cloud validado estructuralmente para $TaskId; checks declarados, no verificados." `
                -Parche $patchTemp -ParcheNombre $patchName 2>&1
            $registrationSucceeded = $?
            $registration = @($registrationOutput | ForEach-Object { "$_" }) -join "`n"
            if (-not $registrationSucceeded) { Fail "registrar-entrada.ps1 failed; inspect output: $registration" }
            $registrationResult = @($registrationOutput | Where-Object { $_.PSObject.Properties['Parche'] -and $_.PSObject.Properties['Numero'] } | Select-Object -Last 1)
            if ($registrationResult.Count -ne 1 -or $registrationResult[0].Parche -notmatch '^\d{3}-[A-Za-z]+-[A-Za-z0-9]+-T\d+-[A-Za-z0-9-]+\.patch$') {
                Fail "Channel entry completed but patch name could not be recovered; inspect output: $registration"
            }
            $registeredPatch = $registrationResult[0].Parche
            Invoke-TaskCardCompletion $channel $taskPath $taskOriginal $Agent $registeredPatch $summary $requestId $TaskId $baseCommit
            if ($requestId) {
                try {
                    $assignmentPath = Get-CloudAssignmentsPath $channel
                    $assignment = @(Get-CloudAssignmentByRequestId -StorePath $assignmentPath -RequestId $requestId)
                    if ($assignment.Count) {
                        Set-CloudAssignmentState -StorePath $assignmentPath -RequestId $requestId -State human_review `
                            -Actor $Agent -Detail "Se validó y registró $registeredPatch; el cambio sigue pendiente de revisión y aplicación humana." | Out-Null
                    } else {
                        Write-Warning "Cloud patch $registeredPatch was registered, but request $requestId has no lifecycle record (legacy reservation)."
                    }
                } catch {
                    Fail "Cloud patch $registeredPatch was registered, but its lifecycle state could not be updated. Record it manually in gestionar-asignaciones-cloud.ps1. $($_.Exception.Message)"
                }
            }
            Write-Host "Registered $registeredPatch. It is in pendientes and was not applied."
        } catch {
            if (Test-Path -LiteralPath $patchTemp) { Remove-Item -LiteralPath $patchTemp -Force }
            throw
        }
    } finally {
        if (Test-Path -LiteralPath $tempRoot) { Remove-Item -LiteralPath $tempRoot -Recurse -Force }
    }
}

try {
    Invoke-Importer
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
