param([switch]$EmitFixture)
$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '../../ver-seguimiento.ps1'
$ast = [Management.Automation.Language.Parser]::ParseFile((Resolve-Path $source), [ref]$null, [ref]$null)
foreach ($name in @('Encode','ConvertTo-PlanDate','Read-PlanJson','Invoke-PlanReport','Get-PlanReport','Format-PlanUsage','Get-ViewerNow')) {
    $node = $ast.Find({param($n) $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name}, $true)
    Invoke-Expression $node.Extent.Text
}
function Assert($condition, $message) { if (-not $condition) { throw $message } }
$channel = Join-Path ([IO.Path]::GetTempPath()) ('plan-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory "$channel/contexto" -Force | Out-Null
$script:nowOverride = [datetime]'2030-10-29T12:00:00Z'
try {
    $report = @{ apps = @(
        @{app='codex';plan='<Plus>';source=@{kind='synthetic';observed_at='2030-10-29T11:59:00Z'};windows=@(@{window='five_hour';used_percent=40;elapsed_percent=50;resets_at='2030-10-29T14:00:00Z'})},
        @{app='copilot';source=@{kind='synthetic';observed_at='2030-10-28T12:00:00Z'};windows=@(@{window='monthly';used_percent=99.5;elapsed_percent=92;resets_at='2030-11-01T00:00:00Z';last_hit_at='2030-10-29T11:00:00Z'})}
    ) }
    @{app='claude';plan='Example';source='synthetic';observed_at='2030-10-29T11:55:00Z';windows=@(@{window='weekly';used_percent=20;resets_at='2030-11-03T12:00:00Z'})} | ConvertTo-Json -Depth 12 | Set-Content "$channel/contexto/uso-claude.json"
    $codex = Format-PlanUsage Codex $report
    $copilot = Format-PlanUsage Copilot $report
    $claude = Format-PlanUsage Claude $report
    Assert ($codex.Contains('&lt;Plus&gt;') -and $codex.Contains('40 % used')) 'plan and percent escaped'
    Assert ($codex.Contains('within elapsed time') -and $codex.Contains('1 min ago')) 'pace and freshness'
    $localReset = ([datetimeoffset]'2030-11-01T00:00:00Z').ToLocalTime().ToString('dd/MM HH:mm zzz')
    Assert ($copilot.Contains('99.5 % used') -and $copilot.Contains("quota unavailable until $localReset")) 'quota independent of percent'
    Assert ($copilot.Contains('stale data')) 'old observation'
    Assert ($claude.Contains('Example') -and $claude.Contains('20 % used')) 'Claude orchestrator snapshot'
    Assert ((Format-PlanUsage Unknown $report).Contains('No data')) 'missing app'
    Set-Content "$channel/contexto/uso-claude.json" '{broken'
    Assert ((Format-PlanUsage Claude $report).Contains('No data')) 'malformed snapshot'
    $script:nowOverride = [datetime]'2030-11-02T00:00:00Z'
    $expired = Format-PlanUsage Copilot $report
    Assert ($expired.Contains('window expired') -and -not $expired.Contains('99.5 % used') -and -not $expired.Contains('quota unavailable until')) 'expired percent and quota suppressed'
    $script:nowOverride = [datetime]'2030-10-29T12:00:00Z'
    $script:calls = 0
    function Invoke-PlanReport { $script:calls++; return $report }
    [void](Get-PlanReport); [void](Get-PlanReport)
    Assert ($script:calls -eq 1) 'cache prevents repeat reads'
    $script:nowOverride = $script:nowOverride.AddMinutes(6)
    function Invoke-PlanReport { $script:calls++; return $null }
    $retained = Get-PlanReport
    [void](Get-PlanReport)
    Assert ($script:calls -eq 2 -and $retained.apps.Count -eq 2) 'failure retains cache and backs off'
    # Export only synthetic rendered markup for the browser layout check.
    $css = [regex]::Match([IO.File]::ReadAllText((Resolve-Path $source)), '<style>([\s\S]*?)</style>').Groups[1].Value
    $markup = "<style>$css</style><div class='agent-grid'><section class='panel agent-card'>$codex$copilot$claude</section></div>"
    $markup | Set-Content (Join-Path $PSScriptRoot 'fixture.tmp.html')
    if ($EmitFixture) { 'FIXTURE_BASE64:' + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($markup)) }
    'PASS: synthetic plans, missing/malformed/expired data, quota, cache and failure backoff'
} finally {
    Remove-Item -LiteralPath (Join-Path $PSScriptRoot 'fixture.tmp.html') -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $channel -Recurse -Force
}


