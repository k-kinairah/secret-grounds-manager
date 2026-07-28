. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv -RequireConfig

$dashboardLog = Get-SgEnv "SECRET_GROUNDS_DASHBOARD_LOG" ".\logs\dashboard.log"
$apiUrl = Get-SgEnv "NEXT_PUBLIC_FASTAPI_BASE_URL" ""
if ([string]::IsNullOrWhiteSpace($apiUrl)) {
    throw "NEXT_PUBLIC_FASTAPI_BASE_URL must be set before building the production dashboard."
}

New-Item -ItemType Directory -Force -Path (Split-Path (Resolve-SgPath $dashboardLog)) | Out-Null
Write-SgLine "Building Secret Grounds dashboard $(Get-SgVersion) with API URL $apiUrl." "INFO" $dashboardLog

Push-Location (Join-Path $Script:SecretGroundsRoot "dashboard")
try {
    npm.cmd run build *> (Resolve-SgPath $dashboardLog)
    if ($LASTEXITCODE -ne 0) {
        throw "Dashboard build failed. See $(Resolve-SgPath $dashboardLog)."
    }
} finally {
    Pop-Location
}

Write-SgLine "Dashboard production build complete." "INFO" $dashboardLog
