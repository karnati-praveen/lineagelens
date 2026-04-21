[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$deployDir = Join-Path $repoRoot 'deploy'
$envFile = Join-Path $deployDir '.env'
$backendUrl = 'http://localhost:8787'
$failedSteps = New-Object System.Collections.Generic.List[string]

if (Test-Path (Join-Path $deployDir 'docker-compose.team.yml')) {
    $bundleMode = 'team'
    $composeFile = Join-Path $deployDir 'docker-compose.team.yml'
    $projectName = 'lineagelens-team'
} elseif (Test-Path (Join-Path $deployDir 'docker-compose.enterprise.yml')) {
    $bundleMode = 'enterprise'
    $composeFile = Join-Path $deployDir 'docker-compose.enterprise.yml'
    $projectName = 'lineagelens-enterprise'
} else {
    throw "Could not detect Team or Enterprise bundle contents under $deployDir."
}

if (Get-Command docker -ErrorAction SilentlyContinue) {
    try {
        & docker compose version | Out-Null
        $script:useComposeV2 = $true
    } catch {
        if (Get-Command docker-compose -ErrorAction SilentlyContinue) {
            $script:useComposeV2 = $false
        } else {
            throw 'Docker Compose is not available.'
        }
    }
} else {
    throw 'Docker is not available.'
}

function Invoke-Compose {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$Arguments
    )

    if ($script:useComposeV2) {
        & docker compose --project-name $projectName -f $composeFile --env-file $envFile @Arguments
    } else {
        & docker-compose --project-name $projectName -f $composeFile --env-file $envFile @Arguments
    }
}

function Invoke-NativeCommand {
    param([scriptblock]$Command)

    & $Command
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne $null -and $exitCode -ne 0) {
        throw "Command exited with $exitCode"
    }
}

function Add-FailedStep {
    param([string]$Label)
    $script:failedSteps.Add($Label) | Out-Null
    Write-Host "  FAIL $Label" -ForegroundColor Red
}

function Add-PassedStep {
    param([string]$Label)
    Write-Host "  PASS $Label" -ForegroundColor Green
}

function Invoke-Check {
    param(
        [string]$Label,
        [scriptblock]$Action
    )

    Write-Host ""
    Write-Host "==> $Label" -ForegroundColor Cyan
    try {
        & $Action
        Add-PassedStep $Label
    } catch {
        Add-FailedStep $Label
        Write-Host "    $_" -ForegroundColor DarkRed
    }
}

function Test-Health {
    param(
        [string]$ServiceName,
        [int]$TimeoutSeconds
    )

    $elapsed = 0
    $health = 'missing'

    while ($elapsed -le $TimeoutSeconds) {
        $containerId = Invoke-Compose ps -q $ServiceName 2>$null
        if ($containerId) {
            try {
                $health = docker inspect --format '{{.State.Health.Status}}' $containerId 2>$null
            } catch {
                $health = 'unknown'
            }
            if ($health -eq 'healthy') {
                return $true
            }
        } else {
            $health = 'missing'
        }

        Start-Sleep -Seconds 3
        $elapsed += 3
    }

    Write-Host "    last health for ${ServiceName}: $health" -ForegroundColor DarkYellow
    return $false
}

function Show-RecentLogs {
    param([string]$ServiceName)

    Write-Host ""
    Write-Host "  recent logs for ${ServiceName}:" -ForegroundColor DarkYellow
    try {
        Invoke-Compose logs --tail 20 $ServiceName
    } catch {
        Write-Host "    unable to read logs for $ServiceName" -ForegroundColor DarkYellow
    }
}

Write-Host ""
Write-Host "LineageLens $bundleMode bundle debug" -ForegroundColor White

Invoke-Check 'bundle files' {
    if (-not (Test-Path $composeFile)) {
        throw "Missing compose file: $composeFile"
    }
    if (-not (Test-Path $envFile)) {
        throw "Missing env file: $envFile"
    }
}

Invoke-Check 'prerequisites' {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'docker not found' }
    if (-not (Get-Command curl -ErrorAction SilentlyContinue)) { throw 'curl not found' }
    if (-not (Get-Command python -ErrorAction SilentlyContinue) -and -not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'python not found' }
}

Invoke-Check 'required env keys' {
    $requiredKeys = @('POSTGRES_PASSWORD', 'JWT_SECRET_KEY', 'JWT_REFRESH_SECRET_KEY')
    if ($bundleMode -eq 'enterprise') {
        $requiredKeys += 'NEO4J_PASSWORD'
    }

    $missingKeys = @()
    foreach ($key in $requiredKeys) {
        if (-not (Select-String -Path $envFile -Pattern "^$([regex]::Escape($key))=" -Quiet)) {
            $missingKeys += $key
        }
    }

    if ($missingKeys.Count -gt 0) {
        throw "Missing env keys: $($missingKeys -join ', ')"
    }
}

Invoke-Check 'compose config' {
    Invoke-NativeCommand { Invoke-Compose config }
}

Invoke-Check 'start database services' {
    if ($bundleMode -eq 'team') {
        Invoke-NativeCommand { Invoke-Compose up -d --build postgres }
    } else {
        Invoke-NativeCommand { Invoke-Compose up -d --build postgres neo4j }
    }
}

Invoke-Check 'postgres healthy' {
    if (-not (Test-Health -ServiceName 'postgres' -TimeoutSeconds 60)) {
        throw 'postgres did not become healthy'
    }
}

if ($bundleMode -eq 'enterprise') {
    Invoke-Check 'neo4j healthy' {
        if (-not (Test-Health -ServiceName 'neo4j' -TimeoutSeconds 120)) {
            throw 'neo4j did not become healthy'
        }
    }
}

Invoke-Check 'alembic heads' {
    Invoke-NativeCommand { Invoke-Compose run --rm --no-deps --build backend alembic heads }
}

Invoke-Check 'alembic current' {
    Invoke-NativeCommand { Invoke-Compose run --rm --no-deps --build backend alembic current }
}

Invoke-Check 'alembic upgrade head' {
    Invoke-NativeCommand { Invoke-Compose run --rm --no-deps --build backend alembic upgrade head }
}

Invoke-Check 'start backend' {
    Invoke-NativeCommand { Invoke-Compose up -d --build backend }
}

Invoke-Check 'backend healthy' {
    $ok = $false
    for ($i = 0; $i -lt 20; $i++) {
        try {
            Invoke-RestMethod -Uri "$backendUrl/health" -TimeoutSec 5 | Out-Null
            $ok = $true
            break
        } catch {
            Start-Sleep -Seconds 3
        }
    }

    if (-not $ok) {
        throw 'backend health check failed'
    }
}

if ($failedSteps.Count -gt 0) {
    if ($bundleMode -eq 'enterprise') {
        Show-RecentLogs -ServiceName 'postgres'
        Show-RecentLogs -ServiceName 'neo4j'
    } else {
        Show-RecentLogs -ServiceName 'postgres'
    }
    Show-RecentLogs -ServiceName 'backend'
}

Write-Host ""
Write-Host 'Summary' -ForegroundColor Cyan
if ($failedSteps.Count -eq 0) {
    Write-Host 'All checks passed.' -ForegroundColor Green
    exit 0
}

Write-Host 'Failed checks:' -ForegroundColor Red
foreach ($step in $failedSteps) {
    Write-Host "  - $step"
}
exit 1