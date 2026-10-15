<#
.SYNOPSIS
  Stop the processes start-demo.ps1 started, and nothing else.

.DESCRIPTION
  Reads %TEMP%\sg-demo\demo-state.json and stops each recorded process (with
  its child processes) only if it is still the same process: same id AND same
  start time, so a reused process id is never touched. The demo data in the
  run folder is kept; delete %TEMP%\sg-demo yourself when you no longer need it.

    powershell -ExecutionPolicy Bypass -File assistant\demo\stop-demo.ps1
#>
$ErrorActionPreference = "Stop"

$StateFile = Join-Path (Join-Path $env:TEMP "sg-demo") "demo-state.json"
if (-not (Test-Path $StateFile)) {
    Write-Host "stop-demo: nothing to stop (no $StateFile)."
    exit 0
}

$state = Get-Content -Raw -Path $StateFile | ConvertFrom-Json
$processes = @($state.processes)
# Stop in reverse start order: dashboard and client first, backend last.
[array]::Reverse($processes)

foreach ($entry in $processes) {
    if ($entry -eq $null) { continue }
    $process = Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
    if ($process -eq $null) {
        Write-Host ("  {0,-10} already stopped" -f $entry.name)
        continue
    }
    if ($entry.started_at -is [datetime]) {
        $recorded = $entry.started_at
    } else {
        $recorded = [datetime]::Parse($entry.started_at, $null, [System.Globalization.DateTimeStyles]::RoundtripKind)
    }
    if ([math]::Abs(($process.StartTime - $recorded).TotalSeconds) -gt 1) {
        Write-Host ("  {0,-10} pid {1} is now a different process; left alone" -f $entry.name, $entry.pid)
        continue
    }
    # /T also stops the children (the venv launcher's python, next dev's workers).
    & taskkill.exe /PID $entry.pid /T /F | Out-Null
    Write-Host ("  {0,-10} stopped (pid {1})" -f $entry.name, $entry.pid)
}

Remove-Item -Path $StateFile
Write-Host "Demo stopped. Data and logs kept in $($state.run_dir)"
