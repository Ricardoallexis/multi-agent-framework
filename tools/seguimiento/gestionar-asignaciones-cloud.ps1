# Copyright 2026 Ricardoallexis and contributors
# SPDX-License-Identifier: Apache-2.0

[CmdletBinding()]
param([Parameter(Mandatory)][string]$SystemRoot, [string]$Actor = 'Usuario')

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Import-Module (Join-Path $PSScriptRoot 'CloudAssignments.psm1') -Force -ErrorAction Stop
$storePath = Get-CloudAssignmentsPath $SystemRoot

function Read-Operators {
    $channelPath = Join-Path $SystemRoot 'CANAL.md'
    if (-not (Test-Path -LiteralPath $channelPath -PathType Leaf)) { throw 'CANAL.md is missing; registered operators cannot be determined.' }
    $text = [System.IO.File]::ReadAllText((Resolve-Path -LiteralPath $channelPath), [System.Text.UTF8Encoding]::new($false, $true))
    $agents = @([regex]::Matches($text, '(?m)^## Para ([^\s()]{1,64}) \(sin leer\)\s*$') |
        ForEach-Object { $_.Groups[1].Value } |
        Where-Object { $_ -cne 'Usuario' -and $_ -cne 'Agente cloud' } |
        Select-Object -Unique)
    @('Usuario') + $agents
}

function Read-Actor([string]$CurrentActor) {
    $operators = @(Read-Operators)
    if ($CurrentActor -notin $operators) { $CurrentActor = 'Usuario' }
    Write-Host 'Who is recording the change (manual declaration):'
    for ($index = 0; $index -lt $operators.Count; $index++) {
        Write-Host "$($index + 1). $($operators[$index])"
    }
    $current = [Array]::IndexOf($operators, $CurrentActor) + 1
    $choice = (Read-Host "Select [$current]").Trim()
    if (-not $choice) { return $CurrentActor }
    $number = 0
    if (-not [int]::TryParse($choice, [ref]$number) -or $number -lt 1 -or $number -gt $operators.Count) {
        throw "Select an operator from 1 to $($operators.Count)."
    }
    $operators[$number - 1]
}

function Get-ActiveAssignments {
    @(
        Get-CloudAssignments -StorePath $storePath |
            Where-Object { $_.state -notin @('completed', 'cancelled', 'preparation_failed') } |
            Sort-Object created_utc
    )
}

function Write-AssignmentList([object[]]$Items) {
    for ($index = 0; $index -lt $Items.Count; $index++) {
        $item = $Items[$index]
        $label = Get-CloudAssignmentStateLabel $item.state
        Write-Host "$($index + 1). $($item.task_id) · $($item.title) · $label"
        Write-Host "   Declared destination: $($item.destination_agent) · follow-up: $($item.follow_up_agent)"
    }
}

function Select-Assignment([object[]]$Items, [string]$Prompt) {
    if (-not $Items.Count) { Write-Host 'No assignments to display.'; return $null }
    Write-AssignmentList $Items
    $choice = (Read-Host $Prompt).Trim()
    $number = 0
    if (-not [int]::TryParse($choice, [ref]$number) -or $number -lt 1 -or $number -gt $Items.Count) {
        throw "Select a number from 1 to $($Items.Count)."
    }
    $Items[$number - 1]
}

function Show-AssignmentHistory($Item) {
    Write-Host "`n$($Item.task_id) · $($Item.title)"
    Write-Host "Goal: $($Item.goal)"
    Write-Host "Origin: $($Item.origin_agent) · declared destination: $($Item.destination_agent)"
    Write-Host "Package: $($Item.package_name) · request_id: $($Item.request_id)"
    if ($Item.package_sha256) { Write-Host "Package SHA-256: $($Item.package_sha256)" }
    Write-Host "Recorded context: $($Item.context_summary)"
    Write-Host "Included files:"
    foreach ($file in $Item.files) { Write-Host "  $($file.path)" }
    Write-Host "`nHistory (UTC dates; actions reported by the actor):"
    foreach ($event in $Item.history) {
        $when = [DateTimeOffset]::Parse($event.occurred_utc).ToString('yyyy-MM-dd HH:mm:ss') + ' UTC'
        Write-Host "$when · $($event.actor) · $(Get-CloudAssignmentStateLabel $event.state)"
        if ($event.detail) { Write-Host "  $($event.detail)" }
    }
}

function Invoke-AssignmentUpdate {
    $item = Select-Assignment (Get-ActiveAssignments) 'Assignment number'
    if (-not $item) { return }
    $nextStates = @(Get-CloudAssignmentNextStates $item.state)
    if (-not $nextStates.Count) { Write-Host 'The assignment is closed; its history is still available.'; return }
    Write-Host "`nCurrent status: $(Get-CloudAssignmentStateLabel $item.state)"
    for ($index = 0; $index -lt $nextStates.Count; $index++) {
        Write-Host "$($index + 1). $(Get-CloudAssignmentStateLabel $nextStates[$index])"
    }
    $choice = (Read-Host 'New status').Trim()
    $number = 0
    if (-not [int]::TryParse($choice, [ref]$number) -or $number -lt 1 -or $number -gt $nextStates.Count) {
        throw "Select a status from 1 to $($nextStates.Count)."
    }
    $next = $nextStates[$number - 1]
    if ($next -eq 'sent_to_cloud' -and (Read-Host 'Confirm that the ZIP was manually uploaded to the cloud destination: type ENVIADO') -cne 'ENVIADO') {
        Write-Host 'The upload was not recorded.'
        return
    }
    if ($next -eq 'completed' -and (Read-Host 'Confirm that integration is complete: type INTEGRADO') -cne 'INTEGRADO') {
        Write-Host 'The completion was not recorded.'
        return
    }
    if ($next -eq 'cancelled' -and (Read-Host 'Confirm cancellation: type CANCELAR') -cne 'CANCELAR') {
        Write-Host 'The cancellation was not recorded.'
        return
    }
    $actor = Read-Actor $Actor
    $detail = (Read-Host 'Action details (optional)').Trim()
    if (-not $detail) {
        $detail = switch ($next) {
            'sent_to_cloud' { 'The operator reports manually uploading the ZIP; the system does not verify the transfer.' }
            'in_execution' { 'The operator reports that work started or is ongoing; this is not a live process signal.' }
            'waiting_response' { 'The operator reports awaiting a response; this is not provider telemetry.' }
            'human_review' { 'The result is awaiting human review.' }
            'integrating_result' { 'The operator reports starting to integrate the result.' }
            'completed' { 'The operator confirms that integration is complete.' }
            'cancelled' { 'The operator confirms cancellation of the assignment.' }
            default { 'Status change recorded by the operator.' }
        }
    }
    $null = Set-CloudAssignmentState -StorePath $storePath -RequestId $item.request_id `
        -State $next -Actor $actor -Detail $detail
    Write-Host "Status recorded: $(Get-CloudAssignmentStateLabel $next)"
}

while ($true) {
    Write-Host "`nCloud assignment tracker (manual record; not live presence)"
    Write-Host '1. View active assignments'
    Write-Host '2. Record a status change'
    Write-Host '3. View history'
    Write-Host '0. Exit'
    $choice = (Read-Host 'Select').Trim()
    if ($choice -eq '0') { break }
    try {
        switch ($choice) {
            '1' {
                $active = @(Get-ActiveAssignments)
                if ($active.Count) { Write-AssignmentList $active } else { Write-Host 'No active cloud assignments.' }
            }
            '2' { Invoke-AssignmentUpdate }
            '3' {
                $items = @(Get-CloudAssignments -StorePath $storePath | Sort-Object created_utc -Descending)
                $item = Select-Assignment $items 'Assignment number'
                if ($item) { Show-AssignmentHistory $item }
            }
            default { Write-Warning 'Invalid selection.' }
        }
    } catch {
        Write-Error -ErrorAction Continue $_.Exception.Message
    }
}
