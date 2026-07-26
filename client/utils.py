import socket
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
