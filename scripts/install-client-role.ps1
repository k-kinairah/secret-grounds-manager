param(
    [Parameter(Mandatory = $true)]
    [string]$ServerHost,
    [int]$ServerPort = 18001,
    [Parameter(Mandatory = $true)]
    [string]$StationId,
    [Parameter(Mandatory = $true)]
    [string]$DisplayName,
    [int]$ReconnectIntervalSeconds = 5,
    [int]$RequestTimeoutSeconds = 5,
    [switch]$UseStartupFolderFallback
)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

# The directory-permissions fix (see install-client-autostart.ps1, which
# this and the GUI installer both call) lives there instead of here --
# install-client-role.ps1 is not actually invoked by the GUI installer,
# which calls configure-client.ps1 and install-client-autostart.ps1
# directly, so a fix placed only here would never run for a real install.

& "$PSScriptRoot\configure-client.ps1" `
    -ServerHost $ServerHost `
    -ServerPort $ServerPort `
    -StationId $StationId `
    -DisplayName $DisplayName `
    -ReconnectIntervalSeconds $ReconnectIntervalSeconds `
    -RequestTimeoutSeconds $RequestTimeoutSeconds

if ($UseStartupFolderFallback) {
    & "$PSScriptRoot\install-startup-folder-shortcuts.ps1" -Role Client
} else {
    & "$PSScriptRoot\install-client-autostart.ps1"
}

Write-SgLine "Customer client role configured for station '$StationId'." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
