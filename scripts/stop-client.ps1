param(
    # Also disable the scheduled task so the 1-minute repetition trigger
    # does not simply bring the supervisor back. Default on -- this script
    # exists to actually stop the client (maintenance, uninstall). Pass
    # -KeepTaskEnabled to only stop the current processes (e.g. before an
    # immediate restart).
    [switch]$KeepTaskEnabled
)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$clientLog = Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log"
$resolvedClientLog = Resolve-SgPath $clientLog
New-Item -ItemType Directory -Force -Path (Split-Path $resolvedClientLog) | Out-Null
$stopSentinel = Join-Path (Split-Path $resolvedClientLog) "client-supervisor.stop"
$taskName = "Secret Grounds Client"

# 1. Tell a running supervisor loop not to relaunch after the client exits.
Set-Content -LiteralPath $stopSentinel -Value "stop" -Encoding ASCII

# 2. Stop the outer watchdog first so the task's repetition trigger cannot
#    start a fresh supervisor while we are shutting down.
if (-not $KeepTaskEnabled) {
    try {
        if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
            Disable-ScheduledTask -TaskName $taskName -ErrorAction Stop | Out-Null
            Write-SgLine "Disabled scheduled task '$taskName'." "INFO" $clientLog
        }
    } catch {
        Write-SgLine "Could not disable scheduled task '$taskName': $($_.Exception.Message)" "WARN" $clientLog
    }
}

# 3. Stop the supervisor (its /T kill takes the client with it) then the
#    client, in that order.
Stop-SgStartedProcess "client-supervisor"
Stop-SgStartedProcess "client"

# 4. Defensive sweep: any SecretGroundsClient.exe from this install, plus
#    any run-client-supervised.ps1 PowerShell host, regardless of PID
#    tracking.
$packagedClientExe = (Join-Path $Script:SecretGroundsRoot "client\SecretGroundsClient.exe").ToLowerInvariant()
$supervisorScript = (Join-Path $PSScriptRoot "run-client-supervised.ps1").ToLowerInvariant()
Get-CimInstance Win32_Process -Filter "Name='SecretGroundsClient.exe' OR Name='powershell.exe' OR Name='pwsh.exe'" -ErrorAction SilentlyContinue |
    Where-Object {
        ($_.ExecutablePath -and $_.ExecutablePath.ToLowerInvariant() -eq $packagedClientExe) -or
        ($_.CommandLine -and $_.CommandLine.ToLowerInvariant().Contains($supervisorScript))
    } |
    ForEach-Object {
        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
    }

# Sentinel has done its job (or will be cleared by the next supervisor start).
Remove-Item -LiteralPath $stopSentinel -Force -ErrorAction SilentlyContinue

if ($KeepTaskEnabled) {
    Write-SgLine "Secret Grounds client stopped (task left enabled; it will start again on next logon or task run)." "INFO" $clientLog
} else {
    Write-SgLine "Secret Grounds client stopped and autostart disabled. Re-run install-client-autostart.ps1 to re-enable." "INFO" $clientLog
}
