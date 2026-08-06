param([int]$TimeoutSeconds = 10)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$serverLog = Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log"

# Uninstall must never leave the client connected, the backend/dashboard
# running, or the install directory locked. The PID-tracked graceful stop
# (Stop-SgStartedProcess) is the fast, precise path -- but a clean uninstall
# cannot rely on it alone: it does nothing for a role this install never had,
# and it does nothing if the tracked PID record is stale (e.g. the process
# was restarted by Task Scheduler since the record was written). Every role
# is covered here regardless of which role this specific install/uninstall
# actually is, since each Stop-SgStartedProcess call for a role that was
# never running is a harmless no-op.
Stop-SgStartedProcess "client"
Stop-SgStartedProcess "dashboard"
Stop-SgStartedProcess "backend"

# Defensive verification pass: find and force-stop ANY process whose
# executable is one of ours, by path, regardless of whether our own PID
# tracking knew about it. This is what actually guarantees "no Secret
# Grounds processes remain" -- trusting only the PID JSON file is not
# sufficient to promise a clean uninstall.
$installedExecutables = @(
    (Join-Path $Script:SecretGroundsRoot "client\SecretGroundsClient.exe"),
    (Join-Path $Script:SecretGroundsRoot "runtime\python\python.exe"),
    (Join-Path $Script:SecretGroundsRoot "runtime\node\node.exe")
) | ForEach-Object { $_.ToLowerInvariant() }

function Get-SgInstalledProcesses {
    Get-CimInstance Win32_Process -Filter "Name='SecretGroundsClient.exe' OR Name='python.exe' OR Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and ($installedExecutables -contains $_.ExecutablePath.ToLowerInvariant()) }
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
do {
    $remaining = @(Get-SgInstalledProcesses)
    if ($remaining.Count -eq 0) {
        break
    }
    foreach ($processInfo in $remaining) {
        try {
            Stop-Process -Id $processInfo.ProcessId -Force -ErrorAction Stop
        } catch {
            Write-SgLine "Could not stop PID $($processInfo.ProcessId) ($($processInfo.Name)): $($_.Exception.Message)" "WARN" $serverLog
        }
    }
    Start-Sleep -Milliseconds 300
} while ((Get-Date) -lt $deadline)

$stillRunning = @(Get-SgInstalledProcesses)
if ($stillRunning.Count -gt 0) {
    $blockers = ($stillRunning | ForEach-Object { "$($_.Name) (PID $($_.ProcessId))" }) -join ", "
    Write-SgLine "Uninstall pre-check timed out after $TimeoutSeconds seconds with processes still running: $blockers" "ERROR" $serverLog
    throw "Secret Grounds processes are still running and must be closed before uninstalling: $blockers"
}

Write-SgLine "All Secret Grounds processes confirmed stopped before uninstall." "INFO" $serverLog
