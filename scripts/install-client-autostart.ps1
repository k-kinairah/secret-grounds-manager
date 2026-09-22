. "$PSScriptRoot\secret-grounds-common.ps1"
Import-SgEnv

# Reported live: the supervised client task (LogonType Interactive, RunLevel
# Highest) failed silently at logon -- Task Scheduler logged "process
# terminated unexpectedly" and client.log showed nothing at all, because
# run-client-supervised.ps1 throws Access Denied writing
# secret-grounds-pids.json under {app}\logs before it ever reaches its
# first log line. Same root cause as the cashier's matching fix: Program
# Files' default ACL only grants BUILTIN\Users read/execute, not write --
# confirmed live even on this machine's admin account, since "RunLevel
# Highest" on an *Interactive* logon trigger does not reliably carry the
# full elevated token the way a SYSTEM/ServiceAccount task does. This runs
# every time (not gated behind any "already configured" check), so a
# reinstall that hits this fix for the first time actually gets it applied
# to whatever is already on disk, not just a brand-new install.
New-Item -ItemType Directory -Force -Path `
    (Join-Path $Script:SecretGroundsRoot "client"),
    (Join-Path $Script:SecretGroundsRoot "logs") | Out-Null
foreach ($writableDir in @("client", "logs")) {
    $writablePath = Join-Path $Script:SecretGroundsRoot $writableDir
    & icacls $writablePath /grant "*S-1-5-32-545:(OI)(CI)M" /T /Q 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Could not grant write access on $writablePath (icacls exit $LASTEXITCODE)."
    }
}

$taskName = "Secret Grounds Client"
# The task action is the supervisor loop, not a one-shot launcher: it keeps
# the customer client alive so ending it in Task Manager just re-locks the
# PC a couple of seconds later. See run-client-supervised.ps1.
$scriptPath = Join-Path $PSScriptRoot "run-client-supervised.ps1"

# Reported from the shop: a PowerShell/console window flashed open and
# vanished right at logon, just before the lock screen appeared. Cause:
# -WindowStyle Hidden is applied by the PowerShell host itself once it
# starts -- but Windows still allocates and briefly shows the console
# window at process creation, before that code runs. It's most visible
# right at logon while the desktop is still settling.
#
# A WScript.Shell.Run(cmd, 0, wait) VBS wrapper avoids this: window style 0
# is passed to CreateProcess itself, so the console is never shown, not even
# for an instant (the same trick install-startup-folder-shortcuts.ps1
# already uses for its fallback launchers). wait=True here (unlike that
# fallback's fire-and-forget False) so wscript.exe blocks for the
# supervisor's entire lifetime and stays the task's own tracked process --
# MultipleInstances IgnoreNew and the 1-minute repetition trigger below both
# rely on that to know an instance is still running.
$vbsPath = Join-Path $PSScriptRoot "run-client-supervised-hidden.vbs"
$hiddenCommand = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""
$vbsContent = @"
Set shell = CreateObject("WScript.Shell")
shell.Run "$($hiddenCommand.Replace('"', '""'))", 0, True
"@
Set-Content -LiteralPath $vbsPath -Value $vbsContent -Encoding ASCII

$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbsPath`"" -WorkingDirectory $Script:SecretGroundsRoot

# AtLogOn starts the supervisor for the customer session. The 1-minute
# repetition is the OUTER watchdog: if a customer also ends the supervisor
# process itself, Task Scheduler brings it back within a minute (the client
# keeps running, still locked, in that gap). While the supervisor is alive
# the repeated trigger is a no-op (MultipleInstances IgnoreNew).
$trigger = New-ScheduledTaskTrigger -AtLogOn
$repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 1) `
        -RepetitionDuration (New-TimeSpan -Days 3650)).Repetition
$trigger.Repetition = $repetition

# Registering without an explicit principal defaults to RunLevel Limited, which
# cannot reliably start the client process/lock screen under Program Files --
# the task reports LastTaskResult 1 and no client process remains, even though
# the action and working directory are correct. Confirmed fixed on a clean
# customer PC by registering with RunLevel Highest (same root cause and fix as
# the cashier autostart task in install-cashier-autostart.ps1).
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Highest
# ExecutionTimeLimit 0 = no limit: the supervisor is meant to run for the
# whole customer session (hours), not be killed after 10 minutes.
# RestartCount is a belt-and-braces on-failure restart on top of the
# repetition trigger's revival.
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable

if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
}

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description "Keeps the Secret Grounds customer PC client running (supervised)." | Out-Null
Write-SgLine "Installed scheduled task '$taskName' for $currentUser (supervised client via hidden VBS launcher, LogonType Interactive, RunLevel Highest, 1-minute watchdog)." "INFO" (Get-SgEnv "SECRET_GROUNDS_CLIENT_LOG" ".\logs\client.log")
