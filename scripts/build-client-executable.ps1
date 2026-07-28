. "$PSScriptRoot\secret-grounds-common.ps1"

$pythonExe = Join-Path $Script:SecretGroundsRoot ".venv\Scripts\python.exe"
if (!(Test-Path $pythonExe)) {
    throw "Python virtual environment is missing: $pythonExe"
}

& $pythonExe -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed in .venv. Install it before building the standalone client executable."
}

Push-Location $Script:SecretGroundsRoot
try {
    & $pythonExe -m PyInstaller `
        --noconfirm `
        --clean `
        --name "SecretGroundsClient" `
        --onedir `
        --collect-submodules client `
        --collect-submodules server `
        --add-data "VERSION;." `
        "scripts\pyinstaller_client_entry.py"
    if ($LASTEXITCODE -ne 0) {
        throw "Client executable build failed."
    }
} finally {
    Pop-Location
}

Write-SgLine "Client executable built at dist\SecretGroundsClient\SecretGroundsClient.exe." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
