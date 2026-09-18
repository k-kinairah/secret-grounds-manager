param([switch]$AtStartup)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

$taskName = "Secret Grounds Cashier Stack"
# The task action is the supervisor loop, not a one-shot launcher: it polls
# the backend and dashboard and restarts the whole stack if either one dies,
# so a crash mid-day is back up within one poll interval instead of staying
# down until someone notices and runs the start scripts by hand. See
# run-cashier-supervised.ps1.
$scriptPath = Join-Path $PSScriptRoot "run-cashier-supervised.ps1"
$argument = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`" -NoBrowser"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $Script:SecretGroundsRoot
$trigger = if ($AtStartup) { New-ScheduledTaskTrigger -AtStartup } else { New-ScheduledTaskTrigger -AtLogOn }
# The 1-minute repetition is the OUTER watchdog: if the supervisor process
# itself is ever killed, Task Scheduler brings it back within a minute (the
# backend/dashboard keep running in that gap -- only the supervisor died).
# While the supervisor is alive the repeated trigger is a no-op
# (MultipleInstances IgnoreNew below).
$repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 1) `
        -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition
$trigger.Repetition = $repetition
# Registering without an explicit principal defaults to RunLevel Limited, which
# cannot write to {app}\data or {app}\logs under C:\Program Files\Secret
# Grounds\ -- the task silently fails (LastTaskResult 1) even though the
# action, working directory, and trigger all look correct. So the principal is
# always RunLevel Highest; the identity depends on the trigger:
#
#  -AtStartup (the cashier/server default): run as SYSTEM. A "run only when
#    user is logged on" (LogonType Interactive) principal is NOT eligible to
#    run at boot while the PC is still sitting on the sign-in screen, and an
#    -AtStartup trigger has already elapsed by the time someone signs in, so
#    the stack would never come up on a normal reboot -- exactly the point of
#    starting at boot (serve the customer PCs before anyone arrives to log in).
#    SYSTEM is always present in session 0, always has full rights to {app},
#    and the stack needs no desktop (it launches with -NoBrowser and its
#    processes are headless).
#
#  -AtLogOn (startup-folder-style fallback): keep the interactive signer, who
#    does have a session by then.
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = if ($AtStartup) {
    New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
} else {
    New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest
}
$principalLabel = if ($AtStartup) { "SYSTEM (LogonType ServiceAccount, at startup)" } else { "$currentUser (LogonType Interactive, at logon)" }
# ExecutionTimeLimit 0 = no limit: the supervisor is meant to run for the
# whole cashier session (hours/days), not be killed after 10 minutes.
# RestartCount is a belt-and-braces on-failure restart on top of the
# repetition trigger's revival.
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Starts and supervises the Secret Grounds backend and production dashboard." | Out-Null
Write-SgLine "Installed scheduled task '$taskName' as $principalLabel, RunLevel Highest, 1-minute watchdog." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
