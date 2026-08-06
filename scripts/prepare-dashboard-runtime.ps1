param([switch]$Force)

. "$PSScriptRoot\secret-grounds-common.ps1"

# The cashier PC must never depend on Node.js/npm being installed (see
# docs\PACKAGING_READINESS.md). The dashboard's Next.js standalone build only
# needs a `node` binary to run its generated server.js; it does not need npm
# or any global Node.js install. A private copy of the official node.exe is
# bundled here instead, the same way scripts\prepare-backend-runtime.ps1
# bundles a private Python runtime for the backend.
$nodeVersion = "24.15.0"
$runtimeDir = Join-Path $Script:SecretGroundsRoot "runtime\node"
$nodeExe = Join-Path $runtimeDir "node.exe"

if ((Test-Path $nodeExe) -and !$Force) {
    Write-SgLine "Dashboard runtime already prepared at $nodeExe. Use -Force to rebuild." "INFO"
    exit 0
}

if (Test-Path $runtimeDir) {
    Remove-Item -LiteralPath $runtimeDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("sg-node-runtime-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
try {
    $zipPath = Join-Path $tempDir "node.zip"
    $zipUrl = "https://nodejs.org/dist/v$nodeVersion/node-v$nodeVersion-win-x64.zip"
    Write-SgLine "Downloading Node.js $nodeVersion runtime from $zipUrl." "INFO"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing

    $extractDir = Join-Path $tempDir "extracted"
    Expand-Archive -LiteralPath $zipPath -DestinationPath $extractDir -Force

    $extractedNodeExe = Get-ChildItem -LiteralPath $extractDir -Filter "node.exe" -Recurse | Select-Object -First 1
    if (!$extractedNodeExe) {
        throw "Downloaded Node.js archive did not contain node.exe."
    }
    # Only node.exe is needed to run the standalone server.js -- npm, corepack,
    # and the bundled npm package tree are build-time tooling, not shipped.
    Copy-Item -LiteralPath $extractedNodeExe.FullName -Destination $nodeExe -Force
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}

$versionOutput = & $nodeExe "--version"
if ($LASTEXITCODE -ne 0) {
    throw "Bundled dashboard runtime failed verification."
}

Write-SgLine "Dashboard runtime prepared at $nodeExe ($versionOutput)." "INFO"
