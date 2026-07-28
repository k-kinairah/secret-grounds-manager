import json
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from client.client import SecretGroundsClient
from client.config import ClientConfigError, load_client_config, save_client_config
from client.setup_wizard import build_setup_payload


def test_missing_config_requires_setup(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    try:
        load_client_config(config_path)
    except ClientConfigError as error:
        assert "was not found" in str(error)
    else:
        raise AssertionError("Missing config should require first-run setup")


def test_save_config_generates_json(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    payload = build_setup_payload("cashier-server", "18001", "PC-01", "Front Row 01", True)
    config = save_client_config(payload, config_path)

    assert config_path.exists()
    assert config.server_host == "cashier-server"
    assert config.server_port == 18001
    assert config.pc_name == "PC-01"
    assert config.display_name == "Front Row 01"
    assert config.auto_connect is True
    assert config.reconnect_interval_seconds == 5
    assert config.request_timeout_seconds == 5
    assert config.server_url == "http://cashier-server:18001"
    assert config.websocket_base_url == "ws://cashier-server:18001"
    assert json.loads(config_path.read_text(encoding="utf-8")) == {
        "server_host": "cashier-server",
        "server_port": 18001,
        "pc_name": "PC-01",
        "display_name": "Front Row 01",
        "auto_connect": True,
        "reconnect_interval_seconds": 5,
        "request_timeout_seconds": 5,
    }


def test_valid_config_builds_client_urls(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_host": "192.168.0.109",
                "server_port": 18001,
                "pc_name": "PC-01",
                "display_name": "Front Row 01",
                "auto_connect": True,
                "reconnect_interval_seconds": 7,
                "request_timeout_seconds": 6,
            }
        ),
        encoding="utf-8",
    )

    config = load_client_config(config_path)
    assert config.display_name == "Front Row 01"
    assert config.reconnect_interval_seconds == 7
    assert config.request_timeout_seconds == 6
    client = SecretGroundsClient(
        server_url=config.server_url,
        pc_name=config.pc_name,
        heartbeat_interval=5,
        request_timeout=1,
        display_name=config.display_name,
        enable_lock_screen=False,
    )

    try:
        assert client.server_url == "http://192.168.0.109:18001"
        assert client.display_name == "Front Row 01"
        assert client.build_websocket_url() == "ws://192.168.0.109:18001/ws/PC-01"
    finally:
        client.stop()


def test_invalid_config_reports_friendly_error(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_host": "http://192.168.1.15",
                "server_port": 18001,
                "pc_name": "PC-01",
                "display_name": "Front Row 01",
                "auto_connect": True,
            }
        ),
        encoding="utf-8",
    )

    try:
        load_client_config(config_path)
    except ClientConfigError as error:
        assert "server_host" in str(error)
        assert "192.168.1.15" in str(error)
    else:
        raise AssertionError("Invalid server_host should fail validation")


def test_setup_payload_validates_port_and_pc_name(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    try:
        save_client_config(build_setup_payload("cashier-server", "70000", "PC-01", "Front Row 01", True), config_path)
    except ClientConfigError as error:
        assert "Server Port" in str(error)
    else:
        raise AssertionError("Invalid port should fail validation")

    try:
        save_client_config(build_setup_payload("192.168.0.109", "18001", " ", "Front Row 01", True), config_path)
    except ClientConfigError as error:
        assert "Station ID" in str(error)
    else:
        raise AssertionError("Blank PC name should fail validation")


def test_server_url_environment_override(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_host": "127.0.0.1",
                "server_port": 8000,
                "pc_name": "PC-01",
                "display_name": "Front Row 01",
                "auto_connect": True,
            }
        ),
        encoding="utf-8",
    )
    old_value = __import__("os").environ.get("SECRET_GROUNDS_SERVER_URL")
    __import__("os").environ["SECRET_GROUNDS_SERVER_URL"] = "http://192.168.0.109:18001"
    try:
        config = load_client_config(config_path)
    finally:
        if old_value is None:
            __import__("os").environ.pop("SECRET_GROUNDS_SERVER_URL", None)
        else:
            __import__("os").environ["SECRET_GROUNDS_SERVER_URL"] = old_value

    assert config.server_url == "http://192.168.0.109:18001"
    assert config.websocket_base_url == "ws://192.168.0.109:18001"


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_missing_config_requires_setup(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_save_config_generates_json(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_valid_config_builds_client_urls(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_invalid_config_reports_friendly_error(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_setup_payload_validates_port_and_pc_name(temp_directory)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_server_url_environment_override(temp_directory)
    print("Client config smoke tests passed.")


if __name__ == "__main__":
    main()
