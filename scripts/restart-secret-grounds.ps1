param(
    [switch]$NoBrowser,
    [switch]$AllowTestMode
)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

# -KeepTaskEnabled: leave the scheduled task enabled so its 1-minute
# repetition trigger re-arms a fresh supervisor within a minute of this
# manual restart, instead of leaving the stack unsupervised until someone
# remembers to re-run install-cashier-autostart.ps1.
& "$PSScriptRoot\stop-secret-grounds.ps1" -KeepTaskEnabled
& "$PSScriptRoot\start-secret-grounds.ps1" -NoBrowser -AllowTestMode:$AllowTestMode
