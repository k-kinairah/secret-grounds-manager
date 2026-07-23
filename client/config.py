import os


def read_bool_config(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


SERVER_URL = "http://192.168.0.109:8000"
PC_NAME = "PC-01"
HEARTBEAT_INTERVAL_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 5
ENABLE_LOCK_SCREEN = read_bool_config("ENABLE_LOCK_SCREEN", True)
ENABLE_DEV_ESCAPE = read_bool_config("ENABLE_DEV_ESCAPE", True)
SESSION_RECOVERY_PATH = os.getenv(
    "SECRET_GROUNDS_RECOVERY_PATH",
    "client_session_recovery.json",
)
