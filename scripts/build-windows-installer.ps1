. "$PSScriptRoot\secret-grounds-common.ps1"

$version = Get-SgVersion
$isccCandidates = @(
    "C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    "C:\Program Files\Inno Setup 6\ISCC.exe",
    "C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
    "C:\Program Files\Inno Setup 5\ISCC.exe"
)
$iscc = ""
foreach ($candidate in $isccCandidates) {
    if (Test-Path $candidate) {
        $iscc = $candidate
        break
    }
}
if (!$iscc) {
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($command) {
        $iscc = $command.Source
    }
}
if (!$iscc) {
    throw "Inno Setup compiler (ISCC.exe) was not found. Install Inno Setup 6, then rerun this script."
}

$clientExe = Join-Path $Script:SecretGroundsRoot "dist\SecretGroundsClient\SecretGroundsClient.exe"
if (!(Test-Path $clientExe)) {
    throw "Packaged client is missing: $clientExe. Run scripts\build-client-executable.ps1 first."
}
if (!(Test-Path (Join-Path $Script:SecretGroundsRoot "dashboard\.next\standalone\server.js"))) {
    throw "Dashboard standalone production build is missing. Run scripts\build-dashboard.ps1 first."
}
if (!(Test-Path (Join-Path $Script:SecretGroundsRoot "runtime\python\python.exe"))) {
    throw "Cashier Python runtime is missing. Run scripts\prepare-backend-runtime.ps1 before building the installer."
}
if (!(Test-Path (Join-Path $Script:SecretGroundsRoot "runtime\node\node.exe"))) {
    throw "Cashier Node.js runtime is missing. Run scripts\prepare-dashboard-runtime.ps1 before building the installer."
}

$installerScript = Join-Path $Script:SecretGroundsRoot "installer\SecretGroundsSetup.iss"
New-Item -ItemType Directory -Force -Path (Join-Path $Script:SecretGroundsRoot "dist\installer") | Out-Null
& $iscc ("/DAppVersion=$version") $installerScript
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup build failed."
}

$output = Join-Path $Script:SecretGroundsRoot "dist\installer\SecretGroundsSetup-$version.exe"
if (!(Test-Path $output)) {
    throw "Installer build completed but expected output was not found: $output"
}
Write-SgLine "Windows installer built at $output." "INFO"
