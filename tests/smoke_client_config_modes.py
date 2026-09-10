import json
import subprocess
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIGURE_SCRIPT = PROJECT_ROOT / "scripts" / "configure-client.ps1"
READ_STATUS_SCRIPT = PROJECT_ROOT / "scripts" / "read-client-config-status.ps1"


def run_powershell(script_path: Path, args: list[str], cwd: str) -> subprocess.CompletedProcess:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    ] + args
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=60)


def run_configure(cwd: str, config_path: Path, mode: str, **kwargs: str) -> subprocess.CompletedProcess:
    args = ["-Mode", mode, "-ClientConfigPath", str(config_path)]
    for key, value in kwargs.items():
        if value is True:
            args.append(f"-{key}")
        elif value is not False and value is not None:
            args.extend([f"-{key}", str(value)])
    return run_powershell(CONFIGURE_SCRIPT, args, cwd)


def run_read_status(cwd: str, config_path: Path) -> subprocess.CompletedProcess:
    return run_powershell(READ_STATUS_SCRIPT, ["-ConfigPath", str(config_path)], cwd)


def parse_status_lines(stdout: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in stdout.splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value.strip()
    return result


def write_config(config_path: Path, payload: dict[str, object]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(payload), encoding="utf-8")


def read_config(config_path: Path) -> dict[str, object]:
    return json.loads(config_path.read_text(encoding="utf-8-sig"))


def backups_dir(config_path: Path) -> Path:
    return config_path.parent / "config-backups"


def test_fresh_config_creation(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    result = run_configure(
        temp_directory,
        config_path,
        "Fresh",
        ServerHost="192.168.1.50",
        ServerPort="18001",
        StationId="PC-01",
        DisplayName="Front Row 01",
        AllowOffline=True,
    )
    assert result.returncode == 0, result.stderr
    assert config_path.exists()
    saved = read_config(config_path)
    assert saved["server_host"] == "192.168.1.50"
    assert saved["pc_name"] == "PC-01"
    assert saved["display_name"] == "Front Row 01"
    assert not backups_dir(config_path).exists()


def test_diskless_config_creation_stores_no_station_id(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    result = run_configure(
        temp_directory,
        config_path,
        "Fresh",
        ServerHost="192.168.1.50",
        ServerPort="18001",
        Diskless=True,
        AllowOffline=True,
    )
    assert result.returncode == 0, result.stderr
    assert config_path.exists()
    saved = read_config(config_path)
    assert saved["pc_name"] == ""
    assert saved["display_name"] == ""
    assert saved["server_host"] == "192.168.1.50"
    assert saved["server_port"] == 18001
    assert saved["auto_connect"] is True
    assert saved["reconnect_interval_seconds"] == 5
    assert saved["request_timeout_seconds"] == 5
    assert not backups_dir(config_path).exists()


def test_adopt_hostname_config_writes_sentinel_pc_name(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    result = run_configure(
        temp_directory,
        config_path,
        "Fresh",
        ServerHost="192.168.1.50",
        ServerPort="18001",
        AdoptHostname=True,
        AllowOffline=True,
    )
    assert result.returncode == 0, result.stderr
    saved = read_config(config_path)
    assert saved["pc_name"] == "@hostname"
    assert saved["display_name"] == ""
    assert saved["server_host"] == "192.168.1.50"

    # The client-config reader must accept the sentinel as a valid config.
    status = parse_status_lines(run_read_status(temp_directory, config_path).stdout)
    assert status.get("STATUS") == "Ok", status
    assert status.get("PC_NAME") == "@hostname", status


def test_fixed_station_still_requires_station_id_without_diskless(temp_directory: str) -> None:
    # The diskless relaxation must not leak into the normal Fixed Station
    # path: a Fresh write with no Station ID still fails and writes nothing.
    config_path = Path(temp_directory) / "config.json"
    result = run_configure(
        temp_directory,
        config_path,
        "Fresh",
        ServerHost="192.168.1.50",
        ServerPort="18001",
        DisplayName="Front Row 01",
        AllowOffline=True,
    )
    assert result.returncode != 0
    assert not config_path.exists()


def test_read_status_accepts_diskless_config_as_ok(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    write_config(
        config_path,
        {
            "server_host": "192.168.1.50",
            "server_port": 18001,
            "pc_name": "",
            "display_name": "",
            "auto_connect": True,
            "reconnect_interval_seconds": 5,
            "request_timeout_seconds": 5,
        },
    )
    status = run_read_status(temp_directory, config_path)
    fields = parse_status_lines(status.stdout)
    assert fields.get("STATUS") == "Ok"
    assert fields.get("PC_NAME", "") == ""


def test_upgrade_preserves_existing_identity(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    original = {
        "server_host": "192.168.1.50",
        "server_port": 18001,
        "pc_name": "PC-07",
        "display_name": "Front Row 07",
        "auto_connect": True,
        "reconnect_interval_seconds": 5,
        "request_timeout_seconds": 5,
    }
    write_config(config_path, original)

    result = run_configure(temp_directory, config_path, "Preserve")
    assert result.returncode == 0, result.stderr
    assert read_config(config_path) == original
    assert not backups_dir(config_path).exists()


def test_unattended_preserve_needs_no_interactive_params(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    original = {
        "server_host": "192.168.1.50",
        "server_port": 18001,
        "pc_name": "PC-07",
        "display_name": "Front Row 07",
        "auto_connect": True,
        "reconnect_interval_seconds": 5,
        "request_timeout_seconds": 5,
    }
    write_config(config_path, original)

    result = run_configure(temp_directory, config_path, "Preserve")
    assert result.returncode == 0, result.stderr
    assert "Preserved existing client configuration" in result.stdout
    assert read_config(config_path) == original


def test_explicit_update_changes_requested_fields(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    write_config(
        config_path,
        {
            "server_host": "192.168.1.50",
            "server_port": 18001,
            "pc_name": "PC-07",
            "display_name": "Front Row 07",
            "auto_connect": True,
            "reconnect_interval_seconds": 5,
            "request_timeout_seconds": 5,
        },
    )

    result = run_configure(
        temp_directory,
        config_path,
        "Update",
        ServerHost="192.168.1.99",
        ServerPort="18001",
        StationId="PC-07",
        DisplayName="Front Row 07 Renamed",
        AllowOffline=True,
    )
    assert result.returncode == 0, result.stderr
    updated = read_config(config_path)
    assert updated["server_host"] == "192.168.1.99"
    assert updated["display_name"] == "Front Row 07 Renamed"


def test_existing_config_backed_up_before_replacement(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    original = {
        "server_host": "192.168.1.50",
        "server_port": 18001,
        "pc_name": "PC-07",
        "display_name": "Front Row 07",
        "auto_connect": True,
        "reconnect_interval_seconds": 5,
        "request_timeout_seconds": 5,
    }
    write_config(config_path, original)

    result = run_configure(
        temp_directory,
        config_path,
        "Update",
        ServerHost="192.168.1.100",
        ServerPort="18001",
        StationId="PC-07",
        DisplayName="Front Row 07",
        AllowOffline=True,
    )
    assert result.returncode == 0, result.stderr

    backup_files = list(backups_dir(config_path).glob("config-*.json"))
    assert len(backup_files) == 1
    assert json.loads(backup_files[0].read_text(encoding="utf-8")) == original


def test_update_with_unchanged_values_skips_backup(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    original = {
        "server_host": "192.168.1.50",
        "server_port": 18001,
        "pc_name": "PC-07",
        "display_name": "Front Row 07",
        "auto_connect": True,
        "reconnect_interval_seconds": 5,
        "request_timeout_seconds": 5,
    }
    write_config(config_path, original)

    result = run_configure(
        temp_directory,
        config_path,
        "Update",
        ServerHost="192.168.1.50",
        ServerPort="18001",
        StationId="PC-07",
        DisplayName="Front Row 07",
        AllowOffline=True,
    )
    assert result.returncode == 0, result.stderr
    assert not backups_dir(config_path).exists()
    assert read_config(config_path) == original


def test_invalid_existing_config_is_not_silently_overwritten(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{ this is not valid json", encoding="utf-8")

    result = run_configure(temp_directory, config_path, "Preserve")
    assert result.returncode != 0

    assert config_path.read_text(encoding="utf-8") == "{ this is not valid json"

    backup_files = list(backups_dir(config_path).glob("config-*.json"))
    assert len(backup_files) == 1
    assert backup_files[0].read_text(encoding="utf-8") == "{ this is not valid json"


def test_invalid_existing_config_blocks_update(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("{ still not valid json", encoding="utf-8")

    result = run_configure(
        temp_directory,
        config_path,
        "Update",
        ServerHost="192.168.1.50",
        ServerPort="18001",
        StationId="PC-07",
        DisplayName="Front Row 07",
        AllowOffline=True,
    )
    assert result.returncode != 0
    assert config_path.read_text(encoding="utf-8") == "{ still not valid json"


def test_failed_write_leaves_original_config_intact(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    original = {
        "server_host": "192.168.1.50",
        "server_port": 18001,
        "pc_name": "PC-07",
        "display_name": "Front Row 07",
        "auto_connect": True,
        "reconnect_interval_seconds": 5,
        "request_timeout_seconds": 5,
    }
    write_config(config_path, original)

    result = run_configure(
        temp_directory,
        config_path,
        "Update",
        ServerHost="192.168.1.100",
        ServerPort="999999",
        StationId="PC-07",
        DisplayName="Front Row 07",
        AllowOffline=True,
    )
    assert result.returncode != 0
    assert read_config(config_path) == original
    assert not backups_dir(config_path).exists()


def test_preserve_requires_existing_file(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    result = run_configure(temp_directory, config_path, "Preserve")
    assert result.returncode != 0
    assert not config_path.exists()


def test_read_status_reports_ok_notfound_invalid(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"

    missing = run_read_status(temp_directory, config_path)
    assert parse_status_lines(missing.stdout).get("STATUS") == "NotFound"

    write_config(
        config_path,
        {
            "server_host": "192.168.1.50",
            "server_port": 18001,
            "pc_name": "PC-07",
            "display_name": "Front Row 07",
            "auto_connect": True,
            "reconnect_interval_seconds": 5,
            "request_timeout_seconds": 5,
        },
    )
    ok = run_read_status(temp_directory, config_path)
    fields = parse_status_lines(ok.stdout)
    assert fields.get("STATUS") == "Ok"
    assert fields.get("PC_NAME") == "PC-07"
    assert fields.get("SERVER_HOST") == "192.168.1.50"

    config_path.write_text("{ broken", encoding="utf-8")
    invalid = run_read_status(temp_directory, config_path)
    invalid_fields = parse_status_lines(invalid.stdout)
    assert invalid_fields.get("STATUS") == "Invalid"
    assert "BACKUP_PATH" in invalid_fields
    assert Path(invalid_fields["BACKUP_PATH"]).exists()


def main() -> None:
    tests = [
        test_fresh_config_creation,
        test_diskless_config_creation_stores_no_station_id,
        test_adopt_hostname_config_writes_sentinel_pc_name,
        test_fixed_station_still_requires_station_id_without_diskless,
        test_read_status_accepts_diskless_config_as_ok,
        test_upgrade_preserves_existing_identity,
        test_unattended_preserve_needs_no_interactive_params,
        test_explicit_update_changes_requested_fields,
        test_existing_config_backed_up_before_replacement,
        test_update_with_unchanged_values_skips_backup,
        test_invalid_existing_config_is_not_silently_overwritten,
        test_invalid_existing_config_blocks_update,
        test_failed_write_leaves_original_config_intact,
        test_preserve_requires_existing_file,
        test_read_status_reports_ok_notfound_invalid,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
    print("Client config mode smoke tests passed.")


if __name__ == "__main__":
    main()
