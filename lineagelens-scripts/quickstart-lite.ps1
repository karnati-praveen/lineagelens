# LineageLens Lite — Quick Start (Windows PowerShell 5.1)
# Single container, SQLite, zero external dependencies.
# Runs on a laptop or VM. Mirrors quickstart-lite.sh step-for-step.
#
# Usage: .\quickstart-lite.ps1
#
# Git Bash / WSL users: bash lineagelens-scripts/quickstart-lite.sh works too.

$ErrorActionPreference = "Stop"

# ── Output helpers (style from _packaging-helpers.ps1) ──────────────────────
function Write-Step {
    param([string]$Number, [string]$Text)
    Write-Host ""
    Write-Host "[$Number] $Text" -ForegroundColor Cyan
}
function Write-Ok   { param([string]$Text) Write-Host "  v  $Text" -ForegroundColor Green }
function Write-Info { param([string]$Text) Write-Host "  -> $Text" -ForegroundColor Yellow }
function Write-Die  {
    param([string]$Text)
    Write-Host ""
    Write-Host "  X  ERROR: $Text" -ForegroundColor Red
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "   LineageLens Lite  -  Quick Start        " -ForegroundColor Cyan
Write-Host "   Single container . SQLite . No deps     " -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# ── 1. Prerequisites ──────────────────────────────────────────────────────────
Write-Step "1/4" "Checking prerequisites"

# docker info also verifies Docker Desktop is running
$dockerInfoOut = docker info 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Die "Docker Desktop is not running (or Docker is not installed). Start Docker Desktop and re-run."
}

# Detect compose command — prefer v2 plugin (docker compose) over legacy binary
$ComposeArgs = $null
$null = docker compose version 2>&1
if ($LASTEXITCODE -eq 0) {
    $ComposeArgs = @("compose")
} else {
    $legacyCheck = docker-compose --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        $ComposeArgs = $null   # signal to use docker-compose as separate executable
        $ComposeBin = "docker-compose"
    } else {
        Write-Die "Docker Compose not found. Install from https://docs.docker.com/compose/install/"
    }
}

docker --version
if ($null -ne $ComposeArgs) {
    & docker $ComposeArgs version
    Write-Ok "Docker (Compose v2) is ready."
} else {
    & $ComposeBin --version
    Write-Ok "Docker Compose (legacy) is ready."
}

# ── 2. Resolve deploy directory ───────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DeployDir1 = Join-Path $ScriptDir "lineagelens-deploy"
$DeployDir2 = Join-Path (Split-Path -Parent $ScriptDir) "lineagelens-deploy"

if (Test-Path $DeployDir1) {
    $DeployDir = $DeployDir1
} elseif (Test-Path $DeployDir2) {
    $DeployDir = $DeployDir2
} else {
    Write-Die "Cannot find lineagelens-deploy. Checked:`n  $DeployDir1`n  $DeployDir2"
}

$EnvFile     = Join-Path $DeployDir ".env"
$ComposeFile = Join-Path $DeployDir "docker-compose.lite.yml"
$DataDir     = Join-Path $DeployDir "data"
$ProjectName = "lineagelens"
$BackendUrl  = "http://localhost:8787"

# ── 3. Generate secrets ───────────────────────────────────────────────────────
Write-Step "2/4" "Setting up configuration"

if (-not (Test-Path $DataDir)) {
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
}

if (Test-Path $EnvFile) {
    Write-Info ".env already exists - reusing existing secrets."
    $existing = Get-Content $EnvFile -Raw
    if (-not ($existing -match "(?m)^JWT_SECRET_KEY=.+")) {
        Write-Die "JWT_SECRET_KEY is missing in $EnvFile. Delete the file and re-run to regenerate."
    }
    Write-Ok "Secrets verified."
} else {
    # Use RandomNumberGenerator for cryptographically secure 48-byte hex secrets.
    # Get-Random is NOT suitable for key material.
    function New-RandomHex {
        param([int]$ByteCount)
        $rng   = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $bytes = New-Object byte[] $ByteCount
        $rng.GetBytes($bytes)
        $rng.Dispose()
        $hex = ""
        foreach ($b in $bytes) { $hex += $b.ToString("x2") }
        return $hex
    }

    $JwtSecret        = New-RandomHex -ByteCount 48
    $JwtRefreshSecret = New-RandomHex -ByteCount 48

    $envContent = "JWT_SECRET_KEY=$JwtSecret`r`nJWT_REFRESH_SECRET_KEY=$JwtRefreshSecret`r`n# Optional: set this to enable AI-powered explanations`r`n# EXPLAIN_LLM_API_KEY=sk-...`r`n# BACKEND_CORS_ORIGINS=http://localhost:3000`r`n"

    # Write UTF-8 WITHOUT BOM; python-dotenv chokes on the UTF-8 BOM that
    # PowerShell's default Out-File / Set-Content adds on Windows.
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($EnvFile, $envContent, $utf8NoBom)

    Write-Ok "JWT_SECRET_KEY         generated"
    Write-Ok "JWT_REFRESH_SECRET_KEY generated"
    Write-Ok "Written to: $EnvFile"
}

# ── 4. Start LineageLens ──────────────────────────────────────────────────────
Write-Step "3/4" "Starting LineageLens (building image if needed)"

if ($null -ne $ComposeArgs) {
    & docker $ComposeArgs --project-name $ProjectName -f $ComposeFile --env-file $EnvFile up -d --build
} else {
    & $ComposeBin --project-name $ProjectName -f $ComposeFile --env-file $EnvFile up -d --build
}
if ($LASTEXITCODE -ne 0) {
    Write-Die "docker compose up failed. Check the output above for details."
}

Write-Info "Waiting for LineageLens to be ready..."
$MaxWait = 90
$Waited  = 0
$Ready   = $false
Write-Host "  " -NoNewline

while ($Waited -lt $MaxWait) {
    Start-Sleep -Seconds 3
    $Waited += 3
    try {
        $resp = Invoke-WebRequest -Uri "$BackendUrl/health" -UseBasicParsing -TimeoutSec 5 -ErrorAction SilentlyContinue
        if ($resp.StatusCode -eq 200) {
            $Ready = $true
            break
        }
    } catch {
        # still starting
    }
    Write-Host "." -NoNewline
}
Write-Host ""

if (-not $Ready) {
    Write-Host ""
    Write-Host "  Container logs:" -ForegroundColor Yellow
    if ($null -ne $ComposeArgs) {
        & docker $ComposeArgs --project-name $ProjectName -f $ComposeFile logs --tail 30
    } else {
        & $ComposeBin --project-name $ProjectName -f $ComposeFile logs --tail 30
    }
    Write-Die "LineageLens did not respond within ${MaxWait}s."
}
Write-Ok "LineageLens is up."

# ── Done ──────────────────────────────────────────────────────────────────────
Write-Step "4/4" "Opening setup wizard"

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "   LineageLens Lite is ready!              " -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Open this URL in your browser:" -ForegroundColor White
Write-Host ""
Write-Host "    $BackendUrl/setup" -ForegroundColor Cyan
Write-Host ""
Write-Host "  The setup wizard will create your admin account."
Write-Host "  No curl commands needed - everything is in the browser."
Write-Host ""
Write-Host "  Data is stored at:  $DataDir\lineagelens.db"
Write-Host ""

if ($null -ne $ComposeArgs) {
    Write-Host "  Stop:  docker compose --project-name $ProjectName -f $ComposeFile down"
    Write-Host "  Logs:  docker compose --project-name $ProjectName -f $ComposeFile logs -f"
} else {
    Write-Host "  Stop:  docker-compose --project-name $ProjectName -f $ComposeFile down"
    Write-Host "  Logs:  docker-compose --project-name $ProjectName -f $ComposeFile logs -f"
}
Write-Host ""

# Open the setup page in the default browser
Start-Process "$BackendUrl/setup"
