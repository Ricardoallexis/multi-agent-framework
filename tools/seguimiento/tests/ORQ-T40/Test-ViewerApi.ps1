param([ValidateSet('success','empty','schema','schema-count','offline','timeout')][string]$Scenario = 'success')
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..\..')).Path
$viewer = Join-Path $root 'tools\seguimiento\ver-seguimiento.ps1'
. (Join-Path $PSScriptRoot '..\Write-PlanCache.ps1')
$fixture = $PSScriptRoot
$temp = Join-Path ([IO.Path]::GetTempPath()) ('orq-t40-' + [guid]::NewGuid().ToString('N'))
$channel = Join-Path $temp 'channel'
New-Item -ItemType Directory -Force (Join-Path $channel 'parches\pendientes'), (Join-Path $channel 'pruebas\integracion'), (Join-Path $channel 'tareas') | Out-Null
Set-Content (Join-Path $channel 'RESUMEN.md') '# Local Markdown canonical' -Encoding utf8
Set-Content (Join-Path $channel 'ROADMAP_VISTA.md') '# Local roadmap' -Encoding utf8
Set-Content (Join-Path $channel 'pruebas\integracion\LECTURAS.md') '| Reporte | Resultado | Pruebas |' -Encoding utf8
$repo = $root
$portListener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$portListener.Start(); $port = ([Net.IPEndPoint]$portListener.LocalEndpoint).Port; $portListener.Stop()
$base = "http://127.0.0.1:$port"
$job = $null
if ($Scenario -in @('success','empty','schema','schema-count','timeout')) {
    $job = Start-Job -ArgumentList $base,$fixture,$Scenario -ScriptBlock {
        param($prefix,$fixturePath,$case)
        $listener = [Net.HttpListener]::new(); $listener.Prefixes.Add("$prefix/"); $listener.Start()
        try {
            while ($listener.IsListening) {
                $context = $listener.GetContext()
                if ($case -eq 'timeout' -and $context.Request.Url.AbsolutePath -eq '/api/v1/task-groups') { Start-Sleep -Seconds 3 }
                $name = switch ($context.Request.Url.AbsolutePath) {
                    '/api/v1/task-groups' { if ($case -eq 'schema') { 'groups-count-mismatch.json' } elseif ($case -eq 'schema-count') { 'groups-count-null.json' } elseif ($case -eq 'empty') { 'empty.json' } else { 'groups.json' } }
                    '/api/v1/tasks' { if ($case -eq 'empty') { 'empty.json' } else { 'tasks.json' } }
                    '/api/v1/team-events' { if ($case -eq 'empty') { 'empty.json' } else { 'events.json' } }
                    default { $null }
                }
                if (-not $name) { $context.Response.StatusCode = 404; $bytes = [Text.Encoding]::UTF8.GetBytes('{}') }
                else { $context.Response.StatusCode = 200; $bytes = [IO.File]::ReadAllBytes((Join-Path $fixturePath $name)) }
                $context.Response.ContentType = 'application/json; charset=utf-8'; $context.Response.ContentLength64 = $bytes.Length
                $context.Response.OutputStream.Write($bytes,0,$bytes.Length); $context.Response.Close()
                if ($case -eq 'timeout' -or $context.Request.Url.AbsolutePath -eq '/api/v1/team-events' -or ($case -in @('schema','schema-count') -and $context.Request.Url.AbsolutePath -eq '/api/v1/tasks')) { break }
            }
        } catch { Write-Output "Server error: $_"; throw } finally { Start-Sleep -Milliseconds 300; $listener.Stop() }
    }
    Start-Sleep -Milliseconds 350
}
try {
    if ($Scenario -eq 'offline') {
        $closed = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0); $closed.Start(); $closedPort = ([Net.IPEndPoint]$closed.LocalEndpoint).Port; $closed.Stop()
        $base = "http://127.0.0.1:$closedPort"
    }
    $timeout = if ($Scenario -eq 'timeout') { 1 } else { 5 }
    Set-ViewerPlanCache $channel $now; & $viewer -ChannelPath $channel -RepoPath $repo -NoAbrir -LeerApi -ApiBaseUrl $base -ApiTimeoutSeconds $timeout | Out-Null
    $html = Get-Content (Join-Path $channel 'SEGUIMIENTO.html') -Raw
    switch ($Scenario) {
        'success' {
            foreach ($needle in @('Core API','task.accepted','accepted</code>','Unknown · API does not provide tokens','Unknown · API does not provide cost','Git and integration reports are read separately')) { if (-not $html.Contains($needle)) { throw ("Success fixture missing: $needle; panel: " + $html.Substring($html.IndexOf('<div class="tab" id="api">'), [Math]::Min(1200, $html.Length - $html.IndexOf('<div class="tab" id="api">')))) } }
            if ($html.Contains('<script>alert') -or -not $html.Contains('&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; review')) { throw 'API title was not HTML escaped.' }
            if ($html.Contains('Integrada · accepted') -or $html.Contains('Probada · accepted')) { throw 'Core accepted status was misrepresented as a legacy milestone.' }
        }
        'empty' { if ($html -notmatch 'API confirmed that groups, tasks, and events are empty') { throw 'Valid empty snapshot not labelled as API-confirmed empty.' } }
        'schema' { if ($html -notmatch 'API status unknown or unavailable' -or $html -notmatch 'Inconsistent API count' -or $html -match 'API confirmed that groups') { throw 'Invalid group counts were not shown as an error.' } }
        'schema-count' { if (-not $html.Contains('API status unknown or unavailable') -or -not $html.Contains('must be an integer') -or $html.Contains('API confirmed that groups')) { throw 'Null group count was not rejected as invalid schema.' } }
        'offline' { if ($html -notmatch 'API status unknown or unavailable' -or $html -match 'API confirmed that groups') { throw 'Offline API was presented as an empty successful board.' } }
        'timeout' { if ($html -notmatch 'API status unknown or unavailable' -or $html -match 'API confirmed that groups') { throw 'API timeout was presented as an empty successful board.' } }
    }
    if ($Scenario -eq 'success') {
        $defaultChannel = Join-Path $temp 'default'
        New-Item -ItemType Directory -Force (Join-Path $defaultChannel 'parches\pendientes'), (Join-Path $defaultChannel 'pruebas\integracion'), (Join-Path $defaultChannel 'tareas') | Out-Null
        Set-Content (Join-Path $defaultChannel 'RESUMEN.md') '# Local' -Encoding utf8
        Set-Content (Join-Path $defaultChannel 'ROADMAP_VISTA.md') '# Roadmap' -Encoding utf8
        Set-Content (Join-Path $defaultChannel 'pruebas\integracion\LECTURAS.md') '' -Encoding utf8
        Set-ViewerPlanCache $defaultChannel $now; & $viewer -ChannelPath $defaultChannel -RepoPath $repo -NoAbrir | Out-Null
        $defaultHtml = Get-Content (Join-Path $defaultChannel 'SEGUIMIENTO.html') -Raw
        if ($defaultHtml -match 'data-tab="api"') { throw 'Default Markdown mode unexpectedly enabled API tab.' }
    }
    Write-Host "ORQ-T40 fixture consumer: $Scenario PASS"
} finally {
    if ($job) { Stop-Job $job -ErrorAction SilentlyContinue; Remove-Job $job -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
