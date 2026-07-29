param(
    [switch]$NoBrowser,
    [switch]$AllowTestMode
)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig
Assert-SgDeploymentConfig -AllowTestMode:$AllowTestMode

$serverLog = Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log"
$dashboardLog = Get-SgEnv "SECRET_GROUNDS_DASHBOARD_LOG" ".\logs\dashboard.log"
$backendPort = [int](Get-SgEnv "SECRET_GROUNDS_PORT" $Script:DefaultBackendPort)
$dashboardPort = [int](Get-SgEnv "SECRET_GROUNDS_DASHBOARD_PORT" $Script:DefaultDashboardPort)
$databasePath = Resolve-SgPath (Get-SgEnv "SECRET_GROUNDS_DB_PATH" ".\data\secret_grounds.db")
$backupPath = Resolve-SgPath (Get-SgEnv "SECRET_GROUNDS_BACKUP_DIR" ".\backups")
if (Test-SgStartedProcess (Get-SgProcessRecord "backend")) {
    throw "Backend is already running from deployment scripts."
}
if (Test-SgStartedProcess (Get-SgProcessRecord "dashboard")) {
    throw "Dashboard is already running from deployment scripts."
}

New-Item -ItemType Directory -Force -Path (Split-Path $databasePath), $backupPath, (Split-Path (Resolve-SgPath $serverLog)), (Split-Path (Resolve-SgPath $dashboardLog)) | Out-Null

$probe = Join-Path (Split-Path $databasePath) ".write-test"
"ok" | Set-Content -LiteralPath $probe -Encoding ASCII
Remove-Item -LiteralPath $probe

try {
    & "$PSScriptRoot\start-backend.ps1" -AllowTestMode:$AllowTestMode
    & "$PSScriptRoot\start-dashboard.ps1"

    $openBrowser = (Get-SgBool "SECRET_GROUNDS_OPEN_BROWSER" $true) -and !$NoBrowser
    if ($openBrowser) {
        Start-Process "http://127.0.0.1:3001"
    }
    Write-SgLine "Secret Grounds cashier stack is running. Backend PID $((Get-SgProcessRecord "backend").Pid), dashboard PID $((Get-SgProcessRecord "dashboard").Pid)." "INFO" $serverLog
} catch {
    Write-SgLine "Startup failed: $($_.Exception.Message)" "ERROR" $serverLog
    Stop-SgStartedProcess "dashboard"
    Stop-SgStartedProcess "backend"
    throw
}
