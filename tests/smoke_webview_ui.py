import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from client.client import run_client_with_production_ui
from client.customer_overlay import CustomerOverlayController
from client.lock_screen import LockScreenController
from client.webview_ui import (
    SecretGroundsWebViewHost,
    WebOverlayState,
    js_set_state_script,
    set_native_window_visibility,
    web_ui_index_path,
)


def test_web_ui_assets_are_local() -> None:
    index_path = web_ui_index_path()
    assert index_path.is_file()
    web_ui_dir = index_path.parent
    assert (web_ui_dir / "styles.css").is_file()
    assert (web_ui_dir / "app.js").is_file()
    assert (PROJECT_ROOT / "client" / "assets" / "secret-grounds-logo.png").is_file()


def test_python_state_bridge_serializes_plain_json() -> None:
    state = WebOverlayState(
        pc_name="PC-01",
        session_type="Member Time",
        customer_name="kinairah",
        remaining_seconds=21574,
        used_seconds=1516,
        connection_state="online",
        can_logout=True,
    )
    payload = {
        "view": "overlay",
        "pc_name": state.pc_name,
        "session_type": state.session_type,
        "customer_name": state.customer_name,
        "remaining_seconds": state.remaining_seconds,
        "used_seconds": state.used_seconds,
        "connection_state": state.connection_state,
        "can_logout": state.can_logout,
    }
    script = js_set_state_script(payload)
    assert script.startswith("window.SecretGroundsUI")
    assert json.loads(script[script.index("(") + 1 : script.rindex(")")]) == payload


def test_production_bridge_renders_controller_state_and_routes_actions() -> None:
    login_finished = threading.Event()
    logout_calls: list[int] = []

    def login(username: str, pin: str) -> str | None:
        assert username == "kinairah"
        assert pin == "1234"
        login_finished.set()
        return None

    lock = LockScreenController(
        "PC-01",
        enable_gui=False,
        enable_dev_escape=True,
        member_login_action=login,
    )
    overlay = CustomerOverlayController(
        "PC-01",
        enable_gui=False,
        logout_action=lambda: logout_calls.append(1) or None,
    )
    host = SecretGroundsWebViewHost(
        pc_name="PC-01",
        lock_controller=lock,
        overlay_controller=overlay,
    )
    assert overlay.external_presentation_active
    overlay.apply_settings(enable_gui=True)
    assert not overlay.enable_gui

    lock.set_connection_online(True)
    assert host.lock_payload()["connection_state"] == "online"
    host.handle_action("lock", "member_login", {"username": "kinairah", "pin": "1234"})
    assert login_finished.wait(2)

    overlay.show_session("Open Time", is_elapsed=True)
    overlay.update_time(83)
    open_payload = host.overlay_payload()
    assert open_payload["session_type"] == "Open Time"
    assert open_payload["elapsed_seconds"] == 83
    assert open_payload["remaining_seconds"] is None

    overlay.show_session("Package", is_elapsed=False)
    overlay.update_time(21574)
    package_payload = host.overlay_payload()
    assert package_payload["session_type"] == "Package"
    assert package_payload["remaining_seconds"] == 21574
    assert package_payload["used_seconds"] >= 0

    overlay.show_session("Member Time", customer_name="Kinairah", is_elapsed=False)
    member_payload = host.overlay_payload()
    assert member_payload["session_type"] == "Member Time"
    assert member_payload["customer_name"] == "Kinairah"

    host.handle_action("overlay", "collapse_overlay", {})
    assert overlay.build_view_model().is_collapsed
    host.handle_action("pill", "restore_overlay", {})
    assert not overlay.build_view_model().is_collapsed
    host.handle_action("overlay", "logout", {})
    assert logout_calls == [1]
    host.detach_controllers()


def test_webview_startup_failure_uses_tk_fallback() -> None:
    class FakeLock:
        def __init__(self) -> None:
            self.enable_gui = False
            self.starts = 0

        def start(self) -> None:
            self.starts += 1

    class FakeOverlay:
        def __init__(self) -> None:
            self.enable_gui = False
            self.external_presentation_active = True

        def set_external_presentation_active(self, is_active: bool) -> None:
            self.external_presentation_active = is_active

        def build_view_model(self):
            return SimpleNamespace(is_visible=False)

    class FailingHost:
        def __init__(self, **_kwargs) -> None:
            self.detached = False

        def start(self, **_kwargs) -> None:
            raise RuntimeError("simulated missing WebView2 runtime")

        def detach_controllers(self) -> None:
            self.detached = True

    class FakeClient:
        def __init__(self) -> None:
            self.pc_name = "PC-01"
            self.lock_screen = FakeLock()
            self.customer_overlay = FakeOverlay()
            self.stop_event = threading.Event()
            self.run_calls = 0

        def run_forever(self) -> None:
            self.run_calls += 1

    client = FakeClient()
    run_client_with_production_ui(
        client,  # type: ignore[arg-type]
        enable_lock_screen=True,
        enable_customer_overlay=True,
        webview_host_factory=FailingHost,
    )
    assert client.lock_screen.starts == 1
    assert client.lock_screen.enable_gui is True
    assert client.customer_overlay.enable_gui is True
    assert client.customer_overlay.external_presentation_active is False
    assert client.run_calls == 1


def test_window_visibility_changes_are_idempotent() -> None:
    class FakeWindow:
        def __init__(self) -> None:
            self.show_calls = 0
            self.hide_calls = 0

        def show(self) -> None:
            self.show_calls += 1

        def hide(self) -> None:
            self.hide_calls += 1

    host = SecretGroundsWebViewHost()
    window = FakeWindow()
    host.windows = {"lock": window}  # type: ignore[dict-item]
    host.window_visibility = {"lock": True}
    host._set_window_visible("lock", True)
    host._set_window_visible("lock", False)
    host._set_window_visible("lock", False)
    host._set_window_visible("lock", True)
    assert window.show_calls == 1
    assert window.hide_calls == 1


def test_native_visibility_returns_false_without_a_loaded_winforms_host() -> None:
    window = SimpleNamespace(native=None)
    assert not set_native_window_visibility(window, True)  # type: ignore[arg-type]


def test_javascript_errors_are_recorded_by_the_bridge() -> None:
    host = SecretGroundsWebViewHost()
    host.handle_action("lock", "ui_error", {"message": "test error", "line": 7})
    assert host.ui_errors == [{"window": "lock", "message": "test error", "line": 7}]


def main() -> None:
    test_web_ui_assets_are_local()
    test_python_state_bridge_serializes_plain_json()
    test_production_bridge_renders_controller_state_and_routes_actions()
    test_webview_startup_failure_uses_tk_fallback()
    test_window_visibility_changes_are_idempotent()
    test_native_visibility_returns_false_without_a_loaded_winforms_host()
    test_javascript_errors_are_recorded_by_the_bridge()
    print("WebView UI smoke tests passed.")


if __name__ == "__main__":
    main()
