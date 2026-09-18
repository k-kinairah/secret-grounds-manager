param(
    [switch]$NoBrowser,
    [switch]$AllowTestMode,
    # --- test seams (production leaves all of these at their defaults) ---
    [string]$StopSentinelPath = "",
    [int]$PollSeconds = 10,
    # 0 = supervise forever (production). A positive value stops the loop
    # after that many restart attempts (tests only).
    [int]$MaxRelaunches = 0
)

# Keep the cashier backend and dashboard alive. This script is the action of
# the "Secret Grounds Cashier Stack" scheduled task: it starts the stack, then
# polls both processes and restarts the whole stack if either one has died --
# so a crash mid-day (or someone ending a process in Task Manager) is back up
# within one poll interval instead of staying down until someone notices and
# runs the start scripts by hand.
#
# The outer watchdog (this supervisor process itself dying) is the task's own
# 1-minute repetition trigger -- see install-cashier-autostart.ps1. That
# trigger is a no-op while this loop is alive (MultipleInstances IgnoreNew).

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

$serverLog = Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log"
$resolvedServerLog = Resolve-SgPath $serverLog
New-Item -ItemType Directory -Force -Path (Split-Path $resolvedServerLog) | Out-Null

$stopSentinel = if ($StopSentinelPath) {
    $StopSentinelPath
} else {
    Join-Path (Split-Path $resolvedServerLog) "cashier-supervisor.stop"
}

function Test-StopRequested {
    if (Test-Path -LiteralPath $stopSentinel) {
        Remove-Item -LiteralPath $stopSentinel -Force -ErrorAction SilentlyContinue
        return $true
    }
    return $false
}

# A stale sentinel from a previous "stop" must not block this fresh start.
Remove-Item -LiteralPath $stopSentinel -Force -ErrorAction SilentlyContinue

Set-SgProcessRecord "cashier-supervisor" ([pscustomobject]@{
    Pid       = $PID
    Role      = "cashier-supervisor"
    Port      = ""
    StartedAt = (Get-Date).ToString("o")
    Identity  = $PSCommandPath
    LogPath   = $resolvedServerLog
    PcName    = ""
})
Write-SgLine "Secret Grounds cashier supervisor started (PID $PID); poll interval ${PollSeconds}s." "INFO" $serverLog

$relaunches = 0
try {
    while ($true) {
        if (Test-StopRequested) {
            Write-SgLine "Stop requested; cashier supervisor exiting without relaunch." "INFO" $serverLog
            break
        }

        $backendUp = Test-SgStartedProcess (Get-SgProcessRecord "backend")
        $dashboardUp = Test-SgStartedProcess (Get-SgProcessRecord "dashboard")

        if (!$backendUp -or !$dashboardUp) {
            Write-SgLine "Cashier stack down (backend running=$backendUp, dashboard running=$dashboardUp); restarting." "WARN" $serverLog
            # Tear down whichever half is still alive first, so the fresh
            # start below never trips over "already running" while
            # relaunching the half that actually crashed.
            Stop-SgStartedProcess "dashboard"
            Stop-SgStartedProcess "backend"
            try {
                & "$PSScriptRoot\start-secret-grounds.ps1" -NoBrowser:$NoBrowser -AllowTestMode:$AllowTestMode
            } catch {
                Write-SgLine "Cashier stack restart failed: $($_.Exception.Message)" "ERROR" $serverLog
            }
            $relaunches++
            if ($MaxRelaunches -gt 0 -and $relaunches -ge $MaxRelaunches) {
                Write-SgLine "Reached MaxRelaunches=$MaxRelaunches; cashier supervisor exiting (test mode)." "INFO" $serverLog
                break
            }
        }

        if (Test-StopRequested) {
            Write-SgLine "Stop requested; cashier supervisor exiting without relaunch." "INFO" $serverLog
            break
        }
        Start-Sleep -Seconds $PollSeconds
    }
} finally {
    Remove-SgProcessRecord "cashier-supervisor"
}
