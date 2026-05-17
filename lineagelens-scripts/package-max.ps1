$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$releaseDir = Join-Path $repoRoot "lineagelens-releases\\max"
$envExample = Join-Path $repoRoot "lineagelens-deploy\\.env.max.example"
$composeFile = Join-Path $repoRoot "lineagelens-deploy\\docker-compose.max.yml"

Push-Location $repoRoot
try {
    $package = Get-Content (Join-Path $repoRoot "package.json") | ConvertFrom-Json
    $version = $package.version
    $artifactName = "lineagelens-max-$version.zip"
    $artifactPath = Join-Path $releaseDir $artifactName
    $bundleRoot = Join-Path $env:TEMP "lineagelens-max-$version"

    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

    if (Test-Path $bundleRoot) {
        Remove-Item $bundleRoot -Recurse -Force
    }

    New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null

    if (Test-Path (Join-Path $bundleRoot "lineagelens-ad")) {
        Remove-Item (Join-Path $bundleRoot "lineagelens-ad") -Recurse -Force
    }

    # Backend
    Copy-Item (Join-Path $repoRoot "lineagelens-backend") (Join-Path $bundleRoot "lineagelens-backend") -Recurse -Force
    Get-ChildItem (Join-Path $bundleRoot "lineagelens-backend") -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('.pytest_cache', '__pycache__') } |
        Remove-Item -Recurse -Force
    # Never ship secret-bearing files
    Get-ChildItem (Join-Path $bundleRoot "lineagelens-backend") -Filter ".env" -Recurse -Force |
        Remove-Item -Force
    Get-ChildItem (Join-Path $bundleRoot "lineagelens-backend") -Filter "*.env" -Recurse -Force |
        Remove-Item -Force

    # Proxy
    Copy-Item (Join-Path $repoRoot "lineagelens-proxy") (Join-Path $bundleRoot "lineagelens-proxy") -Recurse -Force
    Get-ChildItem (Join-Path $bundleRoot "lineagelens-proxy") -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('__pycache__') } |
        Remove-Item -Recurse -Force

    # Deploy
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "lineagelens-deploy") | Out-Null
    Copy-Item $composeFile (Join-Path $bundleRoot "lineagelens-deploy\docker-compose.max.yml") -Force
    Copy-Item $envExample (Join-Path $bundleRoot "lineagelens-deploy\.env.example") -Force

    # MCP server
    Copy-Item (Join-Path $repoRoot "lineagelens-mcp") (Join-Path $bundleRoot "lineagelens-mcp") -Recurse -Force
    Get-ChildItem (Join-Path $bundleRoot "lineagelens-mcp") -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('__pycache__') } |
        Remove-Item -Recurse -Force

    # Docs
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "lineagelens-docs") | Out-Null
    Copy-Item (Join-Path $repoRoot "lineagelens-docs\native-backend.md") (Join-Path $bundleRoot "lineagelens-docs\native-backend.md") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-docs\architecture.md") (Join-Path $bundleRoot "lineagelens-docs\architecture.md") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-docs\shipping-modes.md") (Join-Path $bundleRoot "lineagelens-docs\shipping-modes.md") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-docs\lightweight-adapters.md") (Join-Path $bundleRoot "lineagelens-docs\lightweight-adapters.md") -Force

    # Scripts
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "lineagelens-scripts") | Out-Null
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\run-backend-native.ps1") (Join-Path $bundleRoot "lineagelens-scripts\run-backend-native.ps1") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\test-backend-native.ps1") (Join-Path $bundleRoot "lineagelens-scripts\test-backend-native.ps1") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\debug.sh") (Join-Path $bundleRoot "debug.sh") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\debug.ps1") (Join-Path $bundleRoot "debug.ps1") -Force

    # README
    Copy-Item (Join-Path $repoRoot "README.md") (Join-Path $bundleRoot "README.md") -Force

    # GitHub Actions workflows
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "lineagelens-github-actions") | Out-Null
    Copy-Item (Join-Path $repoRoot ".github\workflows\lineagelens-annotate.yml") (Join-Path $bundleRoot "lineagelens-github-actions\lineagelens-annotate.yml") -Force
    Copy-Item (Join-Path $repoRoot ".github\workflows\lineagelens-risk-check.yml") (Join-Path $bundleRoot "lineagelens-github-actions\lineagelens-risk-check.yml") -Force
    Copy-Item (Join-Path $repoRoot ".github\workflows\pr-policy-check.yml") (Join-Path $bundleRoot "lineagelens-github-actions\pr-policy-check.yml") -Force
    Copy-Item (Join-Path $repoRoot ".github\workflows\provenance-review.yml") (Join-Path $bundleRoot "lineagelens-github-actions\provenance-review.yml") -Force

    npm run compile
    npm test
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\quickstart-max.sh") (Join-Path $bundleRoot "quickstart.sh") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\reset-max.sh") (Join-Path $bundleRoot "reset.sh") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\commands-max.md") (Join-Path $bundleRoot "COMMANDS.md") -Force

    if (Test-Path (Join-Path $bundleRoot "lineagelens-ad")) {
        throw "Packaging safeguard failed: lineagelens-ad was copied into the Max bundle."
    }

    if (Test-Path $artifactPath) {
        Remove-Item $artifactPath -Force
    }

    Compress-Archive -Path $bundleRoot -DestinationPath $artifactPath -Force

    Remove-Item $bundleRoot -Recurse -Force
    Write-Host "Max package ready: $artifactPath"
}
finally {
    Pop-Location
}
