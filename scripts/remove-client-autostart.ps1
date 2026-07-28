. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$taskName = "Secret Grounds Client"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-SgLine "Removed scheduled task '$taskName'." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
} else {
    Write-SgLine "Scheduled task '$taskName' is not installed." "WARN" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
}
