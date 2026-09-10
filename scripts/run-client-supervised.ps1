param(
    [switch]$AllowTestMode,
    # --- test seams (production leaves all of these at their defaults) ---
    # Run this PowerShell command as the "client" instead of the packaged
    # exe, so a smoke test can supervise a fake client that exits on demand.
    [string]$ClientCommandOverride = "",
    # Where the "stop, do not relaunch" sentinel lives (default: next to the
    # client log).
    [string]$StopSentinelPath = "",
    [int]$RelaunchDelaySeconds = 2,
    # 0 = supervise forever (production). A positive value stops the loop
    # after that many relaunches (tests only).
    [int]$MaxRelaunches = 0
)

# Keep the customer client alive. This script is the action of the
# "Secret Grounds Client" scheduled task: it launches the client, waits for
# it to exit, and relaunches it after a short delay -- so a customer who
# ends the client in Task Manager just lands back on the lock screen a
# couple of seconds later (PanCafe's pguard behaviour). The cashier server
# stays authoritative for session and lock state, and a relaunched client
# reconciles that on startup, so killing the client re-locks an idle PC and
# never buys extra time on an active session.
#
# The outer watchdog (a customer ending THIS supervisor process too) is the
# task's own 1-minute repetition trigger -- see install-client-autostart.ps1.

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

if ((Get-SgBool "SECRET_GROUNDS_TEST_MODE" $false) -and !$AllowTestMode) {
    throw "SECRET_GROUNDS_TEST_MODE=true is blocked for production client startup. Re-run with -AllowTestMode only for controlled tests."
}

$clientLog = Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log"
$resolvedClientLog = Resolve-SgPath $clientLog
New-Item -ItemType Directory -Force -Path (Split-Path $resolvedClientLog) | Out-Null

$stopSentinel = if ($StopSentinelPath) {
    $StopSentinelPath
} else {
    Join-Path (Split-Path $resolvedClientLog) "client-supervisor.stop"
}

function Test-StopRequested {
    if (Test-Path -LiteralPath $stopSentinel) {
        Remove-Item -LiteralPath $stopSentinel -Force -ErrorAction SilentlyContinue
        return $true
    }
    return $false
}

# --- resolve what to supervise -------------------------------------------
if ($ClientCommandOverride) {
    $targetFile = "powershell.exe"
    $targetArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $ClientCommandOverride)
    $targetWorkDir = $Script:SecretGroundsRoot
} else {
    $clientConfig = Join-Path $Script:SecretGroundsRoot "client\config.json"
    $pythonExe = Join-Path $Script:SecretGroundsRoot ".venv\Scripts\python.exe"
    $packagedClientExe = Join-Path $Script:SecretGroundsRoot "client\SecretGroundsClient.exe"

    if (!(Test-Path $packagedClientExe) -and !(Test-Path $pythonExe)) {
        throw "Secret Grounds client runtime is missing. Expected packaged client at $packagedClientExe or development Python at $pythonExe."
    }
    if (!(Test-Path $clientConfig)) {
        throw "Client config is missing: $clientConfig. Copy client\config.example.json to client\config.json and configure it."
    }

    $payload = Get-Content -LiteralPath $clientConfig -Raw | ConvertFrom-Json
    $pcNameValue = ([string]$payload.pc_name).Trim()
    # A blank pc_name and the "@hostname" sentinel are both valid automatic
    # identity modes (diskless images) -- only warn on the unconfigured
    # single-PC default.
    if ($pcNameValue -eq "PC-01") {
        Write-SgLine "Client pc_name is PC-01. Confirm this is the assigned customer PC before deploying to more stations." "WARN" $clientLog
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
        if (!$serverUri.IsAbsoluteUri -or $serverUri.Scheme -ne "http") { throw "bad" }
    } catch {
        throw "SECRET_GROUNDS_SERVER_URL must be an absolute http URL such as http://cashier-server:18001."
    }
    if ($serverUri.Port -eq 8000) {
        throw "Production client must not connect to development port 8000. Use the cashier server host with production port 18001."
    }

    $env:SECRET_GROUNDS_CLIENT_LOG = $resolvedClientLog
    $env:SECRET_GROUNDS_SERVER_URL = $serverUrl
    $env:PYTHONUNBUFFERED = "1"

    if (Test-Path $packagedClientExe) {
        $targetFile = $packagedClientExe
        $targetArgs = @()
        $targetWorkDir = (Join-Path $Script:SecretGroundsRoot "client")
    } else {
        $targetFile = $pythonExe
        $targetArgs = @("-u", "-m", "client.client")
        $targetWorkDir = $Script:SecretGroundsRoot
    }
}

# A stale sentinel from a previous "stop" must not kill this fresh start.
Remove-Item -LiteralPath $stopSentinel -Force -ErrorAction SilentlyContinue

Set-SgProcessRecord "client-supervisor" ([pscustomobject]@{
    Pid       = $PID
    Role      = "client-supervisor"
    Port      = ""
    StartedAt = (Get-Date).ToString("o")
    Identity  = $PSCommandPath
    LogPath   = $resolvedClientLog
    PcName    = ""
})
Write-SgLine "Secret Grounds client supervisor started (PID $PID); relaunch delay ${RelaunchDelaySeconds}s." "INFO" $clientLog

$relaunches = 0
try {
    while ($true) {
        if (Test-StopRequested) {
            Write-SgLine "Stop requested; client supervisor exiting without relaunch." "INFO" $clientLog
            break
        }

        $startArgs = @{
            FilePath         = $targetFile
            WorkingDirectory = $targetWorkDir
            WindowStyle      = "Hidden"
            PassThru         = $true
        }
        if ($targetArgs.Count -gt 0) { $startArgs["ArgumentList"] = $targetArgs }
        $proc = Start-Process @startArgs

        Set-SgProcessRecord "client" ([pscustomobject]@{
            Pid       = $proc.Id
            Role      = "client"
            Port      = ""
            StartedAt = (Get-Date).ToString("o")
            Identity  = $targetFile
            LogPath   = $resolvedClientLog
            PcName    = ""
        })
        Write-SgLine "Secret Grounds client process started (PID $($proc.Id))." "INFO" $clientLog

        $proc.WaitForExit()
        try { $exitCode = $proc.ExitCode } catch { $exitCode = "unknown" }
        Write-SgLine "Secret Grounds client process $($proc.Id) exited (code $exitCode)." "WARN" $clientLog

        if (Test-StopRequested) {
            Write-SgLine "Stop requested; client supervisor exiting without relaunch." "INFO" $clientLog
            break
        }

        $relaunches++
        if ($MaxRelaunches -gt 0 -and $relaunches -ge $MaxRelaunches) {
            Write-SgLine "Reached MaxRelaunches=$MaxRelaunches; client supervisor exiting (test mode)." "INFO" $clientLog
            break
        }
        Start-Sleep -Seconds $RelaunchDelaySeconds
    }
} finally {
    Remove-SgProcessRecord "client"
    Remove-SgProcessRecord "client-supervisor"
}
