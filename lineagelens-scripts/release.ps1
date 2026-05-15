$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

# ── Bump patch version ────────────────────────────────────────────────
$pkgPath = Join-Path $repoRoot "package.json"
$pkg = Get-Content $pkgPath -Raw | ConvertFrom-Json

$parts = $pkg.version -split "\."
$parts[2] = [int]$parts[2] + 1
$newVersion = $parts -join "."
$pkg.version = $newVersion

# Write back preserving formatting
$pkg | ConvertTo-Json -Depth 100 | Set-Content $pkgPath -Encoding utf8
Write-Host "Bumped version: $($parts[0]).$($parts[1]).$(([int]$parts[2] - 1)) → $newVersion"

# ── Run all three package scripts ─────────────────────────────────────
$scripts = @("package-base.ps1", "package-plus.ps1", "package-max.ps1")
foreach ($s in $scripts) {
    $scriptPath = Join-Path $PSScriptRoot $s
    Write-Host ""
    Write-Host "── Running $s ──"
    & powershell -ExecutionPolicy Bypass -File $scriptPath
    if ($LASTEXITCODE -ne 0) {
        throw "$s failed (exit $LASTEXITCODE). Version was already bumped to $newVersion."
    }
}

Write-Host ""
Write-Host "Release $newVersion complete."
Write-Host "  lineagelens-releases/base/lineagelens-base-$newVersion.vsix  (VS Code extension)"
Write-Host "  lineagelens-releases/plus/lineagelens-plus-$newVersion.zip"
Write-Host "  lineagelens-releases/max/lineagelens-max-$newVersion.zip"
