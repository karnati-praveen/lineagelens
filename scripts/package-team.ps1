$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$packageJson = Join-Path $repoRoot "package.json"
$releaseDir = Join-Path $repoRoot "releases\\team"
$envExample = Join-Path $repoRoot ".env.team.example"
$composeFile = Join-Path $repoRoot "docker-compose.team.yml"

Push-Location $repoRoot
try {
    $package = Get-Content $packageJson | ConvertFrom-Json
    $version = $package.version
    $artifactName = "lineagelens-team-$version.vsix"
    $artifactPath = Join-Path $repoRoot $artifactName

    Remove-Item $artifactPath -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null

    npm run compile
    npm test
    npx @vscode/vsce package --out $artifactName
    docker compose -f $composeFile build backend

    Copy-Item $artifactPath (Join-Path $releaseDir $artifactName) -Force
    Copy-Item $composeFile (Join-Path $releaseDir "docker-compose.team.yml") -Force
    Copy-Item $envExample (Join-Path $releaseDir ".env.team.example") -Force
    Write-Host "Team package ready: $releaseDir"
}
finally {
    Pop-Location
}
