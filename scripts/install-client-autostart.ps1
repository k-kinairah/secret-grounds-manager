. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$taskName = "Secret Grounds Client"
# The task action is the supervisor loop, not a one-shot launcher: it keeps
# the customer client alive so ending it in Task Manager just re-locks the
# PC a couple of seconds later. See run-client-supervised.ps1.
$scriptPath = Join-Path $PSScriptRoot "run-client-supervised.ps1"
$argument = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $Script:SecretGroundsRoot

# AtLogOn starts the supervisor for the customer session. The 1-minute
# repetition is the OUTER watchdog: if a customer also ends the supervisor
# process itself, Task Scheduler brings it back within a minute (the client
# keeps running, still locked, in that gap). While the supervisor is alive
# the repeated trigger is a no-op (MultipleInstances IgnoreNew).
$trigger = New-ScheduledTaskTrigger -AtLogOn
$repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 1) `
        -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition
$trigger.Repetition = $repetition

# Registering without an explicit principal defaults to RunLevel Limited, which
# cannot reliably start the client process/lock screen under Program Files --
# the task reports LastTaskResult 1 and no client process remains, even though
# the action and working directory are correct. Confirmed fixed on a clean
# customer PC by registering with RunLevel Highest (same root cause and fix as
# the cashier autostart task in install-cashier-autostart.ps1).
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest
# ExecutionTimeLimit 0 = no limit: the supervisor is meant to run for the
# whole customer session (hours), not be killed after 10 minutes.
# RestartCount is a belt-and-braces on-failure restart on top of the
# repetition trigger's revival.
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Keeps the Secret Grounds customer PC client running (supervised)." | Out-Null
Write-SgLine "Installed scheduled task '$taskName' for $currentUser (supervised client, LogonType Interactive, RunLevel Highest, 1-minute watchdog)." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
