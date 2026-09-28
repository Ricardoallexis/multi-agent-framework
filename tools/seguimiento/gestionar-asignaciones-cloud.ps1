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
    Write-Host 'Quien registra el cambio (declaracion manual):'
    for ($index = 0; $index -lt $operators.Count; $index++) {
        Write-Host "$($index + 1). $($operators[$index])"
    }
    $current = [Array]::IndexOf($operators, $CurrentActor) + 1
    $choice = (Read-Host "Seleccion [$current]").Trim()
    if (-not $choice) { return $CurrentActor }
    $number = 0
    if (-not [int]::TryParse($choice, [ref]$number) -or $number -lt 1 -or $number -gt $operators.Count) {
        throw "Selecciona un operador del 1 al $($operators.Count)."
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
        Write-Host "   Destino declarado: $($item.destination_agent) · seguimiento: $($item.follow_up_agent)"
    }
}

function Select-Assignment([object[]]$Items, [string]$Prompt) {
    if (-not $Items.Count) { Write-Host 'No hay asignaciones para mostrar.'; return $null }
    Write-AssignmentList $Items
    $choice = (Read-Host $Prompt).Trim()
    $number = 0
    if (-not [int]::TryParse($choice, [ref]$number) -or $number -lt 1 -or $number -gt $Items.Count) {
        throw "Selecciona un numero del 1 al $($Items.Count)."
    }
    $Items[$number - 1]
}

function Show-AssignmentHistory($Item) {
    Write-Host "`n$($Item.task_id) · $($Item.title)"
    Write-Host "Objetivo: $($Item.goal)"
    Write-Host "Origen: $($Item.origin_agent) · destino declarado: $($Item.destination_agent)"
    Write-Host "Paquete: $($Item.package_name) · request_id: $($Item.request_id)"
    if ($Item.package_sha256) { Write-Host "SHA-256 del paquete: $($Item.package_sha256)" }
    Write-Host "Contexto registrado: $($Item.context_summary)"
    Write-Host "Archivos incluidos:"
    foreach ($file in $Item.files) { Write-Host "  $($file.path)" }
    Write-Host "`nHistorial (fechas UTC; acciones reportadas por el actor):"
    foreach ($event in $Item.history) {
        $when = [DateTimeOffset]::Parse($event.occurred_utc).ToString('yyyy-MM-dd HH:mm:ss') + ' UTC'
        Write-Host "$when · $($event.actor) · $(Get-CloudAssignmentStateLabel $event.state)"
        if ($event.detail) { Write-Host "  $($event.detail)" }
    }
}

function Invoke-AssignmentUpdate {
    $item = Select-Assignment (Get-ActiveAssignments) 'Numero de asignacion'
    if (-not $item) { return }
    $nextStates = @(Get-CloudAssignmentNextStates $item.state)
    if (-not $nextStates.Count) { Write-Host 'La asignacion esta cerrada; su historial sigue disponible.'; return }
    Write-Host "`nEstado actual: $(Get-CloudAssignmentStateLabel $item.state)"
    for ($index = 0; $index -lt $nextStates.Count; $index++) {
        Write-Host "$($index + 1). $(Get-CloudAssignmentStateLabel $nextStates[$index])"
    }
    $choice = (Read-Host 'Nuevo estado').Trim()
    $number = 0
    if (-not [int]::TryParse($choice, [ref]$number) -or $number -lt 1 -or $number -gt $nextStates.Count) {
        throw "Selecciona un estado del 1 al $($nextStates.Count)."
    }
    $next = $nextStates[$number - 1]
    if ($next -eq 'sent_to_cloud' -and (Read-Host 'Confirma que el ZIP ya se subio manualmente al destino cloud: escribe ENVIADO') -cne 'ENVIADO') {
        Write-Host 'No se registro el envio.'
        return
    }
    if ($next -eq 'completed' -and (Read-Host 'Confirma que la integracion fue completada: escribe INTEGRADO') -cne 'INTEGRADO') {
        Write-Host 'No se registro el cierre.'
        return
    }
    if ($next -eq 'cancelled' -and (Read-Host 'Confirma la cancelacion: escribe CANCELAR') -cne 'CANCELAR') {
        Write-Host 'No se registro la cancelacion.'
        return
    }
    $actor = Read-Actor $Actor
    $detail = (Read-Host 'Detalle de la accion (opcional)').Trim()
    if (-not $detail) {
        $detail = switch ($next) {
            'sent_to_cloud' { 'El operador declara que subio manualmente el ZIP; el sistema no verifica la transferencia.' }
            'in_execution' { 'El operador declara el inicio o continuidad; esto no es una señal de proceso en vivo.' }
            'waiting_response' { 'El operador declara que espera respuesta; esto no es telemetria del proveedor.' }
            'human_review' { 'El resultado queda en revision humana.' }
            'integrating_result' { 'El operador declara que comenzo a integrar el resultado.' }
            'completed' { 'El operador confirma que la integracion termino.' }
            'cancelled' { 'El operador confirma la cancelacion de la asignacion.' }
            default { 'Cambio de estado registrado por el operador.' }
        }
    }
    $null = Set-CloudAssignmentState -StorePath $storePath -RequestId $item.request_id `
        -State $next -Actor $actor -Detail $detail
    Write-Host "Estado registrado: $(Get-CloudAssignmentStateLabel $next)"
}

while ($true) {
    Write-Host "`nSeguimiento de asignaciones cloud (registro manual; no es presencia en vivo)"
    Write-Host '1. Ver asignaciones activas'
    Write-Host '2. Registrar cambio de estado'
    Write-Host '3. Consultar historial'
    Write-Host '0. Salir'
    $choice = (Read-Host 'Seleccion').Trim()
    if ($choice -eq '0') { break }
    try {
        switch ($choice) {
            '1' {
                $active = @(Get-ActiveAssignments)
                if ($active.Count) { Write-AssignmentList $active } else { Write-Host 'No hay asignaciones cloud activas.' }
            }
            '2' { Invoke-AssignmentUpdate }
            '3' {
                $items = @(Get-CloudAssignments -StorePath $storePath | Sort-Object created_utc -Descending)
                $item = Select-Assignment $items 'Numero de asignacion'
                if ($item) { Show-AssignmentHistory $item }
            }
            default { Write-Warning 'Seleccion no valida.' }
        }
    } catch {
        Write-Error -ErrorAction Continue $_.Exception.Message
    }
}
