param([switch]$AtStartup)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

$taskName = "Secret Grounds Cashier Stack"
$scriptPath = Join-Path $PSScriptRoot "start-secret-grounds.ps1"
$argument = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`" -NoBrowser"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $Script:SecretGroundsRoot
$trigger = if ($AtStartup) { New-ScheduledTaskTrigger -AtStartup } else { New-ScheduledTaskTrigger -AtLogOn }
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Starts the Secret Grounds backend and production dashboard." | Out-Null
Write-SgLine "Installed scheduled task '$taskName'." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
