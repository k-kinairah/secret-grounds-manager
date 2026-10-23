<#
.SYNOPSIS
  Start the whole assistant demo on 127.0.0.1 with fresh, fake shop data.

.DESCRIPTION
  1. Seeds a NEW demo shop database and handover-notes file under
     %TEMP%\sg-demo\run-<time>\ (assistant\demo\seed_demo.py).
  2. Starts, all on 127.0.0.1:
       backend      :8000  (on the demo database, with the assistant token)
       MCP server   :8765  (actions on)
       agent        :8781  (model settings from assistant\agent\.env)
       test client  PC-01  (TEST_MODE: power commands refused, no lock screen)
       simulated PCs PC-02..PC-15 (assistant\demo\fake_pcs.py; skip with -NoFakePcs)
       dashboard    :3000  (next dev, assistant page on)
  3. Records what it started in %TEMP%\sg-demo\demo-state.json, so
     stop-demo.ps1 stops exactly those processes and nothing else.

  It never touches the live shop: no 18001/3001, no data\ or backups\ folder,
  no installed client or supervisor. Logs go to the run folder.

  Run from anywhere:
    powershell -ExecutionPolicy Bypass -File assistant\demo\start-demo.ps1

  -Lan (off by default, for filming with a second PC): the backend and the
  dashboard listen on this PC's home-LAN address instead of 127.0.0.1, so
  another PC on the home network can open the assistant page. The MCP server
  and the agent stay on 127.0.0.1. Only private addresses (192.168.x.x,
  10.x.x.x, 172.16-31.x.x) are accepted; pass -LanIp if there is more than one.
  Everything is closed again by stop-demo.ps1. See "Filming with two PCs" in
  assistant\README.md for the other PC and the firewall.

  -ForcePopupFailure (off by default, to show an honest failure): PC-01's
  message/announcement pop-ups fail on purpose (the test client is in test
  mode, which this needs) and simulated PC-05's do too, so the assistant
  reports which PCs couldn't show it.
#>
param(
    [switch]$NoBrowser,
    [switch]$NoFakePcs,
    [switch]$Lan,
    [string]$LanIp,
    [switch]$ForcePopupFailure,
    # Password for Tyrone and Admin in the throwaway demo database only (never the shop's).
    [string]$DemoPassword = "sg-demo-2026"
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$DemoHome = Join-Path $env:TEMP "sg-demo"
$StateFile = Join-Path $DemoHome "demo-state.json"
$Ports = [ordered]@{ backend = 8000; mcp = 8765; agent = 8781; dashboard = 3000 }

$MainPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$McpPython = Join-Path $RepoRoot "assistant\mcp_server\.venv\Scripts\python.exe"
$AgentPython = Join-Path $RepoRoot "assistant\agent\.venv\Scripts\python.exe"
$Seed = Join-Path $RepoRoot "assistant\demo\seed_demo.py"
$FakePcs = Join-Path $RepoRoot "assistant\demo\fake_pcs.py"
$NextBin = Join-Path $RepoRoot "dashboard\node_modules\next\dist\bin\next"

function Fail([string]$message) {
    Write-Host "start-demo: $message" -ForegroundColor Red
    exit 1
}

function Test-PortInUse([int]$port, [string]$address = "127.0.0.1") {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $client.Connect($address, $port)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Wait-Http([string]$name, [string]$url, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $response = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -lt 500) { return }
        } catch [System.Net.WebException] {
            if ($_.Exception.Response -ne $null -and [int]$_.Exception.Response.StatusCode -lt 500) { return }
        } catch {
        }
        Start-Sleep -Milliseconds 700
    }
    throw "$name did not answer at $url within $seconds seconds (see the logs in $RunDir)"
}

function Wait-Port([string]$name, [int]$port, [int]$seconds) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-PortInUse $port) { return }
        Start-Sleep -Milliseconds 700
    }
    throw "$name did not open port $port within $seconds seconds (see the logs in $RunDir)"
}

# Start a process with extra environment variables, then put the parent's environment back.
function Start-DemoProcess([string]$name, [string]$file, [string[]]$arguments, [string]$workDir, [hashtable]$environment) {
    $saved = @{}
    foreach ($key in $environment.Keys) {
        $saved[$key] = [Environment]::GetEnvironmentVariable($key, "Process")
        [Environment]::SetEnvironmentVariable($key, $environment[$key], "Process")
    }
    try {
        $process = Start-Process -FilePath $file -ArgumentList $arguments -WorkingDirectory $workDir `
            -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $RunDir "$name.out.log") `
            -RedirectStandardError (Join-Path $RunDir "$name.err.log")
    } finally {
        foreach ($key in $saved.Keys) {
            [Environment]::SetEnvironmentVariable($key, $saved[$key], "Process")
        }
    }
    $script:Started += [pscustomobject]@{
        name       = $name
        pid        = $process.Id
        started_at = $process.StartTime.ToString("o")
    }
    Save-State
    Write-Host ("  started {0,-10} (pid {1})" -f $name, $process.Id)
    return $process
}

function Save-State {
    [pscustomobject]@{ run_dir = $RunDir; processes = $script:Started } |
        ConvertTo-Json -Depth 4 | Set-Content -Path $StateFile -Encoding UTF8
}

# --- checks before anything starts --------------------------------------------------------

foreach ($path in @($MainPython, $McpPython, $AgentPython, $Seed, $FakePcs, $NextBin)) {
    if (-not (Test-Path $path)) { Fail "missing $path (see the Setup section in assistant\README.md)" }
}
if (-not (Test-Path (Join-Path $RepoRoot "assistant\mcp_server\.env"))) {
    Fail "missing assistant\mcp_server\.env (it holds SG_ASSISTANT_TOKEN)"
}
if (-not (Test-Path (Join-Path $RepoRoot "assistant\agent\.env"))) {
    Fail "missing assistant\agent\.env (it holds the model provider settings)"
}
if (Test-Path $StateFile) {
    Fail "a demo already seems to be running ($StateFile exists). Run stop-demo.ps1 first."
}
function Test-PrivateIPv4([string]$address) {
    $ip = $null
    if (-not [System.Net.IPAddress]::TryParse($address, [ref]$ip)) { return $false }
    if ($ip.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { return $false }
    $b = $ip.GetAddressBytes()
    return ($b[0] -eq 10) -or ($b[0] -eq 192 -and $b[1] -eq 168) -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31)
}

# Backend and dashboard listen here: 127.0.0.1 normally, the home-LAN address with -Lan.
$WebHost = "127.0.0.1"
if ($Lan) {
    if (-not $LanIp) {
        $found = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { (Test-PrivateIPv4 $_.IPAddress) -and $_.PrefixOrigin -ne "WellKnown" } |
            Select-Object -ExpandProperty IPAddress -Unique)
        if ($found.Count -ne 1) { Fail "-Lan: found $($found.Count) private addresses ($($found -join ', ')); pass -LanIp <address>" }
        $LanIp = $found[0]
    }
    if (-not (Test-PrivateIPv4 $LanIp)) { Fail "-Lan: $LanIp is not a private home-LAN address" }
    $WebHost = $LanIp
} elseif ($LanIp) {
    Fail "-LanIp only works together with -Lan"
}
$BackendUrl = "http://$($WebHost):$($Ports.backend)"
$DashboardUrl = "http://$($WebHost):$($Ports.dashboard)"

foreach ($name in $Ports.Keys) {
    if (Test-PortInUse $Ports[$name]) { Fail "port $($Ports[$name]) ($name) is already in use on 127.0.0.1" }
    if ($Lan -and (Test-PortInUse $Ports[$name] $WebHost)) { Fail "port $($Ports[$name]) ($name) is already in use on $WebHost" }
}

$tokenLine = Select-String -Path (Join-Path $RepoRoot "assistant\mcp_server\.env") -Pattern '^SG_ASSISTANT_TOKEN=(.+)$' |
    Select-Object -First 1
if ($tokenLine -eq $null) { Fail "SG_ASSISTANT_TOKEN is empty in assistant\mcp_server\.env" }
$AssistantToken = $tokenLine.Matches[0].Groups[1].Value.Trim()

# --- fresh demo data ------------------------------------------------------------------------

New-Item -ItemType Directory -Force -Path $DemoHome | Out-Null
$RunDir = Join-Path $DemoHome ("run-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Path $RunDir | Out-Null
$ShopDb = Join-Path $RunDir "shop.sqlite3"
$NotesDb = Join-Path $RunDir "handover.sqlite3"
$script:Started = @()

function Invoke-Seed([string]$name, [string]$python, [string[]]$arguments) {
    $env:PYTHONIOENCODING = "utf-8"
    $out = Join-Path $RunDir "$name.out.log"
    $err = Join-Path $RunDir "$name.err.log"
    $process = Start-Process -FilePath $python -ArgumentList (@("`"$Seed`"") + $arguments) -WorkingDirectory $RepoRoot `
        -NoNewWindow -Wait -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    if ($process.ExitCode -ne 0) { Fail "$name failed; see $err" }
    Get-Content -Encoding UTF8 $out | Where-Object { $_ -notmatch '^(Sending client command|Command WebSocket|Client )' } |
        ForEach-Object { Write-Host "  $_" }
}

Write-Host "Seeding demo data in $RunDir ..."
# The password operators (Tyrone, Admin) get a demo-only password in the new demo
# database, instead of the backend's well-known built-in default.
$savedAdminPassword = $env:SECRET_GROUNDS_ADMIN_PASSWORD
$env:SECRET_GROUNDS_ADMIN_PASSWORD = $DemoPassword
try {
    Invoke-Seed "seed-shop" $MainPython @("--db", "`"$ShopDb`"")
} finally {
    $env:SECRET_GROUNDS_ADMIN_PASSWORD = $savedAdminPassword
}
Invoke-Seed "seed-notes" $AgentPython @("--notes-db", "`"$NotesDb`"")

# --- start everything -------------------------------------------------------------------------

Write-Host "Starting services ..."
try {
    Start-DemoProcess "backend" $MainPython @("-m", "uvicorn", "server.main:app", "--host", $WebHost, "--port", "$($Ports.backend)") $RepoRoot @{
        SECRET_GROUNDS_DB_PATH      = $ShopDb
        SECRET_GROUNDS_BACKUP_DIR   = (Join-Path $RunDir "backups")
        SECRET_GROUNDS_SERVER_LOG   = (Join-Path $RunDir "backend.log")
        SECRET_GROUNDS_CORS_ORIGINS = $DashboardUrl
        SG_ASSISTANT_TOKEN          = $AssistantToken
        SG_ASSISTANT_USERNAME       = "assistant"
    } | Out-Null
    Wait-Http "backend" "$BackendUrl/healthz" 60

    Start-DemoProcess "mcp" $McpPython @("-m", "sg_assistant.server") (Join-Path $RepoRoot "assistant\mcp_server") @{
        SG_BACKEND_URL       = $BackendUrl
        SG_MCP_PORT          = "$($Ports.mcp)"
        SG_ASSISTANT_ACTIONS = "true"
    } | Out-Null
    Wait-Port "MCP server" $Ports.mcp 60

    Start-DemoProcess "agent" $AgentPython @("-m", "sg_agent.app") (Join-Path $RepoRoot "assistant\agent") @{
        SG_AGENT_PORT         = "$($Ports.agent)"
        SG_MCP_URL            = "http://127.0.0.1:$($Ports.mcp)/mcp"
        SG_AGENT_NOTES_DB     = $NotesDb
        SG_AGENT_CORS_ORIGINS = "http://127.0.0.1:$($Ports.dashboard)"
    } | Out-Null
    Wait-Http "agent" "http://127.0.0.1:$($Ports.agent)/health" 90

    Start-DemoProcess "client" $MainPython @("-m", "client.client") $RepoRoot @{
        SECRET_GROUNDS_SERVER_URL               = $BackendUrl
        SECRET_GROUNDS_TEST_MODE                = "true"
        ENABLE_LOCK_SCREEN                      = "false"
        SECRET_GROUNDS_RECOVERY_PATH            = (Join-Path $RunDir "client-recovery.json")
        SECRET_GROUNDS_CLIENT_LOG               = (Join-Path $RunDir "client.log")
        SECRET_GROUNDS_REMINDER_VOICE_ENABLED   = "false"
        SECRET_GROUNDS_EXPIRATION_SOUND_ENABLED = "false"
        SECRET_GROUNDS_TEST_POPUP_FAILURE       = $(if ($ForcePopupFailure) { "true" } else { "false" })
    } | Out-Null

    if (-not $NoFakePcs) {
        # PC-02..PC-15 as simulated PCs: online on the grid, honest "(simulated PC)" replies.
        $fakeArgs = @("`"$FakePcs`"", "--server", $BackendUrl)
        if ($ForcePopupFailure) { $fakeArgs += @("--fail-popups", "PC-05") }
        Start-DemoProcess "fake-pcs" $MainPython $fakeArgs $RepoRoot @{
            PYTHONIOENCODING = "utf-8"
        } | Out-Null
    }

    Start-DemoProcess "dashboard" "node" @("`"$NextBin`"", "dev", "-H", $WebHost, "-p", "$($Ports.dashboard)") (Join-Path $RepoRoot "dashboard") @{
        NEXT_PUBLIC_ASSISTANT_ENABLED = "true"
        NEXT_PUBLIC_FASTAPI_BASE_URL  = $BackendUrl
        ASSISTANT_BACKEND_URL         = $BackendUrl
        ASSISTANT_AGENT_URL           = "http://127.0.0.1:$($Ports.agent)"
    } | Out-Null
    Wait-Http "dashboard" "$DashboardUrl/login" 180
} catch {
    Write-Host "start-demo: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Stopping what was started ..."
    & (Join-Path $PSScriptRoot "stop-demo.ps1")
    exit 1
}

$url = "$DashboardUrl/assistant"
Write-Host ""
Write-Host "Demo is running." -ForegroundColor Green
Write-Host "  Open $url and sign in as Tyrone (demo password: $DemoPassword; Tyrone is on duty)."
Write-Host "  PC-01 is this laptop's test client; messages pop up here."
if (-not $NoFakePcs) { Write-Host "  PC-02..PC-15 are simulated PCs (they reply honestly; nothing real happens)." }
if ($ForcePopupFailure) { Write-Host "  -ForcePopupFailure: pop-ups on PC-01 and PC-05 fail on purpose." -ForegroundColor Yellow }
if ($Lan) {
    Write-Host "  LAN mode: backend and dashboard are reachable on $WebHost from the home network." -ForegroundColor Yellow
    Write-Host "  The mic doesn't work on this PC's page in LAN mode; speak on the other PC. In PowerShell there, run:"
    Write-Host "    & `"C:\Program Files\Google\Chrome\Application\chrome.exe`" --user-data-dir=`"`$env:TEMP\sg-demo-chrome`" --unsafely-treat-insecure-origin-as-secure=$DashboardUrl $url"
}
Write-Host "  Logs: $RunDir"
Write-Host "  Stop: powershell -ExecutionPolicy Bypass -File assistant\demo\stop-demo.ps1"
if (-not $NoBrowser) { Start-Process $url }
