param([switch]$AllowTestMode)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv
if ((Get-SgBool "SECRET_GROUNDS_TEST_MODE" $false) -and !$AllowTestMode) {
    throw "SECRET_GROUNDS_TEST_MODE=true is blocked for production client startup. Re-run with -AllowTestMode only for controlled tests."
}

$clientLog = Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log"
$clientConsoleLog = Get-SgEnv "SECRET_GROUNDS_CLIENT_CONSOLE_LOG" ".\logs\client-console.log"
$clientErrorLog = Get-SgEnv "SECRET_GROUNDS_CLIENT_ERROR_LOG" ".\logs\client-error.log"
$clientConfig = Join-Path $Script:SecretGroundsRoot "client\config.json"
$pythonExe = Join-Path $Script:SecretGroundsRoot ".venv\Scripts\python.exe"
$packagedClientExe = Join-Path $Script:SecretGroundsRoot "client\SecretGroundsClient.exe"

if (!(Test-Path $packagedClientExe) -and !(Test-Path $pythonExe)) {
    throw "Secret Grounds client runtime is missing. Expected packaged client at $packagedClientExe or development Python at $pythonExe."
}
if (!(Test-Path $clientConfig)) {
    throw "Client config is missing: $clientConfig. Copy client\config.example.json to client\config.json and set a unique PC name."
}

$payload = Get-Content -LiteralPath $clientConfig -Raw | ConvertFrom-Json
if ([string]::IsNullOrWhiteSpace($payload.pc_name) -or $payload.pc_name -eq "PC-01") {
    if ([string]::IsNullOrWhiteSpace($payload.pc_name)) {
        throw "Client pc_name must be configured for this specific customer PC."
    }
    Write-SgLine "Client pc_name is PC-01. Confirm this is the assigned customer PC before installing on additional stations." "WARN" $clientLog
}
if ([string]::IsNullOrWhiteSpace($payload.server_host)) {
    throw "Client server_host is required."
}
$serverUrl = Get-SgEnv "SECRET_GROUNDS_SERVER_URL" ""
if ([string]::IsNullOrWhiteSpace($serverUrl)) {
    $serverUrl = "http://$($payload.server_host):$($payload.server_port)"
}
try {
    $serverUri = [System.Uri]$serverUrl
    if (!$serverUri.IsAbsoluteUri -or $serverUri.Scheme -ne "http") {
        throw "bad"
    }
} catch {
    throw "SECRET_GROUNDS_SERVER_URL must be an absolute http URL such as http://cashier-server:18001."
}
if ($serverUri.Port -eq 8000) {
    throw "Production client must not connect to development port 8000. Use the cashier server host with production port 18001."
}

if (Test-SgStartedProcess (Get-SgProcessRecord "client")) {
    throw "Secret Grounds client is already running from deployment scripts."
}

New-Item -ItemType Directory -Force -Path (Split-Path (Resolve-SgPath $clientLog)) | Out-Null
Write-SgLine "Starting Secret Grounds client $(Get-SgVersion) for $($payload.pc_name) against $serverUrl." "INFO" $clientLog

$env:SECRET_GROUNDS_CLIENT_LOG = Resolve-SgPath $clientLog
$env:SECRET_GROUNDS_SERVER_URL = $serverUrl
$env:PYTHONUNBUFFERED = "1"
if (Test-Path $packagedClientExe) {
    $process = Start-SgLoggedProcess -Role "client" -FilePath $packagedClientExe -Arguments @() -WorkingDirectory (Join-Path $Script:SecretGroundsRoot "client") -LogPath $clientConsoleLog -ErrorLogPath $clientErrorLog
} else {
    $process = Start-SgLoggedProcess -Role "client" -FilePath $pythonExe -Arguments @("-u", "-m", "client.client") -WorkingDirectory $Script:SecretGroundsRoot -LogPath $clientConsoleLog -ErrorLogPath $clientErrorLog
}

Set-SgProcessRecord "client" ([pscustomobject]@{
    Pid = $process.Id
    Role = "client"
    Port = ""
    StartedAt = (Get-Date).ToString("o")
    Identity = $Script:LastSgLaunchIdentity
    LogPath = (Resolve-SgPath $clientLog)
    PcName = $payload.pc_name
})

Write-SgLine "Secret Grounds client process started with PID $($process.Id)." "INFO" $clientLog
