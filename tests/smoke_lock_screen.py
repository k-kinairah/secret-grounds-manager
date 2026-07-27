import sys
import tempfile
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import client.client as client_module
from client.client import SecretGroundsClient
from client.session_recovery import SessionRecoveryStore


class CapturingWebSocket:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []

    def send_json(self, payload: dict[str, object]) -> None:
        self.sent_messages.append(payload)


def create_client() -> SecretGroundsClient:
    client_module.AUTOMATIC_SHUTDOWN_ON_EXPIRATION = False
    temp_directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    recovery_store = SessionRecoveryStore(Path(temp_directory.name) / "recovery.json")
    client = SecretGroundsClient(
        server_url="http://127.0.0.1:8000",
        pc_name="PC-LOCK-TEST",
        heartbeat_interval=1,
        request_timeout=1,
        recovery_store=recovery_store,
        enable_lock_screen=False,
    )
    client._test_temp_directory = temp_directory
    return client


def start_command(session_id: str, duration_seconds: int) -> dict[str, object]:
    return {
        "command_id": f"start-{session_id}",
        "type": "start_session",
        "payload": {
            "session_id": session_id,
            "duration_seconds": duration_seconds,
            "scheduled_end_at": "2026-07-23T00:00:00+00:00",
        },
    }


def end_command(session_id: str) -> dict[str, object]:
    return {
        "command_id": f"end-{session_id}",
        "type": "end_session",
        "payload": {"session_id": session_id},
    }


def test_starts_locked() -> None:
    client = create_client()
    try:
        assert client.lock_screen.is_locked()
    finally:
        client.stop()


def test_start_session_unlocks() -> None:
    client = create_client()
    websocket = CapturingWebSocket()
    client.set_current_websocket(websocket)
    try:
        acknowledgement = client.start_local_session(
            start_command("session-start", 10),
            websocket,
        )
        assert acknowledgement["status"] == "completed"
        assert not client.lock_screen.is_locked()
    finally:
        client.end_local_session(end_command("session-start"))
        client.stop()


def test_manual_end_locks() -> None:
    client = create_client()
    websocket = CapturingWebSocket()
    client.set_current_websocket(websocket)
    try:
        client.start_local_session(start_command("session-end", 10), websocket)
        acknowledgement = client.end_local_session(end_command("session-end"))
        assert acknowledgement["status"] == "completed"
        assert client.lock_screen.is_locked()
    finally:
        client.stop()


def test_timer_expiration_locks() -> None:
    client_module.EXPIRATION_SOUND_ENABLED = False
    client = create_client()
    websocket = CapturingWebSocket()
    client.set_current_websocket(websocket)
    try:
        client.start_local_session(start_command("session-expire", 1), websocket)
        time.sleep(1.5)
        assert client.lock_screen.is_locked()
        assert websocket.sent_messages[-1]["event_type"] == "expiration_grace_started"
        assert websocket.sent_messages[-1]["session_id"] == "session-expire"
    finally:
        client.stop()


def test_final_warning_triggers_once() -> None:
    client_module.EXPIRATION_SOUND_ENABLED = False
    client = create_client()
    websocket = CapturingWebSocket()
    client.set_current_websocket(websocket)
    try:
        client.start_local_session(start_command("session-warning", 10), websocket)
        client.maybe_send_final_warning("session-warning", 30)
        client.maybe_send_final_warning("session-warning", 29)
        final_warning_events = [
            message
            for message in websocket.sent_messages
            if message.get("event_type") == "expiration_final_warning"
        ]
        assert len(final_warning_events) == 1
    finally:
        client.end_local_session(end_command("session-warning"))
        client.stop()


def test_grace_reconnect_uses_authoritative_shutdown_at() -> None:
    client = create_client()
    now = datetime.now(timezone.utc)
    shutdown_at = now + timedelta(seconds=45)
    try:
        adopted = client.initialize_grace_period_from_server(
            session_id="session-reconnect-grace",
            scheduled_end_at=(now - timedelta(seconds=10)).isoformat(),
            shutdown_at=shutdown_at.isoformat(),
            server_time=now.isoformat(),
            session_mode="prepaid",
        )
        assert adopted
        assert client.active_session is not None
        assert client.active_session.expiration_state == "grace_period"
        remaining = int((client.active_session.grace_shutdown_monotonic or 0) - time.monotonic())
        assert 40 <= remaining <= 45
    finally:
        client.stop()


def test_disconnect_during_active_session_stays_unlocked() -> None:
    client = create_client()
    websocket = CapturingWebSocket()
    client.set_current_websocket(websocket)
    try:
        client.start_local_session(start_command("session-disconnect", 10), websocket)
        client.handle_websocket_offline()
        assert not client.lock_screen.is_locked()
        assert not client.lock_screen.is_online()
    finally:
        client.end_local_session(end_command("session-disconnect"))
        client.stop()


def test_disconnect_while_idle_remains_locked() -> None:
    client = create_client()
    try:
        client.handle_websocket_offline()
        assert client.lock_screen.is_locked()
        assert not client.lock_screen.is_online()
    finally:
        client.stop()


def test_development_escape_hides_locked_screen() -> None:
    client = create_client()
    try:
        assert client.lock_screen.is_locked()
        client.lock_screen.use_development_escape()
        assert client.lock_screen.state.dev_escape_active
    finally:
        client.stop()


def main() -> None:
    test_starts_locked()
    test_start_session_unlocks()
    test_manual_end_locks()
    test_timer_expiration_locks()
    test_final_warning_triggers_once()
    test_grace_reconnect_uses_authoritative_shutdown_at()
    test_disconnect_during_active_session_stays_unlocked()
    test_disconnect_while_idle_remains_locked()
    test_development_escape_hides_locked_screen()
    print("Lock-screen smoke tests passed.")


if __name__ == "__main__":
    main()
