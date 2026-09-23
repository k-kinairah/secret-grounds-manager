param(
    # Also disable the scheduled task so the 1-minute repetition trigger does
    # not simply bring the supervisor back. Default on -- this script exists
    # to actually stop the stack (maintenance, uninstall). Pass
    # -KeepTaskEnabled to only stop the current processes (e.g. before an
    # immediate restart).
    [switch]$KeepTaskEnabled
)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$serverLog = Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log"
$resolvedServerLog = Resolve-SgPath $serverLog
New-Item -ItemType Directory -Force -Path (Split-Path $resolvedServerLog) | Out-Null
# NOT under {app}\logs -- confirmed live as an UnauthorizedAccessException:
# that directory is created by the SYSTEM-run supervisor, and Program
# Files' default ACL lets a non-admin interactive user (the cashier,
# clicking the "Stop Secret Grounds" shortcut) modify files they already
# have access to but not CREATE a brand-new one there. See the matching
# comment in run-cashier-supervised.ps1, which must resolve to this same
# path.
$stopSentinelDir = Join-Path $env:ProgramData "Secret Grounds"
New-Item -ItemType Directory -Force -Path $stopSentinelDir | Out-Null
$stopSentinel = Join-Path $stopSentinelDir "cashier-supervisor.stop"
$taskName = "Secret Grounds Cashier Stack"

# 1. Tell a running supervisor loop not to restart the stack after this.
Set-Content -LiteralPath $stopSentinel -Value "stop" -Encoding ASCII

# 2. Stop the outer watchdog first so the task's repetition trigger cannot
#    start a fresh supervisor while we are shutting down.
if (-not $KeepTaskEnabled) {
    try {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
            Write-SgLine "Disabled scheduled task '$taskName'." "INFO" $serverLog
        }
    } catch {
        Write-SgLine "Could not disable scheduled task '$taskName': $($_.Exception.Message)" "WARN" $serverLog
    }
}

# 3. Stop the supervisor (its /T kill takes backend and dashboard with it)
#    then each process directly as a backup in case either was started
#    outside the supervisor (e.g. manually, as tonight).
Stop-SgStartedProcess "cashier-supervisor"
Stop-SgStartedProcess "dashboard"
Stop-SgStartedProcess "backend"

Remove-Item -LiteralPath $stopSentinel -Force -ErrorAction SilentlyContinue

if ($KeepTaskEnabled) {
    Write-SgLine "Secret Grounds cashier stack stopped (task left enabled; it will start again on next boot/logon or task run)." "INFO" $serverLog
} else {
    Write-SgLine "Secret Grounds cashier stack stopped and autostart disabled. Re-run install-cashier-autostart.ps1 to re-enable." "INFO" $serverLog
}
