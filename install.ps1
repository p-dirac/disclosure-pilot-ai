<#
.SYNOPSIS
    Installs / builds the Disclosure Pilot AI reporting app (React + FastAPI, no containers).

.DESCRIPTION
    - Verifies Python 3.13 and Node are present on the host.
    - Creates a Python venv under backend\.venv and installs backend\requirements.txt.
    - Installs frontend npm deps and runs the Vite production build.
    - Copies the built frontend (frontend\dist) into backend\static, which FastAPI
      serves directly (see StaticFiles mount in app\main.py).

.NOTES
    Run from the repository root:  .\install.ps1
#>

$ErrorActionPreference = "Stop"

$RepoRoot    = $PSScriptRoot
$BackendDir  = Join-Path $RepoRoot "backend"
$FrontendDir = Join-Path $RepoRoot "frontend"
$VenvDir     = Join-Path $BackendDir ".venv"
$StaticDir   = Join-Path $BackendDir "static"

function Assert-Command($name, $hint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        throw "$name was not found on PATH. $hint"
    }
}

Write-Host "== Checking prerequisites ==" -ForegroundColor Cyan

Assert-Command "py"  "Install Python 3.13 from python.org and ensure the 'py' launcher is on PATH."
Assert-Command "node" "Install Node.js LTS from nodejs.org."
Assert-Command "npm"  "npm should ship with Node.js - reinstall Node if missing."

$pyVersion = & py -3.13 --version 2>$null
if (-not $pyVersion) {
    throw "Python 3.13 not found via 'py -3.13'. Install Python 3.13 and re-run."
}
Write-Host "Found $pyVersion"

Write-Host "== Setting up backend venv ==" -ForegroundColor Cyan

# A leftover PYTHONHOME (or PYTHONPATH) from a previously-installed Python
# version is a classic cause of "Fatal Python error: Failed to import
# encodings module" - the interpreter gets pointed at a standard library
# location that no longer matches (or no longer exists, e.g. after
# uninstalling an old version like 3.14 in favor of 3.13). Neither
# variable should normally be set for a per-project venv workflow, so
# clear them for this script's process if present, rather than let venv
# creation fail confusingly on every run until someone happens to notice.
foreach ($staleVar in @("PYTHONHOME", "PYTHONPATH")) {
    $existing = [System.Environment]::GetEnvironmentVariable($staleVar, "Process")
    if ($existing) {
        Write-Host "Clearing stale $staleVar='$existing' for this install (leftover from a previous Python install can break venv creation)." -ForegroundColor Yellow
        [System.Environment]::SetEnvironmentVariable($staleVar, $null, "Process")
    }
}
# 
# creates .venv, and copies python from system path to .venv/Scripts
if (-not (Test-Path $VenvDir)) {
    & py -3.13 -m venv $VenvDir
    if ($LASTEXITCODE -ne 0) {
        throw "Venv creation failed (exit code $LASTEXITCODE) - see output above. " +
              "A 'Failed to import encodings module' error here almost always means a " +
              "stale PYTHONHOME/PYTHONPATH (checked above) or a corrupted Python install - " +
              'try py -3.13 -c "import encodings" directly to isolate whether the base ' +
              "interpreter itself is broken."
    }
    Write-Host "Created venv at $VenvDir"
} else {
    Write-Host "Venv already exists at $VenvDir, reusing it."
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPip    = Join-Path $VenvDir "Scripts\pip.exe"

# Best-effort only: a pip self-upgrade failing (e.g. a broken vendored
# rich dependency triggering a spurious TypeError) shouldn't block using
# whatever pip already shipped with the venv - that's perfectly capable
# of installing requirements.txt. Warn and continue rather than treat
# this as fatal.
& $VenvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) {
    Write-Host "pip self-upgrade failed (exit code $LASTEXITCODE) - continuing with the venv's existing pip. See output above if this recurs." -ForegroundColor Yellow
}
& $VenvPip install -r (Join-Path $BackendDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "pip install -r requirements.txt failed (exit code $LASTEXITCODE) - see output above."
}

Write-Host "== Byte-compiling backend sources ==" -ForegroundColor Cyan

# Python has no separate build step - modules are compiled to bytecode
# transparently at import time, cached automatically as .pyc files under
# __pycache__/. This step doesn't change how the app runs; it just does
# that compilation now instead of on first import, so a syntax error
# anywhere in backend\app surfaces here (at install time) rather than the
# first time some rarely-hit code path gets imported at runtime, and the
# first `run.ps1` launch doesn't pay the compile cost itself.
& $VenvPython -m compileall (Join-Path $BackendDir "app")
if ($LASTEXITCODE -ne 0) {
    throw "compileall found a syntax error in backend\app - see output above."
}

Write-Host "== Building frontend ==" -ForegroundColor Cyan

Push-Location $FrontendDir
try {
    & npm install
    & npm run build
} finally {
    Pop-Location
}

$DistDir = Join-Path $FrontendDir "dist"
if (-not (Test-Path $DistDir)) {
    throw "Frontend build did not produce $DistDir - check the npm run build output above."
}

Write-Host "== Copying build output into backend\static ==" -ForegroundColor Cyan

if (Test-Path $StaticDir) {
    Remove-Item $StaticDir -Recurse -Force
}
New-Item -ItemType Directory -Path $StaticDir | Out-Null
Copy-Item (Join-Path $DistDir "*") $StaticDir -Recurse -Force

Write-Host "== Install complete ==" -ForegroundColor Green
Write-Host "Next:  .\run.ps1"
