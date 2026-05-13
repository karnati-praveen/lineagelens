$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$releaseDir = Join-Path $repoRoot "lineagelens-releases\\base"
$composeFile = Join-Path $repoRoot "lineagelens-deploy\\docker-compose.base.yml"

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
    Copy-Item (Join-Path $repoRoot "lineagelens-backend") (Join-Path $bundleRoot "lineagelens-backend") -Recurse -Force
    Get-ChildItem (Join-Path $bundleRoot "lineagelens-backend") -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('.pytest_cache', '__pycache__') } |
        Remove-Item -Recurse -Force
    # Never ship secret-bearing files
    Get-ChildItem (Join-Path $bundleRoot "lineagelens-backend") -Filter ".env" -Recurse -Force |
        Remove-Item -Force
    Get-ChildItem (Join-Path $bundleRoot "lineagelens-backend") -Filter "*.env" -Recurse -Force |
        Remove-Item -Force

    # Deploy
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "lineagelens-deploy") | Out-Null
    Copy-Item $composeFile (Join-Path $bundleRoot "lineagelens-deploy\docker-compose.base.yml") -Force

    # Docs
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "lineagelens-docs") | Out-Null
    Copy-Item (Join-Path $repoRoot "lineagelens-docs\native-backend.md") (Join-Path $bundleRoot "lineagelens-docs\native-backend.md") -Force

    # Scripts
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "lineagelens-scripts") | Out-Null
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\debug.sh") (Join-Path $bundleRoot "debug.sh") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\debug.ps1") (Join-Path $bundleRoot "debug.ps1") -Force

    npm run compile
    npm test
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\quickstart-base.sh") (Join-Path $bundleRoot "quickstart.sh") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\reset-base.sh") (Join-Path $bundleRoot "reset.sh") -Force

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
