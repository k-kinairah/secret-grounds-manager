import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class NoOpRecoveryStore:
    def save(self, **kwargs) -> None:
        pass

    def load(self):
        return None

    def clear(self) -> None:
        pass

    def mark_pending_completion(self, **kwargs) -> None:
        pass


class FakeResponse:
    def __init__(self, status_code: int = 200, json_data: dict | None = None) -> None:
        self.status_code = status_code
        self._json = json_data or {}

    def json(self) -> dict:
        return self._json


class FakeSession:
    def __init__(self, responder) -> None:
        self._responder = responder
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        return self._responder(url, json)


def make_client(responder):
    from client.client import SecretGroundsClient

    client = SecretGroundsClient(
        server_url="http://server.test",
        pc_name="PC-TEST",
        heartbeat_interval=5,
        request_timeout=2,
        recovery_store=NoOpRecoveryStore(),
        enable_lock_screen=False,
        enable_customer_overlay=False,
    )
    client.session = FakeSession(responder)
    return client


def test_overlay_eligibility_member_only() -> None:
    from client.customer_overlay import CustomerOverlayController

    overlay = CustomerOverlayController("PC-01", enable_gui=False, lock_pc_action=lambda: None)
    overlay.show_session("Member Time", customer_name="Kinairah")
    assert overlay.build_view_model().can_lock_pc is True, "Member Time session must show Lock PC"

    overlay.show_session("Open Time", is_elapsed=True)
    assert overlay.build_view_model().can_lock_pc is False, "Open Time must never show Lock PC"

    overlay.show_session("Package")
    assert overlay.build_view_model().can_lock_pc is False, "Package/prepaid must never show Lock PC"

    # Presentation-only gate: with no lock_pc_action wired at all, even a
    # Member Time session must not claim eligibility.
    overlay_without_action = CustomerOverlayController("PC-01", enable_gui=False)
    overlay_without_action.show_session("Member Time", customer_name="Kinairah")
    assert overlay_without_action.build_view_model().can_lock_pc is False


def test_lock_pc_action_wiring_and_failure_message() -> None:
    from client.customer_overlay import CustomerOverlayController

    calls: list[int] = []

    def lock_action() -> str | None:
        calls.append(1)
        return "Only member sessions can be locked."

    overlay = CustomerOverlayController("PC-01", enable_gui=False, lock_pc_action=lock_action)
    overlay.show_session("Member Time", customer_name="Kinairah")
    overlay.handle_lock_pc_clicked()
    assert calls == [1], "Lock PC click must invoke lock_pc_action exactly once"
    assert overlay.build_view_model().notice == "Only member sessions can be locked."


def test_lock_screen_member_locked_mode_and_unlock_wiring() -> None:
    from client.lock_screen import LockScreenController

    unlock_calls: list[str] = []

    def unlock_action(pin: str) -> str | None:
        unlock_calls.append(pin)
        return None

    lock_screen = LockScreenController(
        "PC-01", enable_gui=False, enable_dev_escape=False, session_unlock_action=unlock_action
    )
    lock_screen.show_member_locked("Kinairah")
    model = lock_screen.build_view_model()
    assert model.mode == "member_locked"
    assert model.is_locked is True, "member-locked screen must reuse the same is_locked-gated window"
    assert model.locked_member_name == "Kinairah"
    # Distinct from the idle screen's copy/accent.
    assert model.headline != "Available soon."

    lock_screen.submit_session_unlock("1234")
    for _ in range(50):
        if unlock_calls:
            break
        time.sleep(0.05)
    assert unlock_calls == ["1234"], "PIN submission must call session_unlock_action with just the PIN"


def test_lock_current_session_rejects_non_member_and_no_session() -> None:
    from client.client import LocalSession

    client = make_client(lambda url, payload: FakeResponse(200, {}))
    assert client.lock_current_session() == "No active session to lock."

    client.active_session = LocalSession(
        session_id="sess-open",
        deadline_monotonic=0.0,
        scheduled_end_at="2026-01-01T00:00:00+00:00",
        stop_event=Event(),
        timer_thread=None,
        session_mode="open_time",
    )
    assert client.lock_current_session() == "Only member sessions can be locked."
    assert client.active_session.customer_locked is False


def test_client_lock_and_unlock_http_flow() -> None:
    from client.client import LocalSession

    def responder(url: str, payload: dict) -> FakeResponse:
        if url.endswith("/client/session-lock"):
            assert payload["session_id"] == "sess-1"
            return FakeResponse(200, {"session": {"session_id": "sess-1"}})
        if url.endswith("/client/session-unlock"):
            assert payload["pin"] == "1234"
            return FakeResponse(
                200, {"session": {"session_id": "sess-1"}, "member_display_name": "Kinairah"}
            )
        raise AssertionError(f"unexpected url {url}")

    client = make_client(responder)
    client.active_session = LocalSession(
        session_id="sess-1",
        deadline_monotonic=0.0,
        scheduled_end_at="2026-01-01T00:00:00+00:00",
        stop_event=Event(),
        timer_thread=None,
        session_mode="member_balance",
        customer_name="Kinairah",
    )

    message = client.lock_current_session()
    assert message is None
    assert client.active_session.customer_locked is True, "lock state must be set on the SAME session object"
    assert client.active_session.session_id == "sess-1", "locking must not create/replace the session"
    assert client.lock_screen.build_view_model().mode == "member_locked"
    assert client.customer_overlay.build_view_model().is_visible is False, "overlay must hide while locked"

    message = client.unlock_current_session("1234")
    assert message is None
    assert client.active_session.customer_locked is False
    assert client.active_session.session_id == "sess-1", "unlocking must not reset/replace the session"
    assert client.lock_screen.build_view_model().is_locked is False
    assert client.customer_overlay.build_view_model().is_visible is True, "unlocking must restore the same overlay"


def test_unlock_wrong_pin_keeps_pc_locked() -> None:
    from client.client import LocalSession

    def responder(url: str, payload: dict) -> FakeResponse:
        if url.endswith("/client/session-unlock"):
            return FakeResponse(403, {"detail": "Incorrect PIN. Please try again."})
        raise AssertionError(f"unexpected url {url}")

    client = make_client(responder)
    client.active_session = LocalSession(
        session_id="sess-wrong-pin",
        deadline_monotonic=0.0,
        scheduled_end_at="2026-01-01T00:00:00+00:00",
        stop_event=Event(),
        timer_thread=None,
        session_mode="member_balance",
        customer_name="Kinairah",
        customer_locked=True,
    )
    message = client.unlock_current_session("0000")
    assert message == "Incorrect PIN. Please try again."
    assert client.active_session.customer_locked is True, "a failed PIN must never clear the lock"


def test_cashier_override_command_clears_local_lock() -> None:
    from client.client import LocalSession

    client = make_client(lambda url, payload: FakeResponse(200, {}))
    client.active_session = LocalSession(
        session_id="sess-override",
        deadline_monotonic=0.0,
        scheduled_end_at="2026-01-01T00:00:00+00:00",
        stop_event=Event(),
        timer_thread=None,
        session_mode="member_balance",
        customer_name="Kinairah",
        customer_locked=True,
    )
    client.lock_screen.show_member_locked("Kinairah")

    ack = client.handle_clear_customer_lock_command({"command_id": "cmd-1"})
    assert ack["status"] == "completed"
    assert client.active_session.customer_locked is False
    assert client.customer_overlay.build_view_model().is_visible is True
    assert client.lock_screen.build_view_model().is_locked is False

    # A second override on an already-unlocked session must be a harmless no-op.
    ack_again = client.handle_clear_customer_lock_command({"command_id": "cmd-2"})
    assert ack_again["status"] == "completed"


def test_reconciliation_restores_member_locked_without_exposing_overlay() -> None:
    client = make_client(lambda url, payload: FakeResponse(200, {}))
    future_end = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    server_state = {"server_time": datetime.now(timezone.utc).isoformat()}
    server_session = {
        "session_id": "sess-reconnect",
        "session_mode": "member_balance",
        "status": "active",
        "scheduled_end_at": future_end,
        "member_name_snapshot": "Kinairah",
        "customer_locked": True,
    }

    adopted = client.adopt_server_session(server_state, server_session)
    try:
        assert adopted is True
        assert client.active_session is not None
        assert client.active_session.session_id == "sess-reconnect"
        assert client.active_session.customer_locked is True
        # Timer continuity: the countdown thread must be running even
        # though the customer is locked out of the desktop.
        assert client.active_session.timer_thread is not None
        assert client.active_session.timer_thread.is_alive()
        assert client.lock_screen.build_view_model().mode == "member_locked"
        assert client.lock_screen.build_view_model().locked_member_name == "Kinairah"
        assert client.customer_overlay.build_view_model().is_visible is False, (
            "reconnect must never briefly expose the normal session overlay"
        )
    finally:
        if client.active_session is not None:
            client.active_session.stop_event.set()


def test_webview_bridge_routes_lock_and_unlock_actions() -> None:
    from client.customer_overlay import CustomerOverlayController
    from client.lock_screen import LockScreenController
    from client.webview_ui import SecretGroundsWebViewHost

    lock_calls: list[int] = []
    unlock_calls: list[str] = []
    overlay = CustomerOverlayController(
        "PC-01", enable_gui=False, lock_pc_action=lambda: lock_calls.append(1) or None
    )
    lock = LockScreenController(
        "PC-01",
        enable_gui=False,
        enable_dev_escape=False,
        session_unlock_action=lambda pin: unlock_calls.append(pin) or None,
    )
    host = SecretGroundsWebViewHost(pc_name="PC-01", lock_controller=lock, overlay_controller=overlay)

    host.handle_action("overlay", "lock_pc", {})
    assert lock_calls == [1]

    host.handle_action("lock", "unlock_session", {"pin": "5678"})
    for _ in range(50):
        if unlock_calls:
            break
        time.sleep(0.05)
    assert unlock_calls == ["5678"]

    overlay.show_session("Member Time", customer_name="Kinairah")
    overlay_payload = host.overlay_payload()
    assert overlay_payload["can_lock_pc"] is True

    lock.show_member_locked("Kinairah")
    lock_payload = host.lock_payload()
    assert lock_payload["locked_member_name"] == "Kinairah"
    assert lock_payload["mode"] == "member_locked"

    host.detach_controllers()


def main() -> None:
    test_overlay_eligibility_member_only()
    test_lock_pc_action_wiring_and_failure_message()
    test_lock_screen_member_locked_mode_and_unlock_wiring()
    test_lock_current_session_rejects_non_member_and_no_session()
    test_client_lock_and_unlock_http_flow()
    test_unlock_wrong_pin_keeps_pc_locked()
    test_cashier_override_command_clears_local_lock()
    test_reconciliation_restores_member_locked_without_exposing_overlay()
    test_webview_bridge_routes_lock_and_unlock_actions()
    print("Customer lock client smoke tests passed.")


if __name__ == "__main__":
    main()
