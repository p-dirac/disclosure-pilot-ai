<#
.SYNOPSIS
    Runs the Disclosure Pilot AI reporting app: one FastAPI/uvicorn process serving the
    API and the built React app (see install.ps1 for the build step).

.PARAMETER Port
    Port to listen on. Defaults to 8000.

.PARAMETER NoBrowser
    Skip auto-opening the browser after startup.

.NOTES
    Run from the repository root:  .\run.ps1
    Requires a .env file in the repo root (copy from .env.example).
#>

param(
    [int]$Port = 8000,
    [switch]$NoBrowser,
    [switch]$StrictPreflight = $true
)

$ErrorActionPreference = "Stop"

$RepoRoot   = $PSScriptRoot
$BackendDir = Join-Path $RepoRoot "backend"
$EnvFile    = Join-Path $RepoRoot ".env"
# Use VenvPython path instead of calling activate
# Since run.ps1 only ever runs & $VenvPython -m uvicorn ... — it 
# never calls a bare python or pip anywhere else in the script.
# Thus, activate is not needed.
$VenvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    throw "Venv not found at $VenvPython. Run .\install.ps1 first."
}

if (-not (Test-Path $EnvFile)) {
    throw ".env not found at $EnvFile. Copy .env.example to .env and fill in DB_HOST, OLLAMA_HOST, and AppIO paths."
}

Write-Host "== Loading environment from .env ==" -ForegroundColor Cyan

# .env uses ${VAR} interpolation throughout (e.g.
# WIN_10K_INTRO=${WIN_APPIO_DIR}\sec10k\intro, and DATA_10K_INTRO in turn
# references ${WIN_10K_INTRO} - a two-level chain). python-dotenv (which
# pydantic-settings' env_file loading uses) expands these automatically,
# but a naive "just split on = and set the env var" loop here does NOT -
# it was setting variables like WIN_10K_INTRO to the literal string
# "${WIN_APPIO_DIR}\sec10k\intro", unexpanded braces and all. Since
# docx_service.py reads these as real filesystem paths, that silently
# broke every path-related setting. Read into a table first, expand
# references (repeating until no ${...} pattern remains, so chained
# references resolve fully), then set the expanded values as env vars.
$envTable = @{}
Get-Content $EnvFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -eq "" -or $line.StartsWith("#")) { return }
    $parts = $line -split "=", 2
    if ($parts.Count -eq 2) {
        $key   = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"')
        $envTable[$key] = $value
    }
}

$maxPasses = 10
for ($pass = 0; $pass -lt $maxPasses; $pass++) {
    $anyExpanded = $false
    foreach ($key in @($envTable.Keys)) {
        $value = $envTable[$key]
        $expanded = [System.Text.RegularExpressions.Regex]::Replace(
            $value, '\$\{([A-Za-z_][A-Za-z0-9_]*)\}',
            {
                param($m)
                $refName = $m.Groups[1].Value
                if ($envTable.ContainsKey($refName)) { $envTable[$refName] } else { $m.Value }
            }
        )
        if ($expanded -ne $value) {
            $envTable[$key] = $expanded
            $anyExpanded = $true
        }
    }
    if (-not $anyExpanded) { break }
}

foreach ($key in $envTable.Keys) {
    [System.Environment]::SetEnvironmentVariable($key, $envTable[$key], "Process")
}

# Fill in defaults matching the old docker-compose env vars, in case .env
# only overrides a subset (same fallback pattern as yml-notes.txt).
function Set-DefaultEnv($name, $default) {
    if (-not [System.Environment]::GetEnvironmentVariable($name, "Process")) {
        [System.Environment]::SetEnvironmentVariable($name, $default, "Process")
    }
}
Set-DefaultEnv "DB_USER"     "postgres"
Set-DefaultEnv "DB_PASSWORD" "password"
Set-DefaultEnv "DB_HOST"     "127.0.0.1"
Set-DefaultEnv "DB_PORT"     "5432"
Set-DefaultEnv "DB_NAME"     "finreport"
Set-DefaultEnv "OLLAMA_HOST" "127.0.0.1"
Set-DefaultEnv "OLLAMA_PORT" "11434"

# main.py's FRONTEND_DIST default ("/app/frontend/dist") is a leftover
# container-internal path from the old setup. With no container,
# os.path.isdir() on that path is always False on Windows, so main.py's
# entire "if os.path.isdir(FRONTEND_DIST):" block - which registers "/",
# the SPA catch-all, and the /assets mount - never runs at all. That's
# why "/" 404s even though index.html genuinely exists: the route was
# never registered, not that it failed to find the file. Point it at
# the real, absolute path to where install.ps1 actually copies the
# frontend build (backend\static), computed here rather than hardcoded
# in .env so it's correct regardless of where the repo is checked out.
$FrontendDistPath = Join-Path $BackendDir "static"
[System.Environment]::SetEnvironmentVariable("FRONTEND_DIST", $FrontendDistPath, "Process")

$dbUser = [System.Environment]::GetEnvironmentVariable("DB_USER", "Process")
$dbHost = [System.Environment]::GetEnvironmentVariable("DB_HOST", "Process")
$dbPort = [System.Environment]::GetEnvironmentVariable("DB_PORT", "Process")
$dbName = [System.Environment]::GetEnvironmentVariable("DB_NAME", "Process")
$dbPass = [System.Environment]::GetEnvironmentVariable("DB_PASSWORD", "Process")
$dbOpts = [System.Environment]::GetEnvironmentVariable("DB_OPTIONS", "Process")

[System.Environment]::SetEnvironmentVariable(
    "DATABASE_URL",
    "postgresql://${dbUser}:${dbPass}@${dbHost}:${dbPort}/${dbName}?${dbOpts}",
    "Process"
)

$ollamaHost = [System.Environment]::GetEnvironmentVariable("OLLAMA_HOST", "Process")
$ollamaPort = [System.Environment]::GetEnvironmentVariable("OLLAMA_PORT", "Process")
[System.Environment]::SetEnvironmentVariable(
    "OLLAMA_BASE_URL",
    "http://${ollamaHost}:${ollamaPort}",
    "Process"
)

$PreflightScript = Join-Path $RepoRoot "check-connections.ps1"
if (Test-Path $PreflightScript) {
    & $PreflightScript
    if ($LASTEXITCODE -ne 0) {
        if ($StrictPreflight) {
            throw "Preflight checks failed and -StrictPreflight was set. Aborting."
        } else {
            Write-Host "Continuing despite failed preflight checks (use -StrictPreflight to abort instead)." -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "check-connections.ps1 not found - skipping preflight." -ForegroundColor Yellow
}

Write-Host "== Starting backend (serves API + built frontend on port $Port) ==" -ForegroundColor Cyan

Push-Location $BackendDir
try {
    if (-not $NoBrowser) {
        Start-Job -ScriptBlock {
            Start-Sleep -Seconds 2
            Start-Process "http://127.0.0.1:$using:Port"
        } | Out-Null
    }
    & $VenvPython -m uvicorn app.main:app --host 0.0.0.0 --port $Port --no-use-colors
} finally {
    Pop-Location
}
