from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


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
    assert "ExpandConstant('{app}')" not in source
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


def test_client_launcher_prefers_packaged_executable() -> None:
    source = read("scripts/start-client.ps1")
    assert "SecretGroundsClient.exe" in source
    assert "packagedClientExe" in source
    assert "-m\", \"client.client" in source


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
    ]
    for test in tests:
        test()
    print("Installer smoke tests passed.")


if __name__ == "__main__":
    main()
