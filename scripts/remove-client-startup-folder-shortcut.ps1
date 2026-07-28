. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$startupFolder = [Environment]::GetFolderPath("Startup")
$clientLauncher = Join-Path $startupFolder "Secret Grounds Client.vbs"

if (Test-Path $clientLauncher) {
    Remove-Item -LiteralPath $clientLauncher -Force
    Write-SgLine "Removed current-user Startup launcher '$clientLauncher'." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
} else {
    Write-SgLine "Current-user Secret Grounds Client Startup launcher is not installed." "WARN" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
}
