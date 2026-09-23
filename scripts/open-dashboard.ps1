# Reported live: this scheduled-task action failed at logon with Windows'
# own generic Task Scheduler result 0x1 and left absolutely no trace in
# server.log -- because the failure happened in Import-SgEnv/dot-sourcing,
# before $serverLog (or Write-SgLine itself) was even available. Wrapping
# the whole script in a try/catch that logs to a location that needs no
# prior setup (the OS temp folder, not anything from secret-grounds-common)
# means a future failure like this leaves an actual error message instead
# of just a mystery result code.
$fallbackErrorLog = Join-Path ([System.IO.Path]::GetTempPath()) "secret-grounds-dashboard-autoopen-error.log"

try {
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
} catch {
    $errorMessage = "$(Get-Date -Format o) Dashboard auto-open failed: $($_.Exception.Message)`r`n$($_.ScriptStackTrace)`r`n"
    Add-Content -LiteralPath $fallbackErrorLog -Value $errorMessage -Encoding UTF8 -ErrorAction SilentlyContinue
    throw
}
