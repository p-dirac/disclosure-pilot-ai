<#
.SYNOPSIS
    Runs the disclosure-pilot-ai backend test suite (pytest + SQLite).

.DESCRIPTION
    Activates the backend virtual environment, then runs pytest against
    test_backend.py using "python -m pytest" (not a bare "pytest") so the
    current directory is added to sys.path - required for
    "from app.main import app" etc. to resolve. No Postgres or Ollama
    connection is needed; the tests spin up an in-memory SQLite database
    (see the "engine" fixture in test_backend.py).

.PARAMETER BackendDir
    Path to the backend project root (the folder containing the "app"
    package and the venv). Defaults to a "backend" folder next to this
    script - adjust if your layout differs.

.PARAMETER VenvDir
    Path to the virtual environment. Defaults to "venv" inside BackendDir.
    Adjust if your venv is named ".venv" or lives elsewhere.

.PARAMETER TestPath
    File or folder to pass to pytest. Defaults to "test_backend.py" inside
    BackendDir.

.PARAMETER TestArgs
    Extra arguments to forward to pytest, e.g. "-k TestAuthService" or
    "-x --maxfail=1".

.EXAMPLE
    .\test-backend.ps1
    .\test-backend.ps1 -TestArgs "-k TestAuthService"
    .\test-backend.ps1 -BackendDir "C:\AppIO\backend" -VenvDir "C:\AppIO\backend\.venv"

.NOTES
    If this script is blocked by execution policy, run it as:
        powershell -ExecutionPolicy Bypass -File .\test-backend.ps1
#>

[CmdletBinding()]
param(
    [string]$BackendDir = (Join-Path $PSScriptRoot "backend" ),
    [string]$VenvDir    = (Join-Path $BackendDir ".venv"),
    [string]$TestPath   = (Join-Path $BackendDir "tests\test_backend.py"),
    [string]$TestArgs   = ""
)

function Write-Section($msg) {
    Write-Host ""
    Write-Host "=== $msg ===" -ForegroundColor Cyan
}

Write-Section "Backend test run"
Write-Host "Backend dir : $BackendDir"
Write-Host "Venv dir    : $VenvDir"
Write-Host "Test path   : $TestPath"

if (-not (Test-Path $BackendDir)) {
    Write-Host "Backend directory not found: $BackendDir" -ForegroundColor Red
    Write-Host "Pass -BackendDir to point at the correct location." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $TestPath)) {
    Write-Host "Test file not found: $TestPath" -ForegroundColor Red
    Write-Host "Pass -TestPath to point at the correct location." -ForegroundColor Yellow
    exit 1
}

$ActivateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
if (-not (Test-Path $ActivateScript)) {
    Write-Host "Virtual environment not found at: $VenvDir" -ForegroundColor Red
    Write-Host "Run install.ps1 first, or pass -VenvDir to point at the correct venv." -ForegroundColor Yellow
    exit 1
}

Write-Section "Activating virtual environment"
. $ActivateScript

Write-Section "Checking pytest is installed"
$pytestCheck = & python -m pytest --version 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "pytest is not installed in this venv." -ForegroundColor Red
    Write-Host "Run: pip install pytest pytest-asyncio" -ForegroundColor Yellow
    exit 1
}
Write-Host $pytestCheck

Write-Section "Running backend tests"
$testExitCode = 1
Push-Location $BackendDir
try {
    $pytestArgList = @($TestPath, "-v")
    if ($TestArgs -ne "") {
        $pytestArgList += $TestArgs -split "\s+"
    }
    & python -m pytest @pytestArgList
    $testExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

Write-Section "Result"
if ($testExitCode -eq 0) {
    Write-Host "Backend tests PASSED" -ForegroundColor Green
} else {
    Write-Host "Backend tests FAILED (exit code $testExitCode)" -ForegroundColor Red
}

if (Get-Command deactivate -ErrorAction SilentlyContinue) {
    deactivate
}

exit $testExitCode
