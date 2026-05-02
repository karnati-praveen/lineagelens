param(
    [int]$Port = 8788
)

$proxyUrl = "http://127.0.0.1:$Port"

$env:ANTHROPIC_BASE_URL = $proxyUrl
$env:OPENAI_BASE_URL = $proxyUrl
$env:HTTP_PROXY = $proxyUrl
$env:HTTPS_PROXY = $proxyUrl
$env:NO_PROXY = "127.0.0.1,localhost"

Write-Host "LineageLens proxy environment enabled for this PowerShell session:"
Write-Host "  ANTHROPIC_BASE_URL=$env:ANTHROPIC_BASE_URL"
Write-Host "  OPENAI_BASE_URL=$env:OPENAI_BASE_URL"
Write-Host "  HTTP_PROXY=$env:HTTP_PROXY"
Write-Host "  HTTPS_PROXY=$env:HTTPS_PROXY"
Write-Host ""
Write-Host "Launch Claude Code, OpenAI-compatible CLIs, or SDK commands from this shell so they inherit the proxy settings."
