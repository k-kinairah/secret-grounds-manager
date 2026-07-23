import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from client.client import SecretGroundsClient
from client.session_recovery import SessionRecoveryStore
from server.database import initialize_database
from server.rate_repository import RateRepository
from server.session_repository import SessionRepository
from server.session_service import SessionService
from server.schemas import PCRecord, SessionRecord


class CapturingWebSocket:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []

    def send_json(self, payload: dict[str, object]) -> None:
        self.sent_messages.append(payload)


class DummyConnectionManager:
    def is_connected(self, _: str) -> bool:
        return True


def iso_in(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def create_client(temp_directory: str) -> SecretGroundsClient:
    return SecretGroundsClient(
        server_url="http://127.0.0.1:8000",
        pc_name="PC-RECOVERY",
        heartbeat_interval=1,
        request_timeout=1,
        recovery_store=SessionRecoveryStore(
            Path(temp_directory) / f"recovery-{uuid4()}.json"
        ),
        enable_lock_screen=False,
    )


def server_state(
    session_id: str | None,
    scheduled_end_at: str | None = None,
) -> dict[str, object]:
    return {
        "pc_name": "PC-RECOVERY",
        "has_active_session": session_id is not None,
        "session": (
            {
                "session_id": session_id,
                "status": "active",
                "scheduled_end_at": scheduled_end_at or iso_in(60),
                "total_seconds": 60,
            }
            if session_id is not None
            else None
        ),
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


def start_command(session_id: str, duration_seconds: int, end_at: str) -> dict[str, object]:
    return {
        "command_id": f"start-{session_id}",
        "type": "start_session",
        "payload": {
            "session_id": session_id,
            "duration_seconds": duration_seconds,
            "scheduled_end_at": end_at,
        },
    }


def end_command(session_id: str) -> dict[str, object]:
    return {
        "command_id": f"end-{session_id}",
        "type": "end_session",
        "payload": {"session_id": session_id},
    }


def test_recovery_store_atomic_save_load_clear(temp_directory: str) -> None:
    store = SessionRecoveryStore(Path(temp_directory) / "atomic.json")
    saved = store.save("session-1", "PC-RECOVERY", iso_in(60), "active")
    loaded = store.load()
    assert loaded == saved
    store.clear()
    assert store.load() is None


def test_matching_recovery_resumes(temp_directory: str) -> None:
    client = create_client(temp_directory)
    end_at = iso_in(30)
    client.recovery_store.save("session-match", "PC-RECOVERY", end_at, "active")
    client.get_server_session_state = lambda: server_state("session-match", end_at)
    client.reconcile_session_state()
    assert not client.lock_screen.is_locked()
    assert client.active_session is not None
    assert client.active_session.session_id == "session-match"
    client.end_local_session(end_command("session-match"))
    client.stop()


def test_expired_recovery_locks_and_marks_pending(temp_directory: str) -> None:
    client = create_client(temp_directory)
    websocket = CapturingWebSocket()
    client.set_current_websocket(websocket)
    end_at = iso_in(-2)
    client.recovery_store.save("session-expired", "PC-RECOVERY", end_at, "active")
    client.get_server_session_state = lambda: server_state("session-expired", end_at)
    client.reconcile_session_state()
    loaded = client.recovery_store.load()
    assert client.lock_screen.is_locked()
    assert loaded is not None
    assert loaded.state == "pending_completion"
    assert websocket.sent_messages[-1]["event_type"] == "session_finished"
    client.stop()


def test_stale_local_record_cleared(temp_directory: str) -> None:
    client = create_client(temp_directory)
    client.recovery_store.save("session-stale", "PC-RECOVERY", iso_in(60), "active")
    client.get_server_session_state = lambda: server_state(None)
    client.reconcile_session_state()
    assert client.recovery_store.load() is None
    assert client.lock_screen.is_locked()
    client.stop()


def test_server_only_session_stays_locked(temp_directory: str) -> None:
    client = create_client(temp_directory)
    client.get_server_session_state = lambda: server_state("server-only", iso_in(60))
    client.reconcile_session_state()
    assert client.lock_screen.is_locked()
    assert client.active_session is None
    client.stop()


def test_mismatched_session_ids_stay_locked(temp_directory: str) -> None:
    client = create_client(temp_directory)
    client.recovery_store.save("local-session", "PC-RECOVERY", iso_in(60), "active")
    client.get_server_session_state = lambda: server_state("server-session", iso_in(60))
    client.reconcile_session_state()
    assert client.lock_screen.is_locked()
    assert client.recovery_store.load() is not None
    client.stop()


def test_add_time_updates_recovery(temp_directory: str) -> None:
    client = create_client(temp_directory)
    websocket = CapturingWebSocket()
    client.set_current_websocket(websocket)
    end_at = iso_in(30)
    client.start_local_session(start_command("session-add", 30, end_at), websocket)
    before = client.recovery_store.load()
    acknowledgement = client.add_time_to_local_session(
        {
            "command_id": "add-session-add",
            "type": "add_time",
            "payload": {"session_id": "session-add", "seconds": 30},
        }
    )
    after = client.recovery_store.load()
    assert acknowledgement["status"] == "completed"
    assert before is not None and after is not None
    assert after.scheduled_end_at != before.scheduled_end_at
    client.end_local_session(end_command("session-add"))
    client.stop()


def test_manual_end_clears_recovery(temp_directory: str) -> None:
    client = create_client(temp_directory)
    websocket = CapturingWebSocket()
    client.start_local_session(start_command("session-end", 30, iso_in(30)), websocket)
    client.end_local_session(end_command("session-end"))
    assert client.recovery_store.load() is None
    client.stop()


def test_expiration_offline_creates_pending_completion(temp_directory: str) -> None:
    client = create_client(temp_directory)
    websocket = CapturingWebSocket()
    client.start_local_session(start_command("session-offline", 1, iso_in(1)), websocket)
    client.set_current_websocket(None)
    time.sleep(1.5)
    loaded = client.recovery_store.load()
    assert loaded is not None
    assert loaded.state == "pending_completion"
    assert client.lock_screen.is_locked()
    client.stop()


def test_reconnect_sends_pending_and_confirmation_clears(temp_directory: str) -> None:
    client = create_client(temp_directory)
    websocket = CapturingWebSocket()
    client.set_current_websocket(websocket)
    client.recovery_store.mark_pending_completion(
        "session-pending",
        "PC-RECOVERY",
        iso_in(-1),
    )
    client.flush_pending_completion()
    assert len(websocket.sent_messages) == 1
    client.handle_session_completion_confirmation(
        {
            "event_type": "session_completion_recorded",
            "session_id": "session-pending",
            "recorded": True,
        }
    )
    assert client.recovery_store.load() is None
    client.stop()


def test_duplicate_completion_event_not_duplicated(temp_directory: str) -> None:
    database_path = Path(temp_directory) / "duplicate.sqlite3"
    import os

    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    initialize_database()
    repository = SessionRepository()
    service = SessionService(
        repository=repository,
        rate_repository=RateRepository(),
        connection_manager=DummyConnectionManager(),
        pc_registry={
            "PC-RECOVERY": PCRecord(
                pc_name="PC-RECOVERY",
                ip_address="127.0.0.1",
                status="online",
                last_seen=datetime.now(timezone.utc),
            )
        },
        command_timeout_seconds=1,
    )
    now = datetime.now(timezone.utc)
    repository.create_session(
        SessionRecord(
            session_id="duplicate-session",
            pc_name="PC-RECOVERY",
            status="active",
            started_at=now,
            scheduled_end_at=now + timedelta(seconds=1),
            ended_at=None,
            original_seconds=1,
            added_seconds=0,
            total_seconds=1,
            amount_paid=None,
            ended_reason=None,
            created_at=now,
        )
    )
    assert service.complete_from_client("duplicate-session", "Timer finished")
    assert service.complete_from_client("duplicate-session", "Timer finished")
    with sqlite3.connect(database_path) as connection:
        event_count = connection.execute(
            """
            SELECT COUNT(*) FROM session_events
            WHERE session_id = ? AND event_type = 'session_completed'
            """,
            ("duplicate-session",),
        ).fetchone()[0]
    assert event_count == 1


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_recovery_store_atomic_save_load_clear(temp_directory)
        test_matching_recovery_resumes(temp_directory)
        test_expired_recovery_locks_and_marks_pending(temp_directory)
        test_stale_local_record_cleared(temp_directory)
        test_server_only_session_stays_locked(temp_directory)
        test_mismatched_session_ids_stay_locked(temp_directory)
        test_add_time_updates_recovery(temp_directory)
        test_manual_end_clears_recovery(temp_directory)
        test_expiration_offline_creates_pending_completion(temp_directory)
        test_reconnect_sends_pending_and_confirmation_clears(temp_directory)
        test_duplicate_completion_event_not_duplicated(temp_directory)
    print("Session recovery smoke tests passed.")


if __name__ == "__main__":
    main()
