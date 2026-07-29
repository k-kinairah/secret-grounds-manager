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
        test_cashier_config_generator_uses_secure_secret_and_production_ports,
        test_client_launcher_prefers_packaged_executable,
    ]
    for test in tests:
        test()
    print("Installer smoke tests passed.")


if __name__ == "__main__":
    main()
