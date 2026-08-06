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
    assert 'Source: "..\\dashboard\\.next\\*"' in source
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
    assert "Start Secret Grounds automatically when this computer signs in." in source
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


def main() -> None:
    tests = [
        test_inno_installer_has_role_selection,
        test_installer_separates_cashier_and_client_payloads,
        test_installer_preserves_cashier_data_by_default,
        test_installer_client_validation_is_present,
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
    ]
    for test in tests:
        test()
    print("Installer smoke tests passed.")


if __name__ == "__main__":
    main()
