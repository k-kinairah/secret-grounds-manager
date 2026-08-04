param(
    [Parameter(Mandatory = $true)]
    [string]$InstallPath
)

$ErrorActionPreference = "Stop"

function Write-SgStatusLine {
    param([string]$Key, [string]$Value)
    $safeValue = ($Value -replace "[\r\n]", " ")
    Write-Output "$Key=$safeValue"
}

try {
    $configPath = Join-Path $InstallPath "config\.env"
    if (!(Test-Path -LiteralPath $configPath)) {
        Write-SgStatusLine "STATUS" "NotFound"
        Write-SgStatusLine "AUTOSTART" "false"
        return
    }

    Write-SgStatusLine "STATUS" "Ok"

    $autostartEnabled = $false
    try {
        $task = Get-ScheduledTask -TaskName "Secret Grounds Cashier Stack" -ErrorAction SilentlyContinue
        $autostartEnabled = $null -ne $task
    } catch {
        $autostartEnabled = $false
    }
    Write-SgStatusLine "AUTOSTART" $(if ($autostartEnabled) { "true" } else { "false" })
} catch {
    Write-SgStatusLine "STATUS" "Unknown"
    Write-SgStatusLine "ERROR" $_.Exception.Message
}
