import re
import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def _tracked_files() -> list[str]:
    output = subprocess.run(
        ["git", "-C", str(PROJECT_ROOT), "ls-files"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [line for line in output.splitlines() if line]


def test_inno_installer_has_role_selection() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert "Choose Computer Role" in source
    assert "Cashier Computer" in source
    assert "Customer Computer" in source
    assert "IsCashier" in source
    assert "IsClient" in source


def test_installer_separates_cashier_and_client_payloads() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert 'Source: "..\\server\\*.py"' in source
    assert 'Source: "..\\dashboard\\.next\\standalone\\*"' in source
    assert 'Source: "..\\dist\\SecretGroundsClient\\*"' in source
    assert "Check: IsCashier" in source
    assert "Check: IsClient" in source


def test_installer_preserves_cashier_data_by_default() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert "Keep Secret Grounds shop data" in source
    assert "MB_DEFBUTTON1" in source
    assert "DelTree(ExpandConstant('{app}\\data')" in source
    assert "DelTree(ExpandConstant('{app}\\backups')" in source


def test_installer_client_validation_is_present() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert "/healthz" in source
    assert "/client-registration/check" in source
    assert "Version mismatch" in source
    assert "offline preparation" in source.lower()


def test_installer_supports_fixed_and_diskless_customer_modes() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    # Second-step mode selection under the Customer Computer role.
    assert "Customer Computer Setup" in source
    assert "Fixed Station" in source
    assert "Diskless / Automatic" in source
    assert "ClientModePage" in source
    assert "ClientDisklessPage" in source
    assert "SelectedClientMode" in source

    # Fixed Station keeps online validation. The wizard now has two fields
    # (Station ID + server host), so the port is the fixed 18001.
    assert (
        "ValidateClientOnline(ClientPage.Values[1], '18001', ClientPage.Values[0])"
        in source
    )

    # Diskless config write path: no Station ID, and the -Diskless switch is
    # threaded through to configure-client.ps1.
    assert "GetDisklessSwitchArg" in source
    assert "-Diskless" in source
    assert "ValidateServerReachableAndVersioned" in source
    # ...but no station-availability check for diskless (pc_name is blank).
    diskless_opts_block = source[
        source.index("if SelectedClientMode = 'diskless' then begin") :
        source.index("Result := ValidateClientOnline(ClientPage.Values[1]")
    ]
    assert "client-registration/check" not in diskless_opts_block

    # Image-prep completion message, and no client launch on the master image.
    assert "Diskless image ready" in source
    assert "Do not assign a Station ID to this image." in source
    run_block = source[
        source.index("procedure RunClientPostInstall")
        : source.index("procedure CurStepChanged")
    ]
    assert "if SelectedClientMode = 'diskless' then begin" in run_block
    assert "mbInformation" in run_block
    # The offline-prep guard the existing launch path relies on is untouched.
    assert "if not (IsClientOnline or IsClientPreserve) then Exit;" in run_block


def test_configure_client_supports_diskless_switch() -> None:
    source = read("scripts/configure-client.ps1")
    assert "[switch]$Diskless" in source
    assert "if (-not $Diskless)" in source
    # Fixed-station requirements still enforced when -Diskless is absent.
    assert 'Assert-ClientValue (![string]::IsNullOrWhiteSpace($StationId)) "Station ID is required."' in source


def test_installer_detects_and_preserves_existing_client_config() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert "DetectExistingClientConfig" in source
    assert "ConfigModeValue" in source
    assert "IsClientPreserve" in source
    assert "-Mode Preserve" in source
    assert "-Mode {code:GetConfigMode}" in source
    assert "read-client-config-status.ps1" in source
    assert "client-config-tool.ps1" in source
    assert "Existing configuration detected" in source


def test_installer_aborts_on_invalid_existing_client_config() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert "ExistingConfigStatus = 'Invalid'" in source
    assert "Result := False" in source
    assert "could not be parsed" in source


def test_installer_does_not_expand_app_before_it_is_initialized() -> None:
    # {app} cannot be expanded until Setup has resolved the destination directory,
    # which has not happened yet inside InitializeSetup/DetectExistingClientConfig
    # (that crashed the wizard before it could even open). Detection must use the
    # previous install location from the registry instead.
    source = read("installer/SecretGroundsSetup.iss")
    # Scoped to the functions that run during InitializeSetup, before {app} is
    # resolved -- CurStepChanged(ssPostInstall) runs after files are installed
    # and legitimately uses ExpandConstant('{app}') there.
    early_init_block = source[
        source.index("function GetPreviousInstallLocation"):source.index("procedure InitializeWizard")
    ]
    assert "ExpandConstant('{app}')" not in early_init_block
    assert "function GetPreviousInstallLocation" in source
    assert "RegQueryStringValue(HKLM" in source
    assert "PreviousInstallLocation := GetPreviousInstallLocation" in source

    # Pascal's { } comments do not nest, so a literal {app} written inside one
    # (e.g. "{ {app} is not available here }") silently truncates the comment at
    # its own closing brace and corrupts the following code. Guard against that
    # class of mistake by requiring // line comments wherever {app} is discussed.
    assert "{ {app}" not in source


def test_configure_client_supports_explicit_modes() -> None:
    source = read("scripts/configure-client.ps1")
    assert "ValidateSet(\"Fresh\", \"Preserve\", \"Update\")" in source
    assert "Preserve mode requires an existing client configuration" in source
    assert "Backed up previous client configuration" in source
    assert "no changes made" in source
    assert "exit 1" in source


def test_client_config_tool_writes_atomically_and_backs_up() -> None:
    source = read("scripts/client-config-tool.ps1")
    assert "function Write-SgClientConfigAtomic" in source
    assert "Move-Item" in source
    assert "function Backup-SgClientConfig" in source
    assert "function Get-SgClientConfigStatus" in source


def test_release_candidate_is_rc2_and_rc1_flagged_obsolete() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert '#define AppVersion "1.0.0-rc.2"' in source
    version = read("VERSION").strip()
    assert version == "1.0.0-rc.2"
    manifest = read("installer/MANIFEST.md")
    assert "rc.1" in manifest.lower() and "obsolete" in manifest.lower()


def test_cashier_config_generator_uses_secure_secret_and_production_ports() -> None:
    source = read("scripts/initialize-cashier-config.ps1")
    assert "RandomNumberGenerator" in source
    assert "BackendPort = 18001" in source
    assert "DashboardPort = 3001" in source
    assert "Existing cashier config preserved" in source
    # RandomNumberGenerator.Fill(Span<byte>) is a .NET 6+ static method that
    # does not exist under Windows PowerShell 5.1 (.NET Framework), which is
    # what ships on a clean Windows PC. Create()+GetBytes()+Dispose() is the
    # PS 5.1-compatible equivalent.
    assert "RandomNumberGenerator]::Fill(" not in source
    assert "RandomNumberGenerator]::Create()" in source
    assert ".GetBytes($secretBytes)" in source
    assert ".Dispose()" in source


def test_client_launcher_prefers_packaged_executable() -> None:
    source = read("scripts/start-client.ps1")
    assert "SecretGroundsClient.exe" in source
    assert "packagedClientExe" in source
    assert "-m\", \"client.client" in source


def test_installer_automates_cashier_initialization_and_startup() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert "procedure RunCashierPostInstall" in source
    assert "if SelectedRole <> 'cashier' then Exit" in source
    assert "RunCashierScript('initialize-cashier-config.ps1'" in source
    assert "RunCashierScript('install-backend-firewall-rule.ps1'" in source
    assert "RunCashierScript('install-cashier-role.ps1'" in source
    assert "RunCashierScript('restart-secret-grounds.ps1'" in source
    assert "procedure CurStepChanged" in source
    assert "if CurStep = ssPostInstall then" in source
    # Friendly, non-technical failure text (raw PowerShell output must not
    # be what a clean-PC user sees).
    assert "Secret Grounds could not create the cashier configuration." in source
    assert "Secret Grounds could not install the Private-network firewall rule" in source
    assert "Secret Grounds could not configure the cashier startup role." in source
    assert "Secret Grounds was installed, but the backend could not start." in source
    assert "logs\\server.log" in source
    assert "logs\\dashboard.log" in source


def test_installer_offers_bounded_retry_on_startup_failure() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert "MB_RETRYCANCEL" in source
    assert "IDRETRY" in source
    # The retry loop must only continue on an explicit user click, never spin
    # on its own -- there is exactly one automatic attempt per Retry click.
    assert "Retry := True;\n  while Retry do begin\n    Retry := False;" in source


def test_installer_opens_dashboard_only_after_health_succeeds() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert "function CashierServicesAreHealthy" in source
    assert 'Description: "Open Secret Grounds Dashboard"' in source
    assert "Flags: postinstall shellexec skipifsilent" in source
    assert "Check: CashierServicesAreHealthy" in source
    # CashierServicesHealthy only ever becomes True after restart-secret-grounds.ps1
    # (which itself waits for backend AND dashboard readiness) succeeds.
    assert "CashierServicesHealthy := False;" in source
    assert "CashierServicesHealthy := True;" in source


def test_installer_cashier_autostart_checkbox_defaults_and_preserves_choice() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert "Start Secret Grounds automatically when this computer starts." in source
    assert "CashierOptionsPage.Values[0] := (not ExistingCashierConfigFound) or ExistingCashierAutostartEnabled;" in source
    assert "procedure DetectExistingCashierState" in source
    assert "read-cashier-status.ps1" in source
    assert "ExistingCashierAutostartEnabled" in source


def test_installer_does_not_pass_boolean_literals_to_external_powershell() -> None:
    # A new "powershell.exe -File ... -Param:$true" process does not reliably
    # bind [bool]/[switch] parameters -- the literal text "$true"/"$false"
    # arrives unparsed and fails type conversion at the receiving script. Any
    # Pascal string literal built from these would silently break every
    # cashier install. install-cashier-role.ps1 must instead be driven with a
    # plain presence/absence switch.
    iss_source = read("installer/SecretGroundsSetup.iss")
    assert "'$true'" not in iss_source
    assert "'$false'" not in iss_source
    assert "-DisableAutostart" in iss_source

    role_source = read("scripts/install-cashier-role.ps1")
    assert "[switch]$DisableAutostart" in role_source
    assert "[bool]$EnableAutostart" not in role_source


def test_installer_dashboard_shortcuts_ensure_services_running() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert 'Filename: "http://127.0.0.1:3001"; Check: IsCashier' not in source
    assert "open-dashboard.ps1" in source
    open_dashboard = read("scripts/open-dashboard.ps1")
    assert "Test-SgHealthOnce" in open_dashboard
    assert "start-secret-grounds.ps1" in open_dashboard
    assert "Wait-SgHttpReady" in open_dashboard
    assert "Start-Process $dashboardUrl" in open_dashboard


def test_installer_cashier_role_safety() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    # Cashier automation must never launch or configure the customer client.
    run_cashier_block = source[source.index("procedure RunCashierPostInstall"):source.index("procedure CurStepChanged")]
    assert "SecretGroundsClient.exe" not in run_cashier_block
    assert "install-client-autostart" not in run_cashier_block
    assert "configure-client.ps1" not in run_cashier_block

    role_source = read("scripts/install-cashier-role.ps1")
    assert "remove-client-autostart.ps1" in role_source
    assert "remove-client-startup-folder-shortcut.ps1" in role_source


def test_no_developer_specific_absolute_paths_are_tracked() -> None:
    # A raw `python -m venv` was once shipped straight into the installer: its
    # pyvenv.cfg hardcodes the absolute path of whoever created it (e.g.
    # C:\Users\Kin\AppData\Local\Python\pythoncore-3.14-64\python.exe), which
    # made the installed backend try to launch a Python that only exists on
    # the developer's machine and fail with exit code 103 on a clean PC. The
    # fix replaced that with a self-contained embeddable runtime (see
    # scripts/prepare-backend-runtime.ps1), but nothing stops a future change
    # from reintroducing a machine-specific path some other way. Fail the
    # build if any tracked file contains one.
    dev_path_pattern = re.compile(r"[Cc]:\\Users\\[^\\\s\"']+\\")
    pythoncore_pattern = re.compile(r"pythoncore", re.IGNORECASE)
    self_path = "tests/smoke_installer.py"

    offenders = []
    for relative_path in _tracked_files():
        if relative_path == self_path:
            continue
        try:
            text = (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if dev_path_pattern.search(text) or pythoncore_pattern.search(text):
            offenders.append(relative_path)

    assert not offenders, (
        "Developer-machine absolute paths must never be committed. Found in: "
        + ", ".join(offenders)
    )


def test_installer_ships_bundled_runtime_not_dev_venv() -> None:
    source = read("installer/SecretGroundsSetup.iss")
    assert 'Source: "..\\runtime\\python\\*"; DestDir: "{app}\\runtime\\python"' in source
    assert "..\\.venv" not in source


def test_backend_scripts_use_bundled_runtime_not_dev_venv() -> None:
    for relative_path in [
        "scripts/start-backend.ps1",
        "scripts/check-deployment.ps1",
        "scripts/install-backend-firewall-rule.ps1",
        "scripts/build-windows-installer.ps1",
    ]:
        source = read(relative_path)
        assert "runtime\\python\\python.exe" in source
        assert ".venv\\Scripts\\python.exe" not in source


def test_installer_restart_call_does_not_open_browser_mid_install() -> None:
    # Passing no -NoBrowser here meant start-secret-grounds.ps1 tried to open
    # a browser from inside a hidden (SW_HIDE), elevated Exec() call before
    # the wizard had even finished. If that Start-Process call threw (e.g. no
    # default browser handler reachable from that context), the exception
    # tore down an already-healthy backend/dashboard and the installer
    # reported a misleading "backend could not start". The dashboard is
    # opened for real, once, by the postinstall [Run] entry below.
    source = read("installer/SecretGroundsSetup.iss")
    assert "RunCashierScript('restart-secret-grounds.ps1', '-NoBrowser')" in source


def test_start_backend_logs_command_line_and_failure_diagnostics() -> None:
    source = read("scripts/start-backend.ps1")
    assert "$commandLine" in source
    assert "Backend command line:" in source
    assert "Write-SgFailureDiagnostics" in source
    assert "-ExitCode $capturedExitCode" in source
    assert "-FallbackLogPath $serverLog" in source


def test_start_secret_grounds_treats_browser_open_as_non_fatal() -> None:
    # A failed browser launch must never be treated the same as a failed
    # backend/dashboard start -- the stack is already confirmed healthy by
    # the time the browser is opened.
    source = read("scripts/start-secret-grounds.ps1")
    assert "Could not open the dashboard browser window" in source
    browser_block = source[source.index("if ($openBrowser)"):source.index("Write-SgLine \"Secret Grounds cashier stack is running")]
    assert "try {" in browser_block
    assert "catch {" in browser_block


def test_write_sg_failure_diagnostics_tolerates_locked_log() -> None:
    # The target error log can still be held open by a live process's ">>"
    # redirect. The diagnostics writer must use a share-mode that does not
    # collide with that handle, and must never silently drop the diagnostic
    # if writing still fails after retrying.
    source = read("scripts/secret-grounds-common.ps1")
    assert "[System.IO.FileShare]::ReadWrite" in source
    assert "FallbackLogPath" in source


def test_installer_ships_bundled_node_runtime_not_system_node() -> None:
    # A cashier PC must never be required to have Node.js/npm installed. The
    # dashboard's Next.js standalone build (output: "standalone" in
    # dashboard/next.config.ts) is fully self-contained -- it must ship with
    # its own bundled node.exe (scripts/prepare-dashboard-runtime.ps1) rather
    # than the full dashboard\node_modules tree plus a system/PATH node.
    source = read("installer/SecretGroundsSetup.iss")
    assert 'Source: "..\\runtime\\node\\*"; DestDir: "{app}\\runtime\\node"' in source
    assert 'Source: "..\\dashboard\\.next\\standalone\\*"' in source
    assert "..\\dashboard\\node_modules" not in source
    assert "..\\dashboard\\package.json" not in source


def test_dashboard_scripts_never_shell_out_to_system_node() -> None:
    for relative_path in [
        "scripts/start-dashboard.ps1",
        "scripts/build-windows-installer.ps1",
        "scripts/check-deployment.ps1",
    ]:
        source = read(relative_path)
        assert "runtime\\node\\node.exe" in source
        assert "Resolve-SgNodePath" not in source
        assert "next\\dist\\bin\\next" not in source

    common = read("scripts/secret-grounds-common.ps1")
    assert "Resolve-SgNodePath" not in common
    assert "Get-SgCommandSource" not in common


def test_dashboard_builds_self_contained_standalone_output() -> None:
    config = read("dashboard/next.config.ts")
    assert 'output: "standalone"' in config

    build_script = read("scripts/build-dashboard.ps1")
    assert ".next\\standalone" in build_script
    assert "server.js" in build_script
    # public/ and .next/static are not copied into .next/standalone by `next
    # build` itself -- the build script must do it, or the shipped payload
    # would be missing static assets on a clean install.
    assert ".next\\static" in build_script


def test_client_spec_bundles_logo_asset() -> None:
    # The lock screen loads client/assets/secret-grounds-logo.png at runtime
    # (client/utils.py get_logo_path()); if the .spec doesn't list it as a
    # PyInstaller data file, the packaged exe silently falls back to the "SG"
    # text mark with no build-time signal that the real logo went missing.
    spec = read("SecretGroundsClient.spec")
    assert "secret-grounds-logo.png" in spec
    assert "'assets'" in spec

    logo_path = PROJECT_ROOT / "client" / "assets" / "secret-grounds-logo.png"
    assert logo_path.is_file(), f"Logo asset is missing from the repo: {logo_path}"
    assert logo_path.stat().st_size > 0

    # The .ico is the taskbar / Alt-Tab / title-bar icon: set on the EXE and
    # bundled for tk_runtime._apply_secret_grounds_icon()'s iconbitmap call.
    assert "secret-grounds-logo.ico" in spec
    assert "icon='client\\\\assets\\\\secret-grounds-logo.ico'" in spec
    ico_path = PROJECT_ROOT / "client" / "assets" / "secret-grounds-logo.ico"
    assert ico_path.is_file(), f"Logo .ico is missing from the repo: {ico_path}"
    assert ico_path.stat().st_size > 0


def test_client_executable_is_windowed_for_silent_startup() -> None:
    spec = read("SecretGroundsClient.spec")
    assert "console=False" in spec
    assert "console=True" not in spec


def test_uninstall_stops_all_processes_before_removing_files() -> None:
    # A clean uninstall test found SecretGroundsClient.exe and node.exe still
    # running afterward, the client still connected, and {app} left behind
    # because Windows can't delete a file a running process still has open.
    # The root cause: [UninstallRun] only ever stopped the cashier
    # backend/dashboard (stop-secret-grounds.ps1) -- nothing ever stopped the
    # client, and nothing verified beyond the PID-tracking JSON file (which
    # can go stale) that everything had actually exited.
    iss_source = read("installer/SecretGroundsSetup.iss")
    assert "stop-all-for-uninstall.ps1" in iss_source

    stop_script = read("scripts/stop-all-for-uninstall.ps1")
    assert 'Stop-SgStartedProcess "client"' in stop_script
    assert 'Stop-SgStartedProcess "dashboard"' in stop_script
    assert 'Stop-SgStartedProcess "backend"' in stop_script
    # Must not rely solely on the PID-tracking file: verify by executable
    # path so a stale/missing record can't leave a process behind.
    assert "SecretGroundsClient.exe" in stop_script
    assert "runtime\\python\\python.exe" in stop_script
    assert "runtime\\node\\node.exe" in stop_script
    assert "$TimeoutSeconds = 10" in stop_script
    assert "-Force" in stop_script
    assert "throw" in stop_script


def test_uninstall_gate_runs_before_any_file_removal() -> None:
    # InitializeUninstall runs before the uninstall wizard/confirmation even
    # appears, and can abort the whole uninstall (Result := False) -- unlike
    # [UninstallRun] entries, which cannot stop Inno from proceeding to
    # delete files even if the script inside them fails.
    iss_source = read("installer/SecretGroundsSetup.iss")
    assert "function InitializeUninstall: Boolean;" in iss_source
    init_uninstall_block = iss_source[
        iss_source.index("function InitializeUninstall"):iss_source.index(
            "procedure CurUninstallStepChanged"
        )
    ]
    assert "stop-all-for-uninstall.ps1" in init_uninstall_block
    assert "Result := False" in init_uninstall_block
    assert "ewWaitUntilTerminated" in init_uninstall_block
    # A blocked stop must be reported to the person running the uninstaller,
    # not fail silently.
    assert "MsgBox(" in init_uninstall_block
    assert "could not stop all running processes" in init_uninstall_block.lower()

    # InitializeUninstall must come before CurUninstallStepChanged (and
    # therefore before file removal, which Inno performs after both) in the
    # script's own execution order.
    assert iss_source.index("function InitializeUninstall") < iss_source.index(
        "procedure CurUninstallStepChanged"
    )


def test_uninstall_still_removes_both_scheduled_tasks() -> None:
    iss_source = read("installer/SecretGroundsSetup.iss")
    uninstall_run_block = iss_source[
        iss_source.index("[UninstallRun]"):iss_source.index("[Code]")
    ]
    assert "remove-cashier-autostart.ps1" in uninstall_run_block
    assert "remove-client-autostart.ps1" in uninstall_run_block
    # The old cashier-only stop script is superseded by the comprehensive
    # one that also covers the client -- it must not still be wired in here,
    # or the original bug (client left running) reappears for client-role
    # installs, which stop-secret-grounds.ps1 never touched.
    assert "stop-secret-grounds.ps1" not in uninstall_run_block
    assert "stop-all-for-uninstall.ps1" in uninstall_run_block


def test_uninstall_stop_script_is_shipped_for_both_roles() -> None:
    # Both the cashier and the client role need to be able to stop
    # themselves cleanly at uninstall time, so this file must not be gated
    # behind Check: IsCashier / Check: IsClient like most other scripts are.
    iss_source = read("installer/SecretGroundsSetup.iss")
    files_block = iss_source[iss_source.index("[Files]"):iss_source.index("[Icons]")]
    line = next(
        line for line in files_block.splitlines() if "stop-all-for-uninstall.ps1" in line
    )
    assert "Check: IsCashier" not in line
    assert "Check: IsClient" not in line


def test_installer_starts_client_immediately_after_online_install() -> None:
    # Root cause: the installer registered the "Secret Grounds Client"
    # scheduled task (when "Start automatically with Windows" was checked)
    # but never triggered it -- the task's only trigger is AtLogOn, so on a
    # fresh install nothing actually started the client until the next
    # reboot or sign-in. The task and its action were themselves healthy the
    # whole time: `Start-ScheduledTask -TaskName "Secret Grounds Client"`
    # immediately showed the lock screen and brought the station online when
    # run manually. RunClientPostInstall closes that gap by using the same
    # task as the launch mechanism right after install.
    iss_source = read("installer/SecretGroundsSetup.iss")
    assert "procedure RunClientPostInstall;" in iss_source
    assert "if SelectedRole <> 'client' then Exit;" in iss_source

    client_post_install_block = iss_source[
        iss_source.index("procedure RunClientPostInstall"):iss_source.index("procedure CurStepChanged")
    ]
    assert "start-client-after-install.ps1" in client_post_install_block
    # Offline preparation must never trigger a live connection attempt.
    assert "if not (IsClientOnline or IsClientPreserve) then Exit;" in client_post_install_block
    assert "could not be started automatically" in client_post_install_block.lower()
    assert "ClientLogPathMessage" in client_post_install_block
    assert "function ClientLogPathMessage: String;" in iss_source
    assert "{app}\\logs\\client.log" in iss_source

    assert "RunClientPostInstall;" in iss_source[iss_source.index("procedure CurStepChanged"):]

    # -KeepAutostart must be a plain presence/absence switch (same
    # documented reasoning as CashierAutostartArgValue), not a boolean
    # literal that a new external process cannot reliably parse.
    assert "function ClientAutostartKeepArgValue: String;" in iss_source
    assert "'-KeepAutostart'" in iss_source
    assert "'$true'" not in iss_source
    assert "'$false'" not in iss_source

    files_block = iss_source[iss_source.index("[Files]"):iss_source.index("[Icons]")]
    assert "start-client-after-install.ps1" in files_block


def test_client_post_install_script_uses_scheduled_task_as_launch_mechanism() -> None:
    script = read("scripts/start-client-after-install.ps1")
    # Preferred launch path per the fix: the already-installed scheduled
    # task, not a second/duplicate way of starting the client.
    assert 'Start-ScheduledTask -TaskName $taskName' in script
    assert 'taskName = "Secret Grounds Client"' in script
    # start-client.ps1 is only mentioned in an explanatory comment (the
    # scheduled task's own action already runs it) -- this script itself
    # must never invoke it directly as a second way of starting the client.
    assert '& "$PSScriptRoot\\start-client.ps1"' not in script

    # Must not create a second process if one is already running.
    assert "Get-SgRunningClientProcess" in script
    already_running_block = script[
        script.index("$alreadyRunning = "):script.index("# The checkbox-gated")
    ]
    assert "exit 0" in already_running_block
    assert "Start-ScheduledTask" not in already_running_block

    # Bounded wait-and-verify, not a fire-and-forget launch.
    assert "$TimeoutSeconds = 10" in script
    assert "Get-SgRunningClientProcess" in script[script.index("Start-ScheduledTask -TaskName $taskName"):]
    assert "exit 1" in script

    # KeepAutostart controls whether the task is left registered afterward.
    assert "[switch]$KeepAutostart" in script
    assert "remove-client-autostart.ps1" in script


def main() -> None:
    tests = [
        test_inno_installer_has_role_selection,
        test_installer_separates_cashier_and_client_payloads,
        test_installer_preserves_cashier_data_by_default,
        test_installer_client_validation_is_present,
        test_installer_supports_fixed_and_diskless_customer_modes,
        test_configure_client_supports_diskless_switch,
        test_installer_detects_and_preserves_existing_client_config,
        test_installer_aborts_on_invalid_existing_client_config,
        test_installer_does_not_expand_app_before_it_is_initialized,
        test_configure_client_supports_explicit_modes,
        test_client_config_tool_writes_atomically_and_backs_up,
        test_release_candidate_is_rc2_and_rc1_flagged_obsolete,
        test_cashier_config_generator_uses_secure_secret_and_production_ports,
        test_client_launcher_prefers_packaged_executable,
        test_installer_automates_cashier_initialization_and_startup,
        test_installer_offers_bounded_retry_on_startup_failure,
        test_installer_opens_dashboard_only_after_health_succeeds,
        test_installer_cashier_autostart_checkbox_defaults_and_preserves_choice,
        test_installer_does_not_pass_boolean_literals_to_external_powershell,
        test_installer_dashboard_shortcuts_ensure_services_running,
        test_installer_cashier_role_safety,
        test_no_developer_specific_absolute_paths_are_tracked,
        test_installer_ships_bundled_runtime_not_dev_venv,
        test_backend_scripts_use_bundled_runtime_not_dev_venv,
        test_installer_restart_call_does_not_open_browser_mid_install,
        test_start_backend_logs_command_line_and_failure_diagnostics,
        test_start_secret_grounds_treats_browser_open_as_non_fatal,
        test_write_sg_failure_diagnostics_tolerates_locked_log,
        test_installer_ships_bundled_node_runtime_not_system_node,
        test_dashboard_scripts_never_shell_out_to_system_node,
        test_dashboard_builds_self_contained_standalone_output,
        test_client_spec_bundles_logo_asset,
        test_client_executable_is_windowed_for_silent_startup,
        test_uninstall_stops_all_processes_before_removing_files,
        test_uninstall_gate_runs_before_any_file_removal,
        test_uninstall_still_removes_both_scheduled_tasks,
        test_uninstall_stop_script_is_shipped_for_both_roles,
        test_installer_starts_client_immediately_after_online_install,
        test_client_post_install_script_uses_scheduled_task_as_launch_mechanism,
    ]
    for test in tests:
        test()
    print("Installer smoke tests passed.")


if __name__ == "__main__":
    main()
