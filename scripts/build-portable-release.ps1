. "$PSScriptRoot\secret-grounds-common.ps1"

$version = Get-SgVersion
$distRoot = Join-Path $Script:SecretGroundsRoot "dist"
$releaseRoot = Join-Path $distRoot "secret-grounds-$version"
if (Test-Path $releaseRoot) {
    throw "Release folder already exists: $releaseRoot"
}

New-Item -ItemType Directory -Force -Path $releaseRoot | Out-Null
$include = @("server", "client", "dashboard", "shared", "scripts", "docs", "config", "VERSION", "ARCHITECTURE.md")
foreach ($item in $include) {
    $source = Join-Path $Script:SecretGroundsRoot $item
    if (Test-Path $source) {
        Copy-Item -LiteralPath $source -Destination $releaseRoot -Recurse
    }
}

$remove = @(
    ".git",
    "tests",
    ".agents",
    ".codex",
    "dashboard\node_modules",
    "dashboard\.next\cache",
    "server\__pycache__",
    "client\__pycache__",
    "logs\*.log",
    "data\*.db",
    "backups\*.db",
    "config\.env",
    "server\*.sqlite3"
)
foreach ($pattern in $remove) {
    Get-ChildItem -Path (Join-Path $releaseRoot $pattern) -Force -Recurse -ErrorAction SilentlyContinue | Remove-Item -Force -Recurse
}

Write-SgLine "Portable release assembled at $releaseRoot." "INFO"
