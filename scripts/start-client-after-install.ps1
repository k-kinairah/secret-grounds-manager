param(
    [switch]$KeepAutostart,
    [int]$TimeoutSeconds = 10
)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

# The installer used to register the "Secret Grounds Client" scheduled task
# (when "Start automatically with Windows" was checked) but never triggered
# it -- the task's only trigger is AtLogOn, so on a fresh install nothing
# actually launched the client until the next reboot or sign-in. The task
# and its action (hidden PowerShell -> start-client.ps1 -> the packaged exe)
# were themselves healthy the whole time: manually running
# `Start-ScheduledTask -TaskName "Secret Grounds Client"` immediately showed
# the lock screen and brought the station online. This script performs that
# same immediate launch right after install, using the task as the launch
# mechanism instead of duplicating another way to start the client.
$taskName = "Secret Grounds Client"
$clientLog = Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log"
$packagedClientExe = (Join-Path $Script:SecretGroundsRoot "client\SecretGroundsClient.exe").ToLowerInvariant()

function Get-SgRunningClientProcess {
    Get-CimInstance Win32_Process -Filter "Name='SecretGroundsClient.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and ($_.ExecutablePath.ToLowerInvariant() -eq $packagedClientExe) }
}

$alreadyRunning = Get-SgRunningClientProcess
if ($alreadyRunning) {
    Write-SgLine "Secret Grounds client is already running (PID $($alreadyRunning[0].ProcessId)); skipping post-install launch." "INFO" $clientLog
    if (-not $KeepAutostart) {
        & "$PSScriptRoot\remove-client-autostart.ps1"
    }
    exit 0
}

# The checkbox-gated [Run] entry only registers this task when "Start
# automatically with Windows" was checked. Register it here too if it isn't
# present yet, purely as the one-time launch mechanism for this install --
# it gets removed again below unless the operator actually wants it to
# persist for future logons.
if (!(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
    & "$PSScriptRoot\install-client-autostart.ps1"
}
if (!(Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue)) {
    Write-SgLine "Cannot start Secret Grounds client: scheduled task '$taskName' could not be registered." "ERROR" $clientLog
    exit 1
}

Start-ScheduledTask -TaskName $taskName

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$started = $false
do {
    Start-Sleep -Milliseconds 500
    if (Get-SgRunningClientProcess) {
        $started = $true
        break
    }
} while ((Get-Date) -lt $deadline)

if (-not $KeepAutostart) {
    # The launch already happened; the task itself was only needed to reach
    # this point. Remove it now so a station where the operator declined
    # "Start automatically with Windows" doesn't get it anyway on the next
    # logon just because it happened to exist as the launch mechanism.
    & "$PSScriptRoot\remove-client-autostart.ps1"
}

if (-not $started) {
    Write-SgLine "Secret Grounds client did not start within $TimeoutSeconds seconds after Start-ScheduledTask." "ERROR" $clientLog
    exit 1
}

Write-SgLine "Secret Grounds client started successfully after install." "INFO" $clientLog
exit 0
