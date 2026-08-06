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


def get_logo_path() -> Path | None:
    """Return the packaged Secret Grounds logo, or None if it is missing.

    Checked relative to the running process, never a developer machine's
    absolute path: the PyInstaller bundle root (sys._MEIPASS) and the
    directory the packaged exe lives in when frozen, or client/assets/ next
    to this file in development. Returning None (rather than raising) lets
    callers fall back to a safe text mark if the asset cannot be found.
    """
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            candidates.append(Path(meipass) / LOGO_ASSET_RELATIVE_PATH)
        candidates.append(Path(sys.executable).resolve().parent / LOGO_ASSET_RELATIVE_PATH)
    candidates.append(Path(__file__).resolve().parent / LOGO_ASSET_RELATIVE_PATH)

    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None
