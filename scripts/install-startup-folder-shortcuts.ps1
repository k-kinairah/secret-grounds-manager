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
$clientScript = Join-Path $PSScriptRoot "start-client.ps1"
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
