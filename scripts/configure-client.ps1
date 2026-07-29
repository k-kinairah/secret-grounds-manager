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
    [Alias("ConfigPath")]
    [string]$ClientConfigPath = "",
    [string]$ExpectedVersion = "",
    [switch]$AllowOffline
)

. "$PSScriptRoot\secret-grounds-common.ps1"

function Assert-ClientValue {
    param([bool]$Condition, [string]$Message)
    if (!$Condition) {
        throw $Message
    }
}

$ServerHost = $ServerHost.Trim()
$StationId = $StationId.Trim()
$DisplayName = ($DisplayName.Trim().Split([char[]]" `t`r`n", [System.StringSplitOptions]::RemoveEmptyEntries) -join " ")

Assert-ClientValue (![string]::IsNullOrWhiteSpace($ServerHost)) "Server host is required."
Assert-ClientValue ($ServerPort -ge 1 -and $ServerPort -le 65535) "Server port must be between 1 and 65535."
Assert-ClientValue (![string]::IsNullOrWhiteSpace($StationId)) "Station ID is required."
Assert-ClientValue ($StationId -match '^[A-Za-z0-9_-]+$') "Station ID may contain only letters, numbers, dash, and underscore."
Assert-ClientValue (![string]::IsNullOrWhiteSpace($DisplayName)) "Display name is required."
Assert-ClientValue ($ReconnectIntervalSeconds -ge 2 -and $ReconnectIntervalSeconds -le 300) "Reconnect interval must be between 2 and 300 seconds."
Assert-ClientValue ($RequestTimeoutSeconds -ge 2 -and $RequestTimeoutSeconds -le 60) "Request timeout must be between 2 and 60 seconds."

$baseUrl = "http://${ServerHost}:$ServerPort"
if (!$AllowOffline) {
    $healthUrl = "$baseUrl/healthz"
    try {
        $health = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec $RequestTimeoutSeconds
        $payload = $health.Content | ConvertFrom-Json
    } catch {
        throw "Secret Grounds server is not reachable at $healthUrl. Check the cashier LAN address, port, and Private-network firewall rule. $($_.Exception.Message)"
    }

    Assert-ClientValue ($health.StatusCode -eq 200) "Secret Grounds server returned HTTP $($health.StatusCode) from /healthz."
    Assert-ClientValue ($payload.status -eq "ok") "Secret Grounds /healthz did not return status=ok."

    $expected = if ($ExpectedVersion) { $ExpectedVersion } else { Get-SgVersion }
    Assert-ClientValue ($payload.version -eq $expected) "Secret Grounds version mismatch. Server is $($payload.version); client package expects $expected."

    $macAddress = ""
    try {
        $macAddress = (& "$Script:SecretGroundsRoot\.venv\Scripts\python.exe" -c "from client.utils import get_local_mac_address; print(get_local_mac_address() or '')").Trim()
    } catch {}

    $checkUrl = "$baseUrl/client-registration/check?pc_name=$([uri]::EscapeDataString($StationId))"
    if ($macAddress) {
        $checkUrl += "&mac_address=$([uri]::EscapeDataString($macAddress))"
    }
    try {
        $availability = Invoke-WebRequest -Uri $checkUrl -UseBasicParsing -TimeoutSec $RequestTimeoutSeconds
        $availabilityPayload = $availability.Content | ConvertFrom-Json
        Assert-ClientValue ([bool]$availabilityPayload.available) ([string]$availabilityPayload.detail)
    } catch {
        throw "Station ID validation failed for '$StationId'. $($_.Exception.Message)"
    }
} else {
    Write-SgLine "Offline client configuration was allowed. This station will not connect until the cashier server is reachable." "WARN" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
}

$resolvedConfigPath = if ($ClientConfigPath) {
    Resolve-SgPath $ClientConfigPath
} else {
    Join-Path $Script:SecretGroundsRoot "client\config.json"
}

$config = [ordered]@{
    server_host = $ServerHost
    server_port = $ServerPort
    pc_name = $StationId
    display_name = $DisplayName
    auto_connect = $true
    reconnect_interval_seconds = $ReconnectIntervalSeconds
    request_timeout_seconds = $RequestTimeoutSeconds
}

New-Item -ItemType Directory -Force -Path (Split-Path $resolvedConfigPath) | Out-Null
$configJson = $config | ConvertTo-Json -Depth 3
Set-Content -LiteralPath $resolvedConfigPath -Value $configJson -Encoding UTF8 -ErrorAction Stop
if (!(Test-Path $resolvedConfigPath)) {
    throw "Client config write did not create $resolvedConfigPath."
}
$writtenItem = Get-Item -LiteralPath $resolvedConfigPath -ErrorAction Stop
if ($writtenItem.PSIsContainer) {
    throw "Client config path is a directory, not a file: $resolvedConfigPath."
}

Write-SgLine "Client config saved at $resolvedConfigPath for station '$StationId' ($DisplayName) targeting $baseUrl." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
