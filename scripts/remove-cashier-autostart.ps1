. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$taskName = "Secret Grounds Cashier Stack"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-SgLine "Removed scheduled task '$taskName'." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
} else {
    Write-SgLine "Scheduled task '$taskName' is not installed." "WARN" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
}
