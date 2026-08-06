param([switch]$Force)

. "$PSScriptRoot\secret-grounds-common.ps1"

# The backend must never depend on a Python installation outside the shipped app
# (see docs\PACKAGING_READINESS.md, "Recommended: private bundled Python runtime").
# A plain `python -m venv` is not relocatable: its pyvenv.cfg hardcodes the absolute
# path of the machine that created it, which is exactly what broke clean-PC installs.
# The official embeddable distribution has no such reference, so it is used as the
# shipped runtime instead of copying a developer's .venv.
$pythonVersion = "3.14.6"
$runtimeDir = Join-Path $Script:SecretGroundsRoot "runtime\python"
$pythonExe = Join-Path $runtimeDir "python.exe"

if ((Test-Path $pythonExe) -and !$Force) {
    Write-SgLine "Backend runtime already prepared at $pythonExe. Use -Force to rebuild." "INFO"
    exit 0
}

if (Test-Path $runtimeDir) {
    Remove-Item -LiteralPath $runtimeDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

$tempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("sg-runtime-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $tempDir | Out-Null
try {
    $zipPath = Join-Path $tempDir "python-embed.zip"
    $zipUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-embed-amd64.zip"
    Write-SgLine "Downloading Python $pythonVersion embeddable runtime from $zipUrl." "INFO"
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing

    Expand-Archive -LiteralPath $zipPath -DestinationPath $runtimeDir -Force

    $pthFile = Get-ChildItem -LiteralPath $runtimeDir -Filter "python*._pth" | Select-Object -First 1
    if (!$pthFile) {
        throw "Embeddable runtime did not contain a python*._pth file at $runtimeDir."
    }
    $pthLines = Get-Content -LiteralPath $pthFile.FullName
    $pthLines = $pthLines -replace '^#\s*import site$', 'import site'
    if ($pthLines -notcontains "Lib\site-packages") {
        $pthLines = @($pthLines[0]) + @("Lib\site-packages") + $pthLines[1..($pthLines.Length - 1)]
    }
    Set-Content -LiteralPath $pthFile.FullName -Value $pthLines -Encoding ASCII

    $getPipPath = Join-Path $tempDir "get-pip.py"
    Write-SgLine "Bootstrapping pip." "INFO"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $getPipPath -UseBasicParsing
    & $pythonExe $getPipPath --no-warn-script-location
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to bootstrap pip into the bundled runtime."
    }

    $requirementsPath = Join-Path $Script:SecretGroundsRoot "server\requirements.txt"
    Write-SgLine "Installing backend dependencies from $requirementsPath." "INFO"
    & $pythonExe -m pip install --no-warn-script-location -r $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install backend dependencies into the bundled runtime."
    }
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}

& $pythonExe -c "import fastapi, uvicorn, websockets, sqlite3; print('Backend runtime dependencies ready')"
if ($LASTEXITCODE -ne 0) {
    throw "Bundled backend runtime failed dependency verification."
}

Write-SgLine "Backend runtime prepared at $pythonExe." "INFO"
