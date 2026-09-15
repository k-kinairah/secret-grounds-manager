. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$taskName = "Secret Grounds Client"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    Write-SgLine "Removed scheduled task '$taskName'." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
} else {
    Write-SgLine "Scheduled task '$taskName' is not installed." "WARN" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
}

# install-client-autostart.ps1 generates this hidden-launch wrapper next to
# it; remove it alongside the task so nothing stale is left in scripts\.
$vbsPath = Join-Path $PSScriptRoot "run-client-supervised-hidden.vbs"
Remove-Item -LiteralPath $vbsPath -Force -ErrorAction SilentlyContinue
