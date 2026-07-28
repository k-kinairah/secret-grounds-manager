. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$backend = Get-SgProcessRecord "backend"
$dashboard = Get-SgProcessRecord "dashboard"
$client = Get-SgProcessRecord "client"
$backendRunning = Test-SgStartedProcess $backend
$dashboardRunning = Test-SgStartedProcess $dashboard
$clientRunning = Test-SgStartedProcess $client
$backendPort = [int](Get-SgEnv "SECRET_GROUNDS_PORT" $Script:DefaultBackendPort)
$dashboardPort = [int](Get-SgEnv "SECRET_GROUNDS_DASHBOARD_PORT" $Script:DefaultDashboardPort)

$backendHealth = "Not checked"
if ($backendRunning) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$backendPort/healthz" -UseBasicParsing -TimeoutSec 3
        $backendHealth = "Healthy ($($response.StatusCode))"
    } catch {
        $backendHealth = "Unhealthy"
    }
}

$dashboardHealth = "Not checked"
if ($dashboardRunning) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$dashboardPort" -UseBasicParsing -TimeoutSec 3
        $dashboardHealth = "Available ($($response.StatusCode))"
    } catch {
        $dashboardHealth = "Unavailable"
    }
}

[pscustomobject]@{
    Version = Get-SgVersion
    Backend = if ($backendRunning) { "Running" } else { "Stopped" }
    BackendPid = if ($backend) { $backend.Pid } else { "" }
    BackendPort = $backendPort
    BackendHealth = $backendHealth
    BackendStartedAt = if ($backend) { $backend.StartedAt } else { "" }
    BackendLog = Resolve-SgPath (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
    Dashboard = if ($dashboardRunning) { "Running" } else { "Stopped" }
    DashboardPid = if ($dashboard) { $dashboard.Pid } else { "" }
    DashboardPort = $dashboardPort
    DashboardHealth = $dashboardHealth
    DashboardStartedAt = if ($dashboard) { $dashboard.StartedAt } else { "" }
    DashboardLog = Resolve-SgPath (Get-SgEnv "SECRET_GROUNDS_DASHBOARD_LOG" ".\logs\dashboard.log")
    Client = if ($clientRunning) { "Running" } else { "Stopped" }
    ClientPid = if ($client) { $client.Pid } else { "" }
    ClientPcName = if ($client) { $client.PcName } else { "" }
    ClientStartedAt = if ($client) { $client.StartedAt } else { "" }
    ClientLog = Resolve-SgPath (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
    PidFile = $Script:PidPath
} | Format-List
