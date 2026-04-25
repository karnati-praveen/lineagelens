$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "backend"
$venvDir = Join-Path $repoRoot ".venv"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"

function New-SecureSecret {
    param(
        [int]$ByteLength = 48
    )

    $bytes = New-Object byte[] $ByteLength
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()

    try {
        $random.GetBytes($bytes)
    }
    finally {
        $random.Dispose()
    }

    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

if (-not (Test-Path $pythonExe)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        & $pythonCommand.Path -m venv $venvDir
    } else {
        $pyCommand = Get-Command py -ErrorAction SilentlyContinue
        if ($pyCommand) {
            & $pyCommand.Path -3 -m venv $venvDir
        } else {
            throw "Python 3.11+ is required. Install Python, then run this script again."
        }
    }
}

if (-not (Test-Path $pythonExe)) {
    throw "Failed to create the virtual environment at $venvDir."
}

if ([string]::IsNullOrWhiteSpace($env:JWT_SECRET_KEY)) {
    $env:JWT_SECRET_KEY = New-SecureSecret
}

if ([string]::IsNullOrWhiteSpace($env:JWT_REFRESH_SECRET_KEY)) {
    $env:JWT_REFRESH_SECRET_KEY = New-SecureSecret
}

$env:APP_ENV = "test"
$env:BACKEND_MODE = "team"
$env:NEO4J_ENABLED = "false"
$env:VECTOR_SEARCH_ENABLED = "false"
$env:PYTHONPATH = "."

Push-Location $backendDir
try {
    & $pythonExe -m pip install --upgrade pip
    & $pythonExe -m pip install -r requirements.txt -r requirements-dev.txt
    & $pythonExe -m pytest tests -q
}
finally {
    Pop-Location
}
