. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$taskName = "Secret Grounds Client"
$scriptPath = Join-Path $PSScriptRoot "start-client.ps1"
$argument = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $Script:SecretGroundsRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
# Registering without an explicit principal defaults to RunLevel Limited, which
# cannot reliably start the client process/lock screen under Program Files --
# the task reports LastTaskResult 1 and no client process remains, even though
# the action and working directory are correct. Confirmed fixed on a clean
# customer PC by registering with RunLevel Highest (same root cause and fix as
# the cashier autostart task in install-cashier-autostart.ps1).
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest
# Cap retries so a genuinely broken install doesn't spin forever: at most one
# automatic restart on top of the initial attempt (2 attempts total), and
# Task Scheduler kills a hung instance after 10 minutes either way.
$settings = New-ScheduledTaskSettingsSet -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew -StartWhenAvailable

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Starts the Secret Grounds customer PC client." | Out-Null
Write-SgLine "Installed scheduled task '$taskName' for $currentUser (LogonType Interactive, RunLevel Highest)." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
