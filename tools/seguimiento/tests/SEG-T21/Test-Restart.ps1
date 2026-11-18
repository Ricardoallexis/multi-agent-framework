$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '../../ver-seguimiento.ps1'
$scriptText = [IO.File]::ReadAllText((Resolve-Path $source))
$ast = [Management.Automation.Language.Parser]::ParseFile((Resolve-Path $source), [ref]$null, [ref]$null)
foreach ($name in @('Get-RestartArguments', 'Test-ScriptUpdated')) {
    $node = $ast.Find({ param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name }, $true)
    Invoke-Expression $node.Extent.Text
}
function Assert-True([bool]$ok, [string]$message) { if (-not $ok) { throw "FAIL: $message" } }
$temp = Join-Path ([IO.Path]::GetTempPath()) ('seg restart ' + [guid]::NewGuid().ToString('N'))
try {
    [void][IO.Directory]::CreateDirectory($temp)
    $harness = Join-Path $temp 'viewer with spaces.ps1'
    $start = $scriptText.IndexOf('param(')
    $end = $scriptText.IndexOf('$script:apiBaseUrl')
    $prologue = $scriptText.Substring($start, $end - $start)
    [IO.File]::WriteAllText($harness, $prologue + "`n" + '@{ ChannelPath = $ChannelPath; RepoPath = $RepoPath; Roots = @($SubagentRoots); Api = $ApiBaseUrl; Timeout = $ApiTimeoutSeconds; LeerApi = [bool]$LeerApi; NoAbrir = [bool]$NoAbrir; Vigilar = [bool]$Vigilar; Reanudar = [bool]$Reanudar; Now = $NowOverride.ToString(''o'') } | ConvertTo-Json -Compress')
    $bound = @{ ChannelPath = 'C:\synthetic channel'; RepoPath = 'C:\synthetic repo'; SubagentRoots = @('C:\one folder', 'D:\another folder'); ApiBaseUrl = 'http://local test'; ApiTimeoutSeconds = 9; LeerApi = [switch]$true; NowOverride = [datetime]'2026-11-18T12:00:00Z' }   # $PSBoundParameters holds a switch as SwitchParameter
    $argv = @(Get-RestartArguments $harness $bound $true)
    $output = Join-Path $temp 'output.json'
    $process = Start-Process -FilePath (Get-Command pwsh).Source -ArgumentList $argv -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $output
    Assert-True ($process.ExitCode -eq 0) 'the restarted pwsh -File process exits successfully'
    $actual = Get-Content -LiteralPath $output -Raw | ConvertFrom-Json
    Assert-True ($actual.ChannelPath -eq $bound.ChannelPath -and $actual.RepoPath -eq $bound.RepoPath) 'paths with spaces survive'
    Assert-True ($actual.Roots.Count -eq 2 -and $actual.Roots[0] -eq $bound.SubagentRoots[0] -and $actual.Roots[1] -eq $bound.SubagentRoots[1]) 'string array survives'
    Assert-True ($actual.Api -eq $bound.ApiBaseUrl -and $actual.Timeout -eq 9 -and $actual.LeerApi -and $actual.Reanudar -and -not $actual.NoAbrir) 'bound values and window mode survive'
    Assert-True ($actual.Now -eq '2026-11-18T12:00:00.0000000Z') 'date override survives'
    $windowless = @(Get-RestartArguments $harness $bound $false)
    Assert-True ($windowless -contains '-NoAbrir' -and $windowless -contains '-Vigilar' -and $windowless -notcontains '-Reanudar') 'windowless mode survives'
    $stamp = (Get-Item -LiteralPath $harness).LastWriteTimeUtc
    Assert-True (-not (Test-ScriptUpdated (Join-Path $temp 'missing.ps1') $stamp)) 'missing file does not trigger restart'
    Assert-True (-not (Test-ScriptUpdated $harness $stamp)) 'unchanged file does not trigger restart'
    Assert-True (Test-ScriptUpdated $harness $stamp.AddSeconds(-1)) 'changed file triggers restart'
} finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Recurse -Force }
}
Write-Host 'SEG-T21 restart PASS'
