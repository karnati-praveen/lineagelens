$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$releaseDir = Join-Path $repoRoot "releases\\max"
$envExample = Join-Path $repoRoot "deploy\\.env.max.example"
$composeFile = Join-Path $repoRoot "deploy\\docker-compose.max.yml"

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

    # Backend
    Copy-Item (Join-Path $repoRoot "backend") (Join-Path $bundleRoot "backend") -Recurse -Force
    Get-ChildItem (Join-Path $bundleRoot "backend") -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('.pytest_cache', '__pycache__') } |
        Remove-Item -Recurse -Force

    # Proxy
    Copy-Item (Join-Path $repoRoot "proxy") (Join-Path $bundleRoot "proxy") -Recurse -Force
    Get-ChildItem (Join-Path $bundleRoot "proxy") -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('__pycache__') } |
        Remove-Item -Recurse -Force

    # Deploy
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "deploy") | Out-Null
    Copy-Item $composeFile (Join-Path $bundleRoot "deploy\docker-compose.max.yml") -Force
    Copy-Item $envExample (Join-Path $bundleRoot "deploy\.env.example") -Force

    # Docs
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "docs") | Out-Null
    Copy-Item (Join-Path $repoRoot "docs\native-backend.md") (Join-Path $bundleRoot "docs\native-backend.md") -Force
    Copy-Item (Join-Path $repoRoot "docs\architecture.md") (Join-Path $bundleRoot "docs\architecture.md") -Force
    Copy-Item (Join-Path $repoRoot "docs\shipping-modes.md") (Join-Path $bundleRoot "docs\shipping-modes.md") -Force

    # Scripts
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "scripts") | Out-Null
    Copy-Item (Join-Path $repoRoot "scripts\run-backend-native.ps1") (Join-Path $bundleRoot "scripts\run-backend-native.ps1") -Force
    Copy-Item (Join-Path $repoRoot "scripts\test-backend-native.ps1") (Join-Path $bundleRoot "scripts\test-backend-native.ps1") -Force
    Copy-Item (Join-Path $repoRoot "scripts\debug.sh") (Join-Path $bundleRoot "debug.sh") -Force
    Copy-Item (Join-Path $repoRoot "scripts\debug.ps1") (Join-Path $bundleRoot "debug.ps1") -Force

    npm run compile
    npm test
    Copy-Item (Join-Path $repoRoot "scripts\quickstart-max.sh") (Join-Path $bundleRoot "quickstart.sh") -Force
    Copy-Item (Join-Path $repoRoot "scripts\reset-max.sh") (Join-Path $bundleRoot "reset.sh") -Force
    Copy-Item (Join-Path $repoRoot "scripts\commands-max.md") (Join-Path $bundleRoot "COMMANDS.md") -Force

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
