. (Join-Path $PSScriptRoot "_packaging-helpers.ps1")

$repoRoot     = Split-Path -Parent $PSScriptRoot
$releaseDir   = Join-Path $repoRoot "lineagelens-releases\lite"
$composeFile  = Join-Path $repoRoot "lineagelens-deploy\docker-compose.lite.yml"
$envExample   = Join-Path $repoRoot "lineagelens-deploy\.env.lite.example"

Push-Location $repoRoot
try {
    $version       = Get-LineageLensVersion -RepoRoot $repoRoot
    $artifactName  = "lineagelens-lite-$version.zip"
    $artifactPath  = Join-Path $releaseDir $artifactName
    $bundleRoot    = Join-Path $env:TEMP "lineagelens-lite-$version"

    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
    Initialize-Bundle -BundleRoot $bundleRoot

    Copy-Backend -RepoRoot $repoRoot -BundleRoot $bundleRoot
    Copy-Proxy   -RepoRoot $repoRoot -BundleRoot $bundleRoot

    # Lite-specific deploy files
    $deployDest = Join-Path $bundleRoot "lineagelens-deploy"
    New-Item -ItemType Directory -Force -Path $deployDest | Out-Null
    Copy-Item $composeFile (Join-Path $deployDest "docker-compose.lite.yml") -Force
    Copy-Item $envExample  (Join-Path $deployDest ".env.example") -Force

    Copy-Docs -RepoRoot $repoRoot -BundleRoot $bundleRoot -DocFiles @(
        "architecture.md",
        "lightweight-adapters.md",
        "shipping-modes.md"
    )

    # Lite-specific top-level files
    Copy-Item (Join-Path $repoRoot "README.md")                              (Join-Path $bundleRoot "README.md") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\quickstart-lite.sh") (Join-Path $bundleRoot "quickstart.sh") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\reset-lite.sh")      (Join-Path $bundleRoot "reset.sh") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\commands-lite.md")   (Join-Path $bundleRoot "COMMANDS.md") -Force

    Assert-NoAdLeakage         -BundleRoot $bundleRoot
    Compress-AndCleanBundle    -BundleRoot $bundleRoot -ArtifactPath $artifactPath

    Write-Host ""
    Write-Host "Lite package ready: $artifactPath"
    Write-Host "Install:  unzip $artifactName && bash quickstart.sh"
}
finally {
    Pop-Location
}
