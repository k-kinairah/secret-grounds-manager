. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

Stop-SgStartedProcess "dashboard"
Stop-SgStartedProcess "backend"
Write-SgLine "Secret Grounds cashier stack stopped." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
