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
    member_display_name: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "session_id": session_id,
        "duration_seconds": duration_seconds,
        "scheduled_end_at": scheduled_end_at
        or (datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)).isoformat(),
        "session_mode": session_mode,
    }
    if member_display_name is not None:
        payload["member_display_name"] = member_display_name
    return {
        "command_id": f"start-{session_id}",
        "type": "start_session",
        "payload": payload,
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
    assert model.timer_label == "Remaining Time"
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


def test_lock_screen_member_login_validation_message() -> None:
    lock_screen = LockScreenController(
        "PC-LOGIN",
        enable_gui=False,
        enable_dev_escape=True,
        member_login_action=lambda _username, _pin: None,
    )
    lock_screen._set_login_message("Username or PIN is incorrect.")
    assert lock_screen.state.login_message == "Username or PIN is incorrect."
    assert lock_screen.state.is_login_processing is False


def test_member_start_command_shows_display_name() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        client = create_client(temp_directory)
        websocket = CapturingWebSocket()
        try:
            client.set_current_websocket(websocket)
            ack = client.start_local_session(
                start_command(
                    "member-ui",
                    300,
                    session_mode="member_balance",
                    member_display_name="Nami",
                ),
                websocket,  # type: ignore[arg-type]
            )
            model = client.customer_overlay.build_view_model()
            assert ack["status"] == "completed"
            assert model.title == "Member Session Active"
            assert model.customer_name == "Nami"
            assert not client.lock_screen.is_locked()
        finally:
            client.end_local_session(
                {
                    "command_id": "end-member-ui",
                    "type": "end_session",
                    "payload": {"session_id": "member-ui"},
                }
            )
            client.stop()


def test_customer_logout_sends_completion_and_locks() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        client = create_client(temp_directory)
        websocket = CapturingWebSocket()
        try:
            client.set_current_websocket(websocket)
            client.start_local_session(
                start_command("member-logout", 300, session_mode="member_balance"),
                websocket,  # type: ignore[arg-type]
            )
            message = client.customer_logout_current_session()
            assert message is None
            assert client.lock_screen.is_locked()
            assert any(
                sent.get("event_type") == "session_finished"
                and sent.get("message") == "customer_logout"
                for sent in websocket.sent_messages
            )
        finally:
            client.stop()


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


def test_open_time_elapsed_ticks_schedule_gui_refresh() -> None:
    # Root cause of the frozen "Elapsed Time" overlay: update_time() used to
    # return early for is_elapsed sessions *before* reaching
    # schedule_gui_refresh(), so the background open-time timer thread kept
    # self.state.seconds_value correct every second (build_view_model() below
    # always looked fine) while the live Tk widget was never told to repaint.
    # This directly regressions the scheduling call the countdown path always
    # had and the elapsed path was missing.
    overlay = CustomerOverlayController("PC-09", enable_gui=False)
    overlay.show_session("Open Time", is_elapsed=True)
    refresh_calls: list[int] = []
    overlay.schedule_gui_refresh = lambda: refresh_calls.append(1)
    overlay.update_time(60)
    overlay.update_time(61)
    overlay.update_time(62)
    assert len(refresh_calls) == 3
    assert overlay.build_view_model().timer_value == overlay.format_timer(62)


def test_timed_session_ticks_schedule_gui_refresh() -> None:
    overlay = CustomerOverlayController("PC-10", enable_gui=False)
    overlay.show_session("Prepaid", is_elapsed=False)
    refresh_calls: list[int] = []
    overlay.schedule_gui_refresh = lambda: refresh_calls.append(1)
    overlay.update_time(120)
    overlay.update_time(119)
    assert len(refresh_calls) == 2
    assert overlay.build_view_model().timer_value == "1:59"


def test_reinitializing_active_open_time_session_does_not_spawn_second_timer() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        client = create_client(temp_directory)
        started_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        try:
            assert client.initialize_local_session(
                session_id="open-time-dup",
                duration_seconds=None,
                scheduled_end_at=started_at.isoformat(),
                session_mode="open_time",
            )
            first_thread = client.active_session.timer_thread
            assert first_thread is not None
            assert client.initialize_local_session(
                session_id="open-time-dup",
                duration_seconds=None,
                scheduled_end_at=started_at.isoformat(),
                session_mode="open_time",
            )
            assert client.active_session.timer_thread is first_thread
            assert first_thread.is_alive()
        finally:
            client.end_local_session(
                {
                    "command_id": "end-open-time-dup",
                    "type": "end_session",
                    "payload": {"session_id": "open-time-dup"},
                }
            )
            client.stop()


def test_end_session_hides_customer_overlay() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        client = create_client(temp_directory)
        websocket = CapturingWebSocket()
        try:
            client.set_current_websocket(websocket)
            client.start_local_session(
                start_command("session-hide-overlay", 30),
                websocket,  # type: ignore[arg-type]
            )
            assert client.customer_overlay.state.is_visible
            client.end_local_session(
                {
                    "command_id": "end-session-hide-overlay",
                    "type": "end_session",
                    "payload": {"session_id": "session-hide-overlay"},
                }
            )
            assert not client.customer_overlay.state.is_visible
            assert client.lock_screen.is_locked()
        finally:
            client.stop()


def test_resync_preserves_collapsed_state_but_new_session_expands() -> None:
    # A reconnect/resync refresh (member login updating the display name, or
    # re-adopting an already-active session) must not yank a deliberately
    # minimized overlay back open. A genuinely new session should, so the
    # customer notices it started.
    overlay = CustomerOverlayController("PC-11", enable_gui=False)
    overlay.show_session("Prepaid", is_new_session=True)
    overlay.state.is_collapsed = True
    overlay.show_session("Prepaid", customer_name="Renamed", is_new_session=False)
    assert overlay.state.is_collapsed is True
    overlay.show_session("Prepaid", is_new_session=True)
    assert overlay.state.is_collapsed is False


def test_collapsed_overlay_still_receives_timer_updates() -> None:
    # Heartbeat/timer/command handling must keep running while the overlay
    # is collapsed -- collapse is a pure display flag on the controller, not
    # something the session/network layer checks.
    overlay = CustomerOverlayController("PC-12", enable_gui=False)
    overlay.show_session("Open Time", is_elapsed=True)
    overlay.state.is_collapsed = True
    overlay.update_time(10)
    overlay.update_time(11)
    assert overlay.state.seconds_value == 11
    assert overlay.build_view_model().timer_value == overlay.format_timer(11)


def test_used_time_is_tracked_separately_from_the_primary_timer() -> None:
    overlay = CustomerOverlayController("PC-13", enable_gui=False)
    overlay.show_session("Prepaid")
    overlay.update_time(3600)
    model = overlay.build_view_model()
    assert model.used_time_value in {"0s", "1s"}

    # A resync (is_new_session=False) must not reset the used-time clock --
    # only a genuinely new session does. int() truncation means the used-time
    # value only ticks over once a full second has actually elapsed.
    time.sleep(1.1)
    overlay.show_session("Prepaid", is_new_session=False)
    resynced_model = overlay.build_view_model()
    assert resynced_model.used_time_value != "0s"

    # A genuinely new session resets it.
    overlay.show_session("Prepaid", is_new_session=True)
    fresh_model = overlay.build_view_model()
    assert fresh_model.used_time_value in {"0s", "1s"}


def test_is_member_session_flag_distinguishes_member_from_other_modes() -> None:
    member_overlay = CustomerOverlayController("PC-14", enable_gui=False)
    member_overlay.show_session("Member Time", customer_name="Kinairah")
    assert member_overlay.build_view_model().is_member_session is True

    prepaid_overlay = CustomerOverlayController("PC-15", enable_gui=False)
    prepaid_overlay.show_session("Prepaid")
    assert prepaid_overlay.build_view_model().is_member_session is False

    open_time_overlay = CustomerOverlayController("PC-16", enable_gui=False)
    open_time_overlay.show_session("Open Time", is_elapsed=True)
    assert open_time_overlay.build_view_model().is_member_session is False


def test_format_used_time_is_human_readable_not_clock_style() -> None:
    overlay = CustomerOverlayController("PC-17", enable_gui=False)
    assert overlay.format_used_time(5) == "5s"
    assert overlay.format_used_time(65) == "1m 05s"
    assert overlay.format_used_time(3600) == "1h 00m"
    assert overlay.format_used_time(4325) == "1h 12m"


def test_lock_screen_remains_non_bypassable() -> None:
    # The active-session overlay was deliberately redesigned to be a normal,
    # minimizable window (native title bar, taskbar entry). The lock screen
    # must never gain any of that -- it stays fullscreen, always-on-top,
    # borderless, and closable only via the gated development escape.
    source = (PROJECT_ROOT / "client" / "lock_screen.py").read_text(encoding="utf-8")
    assert 'self.root.attributes("-fullscreen", True)' in source
    assert 'self.root.attributes("-topmost", True)' in source
    assert "self.root.overrideredirect(True)" in source
    assert 'self.root.protocol("WM_DELETE_WINDOW", self._prevent_close)' in source
    assert "def _prevent_close(self) -> None:" in source
    assert '"<Control-Shift-F12>"' in source
    # The close handler must re-show the lock screen, never let it close.
    prevent_close = source[source.index("def _prevent_close"):source.index("def _ensure_visible")]
    assert "_ensure_visible" in prevent_close


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
    test_lock_screen_member_login_validation_message()
    test_member_start_command_shows_display_name()
    test_customer_logout_sends_completion_and_locks()
    test_reminders_fire_once_and_do_not_replay_after_reconnect()
    test_add_time_confirmation_and_countdown_update()
    test_grace_screen_uses_shutdown_countdown_and_final_warning_once()
    test_original_timer_never_negative()
    test_shutdown_transition_and_failure_are_customer_safe()
    test_session_start_notification_clears_old_session_data()
    test_reconnect_restores_open_time_elapsed_state()
    test_open_time_elapsed_ticks_schedule_gui_refresh()
    test_timed_session_ticks_schedule_gui_refresh()
    test_reinitializing_active_open_time_session_does_not_spawn_second_timer()
    test_end_session_hides_customer_overlay()
    test_resync_preserves_collapsed_state_but_new_session_expands()
    test_collapsed_overlay_still_receives_timer_updates()
    test_used_time_is_tracked_separately_from_the_primary_timer()
    test_is_member_session_flag_distinguishes_member_from_other_modes()
    test_format_used_time_is_human_readable_not_clock_style()
    test_lock_screen_remains_non_bypassable()
    test_power_executor_guard_is_enabled_for_tests()
    print("Customer UI smoke tests passed.")


if __name__ == "__main__":
    main()
