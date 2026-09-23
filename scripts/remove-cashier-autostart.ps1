. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$serverLog = Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log"
foreach ($taskName in @("Secret Grounds Cashier Stack", "Secret Grounds Dashboard AutoOpen")) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-SgLine "Removed scheduled task '$taskName'." "INFO" $serverLog
    } else {
        Write-SgLine "Scheduled task '$taskName' is not installed." "WARN" $serverLog
    }
}
