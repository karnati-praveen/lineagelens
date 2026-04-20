$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$packageJson = Join-Path $repoRoot "package.json"
$releaseDir = Join-Path $repoRoot "releases\\solo"

Push-Location $repoRoot
try {
    $package = Get-Content $packageJson | ConvertFrom-Json
    $version = $package.version
    $artifactName = "lineagelens-solo-$version.vsix"
    $artifactPath = Join-Path $releaseDir $artifactName

    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

    npm run compile
    npm test
    npx @vscode/vsce package --out $artifactPath
    Write-Host "Solo package ready: $artifactPath"
}
finally {
    Pop-Location
}
