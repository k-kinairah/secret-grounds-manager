$ErrorActionPreference = "Stop"

$Script:SgClientConfigRequiredFields = @("server_host", "server_port", "pc_name", "display_name")

function Test-SgStationIdFormat {
    param([string]$Value)
    return $Value -match '^[A-Za-z0-9_-]+$'
}

function ConvertTo-SgClientConfigObject {
    param([Parameter(Mandatory = $true)][object]$Payload)

    foreach ($field in $Script:SgClientConfigRequiredFields) {
        if (-not ($Payload.PSObject.Properties.Name -contains $field)) {
            throw "config.json is missing required field '$field'."
        }
    }

    $serverHost = [string]$Payload.server_host
    if ([string]::IsNullOrWhiteSpace($serverHost)) {
        throw "Server host is required."
    }
    $serverHost = $serverHost.Trim()

    $serverPort = 0
    if (-not [int]::TryParse([string]$Payload.server_port, [ref]$serverPort)) {
        throw "Server port must be a number."
    }
    if ($serverPort -lt 1 -or $serverPort -gt 65535) {
        throw "Server port must be between 1 and 65535."
    }

    # A blank pc_name is the automatic/diskless-mode sentinel: the station
    # identity is resolved at runtime from the wired NIC MAC via
    # /stations/resolve, so it is intentionally absent here. A non-blank
    # pc_name still has to be well-formed.
    $pcName = ([string]$Payload.pc_name).Trim()
    if ($pcName -and -not (Test-SgStationIdFormat $pcName)) {
        throw "Station ID may contain only letters, numbers, dash, and underscore."
    }

    # display_name likewise falls back to blank in automatic mode.
    $displayName = ([string]$Payload.display_name).Trim()
    if ($displayName) {
        $displayName = ($displayName.Split([char[]]" `t`r`n", [System.StringSplitOptions]::RemoveEmptyEntries) -join " ")
    }

    $autoConnect = $true
    if ($Payload.PSObject.Properties.Name -contains "auto_connect") {
        $autoConnect = [bool]$Payload.auto_connect
    }

    $reconnectSeconds = 5
    if ($Payload.PSObject.Properties.Name -contains "reconnect_interval_seconds") {
        if (-not [int]::TryParse([string]$Payload.reconnect_interval_seconds, [ref]$reconnectSeconds)) {
            throw "reconnect_interval_seconds must be a number."
        }
    }
    if ($reconnectSeconds -lt 2 -or $reconnectSeconds -gt 300) {
        throw "reconnect_interval_seconds must be between 2 and 300."
    }

    $timeoutSeconds = 5
    if ($Payload.PSObject.Properties.Name -contains "request_timeout_seconds") {
        if (-not [int]::TryParse([string]$Payload.request_timeout_seconds, [ref]$timeoutSeconds)) {
            throw "request_timeout_seconds must be a number."
        }
    }
    if ($timeoutSeconds -lt 2 -or $timeoutSeconds -gt 60) {
        throw "request_timeout_seconds must be between 2 and 60."
    }

    return [ordered]@{
        server_host                = $serverHost
        server_port                = $serverPort
        pc_name                    = $pcName
        display_name               = $displayName
        auto_connect               = $autoConnect
        reconnect_interval_seconds = $reconnectSeconds
        request_timeout_seconds    = $timeoutSeconds
    }
}

function Get-SgClientConfigStatus {
    param([Parameter(Mandatory = $true)][string]$ConfigPath)

    if (!(Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        return [pscustomobject]@{ Status = "NotFound"; Config = $null; Error = "" }
    }

    try {
        $raw = Get-Content -LiteralPath $ConfigPath -Raw -ErrorAction Stop
        $payload = $raw | ConvertFrom-Json -ErrorAction Stop
        $config = ConvertTo-SgClientConfigObject -Payload $payload
        return [pscustomobject]@{ Status = "Ok"; Config = $config; Error = "" }
    } catch {
        return [pscustomobject]@{ Status = "Invalid"; Config = $null; Error = $_.Exception.Message }
    }
}

function Test-SgClientConfigEqual {
    param([Parameter(Mandatory = $true)][object]$A, [Parameter(Mandatory = $true)][object]$B)

    foreach ($field in @("server_host", "server_port", "pc_name", "display_name", "auto_connect", "reconnect_interval_seconds", "request_timeout_seconds")) {
        $left = if ($A -is [System.Collections.IDictionary]) { $A[$field] } else { $A.$field }
        $right = if ($B -is [System.Collections.IDictionary]) { $B[$field] } else { $B.$field }
        if ([string]$left -ne [string]$right) {
            return $false
        }
    }
    return $true
}

function Backup-SgClientConfig {
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][string]$BackupDir
    )

    if (!(Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        throw "Cannot back up client config; no file exists at $ConfigPath."
    }

    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backupPath = Join-Path $BackupDir "config-$timestamp.json"
    if (Test-Path -LiteralPath $backupPath) {
        $backupPath = Join-Path $BackupDir "config-$timestamp-$([guid]::NewGuid().ToString('N').Substring(0,6)).json"
    }
    Copy-Item -LiteralPath $ConfigPath -Destination $backupPath -Force
    return $backupPath
}

function Write-SgClientConfigAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [Parameter(Mandatory = $true)][System.Collections.Specialized.OrderedDictionary]$Config
    )

    $targetDir = Split-Path -Parent $ConfigPath
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

    $tempPath = Join-Path $targetDir (".config-$([guid]::NewGuid().ToString('N')).tmp")
    $json = $Config | ConvertTo-Json -Depth 3
    try {
        Set-Content -LiteralPath $tempPath -Value $json -Encoding UTF8 -ErrorAction Stop
        Move-Item -LiteralPath $tempPath -Destination $ConfigPath -Force -ErrorAction Stop
    } finally {
        if (Test-Path -LiteralPath $tempPath) {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
        }
    }

    if (!(Test-Path -LiteralPath $ConfigPath)) {
        throw "Client config write did not create $ConfigPath."
    }
    $writtenItem = Get-Item -LiteralPath $ConfigPath -ErrorAction Stop
    if ($writtenItem.PSIsContainer) {
        throw "Client config path is a directory, not a file: $ConfigPath."
    }
}
