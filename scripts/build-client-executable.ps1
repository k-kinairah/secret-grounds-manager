. "$PSScriptRoot\secret-grounds-common.ps1"

$pythonExe = Join-Path $Script:SecretGroundsRoot ".venv\Scripts\python.exe"
if (!(Test-Path $pythonExe)) {
    throw "Python virtual environment is missing: $pythonExe"
}

# PyInstaller writes all of its progress ("NNN INFO: ...") to stderr. With
# $ErrorActionPreference = "Stop" (set in secret-grounds-common.ps1) PowerShell
# turns every native-command stderr line into a terminating error, so a plain
# `& $pythonExe -m PyInstaller ...` aborts on the very first INFO line even
# though the build succeeds. Run it through cmd.exe with the streams redirected
# there instead -- the same workaround build-dashboard.ps1 uses for npm.
$buildLog = Resolve-SgPath (Get-SgEnv "SECRET_GROUNDS_CLIENT_BUILD_LOG" ".\logs\client-build.log")
New-Item -ItemType Directory -Force -Path (Split-Path $buildLog) | Out-Null
Write-SgLine "Building Secret Grounds client executable $(Get-SgVersion)." "INFO" $buildLog

# cmd /C strips exactly one outer quote pair, so the whole command -- the
# quoted python path included -- has to sit inside one wrapping pair.
$cmdArgs = '/C "' + `
    ('"{0}" -m PyInstaller --noconfirm --clean SecretGroundsClient.spec > "{1}" 2>&1' -f $pythonExe, $buildLog) + `
    '"'
$build = Start-Process -FilePath "cmd.exe" -ArgumentList $cmdArgs `
    -WorkingDirectory $Script:SecretGroundsRoot -WindowStyle Hidden -Wait -PassThru
if ($build.ExitCode -ne 0) {
    throw "Client executable build failed (exit $($build.ExitCode)). See $buildLog."
}

$clientExe = Join-Path $Script:SecretGroundsRoot "dist\SecretGroundsClient\SecretGroundsClient.exe"
if (!(Test-Path $clientExe)) {
    throw "Build reported success but $clientExe was not produced. See $buildLog."
}

Write-SgLine "Client executable built at dist\SecretGroundsClient\SecretGroundsClient.exe." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
