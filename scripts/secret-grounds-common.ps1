$ErrorActionPreference = "Stop"

$Script:SecretGroundsRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Script:ConfigPath = Join-Path $Script:SecretGroundsRoot "config\.env"
$Script:PidPath = Join-Path $Script:SecretGroundsRoot "logs\secret-grounds-pids.json"
$Script:DefaultBackendPort = 18001
$Script:DefaultDashboardPort = 3001

function Get-SgVersion {
    $versionPath = Join-Path $Script:SecretGroundsRoot "VERSION"
    if (Test-Path $versionPath) {
        return ((Get-Content -LiteralPath $versionPath -Raw).Trim())
    }
    return "0.0.0"
}

function Write-SgLine {
    param(
        [string]$Message,
        [ValidateSet("INFO", "WARN", "ERROR")]
        [string]$Level = "INFO",
        [string]$LogPath = ""
    )
    $line = "{0} {1} {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Level, $Message
    Write-Host $line
    if ($LogPath) {
        $resolvedLog = Resolve-SgPath $LogPath
        New-Item -ItemType Directory -Force -Path (Split-Path $resolvedLog) | Out-Null
        Add-Content -LiteralPath $resolvedLog -Value $line
    }
}

function Resolve-SgPath {
    param([string]$PathValue)
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $PathValue
    }
    $expanded = [Environment]::ExpandEnvironmentVariables($PathValue)
    if ([System.IO.Path]::IsPathRooted($expanded)) {
        return [System.IO.Path]::GetFullPath($expanded)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $Script:SecretGroundsRoot $expanded))
}

function Import-SgEnv {
    param([switch]$RequireConfig)
    if (!(Test-Path $Script:ConfigPath)) {
        if ($RequireConfig) {
            throw "Missing configuration file: $Script:ConfigPath. Copy config\.env.example to config\.env and edit it first."
        }
        return
    }

    foreach ($line in Get-Content -LiteralPath $Script:ConfigPath) {
        $trimmed = $line.Trim()
        if (!$trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($name) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
}

function Get-SgEnv {
    param([string]$Name, [string]$Default = "")
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value
}

function Normalize-SgPathEnvironment {
    $processVars = [Environment]::GetEnvironmentVariables("Process")
    $processPath = ""
    $upperPath = ""
    foreach ($key in $processVars.Keys) {
        if ($key -ceq "Path") {
            $processPath = [string]$processVars[$key]
        } elseif ($key -ceq "PATH") {
            $upperPath = [string]$processVars[$key]
        }
    }

    $pathValue = @($processPath, $upperPath) | Where-Object { ![string]::IsNullOrWhiteSpace($_) } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($pathValue)) {
        $pathParts = @(
            [Environment]::GetEnvironmentVariable("Path", "Machine"),
            [Environment]::GetEnvironmentVariable("Path", "User")
        ) | Where-Object { ![string]::IsNullOrWhiteSpace($_) } | ForEach-Object { $_.Trim(";") } | Where-Object { $_ }
        $pathValue = [string]::Join(";", $pathParts)
    }

    if (![string]::IsNullOrWhiteSpace($pathValue)) {
        [Environment]::SetEnvironmentVariable("Path", $pathValue, "Process")
    }
}

function Get-SgCommandSource {
    param([string]$Name)
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -eq $command) {
        return ""
    }
    return [string]$command.Source
}

function Resolve-SgNodePath {
    Normalize-SgPathEnvironment

    $nodePath = Get-SgCommandSource "node"
    if ([string]::IsNullOrWhiteSpace($nodePath)) {
        $nodePath = Get-SgCommandSource "node.exe"
    }

    $npmPath = Get-SgCommandSource "npm.cmd"
    if ([string]::IsNullOrWhiteSpace($nodePath) -and ![string]::IsNullOrWhiteSpace($npmPath)) {
        $candidate = Join-Path (Split-Path $npmPath) "node.exe"
        if (Test-Path $candidate) {
            $nodePath = $candidate
        }
    }

    if (![string]::IsNullOrWhiteSpace($nodePath) -and (Test-Path $nodePath)) {
        return $nodePath
    }

    $pathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
    if ([string]::IsNullOrWhiteSpace($pathValue)) {
        $pathValue = [Environment]::GetEnvironmentVariable("PATH", "Process")
    }
    $resolvedNode = if ([string]::IsNullOrWhiteSpace($nodePath)) { "<not found>" } else { $nodePath }
    $resolvedNpm = if ([string]::IsNullOrWhiteSpace($npmPath)) { "<not found>" } else { $npmPath }
    throw "Node.js could not be resolved for dashboard startup.`nPATH: $pathValue`nresolved Node location: $resolvedNode`nresolved npm location: $resolvedNpm"
}

function Get-SgBool {
    param([string]$Name, [bool]$Default = $false)
    $value = Get-SgEnv $Name ""
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value.Trim().ToLowerInvariant() -in @("1", "true", "yes", "on")
}

function Test-SgPlaceholderValue {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $true
    }
    $normalized = $Value.Trim().ToLowerInvariant()
    return $normalized -like "replace-*" -or
        $normalized -like "*placeholder*" -or
        $normalized -like "*example*" -or
        $normalized -in @("changeme", "change-me", "secret", "password", "your-secret-here")
}

function Assert-SgDeploymentConfig {
    param([switch]$AllowTestMode)
    Assert-SgProductionMode -AllowTestMode:$AllowTestMode

    $secret = Get-SgEnv "SECRET_GROUNDS_DEPLOYMENT_SECRET" ""
    if (Test-SgPlaceholderValue $secret) {
        throw "SECRET_GROUNDS_DEPLOYMENT_SECRET must be set to a real private value in config\.env."
    }
    if ($secret.Length -lt 32) {
        throw "SECRET_GROUNDS_DEPLOYMENT_SECRET must be at least 32 characters."
    }

    $backendPort = [int](Get-SgEnv "SECRET_GROUNDS_PORT" $Script:DefaultBackendPort)
    $dashboardPort = [int](Get-SgEnv "SECRET_GROUNDS_DASHBOARD_PORT" $Script:DefaultDashboardPort)
    if ($backendPort -eq $dashboardPort) {
        throw "SECRET_GROUNDS_PORT and SECRET_GROUNDS_DASHBOARD_PORT must be different."
    }
    if ($dashboardPort -eq 3000) {
        throw "SECRET_GROUNDS_DASHBOARD_PORT=3000 conflicts with the development dashboard. Use a production port such as 3001."
    }

    $baseUrl = Get-SgEnv "NEXT_PUBLIC_FASTAPI_BASE_URL" ""
    if (Test-SgPlaceholderValue $baseUrl) {
        throw "NEXT_PUBLIC_FASTAPI_BASE_URL must point to the backend, for example http://127.0.0.1:$backendPort."
    }
    try {
        $uri = [System.Uri]$baseUrl
        if (!$uri.IsAbsoluteUri -or $uri.Scheme -notin @("http", "https")) {
            throw "bad"
        }
        if ((Get-SgEnv "SECRET_GROUNDS_APP_MODE" "production").ToLowerInvariant() -eq "production") {
            $expectedBaseUrl = "http://127.0.0.1:$backendPort"
            if ($baseUrl.TrimEnd("/") -ne $expectedBaseUrl) {
                throw "Production NEXT_PUBLIC_FASTAPI_BASE_URL must be $expectedBaseUrl. Do not mix localhost and 127.0.0.1."
            }
            $expectedOrigin = "http://127.0.0.1:$dashboardPort"
            $origins = @((Get-SgEnv "SECRET_GROUNDS_CORS_ORIGINS" "") -split "," | ForEach-Object { $_.Trim().TrimEnd("/") } | Where-Object { $_ })
            if ($origins.Count -ne 1 -or $origins[0] -ne $expectedOrigin) {
                throw "Production SECRET_GROUNDS_CORS_ORIGINS must be exactly $expectedOrigin. Do not mix localhost and 127.0.0.1."
            }
        }
    } catch {
        if ($_.Exception.Message -like "Production *") {
            throw
        }
        throw "NEXT_PUBLIC_FASTAPI_BASE_URL must be an absolute http or https URL."
    }
}

function Get-SgPidState {
    if (!(Test-Path $Script:PidPath)) {
        return [pscustomobject]@{}
    }
    try {
        return Get-Content -LiteralPath $Script:PidPath -Raw | ConvertFrom-Json
    } catch {
        return [pscustomobject]@{}
    }
}

function Save-SgPidState {
    param([object]$State)
    New-Item -ItemType Directory -Force -Path (Split-Path $Script:PidPath) | Out-Null
    $State | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $Script:PidPath -Encoding UTF8
}

function Get-SgProcessRecord {
    param([string]$Role)
    $state = Get-SgPidState
    if ($state.PSObject.Properties.Name -contains $Role) {
        return $state.$Role
    }
    return $null
}

function Set-SgProcessRecord {
    param([string]$Role, [object]$Record)
    $state = Get-SgPidState
    $hash = @{}
    foreach ($property in $state.PSObject.Properties) {
        $hash[$property.Name] = $property.Value
    }
    $hash[$Role] = $Record
    Save-SgPidState ([pscustomobject]$hash)
}

function Remove-SgProcessRecord {
    param([string]$Role)
    $state = Get-SgPidState
    $hash = @{}
    foreach ($property in $state.PSObject.Properties) {
        if ($property.Name -ne $Role) {
            $hash[$property.Name] = $property.Value
        }
    }
    Save-SgPidState ([pscustomobject]$hash)
}

function Test-SgPortAvailable {
    param([int]$Port, [string]$HostName = "127.0.0.1")
    try {
        $listeners = [System.Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
        if ($listeners | Where-Object { $_.Port -eq $Port }) {
            return $false
        }
    } catch {}

    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne(300)
        if ($connected) {
            $client.EndConnect($async)
            return $false
        }
        return $true
    } catch {
        return $true
    } finally {
        $client.Close()
    }
}

function Wait-SgHttpReady {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 45,
        [string]$LogPath = "",
        [System.Diagnostics.Process]$Process = $null,
        [string[]]$TailPaths = @()
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        if ($null -ne $Process) {
            $Process.Refresh()
            if ($Process.HasExited) {
                Write-SgLine "Readiness check stopped because PID $($Process.Id) exited with code $($Process.ExitCode)." "ERROR" $LogPath
                Show-SgLogTail -Paths $TailPaths
                return $false
            }
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        } catch {
            Start-Sleep -Milliseconds 750
        }
    } while ((Get-Date) -lt $deadline)
    Write-SgLine "Readiness check timed out for $Url" "ERROR" $LogPath
    Show-SgLogTail -Paths $TailPaths
    return $false
}

function ConvertTo-SgRedactedLine {
    param([string]$Line)
    return ($Line -replace "(SECRET_GROUNDS_DEPLOYMENT_SECRET=)[^\s]+", '$1[redacted]' `
                  -replace "(secret=)[^\s,;]+", '$1[redacted]' `
                  -replace "(token=)[^\s,;]+", '$1[redacted]' `
                  -replace "(password=)[^\s,;]+", '$1[redacted]')
}

function Show-SgLogTail {
    param(
        [string[]]$Paths,
        [int]$Lines = 30
    )
    foreach ($path in $Paths) {
        if ([string]::IsNullOrWhiteSpace($path)) {
            continue
        }
        $resolved = Resolve-SgPath $path
        if (!(Test-Path $resolved)) {
            continue
        }
        Write-Host "---- tail: $resolved ----"
        Get-Content -LiteralPath $resolved -Tail $Lines | ForEach-Object {
            Write-Host (ConvertTo-SgRedactedLine ([string]$_))
        }
    }
}

function Test-SgStartedProcess {
    param([object]$Record)
    if ($null -eq $Record -or !$Record.Pid) {
        return $false
    }
    $process = Get-Process -Id ([int]$Record.Pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $false
    }
    $identity = [string]$Record.Identity
    if ([string]::IsNullOrWhiteSpace($identity)) {
        return $true
    }

    $commandLine = ""
    try {
        $cim = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f [int]$Record.Pid)
        $commandLine = [string]$cim.CommandLine
    } catch {}
    if (![string]::IsNullOrWhiteSpace($commandLine) -and
        $commandLine.IndexOf($identity, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }

    try {
        $startedAt = [datetime]$Record.StartedAt
        if ($process.StartTime -ge $startedAt.AddSeconds(-2)) {
            return $true
        }
    } catch {}

    return $false
}

function ConvertTo-SgCmdArgument {
    param([string]$Value)
    return '"' + ($Value -replace '"', '""') + '"'
}

function Start-SgLoggedProcess {
    param(
        [string]$Role,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$LogPath,
        [string]$ErrorLogPath
    )
    $resolvedLog = Resolve-SgPath $LogPath
    $resolvedErrorLog = Resolve-SgPath $ErrorLogPath
    New-Item -ItemType Directory -Force -Path (Split-Path $resolvedLog), (Split-Path $resolvedErrorLog) | Out-Null

    if (([System.IO.Path]::GetFullPath($resolvedLog)) -eq ([System.IO.Path]::GetFullPath($resolvedErrorLog))) {
        throw "Output and error logs must be separate files for $Role."
    }

    Normalize-SgPathEnvironment

    $Script:LastSgLaunchIdentity = $FilePath
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -PassThru
    return $process
}

function Stop-SgStartedProcess {
    param([string]$Role, [int]$TimeoutSeconds = 12)
    $record = Get-SgProcessRecord $Role
    if (!(Test-SgStartedProcess $record)) {
        Remove-SgProcessRecord $Role
        return
    }

    $process = Get-Process -Id ([int]$record.Pid) -ErrorAction Stop
    Write-SgLine "Stopping $Role PID $($record.Pid)." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
    try {
        taskkill.exe /PID $process.Id /T /F | Out-Null
    } catch {}
    $process.Refresh()
    if (!$process.HasExited) {
        $process.CloseMainWindow() | Out-Null
        $process.WaitForExit($TimeoutSeconds * 1000)
    }
    if (!$process.HasExited) {
        Stop-Process -Id $process.Id -Force
    }
    Remove-SgProcessRecord $Role
}

function Assert-SgProductionMode {
    param([switch]$AllowTestMode)
    if ((Get-SgEnv "SECRET_GROUNDS_APP_MODE" "production").ToLowerInvariant() -ne "production") {
        throw "SECRET_GROUNDS_APP_MODE must be production for deployment startup."
    }
    if ((Get-SgBool "SECRET_GROUNDS_TEST_MODE" $false) -and !$AllowTestMode) {
        throw "SECRET_GROUNDS_TEST_MODE=true is blocked for production startup. Re-run with -AllowTestMode only for controlled tests."
    }
}
