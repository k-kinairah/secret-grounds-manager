. "$PSScriptRoot\secret-grounds-common.ps1"

# Packages the already-built customer-PC client (scripts\build-client-
# executable.ps1's output) into the two files the Cashier PC's auto-update
# scanner expects to find dropped into updates\incoming\:
#   SecretGroundsClientUpdate-<version>.zip   the packaged client itself
#   SecretGroundsClientUpdate-<version>.json  its manifest (version/sha256/size)
#
# This only produces those two files locally, in dist\updates\. Getting them
# onto the Cashier PC (USB drive, network share, whatever the shop already
# uses) is a manual step by design -- see ARCHITECTURE.md Decision #24.

$version = Get-SgVersion
$clientDist = Join-Path $Script:SecretGroundsRoot "dist\SecretGroundsClient"
$clientExe = Join-Path $clientDist "SecretGroundsClient.exe"
if (!(Test-Path $clientExe)) {
    throw "Packaged client is missing: $clientExe. Run scripts\build-client-executable.ps1 first."
}

$outputDir = Join-Path $Script:SecretGroundsRoot "dist\updates"
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

$packageName = "SecretGroundsClientUpdate-$version.zip"
$zipPath = Join-Path $outputDir $packageName
if (Test-Path -LiteralPath $zipPath) {
    Remove-Item -LiteralPath $zipPath -Force
}

Write-SgLine "Zipping $clientDist -> $zipPath" "INFO"
Compress-Archive -Path (Join-Path $clientDist "*") -DestinationPath $zipPath -CompressionLevel Optimal

$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$size = (Get-Item -LiteralPath $zipPath).Length

$manifestPath = Join-Path $outputDir "SecretGroundsClientUpdate-$version.json"
[ordered]@{
    version    = $version
    sha256     = $hash
    size_bytes = $size
    filename   = $packageName
} | ConvertTo-Json | Set-Content -LiteralPath $manifestPath -Encoding utf8

Write-SgLine "Client update package staged at $zipPath" "INFO"
Write-SgLine "Copy both $packageName and $(Split-Path $manifestPath -Leaf) into the Cashier PC's updates\incoming\ folder." "INFO"
Write-SgLine "The server picks it up within 5 minutes, or POST /system/updates/rescan (signed in as owner/admin) applies it immediately." "INFO"
