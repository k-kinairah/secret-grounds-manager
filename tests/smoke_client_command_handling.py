import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class NoGuiLockScreen:
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def hide_for_session(self) -> None:
        pass

    def show_locked(self, reason: str | None = None) -> None:
        pass

    def show_expiration_grace(self, remaining_seconds: int, final_warning: bool = False) -> None:
        pass

    def set_connection_online(self, is_online: bool) -> None:
        pass


class FakeWebSocket:
    def __init__(self, commands: list[dict]) -> None:
        self._commands = list(commands)
        self.sent: list[dict] = []

    def receive_json(self) -> dict:
        return self._commands.pop(0)

    def send_json(self, payload: dict) -> None:
        self.sent.append(payload)


class RaisingRecoveryStore:
    def __init__(self) -> None:
        self.save_calls = 0

    def save(self, **kwargs) -> None:
        self.save_calls += 1
        raise OSError("simulated disk failure writing recovery file")

    def load(self):
        return None

    def clear(self) -> None:
        pass


def make_client(recovery_store=None):
    from client.client import SecretGroundsClient

    return SecretGroundsClient(
        server_url="http://127.0.0.1:18001",
        pc_name="PC-TEST",
        heartbeat_interval=5,
        request_timeout=2,
        lock_screen=NoGuiLockScreen(),
        recovery_store=recovery_store,
        enable_lock_screen=False,
        enable_customer_overlay=False,
    )


def test_handler_exception_sends_failed_ack_without_crashing() -> None:
    client = make_client(recovery_store=RaisingRecoveryStore())
    websocket = FakeWebSocket(
        [
            {
                "command_id": "start-1",
                "type": "start_session",
                "payload": {
                    "session_id": "session-1",
                    "duration_seconds": 3600,
                    "scheduled_end_at": "2026-01-01T00:00:00+00:00",
                },
            }
        ]
    )

    # Must not raise: handle_command's own try/except is the safety net that
    # keeps the WebSocket read loop (and the whole command thread) alive even
    # when a handler blows up.
    client.handle_command(websocket)

    assert len(websocket.sent) == 2
    assert websocket.sent[0]["status"] == "received"
    assert websocket.sent[1]["status"] == "failed"
    # The failed ack must carry the real reason (not a generic message) so it
    # is visible all the way through to the server-side event log/dashboard.
    assert "simulated disk failure" in (websocket.sent[1]["message"] or "")
    assert client.active_session is None


def test_unsupported_command_type_still_gets_two_acks() -> None:
    client = make_client()
    websocket = FakeWebSocket(
        [{"command_id": "weird-1", "type": "not_a_real_command", "payload": {}}]
    )

    client.handle_command(websocket)

    assert len(websocket.sent) == 2
    assert websocket.sent[0]["status"] == "received"
    assert websocket.sent[1]["status"] == "failed"
    assert "Unsupported command type" in (websocket.sent[1]["message"] or "")


def test_recovery_save_failure_rolls_back_active_session() -> None:
    client = make_client(recovery_store=RaisingRecoveryStore())

    result = client.initialize_local_session(
        session_id="session-2",
        duration_seconds=3600,
        scheduled_end_at="2026-01-01T00:00:00+00:00",
    )

    assert result is False
    assert client.active_session is None
    assert client.recovery_store.save_calls == 1


def test_recovery_save_success_starts_session_normally() -> None:
    client = make_client()

    result = client.initialize_local_session(
        session_id="session-3",
        duration_seconds=1,
        scheduled_end_at="2026-01-01T00:00:00+00:00",
    )

    assert result is True
    assert client.active_session is not None
    assert client.active_session.session_id == "session-3"
    client.active_session.stop_event.set()


def main() -> None:
    test_handler_exception_sends_failed_ack_without_crashing()
    test_unsupported_command_type_still_gets_two_acks()
    test_recovery_save_failure_rolls_back_active_session()
    test_recovery_save_success_starts_session_normally()
    print("Client command handling smoke tests passed.")


if __name__ == "__main__":
    main()
