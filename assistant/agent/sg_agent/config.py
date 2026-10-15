"""Settings for the Secret Grounds agent service, read from environment variables.

assistant/agent/.env is loaded as a convenience (gitignored; copy .env.example);
real environment variables always win.
"""

import os
from dataclasses import dataclass
from pathlib import Path

# Loopback only, deliberately not configurable: /chat has no auth of its own.
AGENT_HOST = "127.0.0.1"

DEFAULT_PORT = 8781
DEFAULT_MCP_URL = "http://127.0.0.1:8765/mcp"
DEFAULT_PROVIDER = "bedrock"
DEFAULT_BEDROCK_REGION = "ap-southeast-2"
DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
DEFAULT_CORS_ORIGIN = "http://localhost:3000,http://127.0.0.1:3000"
DEFAULT_MAX_TURNS = 20
DEFAULT_MAX_SESSIONS = 200
# Spoken replies are 1-3 sentences (~80 tokens max); tool calls need little more.
DEFAULT_MAX_TOKENS = 300
DEFAULT_NOTES_DB = Path(__file__).resolve().parents[1] / "data" / "handover.sqlite3"

PROVIDERS = ("bedrock", "anthropic", "openai", "ollama")

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


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, "").strip() or default


@dataclass(frozen=True)
class Settings:
    port: int
    mcp_url: str
    provider: str
    model_id: str
    max_tokens: int
    temperature: float | None
    bedrock_region: str
    anthropic_api_key: str
    openai_api_key: str
    openai_base_url: str
    ollama_host: str
    cors_origins: tuple[str, ...]
    max_turns: int
    max_sessions: int
    # Shift handover notes file; None when SG_AGENT_NOTES_DB=off.
    notes_db: Path | None = None


def get_settings() -> Settings:
    provider = _env("SG_AGENT_PROVIDER", DEFAULT_PROVIDER).lower()
    if provider not in PROVIDERS:
        raise ValueError(f"SG_AGENT_PROVIDER must be one of {', '.join(PROVIDERS)}; got {provider!r}")
    temperature = _env("SG_AGENT_TEMPERATURE")
    return Settings(
        port=int(_env("SG_AGENT_PORT", str(DEFAULT_PORT))),
        mcp_url=_env("SG_MCP_URL", DEFAULT_MCP_URL),
        provider=provider,
        model_id=_env("SG_AGENT_MODEL_ID"),
        max_tokens=int(_env("SG_AGENT_MAX_TOKENS", str(DEFAULT_MAX_TOKENS))),
        temperature=float(temperature) if temperature else None,
        bedrock_region=_env("SG_AGENT_BEDROCK_REGION", _env("AWS_REGION", DEFAULT_BEDROCK_REGION)),
        anthropic_api_key=_env("ANTHROPIC_API_KEY"),
        openai_api_key=_env("OPENAI_API_KEY"),
        openai_base_url=_env("SG_AGENT_OPENAI_BASE_URL"),
        ollama_host=_env("SG_AGENT_OLLAMA_HOST", DEFAULT_OLLAMA_HOST),
        cors_origins=tuple(
            origin.strip()
            for origin in _env("SG_AGENT_CORS_ORIGINS", DEFAULT_CORS_ORIGIN).split(",")
            if origin.strip()
        ),
        max_turns=int(_env("SG_AGENT_MAX_TURNS", str(DEFAULT_MAX_TURNS))),
        max_sessions=int(_env("SG_AGENT_MAX_SESSIONS", str(DEFAULT_MAX_SESSIONS))),
        notes_db=_notes_db(),
    )


def _notes_db() -> Path | None:
    configured = _env("SG_AGENT_NOTES_DB")
    if configured.lower() in {"off", "false", "0", "none"}:
        return None
    return Path(configured) if configured else DEFAULT_NOTES_DB
