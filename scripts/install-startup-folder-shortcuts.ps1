param(
    [ValidateSet("Cashier", "Client", "Both")]
    [string]$Role = "Cashier"
)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

$startupFolder = [Environment]::GetFolderPath("Startup")
if ([string]::IsNullOrWhiteSpace($startupFolder)) {
    throw "Could not resolve the current user's Startup folder."
}

$cashierScript = Join-Path $PSScriptRoot "start-secret-grounds.ps1"
# The startup-folder fallback launches the supervised client loop too, so
# this path also self-heals a customer ending the client in Task Manager
# (it lacks the scheduled task's outer 1-minute watchdog, but the inner
# ~2s relaunch still covers the common case).
$clientScript = Join-Path $PSScriptRoot "run-client-supervised.ps1"
$cashierLauncher = Join-Path $startupFolder "Secret Grounds Cashier Stack.vbs"
$clientLauncher = Join-Path $startupFolder "Secret Grounds Client.vbs"

$cashierContent = @'
Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{0}"" -NoBrowser", 0, False
'@ -f $cashierScript

$clientContent = @'
Set shell = CreateObject("WScript.Shell")
shell.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""{0}""", 0, False
'@ -f $clientScript

if ($Role -in @("Cashier", "Both")) {
    Set-Content -LiteralPath $cashierLauncher -Value $cashierContent -Encoding ASCII
}
if ($Role -in @("Client", "Both")) {
    Set-Content -LiteralPath $clientLauncher -Value $clientContent -Encoding ASCII
}

Write-SgLine "Installed current-user Startup launcher role '$Role' in $startupFolder." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
