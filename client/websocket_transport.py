import base64
import json
import os
import socket
import ssl
from threading import Lock
from types import TracebackType
from typing import Any
from urllib.parse import urlparse


class SimpleWebSocketClient:
    """Minimal JSON WebSocket client for Secret Grounds commands.

    This transport is intentionally narrow: the rest of the client only needs
    JSON messages, so a future dependency-backed WebSocket client can replace
    this class without changing command handling.
    """

    def __init__(self, url: str, timeout: int) -> None:
        self.url = url
        self.timeout = timeout
        self._socket: socket.socket | None = None
        self._send_lock = Lock()

    def __enter__(self) -> "SimpleWebSocketClient":
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def connect(self) -> None:
        parsed_url = urlparse(self.url)
        host = parsed_url.hostname

        if host is None:
            raise ValueError(f"Invalid WebSocket URL: {self.url}")

        port = parsed_url.port or (443 if parsed_url.scheme == "wss" else 80)
        path = parsed_url.path or "/"
        if parsed_url.query:
            path = f"{path}?{parsed_url.query}"

        raw_socket = socket.create_connection((host, port), timeout=self.timeout)
        if parsed_url.scheme == "wss":
            raw_socket = ssl.create_default_context().wrap_socket(
                raw_socket,
                server_hostname=host,
            )

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        raw_socket.sendall(request.encode("ascii"))
        response = raw_socket.recv(4096).decode("ascii", errors="ignore")

        if " 101 " not in response.split("\r\n", 1)[0]:
            raw_socket.close()
            raise ConnectionError("WebSocket handshake failed")

        raw_socket.settimeout(None)
        self._socket = raw_socket

    def receive_json(self) -> dict[str, Any]:
        while True:
            opcode, payload = self._receive_frame()

            if opcode == 0x1:
                return json.loads(payload.decode("utf-8"))

            if opcode == 0x8:
                raise ConnectionError("WebSocket connection closed")

            if opcode == 0x9:
                self._send_frame(payload, opcode=0xA)

    def send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(json.dumps(payload).encode("utf-8"), opcode=0x1)

    def close(self) -> None:
        if self._socket is None:
            return

        try:
            self._send_frame(b"", opcode=0x8)
        finally:
            self._socket.close()
            self._socket = None

    def _receive_frame(self) -> tuple[int, bytes]:
        first_two_bytes = self._receive_exactly(2)
        first_byte, second_byte = first_two_bytes
        opcode = first_byte & 0x0F
        payload_length = second_byte & 0x7F

        if payload_length == 126:
            payload_length = int.from_bytes(self._receive_exactly(2), "big")
        elif payload_length == 127:
            payload_length = int.from_bytes(self._receive_exactly(8), "big")

        mask = b""
        if second_byte & 0x80:
            mask = self._receive_exactly(4)

        payload = self._receive_exactly(payload_length)
        if mask:
            payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))

        return opcode, payload

    def _send_frame(self, payload: bytes, opcode: int) -> None:
        if self._socket is None:
            raise ConnectionError("WebSocket is not connected")

        frame = bytearray([0x80 | opcode])
        payload_length = len(payload)

        if payload_length < 126:
            frame.append(0x80 | payload_length)
        elif payload_length <= 65535:
            frame.append(0x80 | 126)
            frame.extend(payload_length.to_bytes(2, "big"))
        else:
            frame.append(0x80 | 127)
            frame.extend(payload_length.to_bytes(8, "big"))

        mask = os.urandom(4)
        frame.extend(mask)
        frame.extend(
            byte ^ mask[index % 4]
            for index, byte in enumerate(payload)
        )

        with self._send_lock:
            self._socket.sendall(frame)

    def _receive_exactly(self, byte_count: int) -> bytes:
        if self._socket is None:
            raise ConnectionError("WebSocket is not connected")

        chunks = bytearray()
        while len(chunks) < byte_count:
            chunk = self._socket.recv(byte_count - len(chunks))
            if not chunk:
                raise ConnectionError("WebSocket connection closed")
            chunks.extend(chunk)

        return bytes(chunks)
