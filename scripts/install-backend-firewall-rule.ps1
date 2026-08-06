. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

$backendPort = [int](Get-SgEnv "SECRET_GROUNDS_PORT" $Script:DefaultBackendPort)
$ruleName = "Secret Grounds Backend Private"

if ($backendPort -eq 8000) {
    throw "Refusing to create a production firewall rule for development port 8000."
}

$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($existing) {
    Remove-NetFirewallRule -DisplayName $ruleName
}

New-NetFirewallRule `
    -DisplayName $ruleName `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort $backendPort `
    -Profile Private `
    -Program (Join-Path $Script:SecretGroundsRoot "runtime\python\python.exe") `
    -Description "Allows Secret Grounds customer clients to reach the production backend on private networks only." |
    Out-Null

Write-SgLine "Installed private-network firewall rule '$ruleName' for TCP $backendPort." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
