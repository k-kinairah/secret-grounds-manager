param([switch]$AtStartup)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

$taskName = "Secret Grounds Cashier Stack"
$scriptPath = Join-Path $PSScriptRoot "start-secret-grounds.ps1"
$argument = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`" -NoBrowser"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $Script:SecretGroundsRoot
$trigger = if ($AtStartup) { New-ScheduledTaskTrigger -AtStartup } else { New-ScheduledTaskTrigger -AtLogOn }
# Registering without an explicit principal defaults to RunLevel Limited, which
# cannot write to {app}\data or {app}\logs under C:\Program Files\Secret
# Grounds\ -- the task silently fails (LastTaskResult 1) even though the
# action, working directory, and trigger all look correct. The cashier stack
# must run elevated as the interactive user who signs in on this PC.
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest
# Cap retries so a genuinely broken install doesn't spin forever: at most one
# automatic restart on top of the initial attempt (2 attempts total), and
# Task Scheduler kills a hung instance after 10 minutes either way.
$settings = New-ScheduledTaskSettingsSet -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -StartWhenAvailable

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Starts the Secret Grounds backend and production dashboard." | Out-Null
Write-SgLine "Installed scheduled task '$taskName' for $currentUser (LogonType Interactive, RunLevel Highest)." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
