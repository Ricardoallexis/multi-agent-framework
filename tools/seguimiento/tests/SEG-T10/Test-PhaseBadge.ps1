# SEG-T10: the phase badge reflects real work (7 labels, first match wins).
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg-t10-' + [guid]::NewGuid().ToString('N'))
$repo = Join-Path $temp 'repo'
$channel = Join-Path $temp 'channel'

function Assert-True([bool]$condition, [string]$message) { if (-not $condition) { throw "FAIL: $message" } }
function Get-Summary([string]$html, [string]$id) {
    $m = [regex]::Match($html, "<details[^>]*data-seg-id=""$([regex]::Escape($id))""[^>]*>\s*<summary>(.*?)</summary>", 'Singleline')
    Assert-True $m.Success "summary present for $id"
    [System.Net.WebUtility]::HtmlDecode($m.Groups[1].Value)
}
function Write-Card([string]$code, [string]$state, [string]$deps = '-', [string]$patch = 'ninguno') {
    $lines = @("# AAA-$code Tarea $code", '', '| Campo | Valor |', '|---|---|', "| Estado | $state |", "| Depende de | $deps |", "| Parche | $patch |", '| Agente | - |',
        '', '## Historial', '', '| Fecha | Agente | Acción | Detalle |', '|---|---|---|---|', '| 2026-05-02 10:00 | Claude-Opus | creada | x |')
    Set-Content (Join-Path $channel "tareas\AAA\$code-x.md") $lines -Encoding utf8
}

try {
    New-Item -ItemType Directory -Force $repo, (Join-Path $channel 'tareas\AAA'), (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion') | Out-Null
    Set-Content (Join-Path $channel 'RESUMEN.md') '# Resumen' -Encoding utf8
    Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
    git -C $repo init -q | Out-Null

    $phases = 1..7 | ForEach-Object { $t = @{1 = 'T01, T02'; 2 = 'T03'; 3 = 'T04'; 4 = 'T05, T06'; 5 = 'T07, T08'; 6 = 'T09'; 7 = 'T10' }[$_]; "| Fase $_ | $t | x |" }
    Set-Content (Join-Path $channel 'tareas\AAA\ETAPA.md') (@('# AAA · Etapa de prueba', '', '| Campo | Valor |', '|---|---|', '| Autorizada | sí |', '| Estado | abierta |', '', '## Fases de tareas', '', '| Fase | Tareas | Descripción |', '|---|---|---|') + $phases + @('', '## Historial de decisiones', '', '- 2026-05-01 · Claude-Opus · Se abre la etapa')) -Encoding utf8
    Write-Card 'T01' 'requiere cambios'
    Write-Card 'T02' 'disponible'
    Write-Card 'T03' 'integrada'
    Write-Card 'T04' 'terminada' '-' '001-x.patch'
    Write-Card 'T05' 'en curso'
    Write-Card 'T06' 'integrada'
    Write-Card 'T07' 'integrada'
    Write-Card 'T08' 'disponible'
    Write-Card 'T09' 'disponible'
    Write-Card 'T10' 'disponible' 'T05'

    & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir | Out-Null
    $html = [IO.File]::ReadAllText((Join-Path $channel 'SEGUIMIENTO.html'))

    $expected = [ordered]@{
        'fase-1' = @('Cambios pedidos', 's-requiere-cambios')
        'fase-2' = @('Fase completa', 's-integrada')
        'fase-3' = @('Fase terminada · falta integrar', 's-pendiente')
        'fase-4' = @('En curso', 's-en-curso')
        'fase-5' = @('Avance parcial · 1 de 2', 's-pendiente')
        'fase-6' = @('Lista', 's-disponible')
        'fase-7' = @('En espera de dependencias', 's-idle')
    }
    foreach ($key in $expected.Keys) {
        $summary = Get-Summary $html "seg:AAA:$key"
        Assert-True ($summary -match [regex]::Escape($expected[$key][0])) "$key label '$($expected[$key][0])' in summary: $summary"
        Assert-True ($summary -match [regex]::Escape($expected[$key][1])) "$key class $($expected[$key][1])"
    }
    # Only the in-progress phase says "En curso".
    Assert-True ([regex]::Matches($html, '>En curso</span>').Count -eq 1) 'En curso appears once'
}
catch {
    Write-Host $_.Exception.Message
    $failed = $true
}
finally {
    if ($temp -and (Test-Path -LiteralPath $temp)) { & (Join-Path $PSScriptRoot 'Remove-Temp.ps1') $temp }
}
if ($failed) { exit 1 }
Write-Host 'SEG-T10 phase badge PASS'
