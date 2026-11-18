function Set-ViewerPlanCache([string]$Channel, [Nullable[datetime]]$At = $null) {
    $context = Join-Path $Channel 'contexto'
    [void][IO.Directory]::CreateDirectory($context)
    $now = if ($null -ne $At) { ([datetime]$At).ToUniversalTime() } else { [datetime]::UtcNow }
    @{ attempted_at = $now.ToString('o'); report = @{ apps = @() } } |
        ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $context 'usage-plans-cache.json') -Encoding utf8
}
