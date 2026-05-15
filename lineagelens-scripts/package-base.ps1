$ErrorActionPreference = "Stop"

$repoRoot   = Split-Path -Parent $PSScriptRoot
$extDir     = Join-Path $repoRoot "lineagelens-base-extension"
$releaseDir = Join-Path $repoRoot "lineagelens-releases\base"

Push-Location $extDir
try {
    $pkg     = Get-Content (Join-Path $extDir "package.json") | ConvertFrom-Json
    $version = $pkg.version
    $vsix    = "lineagelens-base-$version.vsix"

    Write-Host "Building lineagelens-base v$version..."

    # Install deps (CI-clean, no postinstall scripts)
    npm ci --ignore-scripts

    # Compile TypeScript → dist/extension.js via esbuild
    npm run build

    # Package VSIX
    npx vsce package --no-dependencies --out (Join-Path $releaseDir $vsix)

    # Copy VSIX into releases dir
    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

    Write-Host ""
    Write-Host "Base extension ready: $releaseDir\$vsix"
    Write-Host "Install:  code --install-extension $releaseDir\$vsix"
    Write-Host "Publish:  npx vsce publish (requires PAT)"
}
finally {
    Pop-Location
}
