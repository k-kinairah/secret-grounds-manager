param([switch]$UseStartupFolderFallback)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig
Assert-SgDeploymentConfig

& "$PSScriptRoot\remove-client-startup-folder-shortcut.ps1"
try {
    & "$PSScriptRoot\remove-client-autostart.ps1"
} catch {
    Write-SgLine "Could not remove Task Scheduler client autostart: $($_.Exception.Message)" "WARN" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
}

if ($UseStartupFolderFallback) {
    & "$PSScriptRoot\install-startup-folder-shortcuts.ps1" -Role Cashier
} else {
    & "$PSScriptRoot\install-cashier-autostart.ps1"
}

Write-SgLine "Cashier/server role configured. Client autostart is not installed by default." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
