. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

$pythonExe = Join-Path $Script:SecretGroundsRoot ".venv\Scripts\python.exe"
if (!(Test-Path $pythonExe)) {
    throw "Missing Python virtual environment at $pythonExe. Create it before deployment or ship a packaged runtime."
}

& $pythonExe -c "import fastapi, uvicorn, requests; print('Python dependencies ready')"
if ($LASTEXITCODE -ne 0) {
    throw "Python dependencies are missing from the virtual environment."
}

Push-Location (Join-Path $Script:SecretGroundsRoot "dashboard")
try {
    npm.cmd ci
    if ($LASTEXITCODE -ne 0) {
        throw "npm ci failed."
    }
} finally {
    Pop-Location
}

Write-SgLine "Secret Grounds dependencies are installed." "INFO" (Get-SgEnv "SECRET_GROUNDS_DASHBOARD_LOG" ".\logs\dashboard.log")
