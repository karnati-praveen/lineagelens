$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$backendDir = Join-Path $repoRoot "lineagelens-backend"
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

function Resolve-RealPython {
    # The bare `python` / `python3` names on Windows often resolve to the
    # Microsoft Store "App execution alias" — a 0-byte stub under WindowsApps
    # that just opens the Store instead of running an interpreter (BUG-5).
    # Prefer the `py` launcher, then any python.exe NOT under WindowsApps.
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        return @{ Path = $pyLauncher.Path; Args = @("-3", "-m", "venv") }
    }

    foreach ($name in @("python3", "python")) {
        $cmds = Get-Command $name -All -ErrorAction SilentlyContinue
        foreach ($cmd in $cmds) {
            $source = $cmd.Source
            if ($source -and (Test-Path $source) -and ($source -notlike "*\WindowsApps\*")) {
                return @{ Path = $source; Args = @("-m", "venv") }
            }
        }
    }

    return $null
}

if (-not (Test-Path $pythonExe)) {
    $interpreter = Resolve-RealPython
    if (-not $interpreter) {
        throw "Python 3.11+ is required, but no real interpreter was found (the bare 'python' command may be the Microsoft Store alias). Install Python from python.org, then run this script again."
    }
    & $interpreter.Path @($interpreter.Args + $venvDir)
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
