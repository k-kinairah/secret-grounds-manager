param(
    [ValidateSet("Fresh", "Preserve", "Update")]
    [string]$Mode = "Fresh",
    [string]$ServerHost = "",
    [int]$ServerPort = 18001,
    [string]$StationId = "",
    [string]$DisplayName = "",
    [int]$ReconnectIntervalSeconds = 5,
    [int]$RequestTimeoutSeconds = 5,
    [Alias("ConfigPath")]
    [string]$ClientConfigPath = "",
    [string]$ExpectedVersion = "",
    [switch]$AllowOffline,
    # Diskless / automatic master-image config: no Station ID is stored;
    # each cloned PC resolves its own identity from its wired NIC MAC.
    [switch]$Diskless,
    # Diskless variant: instead of a blank pc_name (dashboard MAC
    # assignment), write the "@hostname" sentinel so each cloned PC adopts
    # the per-seat Windows computer name the diskless system assigns and the
    # cashier auto-binds it to a matching pre-configured station. Implies
    # -Diskless for validation purposes (no Station ID is entered).
    [switch]$AdoptHostname
)

. "$PSScriptRoot\secret-grounds-common.ps1"
. "$PSScriptRoot\client-config-tool.ps1"

function Assert-ClientValue {
    param([bool]$Condition, [string]$Message)
    if (!$Condition) {
        throw $Message
    }
}

$logPath = Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log"

$resolvedConfigPath = if ($ClientConfigPath) {
    Resolve-SgPath $ClientConfigPath
} else {
    Join-Path $Script:SecretGroundsRoot "client\config.json"
}
$backupDir = Join-Path (Split-Path -Parent $resolvedConfigPath) "config-backups"

try {
    $existingStatus = Get-SgClientConfigStatus -ConfigPath $resolvedConfigPath

    if ($Mode -eq "Preserve") {
        if ($existingStatus.Status -eq "NotFound") {
            throw "Preserve mode requires an existing client configuration at $resolvedConfigPath, but none was found."
        }
        if ($existingStatus.Status -eq "Invalid") {
            $backupPath = Backup-SgClientConfig -ConfigPath $resolvedConfigPath -BackupDir $backupDir
            Write-SgLine "Existing client configuration at $resolvedConfigPath is invalid and was left untouched. Backup saved to $backupPath." "ERROR" $logPath
            throw "Existing client configuration at $resolvedConfigPath is invalid: $($existingStatus.Error) A backup was saved to $backupPath. Repair or remove the file, then re-run setup."
        }

        Write-SgLine "Preserved existing client configuration at $resolvedConfigPath for station '$($existingStatus.Config.pc_name)'." "INFO" $logPath
        exit 0
    }

    $ServerHost = $ServerHost.Trim()
    $StationId = $StationId.Trim()
    $DisplayName = ($DisplayName.Trim().Split([char[]]" `t`r`n", [System.StringSplitOptions]::RemoveEmptyEntries) -join " ")

    if ($AdoptHostname) {
        # Diskless master image, hostname-adopt variant: the sentinel
        # "@hostname" tells the packaged client to report its Windows
        # computer name at /stations/resolve so the cashier can auto-bind
        # it to a matching pre-configured station.
        $Diskless = $true
        $StationId = "@hostname"
        $DisplayName = ""
    }
    elseif ($Diskless) {
        # The generated config is a diskless master image: pc_name and
        # display_name are intentionally blank so the packaged client falls
        # into its existing automatic/MAC station-resolution flow on every
        # cloned PC.
        $StationId = ""
        $DisplayName = ""
    }

    Assert-ClientValue (![string]::IsNullOrWhiteSpace($ServerHost)) "Server host is required."
    Assert-ClientValue ($ServerPort -ge 1 -and $ServerPort -le 65535) "Server port must be between 1 and 65535."
    if (-not $Diskless) {
        Assert-ClientValue (![string]::IsNullOrWhiteSpace($StationId)) "Station ID is required."
        Assert-ClientValue ($StationId -match '^[A-Za-z0-9_-]+$') "Station ID may contain only letters, numbers, dash, and underscore."
        Assert-ClientValue (![string]::IsNullOrWhiteSpace($DisplayName)) "Display name is required."
    }

    if (-not $PSBoundParameters.ContainsKey("ReconnectIntervalSeconds") -and $existingStatus.Status -eq "Ok") {
        $ReconnectIntervalSeconds = $existingStatus.Config.reconnect_interval_seconds
    }
    if (-not $PSBoundParameters.ContainsKey("RequestTimeoutSeconds") -and $existingStatus.Status -eq "Ok") {
        $RequestTimeoutSeconds = $existingStatus.Config.request_timeout_seconds
    }
    Assert-ClientValue ($ReconnectIntervalSeconds -ge 2 -and $ReconnectIntervalSeconds -le 300) "Reconnect interval must be between 2 and 300 seconds."
    Assert-ClientValue ($RequestTimeoutSeconds -ge 2 -and $RequestTimeoutSeconds -le 60) "Request timeout must be between 2 and 60 seconds."

    if ($Mode -eq "Update" -and $existingStatus.Status -eq "Invalid") {
        $backupPath = Backup-SgClientConfig -ConfigPath $resolvedConfigPath -BackupDir $backupDir
        Write-SgLine "Existing client configuration at $resolvedConfigPath is invalid and was left untouched. Backup saved to $backupPath." "ERROR" $logPath
        throw "Existing client configuration at $resolvedConfigPath is invalid: $($existingStatus.Error) A backup was saved to $backupPath. Re-run with corrected values to replace it."
    }

    $newConfig = [ordered]@{
        server_host                = $ServerHost
        server_port                = $ServerPort
        pc_name                    = $StationId
        display_name               = $DisplayName
        auto_connect                = $true
        reconnect_interval_seconds = $ReconnectIntervalSeconds
        request_timeout_seconds    = $RequestTimeoutSeconds
    }

    if ($existingStatus.Status -eq "Ok" -and (Test-SgClientConfigEqual -A $existingStatus.Config -B $newConfig)) {
        Write-SgLine "Requested client configuration matches the existing configuration at $resolvedConfigPath; no changes made." "INFO" $logPath
        exit 0
    }

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

        if (-not $Diskless) {
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
        }
    } elseif ($Diskless) {
        Write-SgLine "Offline diskless image configuration was allowed. Cloned PCs will connect once they can reach the cashier server." "WARN" $logPath
    } else {
        Write-SgLine "Offline client configuration was allowed. This station will not connect until the cashier server is reachable." "WARN" $logPath
    }

    if ($existingStatus.Status -ne "NotFound") {
        $backupPath = Backup-SgClientConfig -ConfigPath $resolvedConfigPath -BackupDir $backupDir
        Write-SgLine "Backed up previous client configuration to $backupPath before applying changes." "INFO" $logPath
    }

    Write-SgClientConfigAtomic -ConfigPath $resolvedConfigPath -Config $newConfig

    $stationLabel = if ($AdoptHostname) { "automatic (diskless image, adopt computer name)" } elseif ($Diskless) { "automatic (diskless image)" } else { "$StationId ($DisplayName)" }
    Write-SgLine "Client config saved at $resolvedConfigPath for station '$stationLabel' targeting $baseUrl." "INFO" $logPath
    exit 0
} catch {
    Write-SgLine "Client configuration update failed: $($_.Exception.Message)" "ERROR" $logPath
    Write-Error $_.Exception.Message
    exit 1
}
