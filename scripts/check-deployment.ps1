param([switch]$AllowTestMode)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

$errors = New-Object System.Collections.Generic.List[string]
$warnings = New-Object System.Collections.Generic.List[string]

function Add-Error([string]$Message) { $errors.Add($Message) | Out-Null }
function Add-Warning([string]$Message) { $warnings.Add($Message) | Out-Null }

$pythonExe = Join-Path $Script:SecretGroundsRoot ".venv\Scripts\python.exe"
if (!(Test-Path $pythonExe)) { Add-Error "Python runtime missing: $pythonExe" }

if (Test-Path $pythonExe) {
    & $pythonExe -c "import fastapi, uvicorn, requests, sqlite3" 2>$null
    if ($LASTEXITCODE -ne 0) { Add-Error "Python dependencies missing from .venv." }
}

if (!(Test-Path (Join-Path $Script:SecretGroundsRoot "dashboard\node_modules"))) { Add-Error "Dashboard node_modules missing. Run scripts\install-dependencies.ps1." }
if (!(Test-Path (Join-Path $Script:SecretGroundsRoot "dashboard\.next\BUILD_ID"))) { Add-Error "Dashboard production build missing. Run scripts\build-dashboard.ps1." }

$secret = Get-SgEnv "SECRET_GROUNDS_DEPLOYMENT_SECRET" ""
if (Test-SgPlaceholderValue $secret) { Add-Error "SECRET_GROUNDS_DEPLOYMENT_SECRET is missing or still an example value." }
if (![string]::IsNullOrWhiteSpace($secret) -and $secret.Length -lt 32) { Add-Error "SECRET_GROUNDS_DEPLOYMENT_SECRET must be at least 32 characters." }
if ((Get-SgBool "SECRET_GROUNDS_TEST_MODE" $false) -and !$AllowTestMode) { Add-Error "SECRET_GROUNDS_TEST_MODE=true is not allowed for production startup." }
if ((Get-SgEnv "SECRET_GROUNDS_APP_MODE" "production").ToLowerInvariant() -ne "production") { Add-Error "SECRET_GROUNDS_APP_MODE must be production." }

$backendPort = [int](Get-SgEnv "SECRET_GROUNDS_PORT" $Script:DefaultBackendPort)
$dashboardPort = [int](Get-SgEnv "SECRET_GROUNDS_DASHBOARD_PORT" $Script:DefaultDashboardPort)
if ($backendPort -eq $dashboardPort) { Add-Error "SECRET_GROUNDS_PORT and SECRET_GROUNDS_DASHBOARD_PORT must be different." }
if ($dashboardPort -eq 3000) { Add-Error "SECRET_GROUNDS_DASHBOARD_PORT=3000 conflicts with the development dashboard. Use a production port such as 3001." }
if (!(Test-SgPortAvailable -Port $backendPort) -and !(Test-SgStartedProcess (Get-SgProcessRecord "backend"))) { Add-Error "Backend port $backendPort is in use by another process." }
if (!(Test-SgPortAvailable -Port $dashboardPort) -and !(Test-SgStartedProcess (Get-SgProcessRecord "dashboard"))) { Add-Error "Dashboard port $dashboardPort is in use by another process." }

$backendUrl = Get-SgEnv "NEXT_PUBLIC_FASTAPI_BASE_URL" ""
if (Test-SgPlaceholderValue $backendUrl) {
    Add-Error "NEXT_PUBLIC_FASTAPI_BASE_URL must point to the backend, for example http://127.0.0.1:$backendPort."
} else {
    try {
        $uri = [System.Uri]$backendUrl
        if (!$uri.IsAbsoluteUri -or $uri.Scheme -notin @("http", "https")) {
            Add-Error "NEXT_PUBLIC_FASTAPI_BASE_URL must be an absolute http or https URL."
        }
        if ($uri.Port -ne $backendPort) {
            Add-Warning "NEXT_PUBLIC_FASTAPI_BASE_URL uses port $($uri.Port), but SECRET_GROUNDS_PORT is $backendPort."
        }
    } catch {
        Add-Error "NEXT_PUBLIC_FASTAPI_BASE_URL must be an absolute http or https URL."
    }
}

$databasePath = Resolve-SgPath (Get-SgEnv "SECRET_GROUNDS_DB_PATH" ".\data\secret_grounds.db")
$backupPath = Resolve-SgPath (Get-SgEnv "SECRET_GROUNDS_BACKUP_DIR" ".\backups")
New-Item -ItemType Directory -Force -Path (Split-Path $databasePath), $backupPath | Out-Null
try {
    $probe = Join-Path (Split-Path $databasePath) ".write-test"
    "ok" | Set-Content -LiteralPath $probe -Encoding ASCII
    Remove-Item -LiteralPath $probe
} catch { Add-Error "Database directory is not writable: $(Split-Path $databasePath)" }
try {
    $probe = Join-Path $backupPath ".write-test"
    "ok" | Set-Content -LiteralPath $probe -Encoding ASCII
    Remove-Item -LiteralPath $probe
} catch { Add-Error "Backup directory is not writable: $backupPath" }

if (Test-Path $databasePath) {
    if (Test-Path $pythonExe) {
        & $pythonExe -c "import sqlite3, sys; con=sqlite3.connect(sys.argv[1]); print(con.execute('PRAGMA integrity_check').fetchone()[0]); con.close()" $databasePath
        if ($LASTEXITCODE -ne 0) { Add-Error "Database integrity check failed: $databasePath" }
    }
} else {
    Add-Warning "Database does not exist yet. Startup will create it at $databasePath."
}

$legacyDatabase = Join-Path $Script:SecretGroundsRoot "server\secret_grounds.sqlite3"
if ((Test-Path $legacyDatabase) -and ([System.IO.Path]::GetFullPath($legacyDatabase) -ne $databasePath)) {
    Add-Warning "A legacy database exists at $legacyDatabase. Confirm production uses $databasePath."
}

if (Test-SgStartedProcess (Get-SgProcessRecord "backend")) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$backendPort/healthz" -UseBasicParsing -TimeoutSec 3 | Out-Null
    } catch {
        Add-Warning "Backend PID file exists, but health endpoint is not responding."
    }
}

Write-Host "Secret Grounds deployment check $(Get-SgVersion)"
foreach ($message in $errors) { Write-Host "Error: $message" -ForegroundColor Red }
foreach ($message in $warnings) { Write-Host "Warning: $message" -ForegroundColor Yellow }
if ($errors.Count -gt 0) { exit 1 }
Write-Host "Deployment check passed with $($warnings.Count) warning(s)." -ForegroundColor Green
