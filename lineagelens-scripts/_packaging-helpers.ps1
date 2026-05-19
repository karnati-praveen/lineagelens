# Shared packaging helpers for package-lite.ps1, package-plus.ps1, package-max.ps1.
# Dot-source this file from each mode-specific script:
#     . (Join-Path $PSScriptRoot "_packaging-helpers.ps1")

$ErrorActionPreference = "Stop"

function Get-LineageLensVersion {
    param([Parameter(Mandatory)] [string] $RepoRoot)
    $package = Get-Content (Join-Path $RepoRoot "package.json") | ConvertFrom-Json
    return $package.version
}

function Initialize-Bundle {
    param([Parameter(Mandatory)] [string] $BundleRoot)
    if (Test-Path $BundleRoot) {
        Remove-Item $BundleRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $BundleRoot | Out-Null
}

function Remove-PythonCaches {
    # Strip __pycache__ / .pytest_cache out of a copied tree.
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path $Path)) { return }
    Get-ChildItem $Path -Directory -Recurse -Force |
        Where-Object { $_.Name -in @('.pytest_cache', '__pycache__') } |
        Remove-Item -Recurse -Force
}

function Remove-EnvFiles {
    # Defence in depth: never ship .env / *.env into a release bundle.
    param([Parameter(Mandatory)] [string] $Path)
    if (-not (Test-Path $Path)) { return }
    Get-ChildItem $Path -Filter ".env" -Recurse -Force | Remove-Item -Force
    Get-ChildItem $Path -Filter "*.env" -Recurse -Force | Remove-Item -Force
}

function Copy-Backend {
    param(
        [Parameter(Mandatory)] [string] $RepoRoot,
        [Parameter(Mandatory)] [string] $BundleRoot
    )
    $dest = Join-Path $BundleRoot "lineagelens-backend"
    Copy-Item (Join-Path $RepoRoot "lineagelens-backend") $dest -Recurse -Force
    Remove-PythonCaches -Path $dest
    Remove-EnvFiles -Path $dest
}

function Copy-Proxy {
    param(
        [Parameter(Mandatory)] [string] $RepoRoot,
        [Parameter(Mandatory)] [string] $BundleRoot
    )
    $dest = Join-Path $BundleRoot "lineagelens-proxy"
    Copy-Item (Join-Path $RepoRoot "lineagelens-proxy") $dest -Recurse -Force
    Remove-PythonCaches -Path $dest
}

function Copy-McpServer {
    param(
        [Parameter(Mandatory)] [string] $RepoRoot,
        [Parameter(Mandatory)] [string] $BundleRoot
    )
    $dest = Join-Path $BundleRoot "lineagelens-mcp"
    Copy-Item (Join-Path $RepoRoot "lineagelens-mcp") $dest -Recurse -Force
    Remove-PythonCaches -Path $dest
}

function Copy-Docs {
    param(
        [Parameter(Mandatory)] [string] $RepoRoot,
        [Parameter(Mandatory)] [string] $BundleRoot,
        [Parameter(Mandatory)] [string[]] $DocFiles
    )
    $docsDest = Join-Path $BundleRoot "lineagelens-docs"
    New-Item -ItemType Directory -Force -Path $docsDest | Out-Null
    foreach ($f in $DocFiles) {
        Copy-Item (Join-Path $RepoRoot "lineagelens-docs\$f") (Join-Path $docsDest $f) -Force
    }
}

function Copy-GithubActionsWorkflows {
    # Workflows the user drops into their own repo's .github/workflows/.
    param(
        [Parameter(Mandatory)] [string] $RepoRoot,
        [Parameter(Mandatory)] [string] $BundleRoot
    )
    $dest = Join-Path $BundleRoot "lineagelens-github-actions"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $files = @(
        "lineagelens-annotate.yml",
        "lineagelens-risk-check.yml",
        "pr-policy-check.yml",
        "provenance-review.yml"
    )
    foreach ($f in $files) {
        Copy-Item (Join-Path $RepoRoot ".github\workflows\$f") (Join-Path $dest $f) -Force
    }
}

function Copy-NativeBackendScripts {
    # The PS1 helpers for running the backend without Docker.
    param(
        [Parameter(Mandatory)] [string] $RepoRoot,
        [Parameter(Mandatory)] [string] $BundleRoot
    )
    $scriptsDest = Join-Path $BundleRoot "lineagelens-scripts"
    New-Item -ItemType Directory -Force -Path $scriptsDest | Out-Null
    Copy-Item (Join-Path $RepoRoot "lineagelens-scripts\run-backend-native.ps1") (Join-Path $scriptsDest "run-backend-native.ps1") -Force
    Copy-Item (Join-Path $RepoRoot "lineagelens-scripts\test-backend-native.ps1") (Join-Path $scriptsDest "test-backend-native.ps1") -Force
}

function Assert-NoAdLeakage {
    # The lineagelens-ad/ folder is internal and must never be in a release.
    param([Parameter(Mandatory)] [string] $BundleRoot)
    if (Test-Path (Join-Path $BundleRoot "lineagelens-ad")) {
        throw "Packaging safeguard failed: lineagelens-ad was copied into the bundle at $BundleRoot."
    }
}

function Compress-AndCleanBundle {
    # Zip the bundle dir to ArtifactPath, then delete the working bundle dir.
    param(
        [Parameter(Mandatory)] [string] $BundleRoot,
        [Parameter(Mandatory)] [string] $ArtifactPath
    )
    if (Test-Path $ArtifactPath) {
        Remove-Item $ArtifactPath -Force
    }
    Compress-Archive -Path $BundleRoot -DestinationPath $ArtifactPath -Force
    Remove-Item $BundleRoot -Recurse -Force
}
