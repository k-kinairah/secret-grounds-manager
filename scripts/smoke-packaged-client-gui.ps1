param(
    [string]$ExecutablePath = "",
    [int]$TimeoutSeconds = 15
)

. "$PSScriptRoot\secret-grounds-common.ps1"

if (-not $ExecutablePath) {
    $ExecutablePath = Join-Path $Script:SecretGroundsRoot "dist\SecretGroundsClient\SecretGroundsClient.exe"
}

$resolvedExecutable = Resolve-Path $ExecutablePath -ErrorAction Stop
$processInfo = [System.Diagnostics.ProcessStartInfo]::new()
$processInfo.FileName = $resolvedExecutable.Path
$processInfo.WorkingDirectory = Split-Path -Parent $resolvedExecutable.Path
$processInfo.UseShellExecute = $false
$processInfo.RedirectStandardOutput = $true
$processInfo.RedirectStandardError = $true

$previousGuiSmoke = $env:SECRET_GROUNDS_CLIENT_GUI_SMOKE
$previousTestMode = $env:SECRET_GROUNDS_TEST_MODE
$previousDevEscape = $env:ENABLE_DEV_ESCAPE

try {
    $env:SECRET_GROUNDS_CLIENT_GUI_SMOKE = "true"
    $env:SECRET_GROUNDS_TEST_MODE = "true"
    $env:ENABLE_DEV_ESCAPE = "true"

    $process = [System.Diagnostics.Process]::Start($processInfo)
    if ($null -eq $process) {
        throw "Could not start packaged client GUI smoke test."
    }

    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        $process.Kill()
        throw "Packaged client GUI smoke test timed out after $TimeoutSeconds seconds."
    }

    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    if ($stdout) {
        Write-Output $stdout.TrimEnd()
    }
    if ($stderr) {
        Write-Error $stderr.TrimEnd()
    }
    if ($process.ExitCode -ne 0) {
        throw "Packaged client GUI smoke test failed with exit code $($process.ExitCode)."
    }
} finally {
    $env:SECRET_GROUNDS_CLIENT_GUI_SMOKE = $previousGuiSmoke
    $env:SECRET_GROUNDS_TEST_MODE = $previousTestMode
    $env:ENABLE_DEV_ESCAPE = $previousDevEscape
}

Write-SgLine "Packaged client GUI smoke test passed." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
