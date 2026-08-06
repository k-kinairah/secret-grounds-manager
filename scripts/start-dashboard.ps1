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

# The cashier PC must never need Node.js/npm installed. scripts\prepare-dashboard-runtime.ps1
# bundles a private node.exe, and dashboard\next.config.ts builds with
# output: "standalone" so the dashboard runs as `node server.js` -- no `next`
# CLI, no node_modules beyond what standalone tracing already copied in.
$dashboardRoot = Join-Path $Script:SecretGroundsRoot "dashboard"
$nodeExe = Join-Path $Script:SecretGroundsRoot "runtime\node\node.exe"
$serverJs = Join-Path $dashboardRoot ".next\standalone\server.js"
$commandLine = (@($nodeExe, $serverJs) | ForEach-Object { ConvertTo-SgCmdArgument $_ }) -join ' '
$capturedExitCode = $null

try {
    if (Test-SgStartedProcess (Get-SgProcessRecord "dashboard")) {
        throw "Secret Grounds dashboard is already running from deployment scripts."
    }
    if (!(Test-SgPortAvailable -Port $dashboardPort)) {
        throw "Dashboard port $dashboardPort is already in use (currently held by $(Get-SgPortOwnerDescription -Port $dashboardPort))."
    }
    if (!(Test-Path $nodeExe)) {
        throw "Dashboard Node.js runtime is missing: $nodeExe. Run scripts\prepare-dashboard-runtime.ps1."
    }
    if (!(Test-Path $serverJs)) {
        throw "Dashboard production build is missing: $serverJs. Run scripts\build-dashboard.ps1 first."
    }
    if (!(Wait-SgHttpReady -Url "http://127.0.0.1:$backendPort/healthz" -TimeoutSeconds 5 -LogPath $dashboardLog)) {
        throw "Backend is unavailable at http://127.0.0.1:$backendPort/healthz."
    }

    New-Item -ItemType Directory -Force -Path (Split-Path (Resolve-SgPath $dashboardLog)) | Out-Null
    Write-SgLine "Starting Secret Grounds dashboard $(Get-SgVersion) on $dashboardHost`:$dashboardPort with backend $backendUrl." "INFO" $dashboardLog
    Write-SgLine "Dashboard command line: $commandLine (working directory: $dashboardRoot)" "INFO" $dashboardLog

    $env:NEXT_PUBLIC_FASTAPI_BASE_URL = $backendUrl
    $env:PORT = $dashboardPort.ToString()
    $env:HOSTNAME = $dashboardHost
    $process = Start-SgLoggedProcess -Role "dashboard" -FilePath $nodeExe -Arguments @($serverJs) -WorkingDirectory $dashboardRoot -LogPath $dashboardConsoleLog -ErrorLogPath $dashboardErrorLog

    Set-SgProcessRecord "dashboard" ([pscustomobject]@{
        Pid = $process.Id
        Role = "dashboard"
        Port = $dashboardPort
        StartedAt = (Get-Date).ToString("o")
        Identity = $Script:LastSgLaunchIdentity
        LogPath = (Resolve-SgPath $dashboardLog)
    })

    if (!(Wait-SgHttpReady -Url "http://127.0.0.1:$dashboardPort" -TimeoutSeconds 60 -LogPath $dashboardLog -Process $process -TailPaths @($dashboardLog, $dashboardConsoleLog, $dashboardErrorLog))) {
        $process.Refresh()
        $capturedExitCode = if ($process.HasExited) { $process.ExitCode } else { $null }
        Stop-SgStartedProcess "dashboard"
        Remove-SgProcessRecord "dashboard"
        throw "Dashboard did not become ready. See $(Resolve-SgPath $dashboardLog)."
    }

    Write-SgLine "Dashboard ready at http://127.0.0.1:$dashboardPort." "INFO" $dashboardLog
} catch {
    Write-SgFailureDiagnostics -ErrorLogPath $dashboardErrorLog -Role "Dashboard" -Reason $_.Exception.Message -CommandLine $commandLine -WorkingDirectory $dashboardRoot -ExitCode $capturedExitCode -FallbackLogPath $dashboardLog
    throw
}
