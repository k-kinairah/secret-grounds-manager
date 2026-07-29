param(
    [switch]$NoBrowser,
    [switch]$AllowTestMode
)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

& "$PSScriptRoot\stop-secret-grounds.ps1"
& "$PSScriptRoot\start-secret-grounds.ps1" -NoBrowser -AllowTestMode:$AllowTestMode
