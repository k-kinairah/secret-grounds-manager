import socket
import sys
from pathlib import Path
from uuid import getnode


def get_local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(("8.8.8.8", 80))
            return connection.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def get_local_mac_address() -> str | None:
    node = getnode()
    if (node >> 40) % 2:
        return None
    raw = f"{node:012X}"
    return ":".join(raw[index : index + 2] for index in range(0, 12, 2))


LOGO_ASSET_RELATIVE_PATH = Path("assets") / "secret-grounds-logo.png"
LOGO_ICO_RELATIVE_PATH = Path("assets") / "secret-grounds-logo.ico"


def _bundled_asset_path(relative_path: Path) -> Path | None:
    """Resolve a packaged asset relative to the running process only, never a
    developer machine's absolute path: the PyInstaller bundle root
    (sys._MEIPASS) and the directory the packaged exe lives in when frozen,
    or client/ next to this file in development. Returns None (rather than
    raising) so callers can fall back to a safe default."""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / relative_path)
        candidates.append(Path(sys.executable).resolve().parent / relative_path)
    candidates.append(Path(__file__).resolve().parent / relative_path)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def get_logo_path() -> Path | None:
    """The packaged Secret Grounds logo PNG, or None if it is missing."""
    return _bundled_asset_path(LOGO_ASSET_RELATIVE_PATH)


def get_logo_ico_path() -> Path | None:
    """The packaged Secret Grounds .ico (multi-size), for window/taskbar
    icons, or None if it is missing."""
    return _bundled_asset_path(LOGO_ICO_RELATIVE_PATH)
