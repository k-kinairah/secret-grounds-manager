"""Settings for the Secret Grounds MCP server, read from environment variables.

A `.env` file next to this package's parent folder (assistant/mcp_server/.env)
is loaded as a convenience, but real environment variables always win. The
file is gitignored -- copy .env.example to create it.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# The server only ever binds to loopback. This is deliberately not
# configurable: the MCP endpoint has no auth of its own in phase 1.
MCP_HOST = "127.0.0.1"

DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_MCP_PORT = 8765
DEFAULT_SHOP_TIMEZONE = "Asia/Manila"
DEFAULT_TIMEOUT_SECONDS = 10.0

ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def load_env_file(path: Path = ENV_FILE) -> None:
    """Minimal KEY=VALUE reader (no extra dependency). Existing environment
    variables are never overridden."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    backend_url: str
    token: str
    mcp_port: int
    shop_timezone: str
    timeout_seconds: float
    enable_actions: bool = False


def get_settings() -> Settings:
    port_text = os.getenv("SG_MCP_PORT", "").strip()
    timeout_text = os.getenv("SG_BACKEND_TIMEOUT_SECONDS", "").strip()
    return Settings(
        backend_url=(os.getenv("SG_BACKEND_URL", "").strip() or DEFAULT_BACKEND_URL).rstrip("/"),
        token=os.getenv("SG_ASSISTANT_TOKEN", "").strip(),
        mcp_port=int(port_text) if port_text else DEFAULT_MCP_PORT,
        shop_timezone=os.getenv("SG_SHOP_TIMEZONE", "").strip() or DEFAULT_SHOP_TIMEZONE,
        timeout_seconds=float(timeout_text) if timeout_text else DEFAULT_TIMEOUT_SECONDS,
        enable_actions=os.getenv("SG_ASSISTANT_ACTIONS", "").strip().lower() in {"1", "true", "yes", "on"},
    )
