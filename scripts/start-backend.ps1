param([switch]$AllowTestMode)

. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig
Assert-SgDeploymentConfig -AllowTestMode:$AllowTestMode

$serverLog = Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log"
$serverConsoleLog = Get-SgEnv "SECRET_GROUNDS_SERVER_CONSOLE_LOG" ".\logs\server-console.log"
$serverErrorLog = Get-SgEnv "SECRET_GROUNDS_SERVER_ERROR_LOG" ".\logs\server-error.log"
$port = [int](Get-SgEnv "SECRET_GROUNDS_PORT" $Script:DefaultBackendPort)
$hostName = Get-SgEnv "SECRET_GROUNDS_BIND_HOST" "0.0.0.0"
$databasePath = Resolve-SgPath (Get-SgEnv "SECRET_GROUNDS_DB_PATH" ".\data\secret_grounds.db")
$backupPath = Resolve-SgPath (Get-SgEnv "SECRET_GROUNDS_BACKUP_DIR" ".\backups")

$pythonExe = Join-Path $Script:SecretGroundsRoot "runtime\python\python.exe"
$arguments = @("-m", "uvicorn", "server.main:app", "--host", $hostName, "--port", $port.ToString(), "--no-access-log")
$commandLine = (@($pythonExe) + $arguments | ForEach-Object { ConvertTo-SgCmdArgument $_ }) -join ' '
$capturedExitCode = $null

try {
    if (Test-SgStartedProcess (Get-SgProcessRecord "backend")) {
        throw "Secret Grounds backend is already running from deployment scripts."
    }
    if (!(Test-SgPortAvailable -Port $port)) {
        throw "Backend port $port is already in use (currently held by $(Get-SgPortOwnerDescription -Port $port))."
    }
    if (!(Test-Path $pythonExe)) {
        throw "Backend Python runtime is missing: $pythonExe. Run scripts\prepare-backend-runtime.ps1."
    }

    New-Item -ItemType Directory -Force -Path (Split-Path $databasePath), $backupPath, (Split-Path (Resolve-SgPath $serverLog)) | Out-Null
    $env:SECRET_GROUNDS_DB_PATH = $databasePath
    $env:SECRET_GROUNDS_BACKUP_DIR = $backupPath
    $env:SECRET_GROUNDS_SERVER_LOG = Resolve-SgPath $serverLog
    $env:SECRET_GROUNDS_PORT = $port.ToString()
    $env:SECRET_GROUNDS_BIND_HOST = $hostName

    Write-SgLine "Starting Secret Grounds backend $(Get-SgVersion) on $hostName`:$port with database $databasePath." "INFO" $serverLog
    Write-SgLine "Backend command line: $commandLine (working directory: $Script:SecretGroundsRoot)" "INFO" $serverLog
    $process = Start-SgLoggedProcess -Role "backend" -FilePath $pythonExe -Arguments $arguments -WorkingDirectory $Script:SecretGroundsRoot -LogPath $serverConsoleLog -ErrorLogPath $serverErrorLog

    Set-SgProcessRecord "backend" ([pscustomobject]@{
        Pid = $process.Id
        Role = "backend"
        Port = $port
        StartedAt = (Get-Date).ToString("o")
        Identity = $Script:LastSgLaunchIdentity
        LogPath = (Resolve-SgPath $serverLog)
    })

    if (!(Wait-SgHttpReady -Url "http://127.0.0.1:$port/healthz" -TimeoutSeconds 45 -LogPath $serverLog -Process $process -TailPaths @($serverLog, $serverConsoleLog, $serverErrorLog))) {
        $process.Refresh()
        $capturedExitCode = if ($process.HasExited) { $process.ExitCode } else { $null }
        Stop-SgStartedProcess "backend"
        Remove-SgProcessRecord "backend"
        throw "Backend did not become ready. See $(Resolve-SgPath $serverLog)."
    }

    Write-SgLine "Backend ready at http://127.0.0.1:$port/healthz." "INFO" $serverLog
} catch {
    Write-SgFailureDiagnostics -ErrorLogPath $serverErrorLog -Role "Backend" -Reason $_.Exception.Message -CommandLine $commandLine -WorkingDirectory $Script:SecretGroundsRoot -ExitCode $capturedExitCode -FallbackLogPath $serverLog
    throw
}
