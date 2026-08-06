. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

$dashboardLog = Get-SgEnv "SECRET_GROUNDS_DASHBOARD_LOG" ".\logs\dashboard.log"
$apiUrl = Get-SgEnv "NEXT_PUBLIC_FASTAPI_BASE_URL" ""
if ([string]::IsNullOrWhiteSpace($apiUrl)) {
    throw "NEXT_PUBLIC_FASTAPI_BASE_URL must be set before building the production dashboard."
}

New-Item -ItemType Directory -Force -Path (Split-Path (Resolve-SgPath $dashboardLog)) | Out-Null
Write-SgLine "Building Secret Grounds dashboard $(Get-SgVersion) with API URL $apiUrl." "INFO" $dashboardLog

$dashboardRoot = Join-Path $Script:SecretGroundsRoot "dashboard"
$resolvedDashboardLog = Resolve-SgPath $dashboardLog

# `*> file` pipes npm's stderr through PowerShell's error-record machinery. With
# $ErrorActionPreference = "Stop" (set globally in secret-grounds-common.ps1),
# any benign line npm writes to stderr (e.g. an npm update notice) becomes a
# terminating error even on a successful build. Redirect at the cmd.exe level
# instead, the same way Start-SgLoggedProcess avoids this for the backend and
# dashboard's own runtime processes.
$cmdParams = '/C npm.cmd run build > "' + $resolvedDashboardLog + '" 2>&1'
$buildProcess = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdParams -WorkingDirectory $dashboardRoot -WindowStyle Hidden -Wait -PassThru
if ($buildProcess.ExitCode -ne 0) {
    throw "Dashboard build failed. See $resolvedDashboardLog."
}

# `output: "standalone"` (dashboard\next.config.ts) traces only the
# dependencies server.js actually needs into .next\standalone, but does not
# copy public\ or .next\static itself -- both must be placed next to the
# generated server.js for the cashier installer to ship a runnable,
# self-contained payload (see scripts\start-dashboard.ps1 and
# scripts\prepare-dashboard-runtime.ps1, which bundle a private node.exe so
# the cashier PC never needs Node.js/npm installed).
$standaloneDir = Join-Path $dashboardRoot ".next\standalone"
if (!(Test-Path (Join-Path $standaloneDir "server.js"))) {
    throw "Dashboard build did not produce .next\standalone\server.js. Confirm next.config.ts sets output: 'standalone'."
}
$publicDir = Join-Path $dashboardRoot "public"
if (Test-Path $publicDir) {
    Copy-Item -LiteralPath $publicDir -Destination (Join-Path $standaloneDir "public") -Recurse -Force
}
Copy-Item -LiteralPath (Join-Path $dashboardRoot ".next\static") -Destination (Join-Path $standaloneDir ".next\static") -Recurse -Force

Write-SgLine "Dashboard production build complete." "INFO" $dashboardLog
