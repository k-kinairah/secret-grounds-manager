import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"
os.environ["SECRET_GROUNDS_AUTOMATIC_SHUTDOWN_ON_EXPIRATION"] = "false"

import client.client as client_module
from client.client import SecretGroundsClient
from client.customer_overlay import CustomerOverlayController
from client.lock_screen import LockScreenController
from client.session_recovery import SessionRecoveryStore


class CapturingWebSocket:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, object]] = []

    def send_json(self, payload: dict[str, object]) -> None:
        self.sent_messages.append(payload)


def create_client(temp_directory: str) -> SecretGroundsClient:
    client_module.AUTOMATIC_SHUTDOWN_ON_EXPIRATION = False
    return SecretGroundsClient(
        server_url="http://127.0.0.1:8000",
        pc_name="PC-CUSTOMER-UI",
        heartbeat_interval=1,
        request_timeout=1,
        recovery_store=SessionRecoveryStore(Path(temp_directory) / "recovery.json"),
        enable_lock_screen=False,
        enable_customer_overlay=False,
    )


def start_command(
    session_id: str,
    duration_seconds: int,
    scheduled_end_at: str | None = None,
    session_mode: str = "prepaid",
) -> dict[str, object]:
    return {
        "command_id": f"start-{session_id}",
        "type": "start_session",
        "payload": {
            "session_id": session_id,
            "duration_seconds": duration_seconds,
            "scheduled_end_at": scheduled_end_at
            or (datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)).isoformat(),
            "session_mode": session_mode,
        },
    }


def test_available_lock_screen() -> None:
    lock_screen = LockScreenController("PC-01", enable_gui=False, enable_dev_escape=True)
    model = lock_screen.build_view_model()
    assert model.mode == "available"
    assert model.brand == "Secret Grounds"
    assert model.pc_name == "PC-01"
    assert model.connection_label == "Offline"
    assert "cashier" in model.instruction.lower()


def test_active_prepaid_overlay() -> None:
    overlay = CustomerOverlayController("PC-01", enable_gui=False)
    overlay.show_session("Prepaid")
    overlay.update_time(65)
    model = overlay.build_view_model()
    assert model.title == "Session Active"
    assert model.timer_label == "Time Remaining"
    assert model.timer_value == "1:05"
    assert model.customer_name == "Guest"


def test_open_time_elapsed_overlay() -> None:
    overlay = CustomerOverlayController("PC-02", enable_gui=False)
    overlay.show_session("Open Time", is_elapsed=True)
    overlay.update_time(3723)
    model = overlay.build_view_model()
    assert model.title == "Open Time Active"
    assert model.timer_label == "Elapsed Time"
    assert model.timer_value == "1:02:03"


def test_member_display_has_no_sensitive_text() -> None:
    overlay = CustomerOverlayController("PC-03", enable_gui=False)
    overlay.show_session("Member Time", customer_name="Luffy")
    overlay.update_time(300)
    visible_text = " ".join(
        str(value)
        for value in overlay.build_view_model().__dict__.values()
        if value is not None
    ).lower()
    assert "member session active" in visible_text
    assert "luffy" in visible_text
    for forbidden in ("pin", "admin", "password", "payment"):
        assert forbidden not in visible_text


def test_reminders_fire_once_and_do_not_replay_after_reconnect() -> None:
    overlay = CustomerOverlayController("PC-04", enable_gui=False)
    overlay.show_session("Prepaid")
    overlay.update_time(901)
    assert overlay.update_time(900) == [
        "You have 15 minutes remaining. Please visit the cashier if you would like to add more time."
    ]
    assert overlay.update_time(899) == []
    overlay.set_connection_online(False)
    overlay.set_connection_online(True)
    assert overlay.update_time(898) == []


def test_add_time_confirmation_and_countdown_update() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        client = create_client(temp_directory)
        websocket = CapturingWebSocket()
        try:
            client.set_current_websocket(websocket)
            client.start_local_session(start_command("session-add-ui", 30), websocket)  # type: ignore[arg-type]
            acknowledgement = client.add_time_to_local_session(
                {
                    "command_id": "add-session-add-ui",
                    "type": "add_time",
                    "payload": {
                        "session_id": "session-add-ui",
                        "seconds": 60,
                        "scheduled_end_at": (
                            datetime.now(timezone.utc) + timedelta(seconds=90)
                        ).isoformat(),
                    },
                }
            )
            model = client.customer_overlay.build_view_model()
            assert acknowledgement["status"] == "completed"
            assert model.notice is not None
            assert "Time added" in model.notice
            assert model.timer_value in {"1:28", "1:29", "1:30"}
        finally:
            client.end_local_session(
                {
                    "command_id": "end-session-add-ui",
                    "type": "end_session",
                    "payload": {"session_id": "session-add-ui"},
                }
            )
            client.stop()


def test_grace_screen_uses_shutdown_countdown_and_final_warning_once() -> None:
    lock_screen = LockScreenController("PC-05", enable_gui=False, enable_dev_escape=True)
    lock_screen.show_expiration_grace(120)
    model = lock_screen.build_view_model()
    assert model.mode == "grace"
    assert model.countdown == "02:00"
    assert "cashier" in model.instruction.lower()
    assert "time" in model.instruction.lower()
    lock_screen.show_expiration_grace(30, final_warning=True)
    final_model = lock_screen.build_view_model()
    assert final_model.countdown == "00:30"
    assert final_model.is_final_warning


def test_original_timer_never_negative() -> None:
    overlay = CustomerOverlayController("PC-06", enable_gui=False)
    assert overlay.format_timer(-10) == "0:00"


def test_shutdown_transition_and_failure_are_customer_safe() -> None:
    lock_screen = LockScreenController("PC-07", enable_gui=False, enable_dev_escape=True)
    lock_screen.show_shutdown_transition()
    transition = lock_screen.build_view_model()
    assert transition.mode == "shutting_down"
    assert "shutting down" in transition.headline.lower()
    lock_screen.show_shutdown_failure()
    failed = lock_screen.build_view_model()
    assert failed.mode == "shutdown_failed"
    assert "cashier" in failed.instruction.lower()


def test_session_start_notification_clears_old_session_data() -> None:
    overlay = CustomerOverlayController("PC-08", enable_gui=False)
    overlay.show_session("Prepaid")
    overlay.show_time_added_confirmation(60, 120)
    assert overlay.build_view_model().notice is not None
    overlay.show_session("Open Time", is_elapsed=True)
    model = overlay.build_view_model()
    assert model.notice is None
    assert model.title == "Open Time Active"


def test_reconnect_restores_open_time_elapsed_state() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        client = create_client(temp_directory)
        started_at = datetime.now(timezone.utc) - timedelta(seconds=65)
        try:
            assert client.initialize_local_session(
                session_id="open-time-restore",
                duration_seconds=None,
                scheduled_end_at=started_at.isoformat(),
                session_mode="open_time",
            )
            time.sleep(0.2)
            model = client.customer_overlay.build_view_model()
            assert model.timer_label == "Elapsed Time"
            assert model.timer_value in {"1:05", "1:06"}
            assert not client.lock_screen.is_locked()
        finally:
            client.end_local_session(
                {
                    "command_id": "end-open-time-restore",
                    "type": "end_session",
                    "payload": {"session_id": "open-time-restore"},
                }
            )
            client.stop()


def test_power_executor_guard_is_enabled_for_tests() -> None:
    from client.remote_controls import default_power_executor

    try:
        default_power_executor(["shutdown", "/s", "/t", "0"])
    except RuntimeError as error:
        assert "blocked" in str(error)
    else:
        raise AssertionError("Tests must never reach the real shutdown handler")


def main() -> None:
    test_available_lock_screen()
    test_active_prepaid_overlay()
    test_open_time_elapsed_overlay()
    test_member_display_has_no_sensitive_text()
    test_reminders_fire_once_and_do_not_replay_after_reconnect()
    test_add_time_confirmation_and_countdown_update()
    test_grace_screen_uses_shutdown_countdown_and_final_warning_once()
    test_original_timer_never_negative()
    test_shutdown_transition_and_failure_are_customer_safe()
    test_session_start_notification_clears_old_session_data()
    test_reconnect_restores_open_time_elapsed_state()
    test_power_executor_guard_is_enabled_for_tests()
    print("Customer UI smoke tests passed.")


if __name__ == "__main__":
    main()
