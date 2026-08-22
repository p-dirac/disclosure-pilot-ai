<#
.SYNOPSIS
    Runs the disclosure-pilot-ai frontend test suite (Vitest + React
    Testing Library).

.DESCRIPTION
    Installs npm dependencies if node_modules is missing, checks that the
    Vitest setup file exists at the path vite.config.js's
    test.setupFiles entry expects (src/tests/setup.js), then runs
    "npx vitest run".

.PARAMETER FrontendDir
    Path to the frontend project root (the folder containing package.json
    and vite.config.js). Defaults to a "frontend" folder next to this
    script - adjust if your layout differs.

.PARAMETER SetupFile
    Expected path to the Vitest setup file, relative to FrontendDir.
    Defaults to "src\tests\setup.js" - must match vite.config.js's
    test.setupFiles entry exactly, or jest-dom matchers like
    toBeInTheDocument() won't be registered.

.PARAMETER TestArgs
    Extra arguments to forward to vitest, e.g. "--ui" or a name filter
    like "BalanceSheetTable".

.EXAMPLE
    .\test-frontend.ps1
    .\test-frontend.ps1 -TestArgs "--ui"
    .\test-frontend.ps1 -FrontendDir "C:\AppIO\frontend"

.NOTES
    If this script is blocked by execution policy, run it as:
        powershell -ExecutionPolicy Bypass -File .\test-frontend.ps1
#>

[CmdletBinding()]
param(
    [string]$FrontendDir = (Join-Path $PSScriptRoot "frontend"),
    [string]$SetupFile   = "src\tests\setup.js",
    [string]$TestArgs    = ""
)

function Write-Section($msg) {
    Write-Host ""
    Write-Host "=== $msg ===" -ForegroundColor Cyan
}

Write-Section "Frontend test run"
Write-Host "Frontend dir : $FrontendDir"

if (-not (Test-Path $FrontendDir)) {
    Write-Host "Frontend directory not found: $FrontendDir" -ForegroundColor Red
    Write-Host "Pass -FrontendDir to point at the correct location." -ForegroundColor Yellow
    exit 1
}

$PackageJson = Join-Path $FrontendDir "package.json"
if (-not (Test-Path $PackageJson)) {
    Write-Host "package.json not found in: $FrontendDir" -ForegroundColor Red
    exit 1
}

$SetupFilePath = Join-Path $FrontendDir $SetupFile
if (-not (Test-Path $SetupFilePath)) {
    Write-Host "Vitest setup file not found: $SetupFilePath" -ForegroundColor Yellow
    Write-Host "Confirm vite.config.js's test.setupFiles entry matches this path." -ForegroundColor Yellow
}

$testExitCode = 1
$installFailed = $false
Push-Location $FrontendDir
try {
    $NodeModules = Join-Path $FrontendDir "node_modules"
    if (-not (Test-Path $NodeModules)) {
        Write-Section "Installing npm dependencies"
        & npm install
        if ($LASTEXITCODE -ne 0) {
            Write-Host "npm install failed (exit code $LASTEXITCODE)" -ForegroundColor Red
            $installFailed = $true
        }
    }

    if (-not $installFailed) {
        Write-Section "Running frontend tests"
        $vitestArgList = @("vitest", "run")
        if ($TestArgs -ne "") {
            $vitestArgList += $TestArgs -split "\s+"
        }
        & npx @vitestArgList
        $testExitCode = $LASTEXITCODE
    } else {
        $testExitCode = 1
    }
}
finally {
    Pop-Location
}

Write-Section "Result"
if ($testExitCode -eq 0) {
    Write-Host "Frontend tests PASSED" -ForegroundColor Green
} else {
    Write-Host "Frontend tests FAILED (exit code $testExitCode)" -ForegroundColor Red
}

exit $testExitCode
