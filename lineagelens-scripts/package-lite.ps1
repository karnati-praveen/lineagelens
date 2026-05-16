$ErrorActionPreference = "Stop"

$repoRoot   = Split-Path -Parent $PSScriptRoot
$releaseDir = Join-Path $repoRoot "lineagelens-releases\lite"
$composeFile = Join-Path $repoRoot "lineagelens-deploy\docker-compose.lite.yml"
$envExample  = Join-Path $repoRoot "lineagelens-deploy\.env.lite.example"

Push-Location $repoRoot
try {
    $pkg     = Get-Content (Join-Path $repoRoot "package.json") | ConvertFrom-Json
    $version = $pkg.version
    $artifactName = "lineagelens-lite-$version.zip"
    $artifactPath = Join-Path $releaseDir $artifactName
    $bundleRoot   = Join-Path $env:TEMP "lineagelens-lite-$version"

    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

    if (Test-Path $bundleRoot) { Remove-Item $bundleRoot -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null

    # Backend source
    Copy-Item (Join-Path $repoRoot "lineagelens-backend") (Join-Path $bundleRoot "lineagelens-backend") -Recurse -Force
    Get-ChildItem (Join-Path $bundleRoot "lineagelens-backend") -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('.pytest_cache', '__pycache__') } |
        Remove-Item -Recurse -Force
    Get-ChildItem (Join-Path $bundleRoot "lineagelens-backend") -Filter ".env"  -Recurse -Force | Remove-Item -Force
    Get-ChildItem (Join-Path $bundleRoot "lineagelens-backend") -Filter "*.env" -Recurse -Force | Remove-Item -Force

    # Deploy files
    New-Item -ItemType Directory -Force -Path (Join-Path $bundleRoot "lineagelens-deploy") | Out-Null
    Copy-Item $composeFile (Join-Path $bundleRoot "lineagelens-deploy\docker-compose.lite.yml") -Force
    Copy-Item $envExample  (Join-Path $bundleRoot "lineagelens-deploy\.env.example") -Force

    # Scripts
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\quickstart-lite.sh") (Join-Path $bundleRoot "quickstart.sh") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\reset-lite.sh")      (Join-Path $bundleRoot "reset.sh")      -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\commands-lite.md")   (Join-Path $bundleRoot "COMMANDS.md")   -Force

    if (Test-Path $artifactPath) { Remove-Item $artifactPath -Force }
    Compress-Archive -Path $bundleRoot -DestinationPath $artifactPath -Force
    Remove-Item $bundleRoot -Recurse -Force

    Write-Host ""
    Write-Host "Lite package ready: $artifactPath"
    Write-Host "Install:  unzip $artifactName && bash quickstart.sh"
}
finally {
    Pop-Location
}
