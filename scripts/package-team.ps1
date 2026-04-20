$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$releaseDir = Join-Path $repoRoot "releases\\team"
$envExample = Join-Path $repoRoot "deploy\\.env.team.example"
$composeFile = Join-Path $repoRoot "deploy\\docker-compose.team.yml"

Push-Location $repoRoot
try {
    $package = Get-Content (Join-Path $repoRoot "package.json") | ConvertFrom-Json
    $version = $package.version
    $artifactName = "lineagelens-team-$version.zip"
    $artifactPath = Join-Path $releaseDir $artifactName
    $bundleRoot = Join-Path $env:TEMP "lineagelens-team-$version"

    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

    if (Test-Path $bundleRoot) {
        Remove-Item $bundleRoot -Recurse -Force
    }

    New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null
    Copy-Item (Join-Path $repoRoot "backend") (Join-Path $bundleRoot "backend") -Recurse -Force
    Get-ChildItem (Join-Path $bundleRoot "backend") -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('.pytest_cache', '__pycache__') } |
        Remove-Item -Recurse -Force
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "deploy") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "scripts") | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "docs") | Out-Null

    npm run compile
    npm test
    Copy-Item $composeFile (Join-Path $bundleRoot "deploy\docker-compose.team.yml") -Force
    Copy-Item $envExample (Join-Path $bundleRoot "deploy\.env.team.example") -Force
    Copy-Item (Join-Path $repoRoot "docs\native-backend.md") (Join-Path $bundleRoot "docs\native-backend.md") -Force
    Copy-Item (Join-Path $repoRoot "scripts\run-backend-native.ps1") (Join-Path $bundleRoot "scripts\run-backend-native.ps1") -Force
    Copy-Item (Join-Path $repoRoot "scripts\test-backend-native.ps1") (Join-Path $bundleRoot "scripts\test-backend-native.ps1") -Force

    if (Test-Path $artifactPath) {
        Remove-Item $artifactPath -Force
    }

    Compress-Archive -Path $bundleRoot -DestinationPath $artifactPath -Force

    Remove-Item $bundleRoot -Recurse -Force
    Write-Host "Team package ready: $artifactPath"
}
finally {
    Pop-Location
}
