param(
    [string]$ServerHost = "127.0.0.1",
    [int]$ServerPort = 18001,
    [string]$StationId = "CASHIER-TEST",
    [string]$DisplayName = "Cashier Test Client",
    [switch]$InstallAutostart,
    [switch]$UseStartupFolderFallback
)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

& "$PSScriptRoot\configure-client.ps1" `
    -ServerHost $ServerHost `
    -ServerPort $ServerPort `
    -StationId $StationId `
    -DisplayName $DisplayName

if ($InstallAutostart) {
    if ($UseStartupFolderFallback) {
        & "$PSScriptRoot\install-startup-folder-shortcuts.ps1" -Role Client
    } else {
        & "$PSScriptRoot\install-client-autostart.ps1"
    }
}

Write-SgLine "Cashier test client config prepared. Manual testing remains available through scripts\start-client.ps1." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
