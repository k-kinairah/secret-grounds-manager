. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig
Assert-SgDeploymentConfig

$dashboardLog = Get-SgEnv "SECRET_GROUNDS_DASHBOARD_LOG" ".\logs\dashboard.log"
$dashboardConsoleLog = Get-SgEnv "SECRET_GROUNDS_DASHBOARD_CONSOLE_LOG" ".\logs\dashboard-console.log"
$dashboardErrorLog = Get-SgEnv "SECRET_GROUNDS_DASHBOARD_ERROR_LOG" ".\logs\dashboard-error.log"
$dashboardPort = [int](Get-SgEnv "SECRET_GROUNDS_DASHBOARD_PORT" $Script:DefaultDashboardPort)
$dashboardHost = Get-SgEnv "SECRET_GROUNDS_DASHBOARD_HOST" "0.0.0.0"
$backendPort = [int](Get-SgEnv "SECRET_GROUNDS_PORT" $Script:DefaultBackendPort)
$backendUrl = Get-SgEnv "NEXT_PUBLIC_FASTAPI_BASE_URL" "http://127.0.0.1:$backendPort"

if (Test-SgStartedProcess (Get-SgProcessRecord "dashboard")) {
    throw "Secret Grounds dashboard is already running from deployment scripts."
}
if (!(Test-SgPortAvailable -Port $dashboardPort)) {
    throw "Dashboard port $dashboardPort is already in use."
}
if (!(Test-Path (Join-Path $Script:SecretGroundsRoot "dashboard\.next\BUILD_ID"))) {
    throw "Dashboard production build is missing. Run scripts\build-dashboard.ps1 first."
}
if (!(Wait-SgHttpReady -Url "http://127.0.0.1:$backendPort/healthz" -TimeoutSeconds 5 -LogPath $dashboardLog)) {
    throw "Backend is unavailable at http://127.0.0.1:$backendPort/healthz."
}

New-Item -ItemType Directory -Force -Path (Split-Path (Resolve-SgPath $dashboardLog)) | Out-Null
Write-SgLine "Starting Secret Grounds dashboard $(Get-SgVersion) on $dashboardHost`:$dashboardPort with backend $backendUrl." "INFO" $dashboardLog

$node = Resolve-SgNodePath
$nextCli = Join-Path $Script:SecretGroundsRoot "dashboard\node_modules\next\dist\bin\next"
if (!(Test-Path $nextCli)) {
    throw "Next.js production CLI is missing: $nextCli. Run scripts\install-dependencies.ps1 first."
}
$env:NEXT_PUBLIC_FASTAPI_BASE_URL = $backendUrl
$env:PORT = $dashboardPort.ToString()
$env:HOSTNAME = $dashboardHost
$arguments = @($nextCli, "start", "-H", $dashboardHost, "-p", $dashboardPort.ToString())
$process = Start-SgLoggedProcess -Role "dashboard" -FilePath $node -Arguments $arguments -WorkingDirectory (Join-Path $Script:SecretGroundsRoot "dashboard") -LogPath $dashboardConsoleLog -ErrorLogPath $dashboardErrorLog

Set-SgProcessRecord "dashboard" ([pscustomobject]@{
    Pid = $process.Id
    Role = "dashboard"
    Port = $dashboardPort
    StartedAt = (Get-Date).ToString("o")
    Identity = $Script:LastSgLaunchIdentity
    LogPath = (Resolve-SgPath $dashboardLog)
})

if (!(Wait-SgHttpReady -Url "http://127.0.0.1:$dashboardPort" -TimeoutSeconds 60 -LogPath $dashboardLog -Process $process -TailPaths @($dashboardLog, $dashboardConsoleLog, $dashboardErrorLog))) {
    Stop-SgStartedProcess "dashboard"
    Remove-SgProcessRecord "dashboard"
    throw "Dashboard did not become ready. See $(Resolve-SgPath $dashboardLog)."
}

Write-SgLine "Dashboard ready at http://127.0.0.1:$dashboardPort." "INFO" $dashboardLog
