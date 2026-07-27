import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import client.client as client_module
from client.client import SecretGroundsClient
from client.session_recovery import SessionRecoveryStore
from client.websocket_transport import SimpleWebSocketClient


SERVER_URL = "http://127.0.0.1:8766"


def bootstrap_owner(database_path: str) -> None:
    os.environ["SECRET_GROUNDS_DB_PATH"] = database_path
    from server.database import initialize_database
    from server.schemas import UserCreateRequest
    from server.user_repository import UserRepository

    initialize_database()
    repository = UserRepository()
    if repository.get_by_username("owner") is None:
        repository.create_user(
            UserCreateRequest(
                username="owner",
                display_name="Owner",
                password="owner-pass",
                role="owner",
            )
        )


def login_owner() -> requests.Session:
    session = requests.Session()
    response = session.post(
        f"{SERVER_URL}/auth/login",
        json={"username": "owner", "password": "owner-pass"},
        timeout=5,
    )
    response.raise_for_status()
    return session


def start_server(database_path: str) -> subprocess.Popen[str]:
    environment = os.environ.copy()
    environment["SECRET_GROUNDS_DB_PATH"] = database_path
    environment["SECRET_GROUNDS_TEST_MODE"] = "true"
    environment["SECRET_GROUNDS_EXPIRATION_GRACE_SECONDS"] = "1"
    environment["SECRET_GROUNDS_AUTOMATIC_SHUTDOWN_ON_EXPIRATION"] = "false"

    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "server.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8766",
        ],
        cwd=PROJECT_ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def stop_server(server_process: subprocess.Popen[str]) -> None:
    server_process.terminate()
    try:
        server_process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server_process.kill()


def wait_for_server() -> None:
    for _ in range(40):
        try:
            response = requests.get(f"{SERVER_URL}/", timeout=1)
            if response.status_code == 200:
                return
        except requests.RequestException:
            time.sleep(0.25)

    raise RuntimeError("Server did not start")


def wait_for_ping(session: requests.Session, pc_name: str) -> dict[str, object]:
    last_error = ""
    for _ in range(40):
        try:
            response = session.post(
                f"{SERVER_URL}/commands/test-ping",
                json={"pc_name": pc_name},
                timeout=5,
            )
            if response.status_code == 200:
                return response.json()
            last_error = response.text
        except requests.RequestException as error:
            last_error = str(error)
        time.sleep(0.25)

    raise RuntimeError(f"Ping never succeeded: {last_error}")


def wait_for_session_status(
    session: requests.Session,
    session_id: str,
    status: str,
) -> dict[str, object]:
    for _ in range(40):
        response = session.get(f"{SERVER_URL}/sessions/{session_id}", timeout=5)
        response.raise_for_status()
        session_payload = response.json()
        if session_payload["status"] == status:
            return session_payload
        time.sleep(0.25)

    raise RuntimeError(f"Session {session_id} did not become {status}")


def post_json(
    session: requests.Session,
    path: str,
    payload: dict[str, object],
) -> requests.Response:
    return session.post(f"{SERVER_URL}{path}", json=payload, timeout=10)


def assert_client_rejects_mismatched_session() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        recovery_store = SessionRecoveryStore(Path(temp_directory) / "local.json")
        client = SecretGroundsClient(
            SERVER_URL,
            "PC-LOCAL",
            1,
            5,
            recovery_store=recovery_store,
            enable_lock_screen=False,
        )
        command = {
            "command_id": "mismatch-test",
            "type": "add_time",
            "payload": {"session_id": "missing-session", "seconds": 5},
        }
        acknowledgement = client.add_time_to_local_session(command)

        assert acknowledgement["status"] == "failed"
        assert "Session ID does not match" in str(acknowledgement["message"])
        client.stop()


def create_smoke_client(temp_directory: str) -> SecretGroundsClient:
    recovery_store = SessionRecoveryStore(Path(temp_directory) / "pc01_recovery.json")
    client = SecretGroundsClient(
        SERVER_URL,
        "PC-01",
        1,
        5,
        recovery_store=recovery_store,
        enable_lock_screen=False,
    )
    return client


def main() -> None:
    os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"
    os.environ["SECRET_GROUNDS_AUTOMATIC_SHUTDOWN_ON_EXPIRATION"] = "false"
    client_module.EXPIRATION_GRACE_SECONDS = 1
    client_module.AUTOMATIC_SHUTDOWN_ON_EXPIRATION = False
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        database_path = str(Path(temp_directory) / "secret_grounds_test.sqlite3")
        bootstrap_owner(database_path)
        server_process = start_server(database_path)
        client = create_smoke_client(temp_directory)
        client_thread = threading.Thread(target=client.run_forever, daemon=True)

        try:
            wait_for_server()
            admin_session = login_owner()
            client_thread.start()

            ping_payload = wait_for_ping(admin_session, "PC-01")
            assert ping_payload["acknowledgements"][-1]["message"] == "pong"

            first_start = post_json(
                admin_session,
                "/sessions/start",
                {"pc_name": "PC-01", "duration_seconds": 3},
            )
            first_start.raise_for_status()
            first_session = first_start.json()["session"]
            first_session_id = first_session["session_id"]

            duplicate_start = post_json(
                admin_session,
                "/sessions/start",
                {"pc_name": "PC-01", "duration_seconds": 3},
            )
            assert duplicate_start.status_code == 409

            add_time = post_json(
                admin_session,
                f"/sessions/{first_session_id}/add-time",
                {"seconds": 2},
            )
            add_time.raise_for_status()
            assert add_time.json()["session"]["added_seconds"] == 2
            assert add_time.json()["session"]["total_seconds"] == 5

            manual_end = admin_session.post(
                f"{SERVER_URL}/sessions/{first_session_id}/end",
                timeout=10,
            )
            manual_end.raise_for_status()
            assert manual_end.json()["session"]["status"] == "completed"

            auto_start = post_json(
                admin_session,
                "/sessions/start",
                {"pc_name": "PC-01", "duration_seconds": 2},
            )
            auto_start.raise_for_status()
            auto_session_id = auto_start.json()["session"]["session_id"]
            wait_for_session_status(admin_session, auto_session_id, "completed")

            with SimpleWebSocketClient("ws://127.0.0.1:8766/ws/PC-01", timeout=5) as websocket:
                websocket.send_json(
                    {
                        "event_type": "session_finished",
                        "session_id": auto_session_id,
                        "message": "duplicate",
                    }
                )
                websocket.send_json(
                    {
                        "event_type": "session_finished",
                        "session_id": auto_session_id,
                        "message": "duplicate",
                    }
                )
            time.sleep(0.5)
            response = admin_session.get(
                f"{SERVER_URL}/sessions/{auto_session_id}",
                timeout=5,
            )
            response.raise_for_status()
            assert response.json()["status"] == "completed"
            with sqlite3.connect(database_path) as connection:
                completed_event_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM session_events
                    WHERE session_id = ? AND event_type = 'session_completed'
                    """,
                    (auto_session_id,),
                ).fetchone()[0]
            assert completed_event_count == 1

            assert_client_rejects_mismatched_session()
            client.stop()

            requests.post(
                f"{SERVER_URL}/register",
                json={"pc_name": "PC-OFFLINE", "ip_address": "127.0.0.2"},
                timeout=5,
            ).raise_for_status()
            time.sleep(16)
            offline_start = post_json(
                admin_session,
                "/sessions/start",
                {"pc_name": "PC-OFFLINE", "duration_seconds": 1},
            )
            assert offline_start.status_code == 409

            history_before_restart = admin_session.get(
                f"{SERVER_URL}/sessions/history",
                timeout=5,
            )
            history_before_restart.raise_for_status()
            assert len(history_before_restart.json()) >= 2

            stop_server(server_process)
            server_process = start_server(database_path)
            wait_for_server()
            admin_session = login_owner()

            history_after_restart = admin_session.get(
                f"{SERVER_URL}/sessions/history",
                timeout=5,
            )
            history_after_restart.raise_for_status()
            assert len(history_after_restart.json()) >= 2

            print("Session management smoke test passed.")

        finally:
            client.stop()
            stop_server(server_process)


if __name__ == "__main__":
    main()
