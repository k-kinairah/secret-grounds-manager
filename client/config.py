import json
import os
import sys
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


DEFAULT_SERVER_HOST = "127.0.0.1"
DEFAULT_SERVER_PORT = 18001
DEFAULT_PC_NAME = "PC-01"
DEFAULT_DISPLAY_NAME = "Station 01"
DEFAULT_AUTO_CONNECT = True
DEFAULT_RECONNECT_INTERVAL_SECONDS = 5
DEFAULT_REQUEST_TIMEOUT_SECONDS = 5


def get_app_version() -> str:
    version_path = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        return version_path.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION = get_app_version()


class ClientConfigError(ValueError):
    """Raised when the runtime client configuration cannot be used."""


@dataclass(frozen=True)
class ClientConfig:
    server_host: str
    server_port: int
    pc_name: str
    display_name: str
    auto_connect: bool
    reconnect_interval_seconds: int
    request_timeout_seconds: int
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
        "display_name": DEFAULT_DISPLAY_NAME,
        "auto_connect": DEFAULT_AUTO_CONNECT,
        "reconnect_interval_seconds": DEFAULT_RECONNECT_INTERVAL_SECONDS,
        "request_timeout_seconds": DEFAULT_REQUEST_TIMEOUT_SECONDS,
    }


def create_default_config(config_path: Path) -> None:
    save_client_config(default_config_payload(), config_path)


def save_client_config(
    payload: dict[str, object],
    config_path: Path | None = None,
    require_pc_name: bool = True,
) -> ClientConfig:
    # The interactive setup wizard is the "explicit/development mode" entry
    # point -- a human filling out this form is choosing a specific station,
    # so it keeps requiring a Station ID by default. A generic/automatic-
    # mode config.json is never produced by this path.
    resolved_path = config_path or get_client_config_path()
    config = validate_client_config(payload, resolved_path, require_pc_name=require_pc_name)
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
                "display_name": config.display_name,
                "auto_connect": config.auto_connect,
                "reconnect_interval_seconds": config.reconnect_interval_seconds,
                "request_timeout_seconds": config.request_timeout_seconds,
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
        with resolved_path.open("r", encoding="utf-8-sig") as config_file:
            payload = json.load(config_file)
    except json.JSONDecodeError as error:
        raise ClientConfigError(
            f"config.json is not valid JSON: {error.msg} at line {error.lineno}, "
            f"column {error.colno}."
        ) from error
    except OSError as error:
        raise ClientConfigError(f"Could not read config.json: {error}") from error

    # Loading (unlike the wizard's save path above) must accept a blank/
    # absent pc_name: that is exactly what marks a config as generic/
    # automatic-mode, resolved later from this PC's wired NIC MAC.
    config = validate_client_config(payload, resolved_path, require_pc_name=False)
    return apply_server_url_override(config)


def apply_server_url_override(config: ClientConfig) -> ClientConfig:
    override = os.getenv("SECRET_GROUNDS_SERVER_URL", "").strip()
    if not override:
        return config

    parsed = urlparse(override)
    if parsed.scheme != "http" or not parsed.hostname or parsed.path not in {"", "/"}:
        raise ClientConfigError(
            "SECRET_GROUNDS_SERVER_URL must be an http URL with host and optional port, "
            "for example http://192.168.0.109:18001."
        )
    if parsed.username or parsed.password or parsed.params or parsed.query or parsed.fragment:
        raise ClientConfigError("SECRET_GROUNDS_SERVER_URL must not include credentials, query, or fragment.")
    try:
        parsed_address = ip_address(parsed.hostname)
    except ValueError as error:
        if not is_valid_hostname(parsed.hostname):
            raise ClientConfigError("SECRET_GROUNDS_SERVER_URL host must be a valid IPv4 address or hostname.") from error
    else:
        if parsed_address.version != 4:
            raise ClientConfigError("SECRET_GROUNDS_SERVER_URL host must be a valid IPv4 address or hostname.")

    return ClientConfig(
        server_host=parsed.hostname,
        server_port=parsed.port or DEFAULT_SERVER_PORT,
        pc_name=config.pc_name,
        display_name=config.display_name,
        auto_connect=config.auto_connect,
        reconnect_interval_seconds=config.reconnect_interval_seconds,
        request_timeout_seconds=config.request_timeout_seconds,
        config_path=config.config_path,
    )


def validate_client_config(payload: Any, config_path: Path, require_pc_name: bool = False) -> ClientConfig:
    if not isinstance(payload, dict):
        raise ClientConfigError("config.json must contain a JSON object.")

    # pc_name is deliberately not in required_fields: a generic/automatic-
    # mode config validly omits it entirely. require_pc_name gates the
    # separate "blank Station ID" check below for callers (the setup
    # wizard) that still need to force a human to choose one.
    required_fields = {"server_host", "server_port", "auto_connect"}
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
            "server_host must be only the server IP address or hostname, "
            "for example 192.168.1.15 or cashier-pc."
        )
    try:
        parsed_address = ip_address(server_host)
    except ValueError as error:
        if not is_valid_hostname(server_host):
            raise ClientConfigError(
                "Server Host must be a valid IPv4 address or hostname."
            ) from error
    else:
        if parsed_address.version != 4:
            raise ClientConfigError("Server Host must be a valid IPv4 address or hostname.")

    server_port = payload["server_port"]
    if not isinstance(server_port, int) or isinstance(server_port, bool):
        raise ClientConfigError("Server Port must be a number.")
    if server_port < 1 or server_port > 65535:
        raise ClientConfigError("Server Port must be between 1 and 65535.")

    raw_pc_name = payload.get("pc_name", "")
    if not isinstance(raw_pc_name, str):
        raise ClientConfigError("Station ID must be text.")
    pc_name = raw_pc_name.strip()
    if require_pc_name and not pc_name:
        raise ClientConfigError("Station ID is required.")
    # A non-blank Station ID still has to be well-formed; a blank one is
    # the automatic-mode sentinel and skips this check entirely -- it is
    # resolved later from this PC's wired NIC MAC, never validated as a
    # station-id string.
    if pc_name and not is_valid_station_id(pc_name):
        raise ClientConfigError("Station ID may contain only letters, numbers, dash, and underscore.")

    raw_display_name = payload.get("display_name")
    if raw_display_name is None:
        display_name = pc_name
    else:
        if not isinstance(raw_display_name, str) or not raw_display_name.strip():
            raise ClientConfigError("Display Name is required.")
        display_name = " ".join(raw_display_name.strip().split())

    auto_connect = payload["auto_connect"]
    if not isinstance(auto_connect, bool):
        raise ClientConfigError("auto_connect must be true or false.")

    reconnect_interval_seconds = payload.get("reconnect_interval_seconds", DEFAULT_RECONNECT_INTERVAL_SECONDS)
    if not isinstance(reconnect_interval_seconds, int) or isinstance(reconnect_interval_seconds, bool):
        raise ClientConfigError("reconnect_interval_seconds must be a number.")
    if reconnect_interval_seconds < 2 or reconnect_interval_seconds > 300:
        raise ClientConfigError("reconnect_interval_seconds must be between 2 and 300.")

    request_timeout_seconds = payload.get("request_timeout_seconds", DEFAULT_REQUEST_TIMEOUT_SECONDS)
    if not isinstance(request_timeout_seconds, int) or isinstance(request_timeout_seconds, bool):
        raise ClientConfigError("request_timeout_seconds must be a number.")
    if request_timeout_seconds < 2 or request_timeout_seconds > 60:
        raise ClientConfigError("request_timeout_seconds must be between 2 and 60.")

    return ClientConfig(
        server_host=server_host,
        server_port=server_port,
        pc_name=pc_name,
        display_name=display_name,
        auto_connect=auto_connect,
        reconnect_interval_seconds=reconnect_interval_seconds,
        request_timeout_seconds=request_timeout_seconds,
        config_path=config_path,
    )


def is_valid_hostname(value: str) -> bool:
    if len(value) > 253:
        return False
    labels = value.rstrip(".").split(".")
    return all(
        label
        and len(label) <= 63
        and not label.startswith("-")
        and not label.endswith("-")
        and all(character.isalnum() or character == "-" for character in label)
        for label in labels
    )


def is_valid_station_id(value: str) -> bool:
    return bool(value) and all(character.isalnum() or character in {"-", "_"} for character in value)


HEARTBEAT_INTERVAL_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 5
ENABLE_LOCK_SCREEN = read_bool_config("ENABLE_LOCK_SCREEN", True)
ENABLE_DEV_ESCAPE = read_bool_config("ENABLE_DEV_ESCAPE", True)
ENABLE_CUSTOMER_OVERLAY = read_bool_config("ENABLE_CUSTOMER_OVERLAY", True)
CUSTOMER_OVERLAY_POSITION = os.getenv("CUSTOMER_OVERLAY_POSITION", "bottom-right")
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
