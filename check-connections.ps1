<#
.SYNOPSIS
    Preflight check: verifies PostgreSQL, Ollama, and the Arelle taxonomy
    host are reachable before starting the app.

.DESCRIPTION
    Reads connection targets from environment variables already loaded by
    run.ps1 (DB_HOST/DB_PORT, OLLAMA_BASE_URL, ARELLE_TAXONOMY_URL).
    Each check is short-timeout and non-fatal by default - failures are
    reported clearly, and the script exit code reflects overall pass/fail
    so callers can decide whether to abort.

.PARAMETER TimeoutSeconds
    Per-check timeout. Defaults to 4 seconds.

.NOTES
    Set ARELLE_TAXONOMY_URL in .env to whatever host Arelle actually loads
    the us-gaap taxonomy from (e.g. the FASB or XBRL.org taxonomy entry
    point URL). If it's not set, a default setting is used.
#>

param(
    [int]$TimeoutSeconds = 4
)

# ── Self-contained .env loader ─────────────────────────────────────────────
# Lets this script run standalone (".\check-connections.ps1") for a quick
# check before starting the app, not just as a child call from run.ps1.
#
# Guarded on DB_HOST already being set in the process environment: when
# run.ps1 calls this script via "& $PreflightScript", it has already loaded
# .env and populated the process environment block, which this child
# process inherits. In that case we skip re-loading entirely, so run.ps1
# remains the single source of truth and this block only fires when the
# script is invoked directly with none of that already in place.
if (-not [System.Environment]::GetEnvironmentVariable("DB_HOST", "Process")) {
    $RepoRoot = $PSScriptRoot
    $EnvFile  = Join-Path $RepoRoot ".env"

    if (Test-Path $EnvFile) {
        Write-Host "== Loading environment from .env ==" -ForegroundColor Cyan

        # Same two-pass ${VAR} expansion as run.ps1, needed because .env
        # chains references (e.g. WIN_10K_INTRO=${WIN_APPIO_DIR}\...).
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
    } else {
        Write-Host "No .env found at $EnvFile - checks below will show as not set." -ForegroundColor Yellow
    }

    # Same fallback defaults as run.ps1, in case .env only overrides a subset.
    function Set-DefaultEnv($name, $default) {
        if (-not [System.Environment]::GetEnvironmentVariable($name, "Process")) {
            [System.Environment]::SetEnvironmentVariable($name, $default, "Process")
        }
    }
    Set-DefaultEnv "DB_HOST"     "127.0.0.1"
    Set-DefaultEnv "DB_PORT"     "5432"
    Set-DefaultEnv "OLLAMA_HOST" "127.0.0.1"
    Set-DefaultEnv "OLLAMA_PORT" "11434"

    # OLLAMA_BASE_URL isn't read directly from .env by run.ps1 either - it's
    # derived from OLLAMA_HOST/OLLAMA_PORT. Mirror that here so the Ollama
    # check below has something to hit.
    if (-not [System.Environment]::GetEnvironmentVariable("OLLAMA_BASE_URL", "Process")) {
        $ollamaHost = [System.Environment]::GetEnvironmentVariable("OLLAMA_HOST", "Process")
        $ollamaPort = [System.Environment]::GetEnvironmentVariable("OLLAMA_PORT", "Process")
        [System.Environment]::SetEnvironmentVariable("OLLAMA_BASE_URL", "http://${ollamaHost}:${ollamaPort}", "Process")
    }
}

$results = @()

function Test-TcpPort($Name, $HostName, $Port, $TimeoutSec) {
    if (-not $HostName -or -not $Port) {
        return [PSCustomObject]@{ Name = $Name; Ok = $false; Detail = "Host/port not set" }
    }
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async  = $client.BeginConnect($HostName, [int]$Port, $null, $null)
        $ok     = $async.AsyncWaitHandle.WaitOne($TimeoutSec * 1000)
        if ($ok -and $client.Connected) {
            $client.EndConnect($async)
            $client.Close()
            return [PSCustomObject]@{ Name = $Name; Ok = $true; Detail = "${HostName}:${Port} reachable" }
        } else {
            $client.Close()
            return [PSCustomObject]@{ Name = $Name; Ok = $false; Detail = "${HostName}:${Port} timed out after ${TimeoutSec}s" }
        }
    } catch {
        return [PSCustomObject]@{ Name = $Name; Ok = $false; Detail = $_.Exception.Message }
    }
}

function Test-HttpEndpoint($Name, $Url, $TimeoutSec) {
    if (-not $Url) {
        return [PSCustomObject]@{ Name = $Name; Ok = $false; Detail = "URL not set - skipped" }
    }
    try {
        $resp = Invoke-WebRequest -Uri $Url -Method Head -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
        return [PSCustomObject]@{ Name = $Name; Ok = $true; Detail = "$Url -> HTTP $($resp.StatusCode)" }
    } catch {
        # Some servers reject HEAD; retry with GET before giving up.
        try {
            $resp = Invoke-WebRequest -Uri $Url -Method Get -TimeoutSec $TimeoutSec -UseBasicParsing -ErrorAction Stop
            return [PSCustomObject]@{ Name = $Name; Ok = $true; Detail = "$Url -> HTTP $($resp.StatusCode)" }
        } catch {
            return [PSCustomObject]@{ Name = $Name; Ok = $false; Detail = $_.Exception.Message }
        }
    }
}

Write-Host "== Preflight connectivity check ==" -ForegroundColor Cyan

# PostgreSQL - TCP reachability only (doesn't validate credentials, just
# that something is listening on that host/port).
$dbHost = [System.Environment]::GetEnvironmentVariable("DB_HOST", "Process")
$dbPort = [System.Environment]::GetEnvironmentVariable("DB_PORT", "Process")
Write-Host "dbHost: " $dbHost "dbPort: " $dbPort
$results += Test-TcpPort "PostgreSQL" $dbHost $dbPort $TimeoutSeconds

# Ollama - hits its tags endpoint, which responds even with no model loaded.
$ollamaBase = [System.Environment]::GetEnvironmentVariable("OLLAMA_BASE_URL", "Process")
$ollamaUrl  = if ($ollamaBase) { "$ollamaBase/api/tags" } else { $null }
Write-Host "ollamaUrl: " $ollamaUrl
$results += Test-HttpEndpoint "Ollama" $ollamaUrl $TimeoutSeconds

# Arelle taxonomy host - defaults to the us-gaap-2026 schemaLocation your
# extension taxonomy imports from. Override via ARELLE_TAXONOMY_URL in .env
# if Arelle resolves against a different URL.
$arelleUrl = [System.Environment]::GetEnvironmentVariable("ARELLE_TAXONOMY_URL", "Process")
if (-not $arelleUrl) {
    $arelleUrl = "https://xbrl.fasb.org/us-gaap/2026/elts/us-gaap-2026.xsd"
}
Write-Host "arelleUrl: " $arelleUrl
$results += Test-HttpEndpoint "Arelle taxonomy host" $arelleUrl $TimeoutSeconds

Write-Host ""
$allOk = $true
foreach ($r in $results) {
    if ($r.Ok) {
        Write-Host ("  [OK]   {0,-22} {1}" -f $r.Name, $r.Detail) -ForegroundColor Green
    } else {
        Write-Host ("  [FAIL] {0,-22} {1}" -f $r.Name, $r.Detail) -ForegroundColor Yellow
        $allOk = $false
    }
}
Write-Host ""

if (-not $allOk) {
    Write-Host "One or more preflight checks failed. See details above." -ForegroundColor Yellow
    exit 1
} else {
    Write-Host "All preflight checks passed." -ForegroundColor Green
    exit 0
}
