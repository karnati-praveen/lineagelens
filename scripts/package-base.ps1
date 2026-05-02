$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$releaseDir = Join-Path $repoRoot "releases\\base"
$composeFile = Join-Path $repoRoot "deploy\\docker-compose.base.yml"

Push-Location $repoRoot
try {
    $package = Get-Content (Join-Path $repoRoot "package.json") | ConvertFrom-Json
    $version = $package.version
    $artifactName = "lineagelens-base-$version.zip"
    $artifactPath = Join-Path $releaseDir $artifactName
    $bundleRoot = Join-Path $env:TEMP "lineagelens-base-$version"

    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

    if (Test-Path $bundleRoot) {
        Remove-Item $bundleRoot -Recurse -Force
    }

    New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null

    if (Test-Path (Join-Path $bundleRoot "lineagelens-ad")) {
        Remove-Item (Join-Path $bundleRoot "lineagelens-ad") -Recurse -Force
    }

    # Backend
    Copy-Item (Join-Path $repoRoot "backend") (Join-Path $bundleRoot "backend") -Recurse -Force
    Get-ChildItem (Join-Path $bundleRoot "backend") -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('.pytest_cache', '__pycache__') } |
        Remove-Item -Recurse -Force
    # Never ship secret-bearing files
    Get-ChildItem (Join-Path $bundleRoot "backend") -Filter ".env" -Recurse -Force |
        Remove-Item -Force
    Get-ChildItem (Join-Path $bundleRoot "backend") -Filter "*.env" -Recurse -Force |
        Remove-Item -Force

    # Deploy
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "deploy") | Out-Null
    Copy-Item $composeFile (Join-Path $bundleRoot "deploy\docker-compose.base.yml") -Force

    # Docs
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "docs") | Out-Null
    Copy-Item (Join-Path $repoRoot "docs\native-backend.md") (Join-Path $bundleRoot "docs\native-backend.md") -Force

    # Scripts
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "scripts") | Out-Null
    Copy-Item (Join-Path $repoRoot "scripts\debug.sh") (Join-Path $bundleRoot "debug.sh") -Force
    Copy-Item (Join-Path $repoRoot "scripts\debug.ps1") (Join-Path $bundleRoot "debug.ps1") -Force

    npm run compile
    npm test
    Copy-Item (Join-Path $repoRoot "scripts\quickstart-base.sh") (Join-Path $bundleRoot "quickstart.sh") -Force
    Copy-Item (Join-Path $repoRoot "scripts\reset-base.sh") (Join-Path $bundleRoot "reset.sh") -Force

    if (Test-Path (Join-Path $bundleRoot "lineagelens-ad")) {
        throw "Packaging safeguard failed: lineagelens-ad was copied into the Base bundle."
    }

    if (Test-Path $artifactPath) {
        Remove-Item $artifactPath -Force
    }

    Compress-Archive -Path $bundleRoot -DestinationPath $artifactPath -Force

    Remove-Item $bundleRoot -Recurse -Force
    Write-Host "Base package ready: $artifactPath"
}
finally {
    Pop-Location
}
