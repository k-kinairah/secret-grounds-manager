param(
    [switch]$UseStartupFolderFallback,
    # A plain [switch] (present/absent), not [bool]/[switch]:$true|$false: a new
    # external "powershell.exe -File ... -Param:$true" process invocation does not
    # reliably bind boolean-valued parameters -- the literal text "$true" arrives
    # unparsed and fails type conversion. Presence/absence of the switch is the
    # only form that is reliable across a process boundary.
    [switch]$DisableAutostart
)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig
Assert-SgDeploymentConfig

& "$PSScriptRoot\remove-client-startup-folder-shortcut.ps1"
try {
    & "$PSScriptRoot\remove-client-autostart.ps1"
} catch {
    Write-SgLine "Could not remove Task Scheduler client autostart: $($_.Exception.Message)" "WARN" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
}

if (!$DisableAutostart) {
    if ($UseStartupFolderFallback) {
        & "$PSScriptRoot\install-startup-folder-shortcuts.ps1" -Role Cashier
    } else {
        # -AtStartup, not the script's default -AtLogOn: a cashier/server PC
        # must start the backend the moment Windows boots, not wait for
        # someone to physically sign in first -- a PC left on overnight or
        # rebooted by Windows Update should already be serving customer PCs
        # before anyone arrives to log in.
        & "$PSScriptRoot\install-cashier-autostart.ps1" -AtStartup
    }
    Write-SgLine "Cashier/server role configured with autostart enabled. Client autostart is not installed by default." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
} else {
    & "$PSScriptRoot\remove-cashier-autostart.ps1"
    Write-SgLine "Cashier/server role configured with autostart disabled. Client autostart is not installed by default." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
}
