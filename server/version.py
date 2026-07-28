from pathlib import Path


def get_app_version() -> str:
    version_path = Path(__file__).resolve().parent.parent / "VERSION"
    try:
        return version_path.read_text(encoding="utf-8").strip() or "0.0.0"
    except OSError:
        return "0.0.0"


APP_VERSION = get_app_version()
