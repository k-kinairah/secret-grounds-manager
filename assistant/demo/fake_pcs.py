r"""Simulated shop PCs for the demo (PC-02 to PC-15 by default).

Each one talks to the demo backend exactly like the real client does:
POST /register, POST /heartbeat every 5 seconds, and the command WebSocket
(/ws/<pc>) where every command gets the same two acknowledgements a real PC
sends ("received", then "completed" or "failed"). So they show online on the
dashboard grid and the assistant can message, add time to or "shut down" them.

They are honest about being simulated: a message is acknowledged as
"displayed (simulated PC)" and a shutdown as "received (simulated PC, test mode:
nothing powered off)". Nothing here touches this machine -- no windows, no lock
screen, no power commands; the only import from the client is its small
WebSocket transport. When a session's time runs out, the PC reports it
finished, like a real PC whose timer ends.

    .\.venv\Scripts\python.exe assistant\demo\fake_pcs.py --server http://127.0.0.1:8000

Refuses the live backend port (18001) and PC-01 (the real test client).
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from client.websocket_transport import SimpleWebSocketClient  # noqa: E402  (transport only)

HEARTBEAT_SECONDS = 5
REQUEST_TIMEOUT = 5
LIVE_PORTS = {18001}
REAL_TEST_CLIENT = "PC-01"

ACK_MESSAGES = {
    "ping": "pong (simulated PC)",
    "start_session": "Session started (simulated PC)",
    "add_time": "Time added (simulated PC)",
    "end_session": "Session ended (simulated PC)",
    "show_message": "displayed (simulated PC)",
    "show_announcement": "displayed (simulated PC)",
    "clear_customer_lock": "Customer lock cleared (simulated PC)",
    # "test mode" is the phrase the assistant looks for in a power acknowledgement, so its
    # result card says "test mode: not powered off" instead of "should power off".
    "shutdown": "received (simulated PC, test mode: nothing powered off)",
    "restart": "received (simulated PC, test mode: nothing restarted)",
    "sleep": "received (simulated PC, test mode: nothing put to sleep)",
}


def log(pc: str, text: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {pc}: {text}", flush=True)


class FakePc:
    def __init__(self, server: str, pc_name: str, number: int, stop: threading.Event) -> None:
        self.server = server.rstrip("/")
        self.pc_name = pc_name
        self.stop = stop
        self.http = requests.Session()
        # A locally administered MAC (02:...) that belongs to no real device.
        self.mac = f"02:53:47:00:00:{number:02x}"
        self.websocket: SimpleWebSocketClient | None = None
        self.send_lock = threading.Lock()
        self.reported_finished: set[str] = set()

    # --- like the real client: register + heartbeat -----------------------------------------
    def payload(self, include_mac: bool = False) -> dict[str, str]:
        body = {"pc_name": self.pc_name, "display_name": self.pc_name, "ip_address": "127.0.0.1"}
        if include_mac:
            body["mac_address"] = self.mac
        return body

    def register(self) -> None:
        while not self.stop.is_set():
            try:
                self.http.post(f"{self.server}/register", json=self.payload(True), timeout=REQUEST_TIMEOUT).raise_for_status()
                log(self.pc_name, "registered")
                return
            except requests.RequestException as error:
                log(self.pc_name, f"register failed ({error}); retrying")
                self.stop.wait(HEARTBEAT_SECONDS)

    def heartbeat_loop(self) -> None:
        while not self.stop.wait(HEARTBEAT_SECONDS):
            try:
                response = self.http.post(f"{self.server}/heartbeat", json=self.payload(), timeout=REQUEST_TIMEOUT)
                if response.status_code == 404:
                    self.register()
                self.finish_expired_session()
            except requests.RequestException:
                pass

    # --- a session whose time ran out is reported finished, as a real PC's timer would --------
    def finish_expired_session(self) -> None:
        try:
            state = self.http.get(f"{self.server}/pcs/{quote(self.pc_name)}/session-state", timeout=REQUEST_TIMEOUT).json()
        except (requests.RequestException, ValueError):
            return
        session = state.get("session") if isinstance(state, dict) else None
        if not state.get("has_active_session") or not isinstance(session, dict):
            return
        if session.get("status") != "active" or session.get("session_mode") == "open_time":
            return
        end = session.get("scheduled_end_at")
        session_id = str(session.get("session_id") or "")
        if not end or not session_id or session_id in self.reported_finished:
            return
        ends = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        if ends.tzinfo is None:
            ends = ends.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) < ends + timedelta(seconds=2):
            return
        if self.send({"event_type": "session_finished", "session_id": session_id, "message": "Timer finished (simulated PC)"}):
            self.reported_finished.add(session_id)
            log(self.pc_name, f"session {session_id[:8]} time is up; reported finished")

    def send(self, message: dict) -> bool:
        with self.send_lock:
            if self.websocket is None:
                return False
            try:
                self.websocket.send_json(message)
                return True
            except (ConnectionError, OSError):
                return False

    # --- the command WebSocket ------------------------------------------------------------------
    def websocket_loop(self) -> None:
        url = self.server.replace("https://", "wss://", 1).replace("http://", "ws://", 1) + f"/ws/{quote(self.pc_name)}"
        while not self.stop.is_set():
            try:
                self.register()
                with SimpleWebSocketClient(url=url, timeout=REQUEST_TIMEOUT) as websocket:
                    with self.send_lock:
                        self.websocket = websocket
                    log(self.pc_name, "online")
                    while not self.stop.is_set():
                        self.handle(websocket.receive_json())
            except (ConnectionError, OSError, ValueError) as error:
                if not self.stop.is_set():
                    log(self.pc_name, f"disconnected ({error}); reconnecting")
            finally:
                with self.send_lock:
                    self.websocket = None
            self.stop.wait(HEARTBEAT_SECONDS)

    def handle(self, message: dict) -> None:
        if "event_type" in message:  # keepalive frames and server confirmations need no reply
            return
        command_id = str(message.get("command_id", ""))
        command_type = str(message.get("type", ""))
        self.send({"command_id": command_id, "status": "received", "message": "Command received"})
        text = ACK_MESSAGES.get(command_type)
        if text is None:
            self.send({"command_id": command_id, "status": "failed", "message": f"Unsupported command type: {command_type}"})
            log(self.pc_name, f"{command_type}: unsupported")
            return
        self.send({"command_id": command_id, "status": "completed", "message": text})
        log(self.pc_name, f"{command_type}: {text}")

    def start(self) -> list[threading.Thread]:
        threads = [
            threading.Thread(target=self.websocket_loop, name=f"{self.pc_name}-ws", daemon=True),
            threading.Thread(target=self.heartbeat_loop, name=f"{self.pc_name}-hb", daemon=True),
        ]
        for thread in threads:
            thread.start()
        return threads


def pc_names(first: int, last: int) -> list[str]:
    return [f"PC-{n:02d}" for n in range(first, last + 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulated shop PCs for the assistant demo.")
    parser.add_argument("--server", required=True, help="demo backend, e.g. http://127.0.0.1:8000")
    parser.add_argument("--first", type=int, default=2)
    parser.add_argument("--last", type=int, default=15)
    args = parser.parse_args()

    parsed = urlparse(args.server)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        sys.exit(f"fake_pcs: --server must be an http(s) URL, got {args.server!r}")
    if (parsed.port or 80) in LIVE_PORTS:
        sys.exit("fake_pcs: refusing to connect to the live shop backend port.")
    names = pc_names(args.first, args.last)
    if REAL_TEST_CLIENT in names:
        sys.exit(f"fake_pcs: {REAL_TEST_CLIENT} is the real test client; start from PC-02.")

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    for index, name in enumerate(names):
        FakePc(args.server, name, args.first + index, stop).start()
    print(f"Simulated PCs {names[0]}..{names[-1]} running against {args.server}", flush=True)
    while not stop.wait(1):
        pass


if __name__ == "__main__":
    main()
