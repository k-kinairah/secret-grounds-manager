import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from client.websocket_transport import SimpleWebSocketClient


SERVER_URL = "http://127.0.0.1:8765"
WEBSOCKET_URL = "ws://127.0.0.1:8765/ws/PC-01"


def wait_for_server() -> None:
    for _ in range(40):
        try:
            response = requests.get(f"{SERVER_URL}/", timeout=1)
            if response.status_code == 200:
                return
        except requests.RequestException:
            time.sleep(0.25)

    raise RuntimeError("Server did not start")


def register_pc(pc_name: str, ip_address: str) -> None:
    response = requests.post(
        f"{SERVER_URL}/register",
        json={"pc_name": pc_name, "ip_address": ip_address},
        timeout=5,
    )
    response.raise_for_status()


def run_ping_client(received_commands: list[dict[str, Any]]) -> None:
    with SimpleWebSocketClient(WEBSOCKET_URL, timeout=5) as websocket:
        command = websocket.receive_json()
        received_commands.append(command)
        websocket.send_json(
            {
                "command_id": command["command_id"],
                "status": "received",
                "message": "Command received",
            }
        )
        websocket.send_json(
            {
                "command_id": command["command_id"],
                "status": "completed",
                "message": "pong",
            }
        )


def main() -> None:
    database_path = str(PROJECT_ROOT / "tests" / "smoke_websocket_commands.sqlite3")
    if Path(database_path).exists():
        Path(database_path).unlink()

    environment = os.environ.copy()
    environment["SECRET_GROUNDS_DB_PATH"] = database_path

    server_process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8765",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )

    try:
        wait_for_server()
        register_pc("PC-01", "127.0.0.1")

        received_commands: list[dict[str, Any]] = []
        websocket_thread = threading.Thread(
            target=run_ping_client,
            args=(received_commands,),
        )
        websocket_thread.start()
        time.sleep(0.5)

        command_response = requests.post(
            f"{SERVER_URL}/commands/test-ping",
            json={"pc_name": "PC-01"},
            timeout=10,
        )
        command_response.raise_for_status()
        command_payload = command_response.json()

        websocket_thread.join(timeout=5)
        assert received_commands, "WebSocket client did not receive command"
        assert received_commands[0]["type"] == "ping"
        assert command_payload["acknowledgements"][0]["status"] == "received"
        assert command_payload["acknowledgements"][1]["status"] == "completed"
        assert command_payload["acknowledgements"][1]["message"] == "pong"

        register_pc("PC-OFFLINE", "127.0.0.2")
        time.sleep(16)
        rejected_response = requests.post(
            f"{SERVER_URL}/commands/test-ping",
            json={"pc_name": "PC-OFFLINE"},
            timeout=5,
        )
        assert rejected_response.status_code == 409
        assert "offline" in rejected_response.json()["detail"]

        print("WebSocket command smoke test passed.")

    finally:
        server_process.terminate()
        try:
            server_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server_process.kill()
        if Path(database_path).exists():
            Path(database_path).unlink()


if __name__ == "__main__":
    main()
