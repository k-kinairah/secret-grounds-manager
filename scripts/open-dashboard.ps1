. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

$serverLog = Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log"
$backendPort = [int](Get-SgEnv "SECRET_GROUNDS_PORT" $Script:DefaultBackendPort)
$dashboardPort = [int](Get-SgEnv "SECRET_GROUNDS_DASHBOARD_PORT" $Script:DefaultDashboardPort)
$dashboardUrl = "http://127.0.0.1:$dashboardPort"

function Test-SgHealthOnce {
    param([string]$Url)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch {
        return $false
    }
}

$backendHealthy = Test-SgHealthOnce "http://127.0.0.1:$backendPort/healthz"
$dashboardHealthy = Test-SgHealthOnce $dashboardUrl

if (!$backendHealthy -or !$dashboardHealthy) {
    Write-SgLine "Dashboard shortcut: services not healthy yet, starting Secret Grounds." "INFO" $serverLog
    try {
        & "$PSScriptRoot\start-secret-grounds.ps1" -NoBrowser
    } catch {
        # The stack may already be starting from autostart and just not
        # answering yet, or genuinely failed; either way fall through to the
        # readiness wait below instead of aborting the shortcut outright.
        Write-SgLine "Dashboard shortcut: start-secret-grounds reported: $($_.Exception.Message)" "WARN" $serverLog
    }

    $backendReady = Wait-SgHttpReady -Url "http://127.0.0.1:$backendPort/healthz" -TimeoutSeconds 30 -LogPath $serverLog
    $dashboardReady = $backendReady -and (Wait-SgHttpReady -Url $dashboardUrl -TimeoutSeconds 30 -LogPath $serverLog)
    if (!$backendReady -or !$dashboardReady) {
        Write-SgLine "Dashboard shortcut: Secret Grounds did not become ready in time. See $serverLog." "ERROR" $serverLog
    }
}

Start-Process $dashboardUrl
