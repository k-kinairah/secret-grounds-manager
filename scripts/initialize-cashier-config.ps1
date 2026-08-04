param(
    [string]$BindHost = "0.0.0.0",
    [int]$BackendPort = 18001,
    [string]$DashboardHost = "0.0.0.0",
    [int]$DashboardPort = 3001,
    [string]$DashboardApiUrl = "",
    [string]$CorsOrigins = "",
    [switch]$Force
)

. "$PSScriptRoot\secret-grounds-common.ps1"

if ($BackendPort -lt 1 -or $BackendPort -gt 65535) {
    throw "Backend port must be between 1 and 65535."
}
if ($DashboardPort -lt 1 -or $DashboardPort -gt 65535) {
    throw "Dashboard port must be between 1 and 65535."
}
if ($BackendPort -eq $DashboardPort) {
    throw "Backend and dashboard ports must be different."
}
if ($BackendPort -eq 8000 -or $DashboardPort -eq 3000) {
    throw "Installer production defaults must not use development ports 8000 or 3000."
}

if ((Test-Path $Script:ConfigPath) -and !$Force) {
    Import-SgEnv -RequireConfig
    Assert-SgDeploymentConfig
    Write-SgLine "Existing cashier config preserved at $Script:ConfigPath." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
    return
}

if ([string]::IsNullOrWhiteSpace($DashboardApiUrl)) {
    $DashboardApiUrl = "http://127.0.0.1:$BackendPort"
}
if ([string]::IsNullOrWhiteSpace($CorsOrigins)) {
    $CorsOrigins = "http://127.0.0.1:$DashboardPort"
}

$secretBytes = [byte[]]::new(32)
$secretRandomNumberGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try {
    $secretRandomNumberGenerator.GetBytes($secretBytes)
} finally {
    $secretRandomNumberGenerator.Dispose()
}
$secret = [Convert]::ToBase64String($secretBytes)

New-Item -ItemType Directory -Force -Path `
    (Join-Path $Script:SecretGroundsRoot "config"),
    (Join-Path $Script:SecretGroundsRoot "data"),
    (Join-Path $Script:SecretGroundsRoot "backups"),
    (Join-Path $Script:SecretGroundsRoot "logs") | Out-Null

$config = @"
SECRET_GROUNDS_APP_MODE=production
SECRET_GROUNDS_TEST_MODE=false
SECRET_GROUNDS_BIND_HOST=$BindHost
SECRET_GROUNDS_PORT=$BackendPort
SECRET_GROUNDS_DASHBOARD_HOST=$DashboardHost
SECRET_GROUNDS_DASHBOARD_PORT=$DashboardPort
SECRET_GROUNDS_OPEN_BROWSER=true
SECRET_GROUNDS_DB_PATH=.\data\secret_grounds.db
SECRET_GROUNDS_BACKUP_DIR=.\backups
SECRET_GROUNDS_SERVER_LOG=.\logs\server.log
SECRET_GROUNDS_SERVER_CONSOLE_LOG=.\logs\server-console.log
SECRET_GROUNDS_SERVER_ERROR_LOG=.\logs\server-error.log
SECRET_GROUNDS_DASHBOARD_LOG=.\logs\dashboard.log
SECRET_GROUNDS_DASHBOARD_CONSOLE_LOG=.\logs\dashboard-console.log
SECRET_GROUNDS_DASHBOARD_ERROR_LOG=.\logs\dashboard-error.log
SECRET_GROUNDS_CLIENT_LOG=.\logs\client.log
SECRET_GROUNDS_CLIENT_CONSOLE_LOG=.\logs\client-console.log
SECRET_GROUNDS_CLIENT_ERROR_LOG=.\logs\client-error.log
SECRET_GROUNDS_SERVER_URL=http://127.0.0.1:$BackendPort
SECRET_GROUNDS_LOG_MAX_BYTES=1048576
SECRET_GROUNDS_LOG_BACKUP_COUNT=5
SECRET_GROUNDS_SHOP_DISPLAY_NAME=Secret Grounds
SECRET_GROUNDS_SHOP_SUBTITLE=Computer Shop
SECRET_GROUNDS_TIMEZONE_DISPLAY=Asia/Manila
SECRET_GROUNDS_CORS_ORIGINS=$CorsOrigins
SECRET_GROUNDS_AUTH_SECURE_COOKIE=false
SECRET_GROUNDS_AUTH_SECONDS=28800
SECRET_GROUNDS_DEPLOYMENT_SECRET=$secret
NEXT_PUBLIC_FASTAPI_BASE_URL=$DashboardApiUrl
NEXT_PUBLIC_POLL_INTERVAL_MS=4000
"@

Set-Content -LiteralPath $Script:ConfigPath -Value $config -Encoding UTF8
Import-SgEnv -RequireConfig
Assert-SgDeploymentConfig
Write-SgLine "Cashier config initialized at $Script:ConfigPath with generated deployment secret." "INFO" (Get-SgEnv "SECRET_GROUNDS_SERVER_LOG" ".\logs\server.log")
