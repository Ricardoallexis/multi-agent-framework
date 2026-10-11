param([Parameter(Mandatory)][string]$Path)
Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue
