. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$taskName = "Secret Grounds Client"
$scriptPath = Join-Path $PSScriptRoot "start-client.ps1"
$argument = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument -WorkingDirectory $Script:SecretGroundsRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Description "Starts the Secret Grounds customer PC client." | Out-Null
Write-SgLine "Installed scheduled task '$taskName'." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
