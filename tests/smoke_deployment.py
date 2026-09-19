import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from client.config import APP_VERSION as CLIENT_VERSION
from server.database import get_database_path
from server.version import APP_VERSION


SCRIPT_NAMES = {
    "secret-grounds-common.ps1",
    "install-dependencies.ps1",
    "build-dashboard.ps1",
    "start-backend.ps1",
    "start-dashboard.ps1",
    "start-secret-grounds.ps1",
    "stop-secret-grounds.ps1",
    "restart-secret-grounds.ps1",
    "status-secret-grounds.ps1",
    "install-cashier-autostart.ps1",
    "remove-cashier-autostart.ps1",
    "run-cashier-supervised.ps1",
    "start-client.ps1",
    "install-client-autostart.ps1",
    "remove-client-autostart.ps1",
    "check-deployment.ps1",
    "build-portable-release.ps1",
}


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_version_consistency() -> None:
    version = read("VERSION").strip()
    package = json.loads(read("dashboard/package.json"))
    assert version == "1.0.0-rc.2"
    assert APP_VERSION == version
    assert CLIENT_VERSION == version
    assert package["version"] == version


def test_required_deployment_scripts_exist() -> None:
    scripts = {path.name for path in (PROJECT_ROOT / "scripts").glob("*.ps1")}
    assert SCRIPT_NAMES <= scripts


def test_cashier_scripts_use_production_startup_and_pid_identity() -> None:
    backend = read("scripts/start-backend.ps1")
    dashboard = read("scripts/start-dashboard.ps1")
    common = read("scripts/secret-grounds-common.ps1")
    stop = read("scripts/stop-secret-grounds.ps1")

    assert "--reload" not in backend
    assert "server.main:app" in backend
    assert "uvicorn" in backend
    assert "Wait-SgHttpReady" in backend
    assert "healthz" in backend
    # The dashboard must run from a bundled private Node.js runtime, never a
    # system/PATH-resolved node, npm, or the `next` CLI -- a cashier PC is not
    # guaranteed to have Node.js installed at all.
    assert "runtime\\node\\node.exe" in dashboard
    assert ".next\\standalone\\server.js" in dashboard
    assert "Resolve-SgNodePath" not in dashboard
    assert "npm.cmd" not in dashboard
    assert "next\\dist\\bin\\next" not in dashboard
    assert "next dev" not in dashboard.lower()
    assert "Resolve-SgNodePath" not in common
    assert "secret-grounds-pids.json" in common
    assert "CommandLine" in common
    assert "Start-Process" in common
    assert "function Start-SgLoggedProcess" in common
    assert "launch-wrappers" in common
    assert "HasExited" in common
    assert "Show-SgLogTail" in common
    assert "dashboard-console.log" in dashboard
    assert "server-console.log" in backend
    assert "Stop-SgStartedProcess" in stop
    assert "Stop-Process -Name" not in stop


def test_autostart_task_names_are_stable() -> None:
    cashier = read("scripts/install-cashier-autostart.ps1")
    client = read("scripts/install-client-autostart.ps1")
    assert "Secret Grounds Cashier Stack" in cashier
    assert "Secret Grounds Client" in client
    assert "MultipleInstances IgnoreNew" in cashier
    assert "MultipleInstances IgnoreNew" in client


def test_cashier_autostart_task_runs_at_boot_as_system() -> None:
    # Two failure modes are guarded here:
    #  1. No explicit -Principal defaults to RunLevel Limited, which cannot
    #     write to {app}\data or {app}\logs under C:\Program Files\Secret
    #     Grounds\ -- LastTaskResult 1, both services stay stopped. Fixed by
    #     RunLevel Highest.
    #  2. A "run only when user is logged on" (LogonType Interactive) principal
    #     is not eligible to run at boot while the PC is on the sign-in screen,
    #     and the -AtStartup trigger has elapsed by the time anyone signs in --
    #     so the stack never came up on a normal reboot. Fixed by running the
    #     -AtStartup task as SYSTEM (present in session 0, full rights to
    #     {app}, no desktop needed since the stack starts with -NoBrowser).
    cashier = read("scripts/install-cashier-autostart.ps1")
    assert "New-ScheduledTaskPrincipal" in cashier
    assert "-RunLevel Highest" in cashier
    assert "-Principal $principal" in cashier
    # -AtStartup -> SYSTEM; the -AtLogOn fallback keeps the interactive signer.
    assert '-UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest' in cashier
    assert "-LogonType Interactive" in cashier
    assert "[System.Security.Principal.WindowsIdentity]::GetCurrent().Name" in cashier

    # Upgrades must replace a stale Limited-level task, never leave a
    # duplicate registered alongside the corrected one.
    assert "Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue" in cashier
    assert "Unregister-ScheduledTask -TaskName $taskName -Confirm:$false" in cashier
    unregister_index = cashier.index("Unregister-ScheduledTask -TaskName $taskName -Confirm:$false")
    register_index = cashier.index("Register-ScheduledTask -TaskName $taskName")
    assert unregister_index < register_index

    # The action itself (hidden PowerShell, -NoBrowser) must be unchanged by
    # the principal/settings fix.
    assert '-WindowStyle Hidden -File `"$scriptPath`" -NoBrowser' in cashier


def test_cashier_autostart_task_supervises_and_watchdogs_the_stack() -> None:
    # Reported live: the cashier reinstall replaced this task, but since
    # -AtStartup only fires on an actual boot, the stack silently never came
    # up until someone ran the start scripts by hand. Even once running,
    # there was previously no ongoing supervision -- if the backend or
    # dashboard crashed mid-day, nothing brought it back. The action is now a
    # polling supervisor loop, matching the client's supervised pattern.
    cashier = read("scripts/install-cashier-autostart.ps1")

    # The action is now the supervisor loop, not a one-shot launcher.
    assert "run-cashier-supervised.ps1" in cashier
    # The supervisor must be allowed to run for the whole session...
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in cashier
    assert "New-TimeSpan -Minutes 10" not in cashier
    # ...and a 1-minute repetition is the outer watchdog for a killed
    # supervisor process (distinct from the supervisor's own inner health
    # poll of the backend/dashboard processes).
    assert "$trigger.Repetition" in cashier
    assert "-RepetitionInterval (New-TimeSpan -Minutes 1)" in cashier
    assert "-MultipleInstances IgnoreNew" in cashier


def test_cashier_supervisor_relaunches_and_is_stoppable() -> None:
    supervisor = read("scripts/run-cashier-supervised.ps1")
    # The loop polls both processes and restarts the whole stack if either
    # one has died...
    assert 'Test-SgStartedProcess (Get-SgProcessRecord "backend")' in supervisor
    assert 'Test-SgStartedProcess (Get-SgProcessRecord "dashboard")' in supervisor
    assert "start-secret-grounds.ps1" in supervisor
    assert "Start-Sleep -Seconds $PollSeconds" in supervisor
    # ...but a stop sentinel makes it exit without relaunching, so teardown
    # and uninstall can actually stop it.
    assert "cashier-supervisor.stop" in supervisor
    assert "Test-StopRequested" in supervisor
    # It tracks its own PID for the stop scripts.
    assert 'Set-SgProcessRecord "cashier-supervisor"' in supervisor
    # Reported live: writing the sentinel under {app}\logs threw
    # UnauthorizedAccessException for the non-admin cashier -- that
    # directory is created by this SYSTEM-run supervisor, and Program
    # Files' default ACL lets a standard user modify files they already
    # made but not create a brand-new one there. Must live under
    # %ProgramData% instead, which does not require elevation.
    assert 'Join-Path $env:ProgramData "Secret Grounds"' in supervisor

    stop = read("scripts/stop-secret-grounds.ps1")
    assert "cashier-supervisor.stop" in stop
    assert 'Stop-SgStartedProcess "cashier-supervisor"' in stop
    assert "Disable-ScheduledTask" in stop
    assert "KeepTaskEnabled" in stop
    assert 'Join-Path $env:ProgramData "Secret Grounds"' in stop

    # A manual restart must re-arm the watchdog rather than leave the stack
    # unsupervised until someone remembers to reinstall the autostart task.
    restart = read("scripts/restart-secret-grounds.ps1")
    assert "stop-secret-grounds.ps1" in restart
    assert "-KeepTaskEnabled" in restart


def test_client_autostart_task_supervises_and_watchdogs_the_client() -> None:
    client = read("scripts/install-client-autostart.ps1")
    # Elevated interactive principal (RunLevel Highest) is still required --
    # a Limited-level task cannot start the client under Program Files.
    assert "New-ScheduledTaskPrincipal" in client
    assert "-LogonType Interactive" in client
    assert "-RunLevel Highest" in client
    assert "[System.Security.Principal.WindowsIdentity]::GetCurrent().Name" in client
    assert "-Principal $principal" in client

    # The action is now the supervisor loop, not a one-shot launcher.
    assert "run-client-supervised.ps1" in client
    assert "start-client.ps1" not in client
    # The supervisor must be allowed to run for the whole session...
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in client
    assert "New-TimeSpan -Minutes 10" not in client
    # ...and a 1-minute repetition is the outer watchdog for a killed supervisor.
    assert "$trigger.Repetition" in client
    assert "-RepetitionInterval (New-TimeSpan -Minutes 1)" in client
    assert "-MultipleInstances IgnoreNew" in client

    # Upgrades must replace a stale task, never leave a duplicate.
    assert "Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue" in client
    assert "Unregister-ScheduledTask -TaskName $taskName -Confirm:$false" in client
    unregister_index = client.index("Unregister-ScheduledTask -TaskName $taskName -Confirm:$false")
    register_index = client.index("Register-ScheduledTask -TaskName $taskName")
    assert unregister_index < register_index

    # Cashier autostart is a separate task and keeps its own wiring.
    cashier = read("scripts/install-cashier-autostart.ps1")
    assert 'taskName = "Secret Grounds Cashier Stack"' in cashier
    assert 'taskName = "Secret Grounds Client"' in client


def test_client_task_launches_through_a_hidden_vbs_wrapper_no_console_flash() -> None:
    # Reported from the shop: a console window flashed open and vanished
    # right at logon, just before the lock screen appeared. -WindowStyle
    # Hidden alone doesn't prevent this -- Windows shows the console for an
    # instant before PowerShell's own startup code hides it. The task must
    # launch through a WScript.Shell.Run(cmd, 0, True) VBS wrapper instead
    # (window style 0 reaches CreateProcess itself, so no flash ever
    # occurs), with wait=True so wscript.exe -- not the inner powershell --
    # is the task's own tracked process for its whole run.
    client = read("scripts/install-client-autostart.ps1")
    assert 'New-ScheduledTaskAction -Execute "wscript.exe"' in client
    assert '-Execute "powershell.exe"' not in client
    assert 'CreateObject("WScript.Shell")' in client
    assert "shell.Run" in client
    assert ", 0, True" in client
    assert "run-client-supervised-hidden.vbs" in client

    remover = read("scripts/remove-client-autostart.ps1")
    assert "run-client-supervised-hidden.vbs" in remover
    assert "Remove-Item" in remover


def test_client_supervisor_relaunches_and_is_stoppable() -> None:
    supervisor = read("scripts/run-client-supervised.ps1")
    # The loop relaunches the client after each exit...
    assert "$proc.WaitForExit()" in supervisor
    assert "Start-Sleep -Seconds $RelaunchDelaySeconds" in supervisor
    # ...but a stop sentinel makes it exit without relaunching, so teardown
    # and uninstall can actually stop it.
    assert "client-supervisor.stop" in supervisor
    assert "Test-StopRequested" in supervisor
    # It tracks both its own PID and the live client PID for the stop scripts.
    assert 'Set-SgProcessRecord "client-supervisor"' in supervisor
    assert 'Set-SgProcessRecord "client"' in supervisor

    stop = read("scripts/stop-client.ps1")
    assert "client-supervisor.stop" in stop
    assert 'Disable-ScheduledTask -TaskName $taskName' in stop
    assert 'Stop-SgStartedProcess "client-supervisor"' in stop
    assert 'Stop-SgStartedProcess "client"' in stop

    # Uninstall/teardown disables the tasks and drops the sentinel BEFORE
    # killing processes, so nothing respawns during file deletion.
    stop_all = read("scripts/stop-all-for-uninstall.ps1")
    assert "Disable-ScheduledTask" in stop_all
    assert "client-supervisor.stop" in stop_all
    assert 'Stop-SgStartedProcess "client-supervisor"' in stop_all
    disable_index = stop_all.index("Disable-ScheduledTask")
    kill_index = stop_all.index('Stop-SgStartedProcess "client-supervisor"')
    assert disable_index < kill_index


def test_config_examples_are_secret_safe() -> None:
    env_example = read("config/.env.example")
    client_example = json.loads(read("client/config.example.json"))

    assert "SECRET_GROUNDS_DEPLOYMENT_SECRET=replace-with-a-long-random-secret" in env_example
    assert not re.search(r"(password|token)\s*=\s*(?!replace|example|false|true)[^#\r\n]+", env_example, re.IGNORECASE)
    assert "SECRET_GROUNDS_AUTH_SECURE_COOKIE=false" in env_example
    assert "SECRET_GROUNDS_PORT=18001" in env_example
    assert "SECRET_GROUNDS_DASHBOARD_PORT=3001" in env_example
    assert "NEXT_PUBLIC_FASTAPI_BASE_URL=http://127.0.0.1:18001" in env_example
    assert "SECRET_GROUNDS_CORS_ORIGINS=http://127.0.0.1:3001" in env_example
    assert "localhost" not in env_example
    assert client_example["server_host"] == "cashier-server"
    assert client_example["server_port"] == 18001
    assert client_example["pc_name"] == "CHANGE-ME"
    assert client_example["pc_name"] == "CHANGE-ME"


def test_development_and_production_urls_are_separate() -> None:
    dashboard_env = read("dashboard/.env.example")
    cashier_config = read("config/.env.example")
    common = read("scripts/secret-grounds-common.ps1")
    initializer = read("scripts/initialize-cashier-config.ps1")
    ports_doc = read("docs/PORTS.md")

    assert "NEXT_PUBLIC_FASTAPI_BASE_URL=http://localhost:8000" in dashboard_env
    assert "127.0.0.1:18001" not in dashboard_env
    assert "localhost:8000" not in cashier_config
    assert "$Script:DefaultBackendPort = 18001" in common
    assert '"http://127.0.0.1:$DashboardPort"' in initializer
    assert "http://localhost:3000" in ports_doc
    assert "http://localhost:8000" in ports_doc
    assert "http://127.0.0.1:3001" in ports_doc
    assert "http://127.0.0.1:18001" in ports_doc


def test_deployment_paths_are_outside_source_defaults(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_GROUNDS_DB_PATH", r".\data\secret_grounds.db")
    resolved = get_database_path()
    assert resolved.is_absolute()
    assert resolved.name == "secret_grounds.db"
    assert "data" in resolved.parts


def test_check_deployment_covers_requested_safety_checks() -> None:
    check = read("scripts/check-deployment.ps1")
    for expected in [
        "SECRET_GROUNDS_DEPLOYMENT_SECRET",
        "SECRET_GROUNDS_TEST_MODE",
        "dashboard\\.next\\standalone\\server.js",
        "PRAGMA integrity_check",
        "legacy database",
        "Test-SgPortAvailable",
        "SECRET_GROUNDS_ADMIN_PASSWORD",
    ]:
        assert expected in check


def test_client_start_rejects_shared_default_pc_name() -> None:
    start_client = read("scripts/start-client.ps1")
    assert "Confirm this is the assigned customer PC" in start_client
    assert "SECRET_GROUNDS_TEST_MODE=true is blocked" in start_client
    assert "client-error.log" in start_client


def test_docs_cover_manual_retest_and_firewall_guidance() -> None:
    docs = read("docs/DEPLOYMENT_WINDOWS.md")
    assert "Safe Manual Retest" in docs
    assert "Do not disable the firewall" in docs
    assert "Secret Grounds Cashier Stack" in docs
    assert "Secret Grounds Client" in docs


def main() -> None:
    tests = [
        test_version_consistency,
        test_required_deployment_scripts_exist,
        test_cashier_scripts_use_production_startup_and_pid_identity,
        test_autostart_task_names_are_stable,
        test_cashier_autostart_task_runs_at_boot_as_system,
        test_cashier_autostart_task_supervises_and_watchdogs_the_stack,
        test_cashier_supervisor_relaunches_and_is_stoppable,
        test_client_autostart_task_supervises_and_watchdogs_the_client,
        test_client_task_launches_through_a_hidden_vbs_wrapper_no_console_flash,
        test_client_supervisor_relaunches_and_is_stoppable,
        test_config_examples_are_secret_safe,
        test_development_and_production_urls_are_separate,
        lambda: test_deployment_paths_are_outside_source_defaults(
            type(
                "MonkeyPatch",
                (),
                {"setenv": staticmethod(lambda name, value: __import__("os").environ.__setitem__(name, value))},
            )
        ),
        test_check_deployment_covers_requested_safety_checks,
        test_client_start_rejects_shared_default_pc_name,
        test_docs_cover_manual_retest_and_firewall_guidance,
    ]
    for test in tests:
        test()
    print("Deployment smoke tests passed.")


if __name__ == "__main__":
    main()
