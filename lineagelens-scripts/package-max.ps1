. (Join-Path $PSScriptRoot "_packaging-helpers.ps1")

$repoRoot     = Split-Path -Parent $PSScriptRoot
$releaseDir   = Join-Path $repoRoot "lineagelens-releases\max"
$envExample   = Join-Path $repoRoot "lineagelens-deploy\.env.max.example"
$composeFile  = Join-Path $repoRoot "lineagelens-deploy\docker-compose.max.yml"

Push-Location $repoRoot
try {
    $version       = Get-LineageLensVersion -RepoRoot $repoRoot
    $artifactName  = "lineagelens-max-$version.zip"
    $artifactPath  = Join-Path $releaseDir $artifactName
    $bundleRoot    = Join-Path $env:TEMP "lineagelens-max-$version"

    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
    Initialize-Bundle -BundleRoot $bundleRoot

    Copy-Backend    -RepoRoot $repoRoot -BundleRoot $bundleRoot
    Copy-Proxy      -RepoRoot $repoRoot -BundleRoot $bundleRoot
    Copy-McpServer  -RepoRoot $repoRoot -BundleRoot $bundleRoot

    # Max-specific deploy files
    $deployDest = Join-Path $bundleRoot "lineagelens-deploy"
    New-Item -ItemType Directory -Force -Path $deployDest | Out-Null
    Copy-Item $composeFile (Join-Path $deployDest "docker-compose.max.yml") -Force
    Copy-Item $envExample (Join-Path $deployDest ".env.example") -Force

    Copy-Docs -RepoRoot $repoRoot -BundleRoot $bundleRoot -DocFiles @(
        "native-backend.md",
        "architecture.md",
        "shipping-modes.md",
        "lightweight-adapters.md"
    )
    Copy-NativeBackendScripts     -RepoRoot $repoRoot -BundleRoot $bundleRoot
    Copy-GithubActionsWorkflows   -RepoRoot $repoRoot -BundleRoot $bundleRoot

    npm run compile
    npm test

    # Max-specific top-level files
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\debug.sh")          (Join-Path $bundleRoot "debug.sh") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\debug.ps1")         (Join-Path $bundleRoot "debug.ps1") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\quickstart-max.sh") (Join-Path $bundleRoot "quickstart.sh") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\reset-max.sh")     (Join-Path $bundleRoot "reset.sh") -Force
    Copy-Item (Join-Path $repoRoot "lineagelens-scripts\commands-max.md")  (Join-Path $bundleRoot "COMMANDS.md") -Force
    Copy-Item (Join-Path $repoRoot "README.md")                              (Join-Path $bundleRoot "README.md") -Force

    Assert-NoAdLeakage         -BundleRoot $bundleRoot
    Compress-AndCleanBundle    -BundleRoot $bundleRoot -ArtifactPath $artifactPath

    Write-Host "Max package ready: $artifactPath"
}
finally {
    Pop-Location
}
