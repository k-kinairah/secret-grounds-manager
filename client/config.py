import json
import os
import sys
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any


DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 8000
DEFAULT_PC_NAME = "PC-01"
DEFAULT_AUTO_CONNECT = True


class ClientConfigError(ValueError):
    """Raised when the runtime client configuration cannot be used."""


@dataclass(frozen=True)
class ClientConfig:
    server_host: str
    server_port: int
    pc_name: str
    auto_connect: bool
    config_path: Path

    @property
    def server_url(self) -> str:
        return f"http://{self.server_host}:{self.server_port}"

    @property
    def websocket_base_url(self) -> str:
        return f"ws://{self.server_host}:{self.server_port}"


def read_bool_config(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def get_client_config_path() -> Path:
    """Return config.json beside the packaged executable, or the client package in dev."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name("config.json")

    return Path(__file__).resolve().with_name("config.json")


def default_config_payload() -> dict[str, object]:
    return {
        "server_host": DEFAULT_SERVER_HOST,
        "server_port": DEFAULT_SERVER_PORT,
        "pc_name": DEFAULT_PC_NAME,
        "auto_connect": DEFAULT_AUTO_CONNECT,
    }


def create_default_config(config_path: Path) -> None:
    save_client_config(default_config_payload(), config_path)


def save_client_config(payload: dict[str, object], config_path: Path | None = None) -> ClientConfig:
    resolved_path = config_path or get_client_config_path()
    config = validate_client_config(payload, resolved_path)
    write_client_config(config, resolved_path)
    return config


def write_client_config(config: ClientConfig, config_path: Path | None = None) -> None:
    resolved_path = config_path or config.config_path
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    with resolved_path.open("w", encoding="utf-8") as config_file:
        json.dump(
            {
                "server_host": config.server_host,
                "server_port": config.server_port,
                "pc_name": config.pc_name,
                "auto_connect": config.auto_connect,
            },
            config_file,
            indent=2,
        )
        config_file.write("\n")


def load_client_config(config_path: Path | None = None) -> ClientConfig:
    resolved_path = config_path or get_client_config_path()
    if not resolved_path.exists():
        raise ClientConfigError(f"config.json was not found at {resolved_path}.")

    try:
        with resolved_path.open("r", encoding="utf-8") as config_file:
            payload = json.load(config_file)
    except json.JSONDecodeError as error:
        raise ClientConfigError(
            f"config.json is not valid JSON: {error.msg} at line {error.lineno}, "
            f"column {error.colno}."
        ) from error
    except OSError as error:
        raise ClientConfigError(f"Could not read config.json: {error}") from error

    return validate_client_config(payload, resolved_path)


def validate_client_config(payload: Any, config_path: Path) -> ClientConfig:
    if not isinstance(payload, dict):
        raise ClientConfigError("config.json must contain a JSON object.")

    required_fields = {"server_host", "server_port", "pc_name", "auto_connect"}
    missing_fields = sorted(required_fields.difference(payload))
    if missing_fields:
        raise ClientConfigError(
            "config.json is missing required field(s): "
            f"{', '.join(missing_fields)}."
        )

    server_host = payload["server_host"]
    if not isinstance(server_host, str) or not server_host.strip():
        raise ClientConfigError("Server IP is required.")
    server_host = server_host.strip()
    if "://" in server_host or "/" in server_host or ":" in server_host:
        raise ClientConfigError(
            "server_host must be only the server IP address, "
            "for example 192.168.1.15."
        )
    try:
        parsed_address = ip_address(server_host)
    except ValueError as error:
        raise ClientConfigError(
            "Server IP must be a valid IPv4 address."
        ) from error
    if parsed_address.version != 4:
        raise ClientConfigError("Server IP must be a valid IPv4 address.")

    server_port = payload["server_port"]
    if not isinstance(server_port, int) or isinstance(server_port, bool):
        raise ClientConfigError("Server Port must be a number.")
    if server_port < 1 or server_port > 65535:
        raise ClientConfigError("Server Port must be between 1 and 65535.")

    pc_name = payload["pc_name"]
    if not isinstance(pc_name, str) or not pc_name.strip():
        raise ClientConfigError("PC Name is required.")
    pc_name = pc_name.strip()

    auto_connect = payload["auto_connect"]
    if not isinstance(auto_connect, bool):
        raise ClientConfigError("auto_connect must be true or false.")

    return ClientConfig(
        server_host=server_host,
        server_port=server_port,
        pc_name=pc_name,
        auto_connect=auto_connect,
        config_path=config_path,
    )


HEARTBEAT_INTERVAL_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 5
ENABLE_LOCK_SCREEN = read_bool_config("ENABLE_LOCK_SCREEN", True)
ENABLE_DEV_ESCAPE = read_bool_config("ENABLE_DEV_ESCAPE", True)
ENABLE_CUSTOMER_OVERLAY = read_bool_config("ENABLE_CUSTOMER_OVERLAY", True)
CUSTOMER_OVERLAY_POSITION = os.getenv("CUSTOMER_OVERLAY_POSITION", "top-right")
SESSION_RECOVERY_PATH = os.getenv(
    "SECRET_GROUNDS_RECOVERY_PATH",
    "client_session_recovery.json",
)
EXPIRATION_GRACE_SECONDS = int(os.getenv("SECRET_GROUNDS_EXPIRATION_GRACE_SECONDS", "120"))
TEST_MODE = read_bool_config("SECRET_GROUNDS_TEST_MODE", False)
AUTOMATIC_SHUTDOWN_ON_EXPIRATION = read_bool_config(
    "SECRET_GROUNDS_AUTOMATIC_SHUTDOWN_ON_EXPIRATION",
    not TEST_MODE,
)
EXPIRATION_SOUND_ENABLED = read_bool_config("SECRET_GROUNDS_EXPIRATION_SOUND_ENABLED", True)
