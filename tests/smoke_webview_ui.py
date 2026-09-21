import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from client.client import run_client_with_production_ui
import client.customer_overlay as customer_overlay_module
from client.customer_overlay import CustomerOverlayController
from client.lock_screen import LockScreenController
from client.webview_ui import (
    SecretGroundsWebViewHost,
    WebOverlayState,
    js_set_state_script,
    set_native_window_visibility,
    web_ui_index_path,
)

# Reminder crossings would otherwise spawn a real, audible PowerShell
# text-to-speech process (client/voice_announcer.py) -- silence it for the
# whole file regardless of REMINDER_VOICE_ENABLED's default.
customer_overlay_module.announce_reminder = lambda *_a, **_kw: None


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
            self.initial_reconciliation_complete = threading.Event()
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


def test_webview_collapse_shows_desktop_control() -> None:
    class FakeWindow:
        def __init__(self) -> None:
            self.show_calls = 0
            self.hide_calls = 0
            self.on_top = True
            self.size = (0, 0)
            self.position = (0, 0)

        def show(self) -> None:
            self.show_calls += 1

        def hide(self) -> None:
            self.hide_calls += 1

        def resize(self, width: int, height: int) -> None:
            self.size = (width, height)

        def move(self, x: int, y: int) -> None:
            self.position = (x, y)

    host = SecretGroundsWebViewHost()
    overlay_window = FakeWindow()
    pill_window = FakeWindow()
    host.windows = {
        "overlay": overlay_window,  # type: ignore[dict-item]
        "pill": pill_window,  # type: ignore[dict-item]
    }
    host.loaded = {"overlay", "pill"}
    host.window_visibility = {"overlay": True, "pill": False}
    host.collapse_overlay()
    assert overlay_window.hide_calls == 1
    assert pill_window.show_calls == 1
    assert pill_window.on_top is False
    assert host.window_visibility["pill"] is True


def test_screen_geometry_reconciliation_only_acts_on_a_real_change() -> None:
    """Regression for the diskless-boot bug: a generic display driver runs
    at a lower resolution for the first few seconds, the lock window
    fullscreens to that, then Windows applies the monitor's real resolution
    -- leaving the window's stale, smaller size exposing a strip of desktop
    (and the taskbar) at the edge. The fix must re-cover the window when the
    resolution changes, and do nothing on every other tick."""
    import client.webview_ui as webview_ui_module

    calls: list[tuple[object, int, int]] = []
    original_resize = webview_ui_module.resize_and_reposition_fullscreen
    sizes = iter([(1920, 1080), (1920, 1080), (1024, 768), (1024, 768)])
    original_screen_size = webview_ui_module.current_screen_size

    def fake_resize(window, width, height):
        calls.append((window, width, height))
        return True

    webview_ui_module.resize_and_reposition_fullscreen = fake_resize
    webview_ui_module.current_screen_size = lambda: next(sizes)
    try:
        host = SecretGroundsWebViewHost()
        window = object()
        host.windows = {"lock": window}  # type: ignore[dict-item]

        host._reconcile_screen_geometry()  # None -> 1920x1080: first-ever size, seeds it
        assert calls == [(window, 1920, 1080)]

        host._reconcile_screen_geometry()  # unchanged -> no-op
        assert len(calls) == 1

        host._reconcile_screen_geometry()  # 1920x1080 -> 1024x768: real change
        assert calls == [(window, 1920, 1080), (window, 1024, 768)]

        host._reconcile_screen_geometry()  # unchanged again -> no-op
        assert len(calls) == 2
    finally:
        webview_ui_module.resize_and_reposition_fullscreen = original_resize
        webview_ui_module.current_screen_size = original_screen_size


def test_collapsed_pill_gets_taskbar_presence_and_starts_minimized() -> None:
    # The customer's own request: the pill must not float on top of whatever
    # they're doing. It should be a real taskbar button, minimized by
    # default, that they click to check their remaining time -- and the
    # Deactivate guard (bound in _bind_pill_auto_minimize_guard) sends it
    # back down the moment they click back into their game.
    import client.webview_ui as webview_ui_module

    class FakeWindow:
        def show(self) -> None:
            pass

        def hide(self) -> None:
            pass

        def resize(self, width: int, height: int) -> None:
            pass

        def move(self, x: int, y: int) -> None:
            pass

    order: list[str] = []
    taskbar_calls: list[tuple[object, bool]] = []
    minimize_calls: list[object] = []
    restore_calls: list[object] = []
    original_taskbar_presence = webview_ui_module.set_native_taskbar_presence
    original_minimize = webview_ui_module.minimize_native_window
    original_restore = webview_ui_module.restore_native_window

    def fake_taskbar_presence(window: object, visible: bool) -> bool:
        order.append("taskbar")
        taskbar_calls.append((window, visible))
        return True

    def fake_minimize(window: object) -> bool:
        order.append("minimize")
        minimize_calls.append(window)
        return True

    def fake_restore(window: object) -> bool:
        restore_calls.append(window)
        return True

    webview_ui_module.set_native_taskbar_presence = fake_taskbar_presence
    webview_ui_module.minimize_native_window = fake_minimize
    webview_ui_module.restore_native_window = fake_restore
    try:
        host = SecretGroundsWebViewHost()
        pill_window = FakeWindow()
        host.windows = {"pill": pill_window}  # type: ignore[dict-item]
        host.loaded = {"pill"}
        host.window_visibility = {"pill": False}
        host.show_collapsed_control()
        assert taskbar_calls == [(pill_window, True)]
        assert minimize_calls == [pill_window]
        assert restore_calls == [pill_window]
        # Taskbar presence and minimize must apply only after the pill is
        # actually positioned/shown, in that order.
        assert order == ["taskbar", "minimize"]

        # refresh_overlay() calls collapse_overlay() -> show_collapsed_control()
        # on every refresh tick (about once a second) for as long as the
        # session stays collapsed, not just on the transition into being
        # collapsed -- window_visibility["pill"] is now True, exactly like a
        # second tick while nothing has changed. None of these three calls
        # may repeat: restore_native_window would pull the pill back out of
        # its minimized state every second, and the taskbar toggle/minimize
        # would force a visible redraw/re-animate every second -- together
        # this is what actually read as "glitching" on screen.
        host.show_collapsed_control()
        assert taskbar_calls == [(pill_window, True)]
        assert minimize_calls == [pill_window]
        assert restore_calls == [pill_window]
    finally:
        webview_ui_module.set_native_taskbar_presence = original_taskbar_presence
        webview_ui_module.minimize_native_window = original_minimize
        webview_ui_module.restore_native_window = original_restore


def test_pill_geometry_is_not_reapplied_every_tick() -> None:
    # Reported live: the pill felt "choppy" specifically while restored on
    # screen (clicked from the taskbar) and when it dropped back down.
    # show_collapsed_control() runs on every refresh tick (~1s) for as long
    # as the session stays collapsed, and it was calling
    # apply_pill_native_geometry() -- resize, move, and re-cut the rounded
    # window region, which forces a repaint -- unconditionally every single
    # tick, including while the customer had the pill open on screen. The
    # pill's content never changes (logo + pc_name, no countdown text), so
    # there was nothing to actually reapply after the first tick.
    import client.webview_ui as webview_ui_module

    class FakeWindow:
        def show(self) -> None:
            pass

        def hide(self) -> None:
            pass

        def resize(self, width: int, height: int) -> None:
            pass

        def move(self, x: int, y: int) -> None:
            pass

    geometry_calls: list[tuple[object, int, int]] = []
    original_geometry = webview_ui_module.apply_pill_native_geometry
    original_taskbar_presence = webview_ui_module.set_native_taskbar_presence
    original_minimize = webview_ui_module.minimize_native_window
    original_restore = webview_ui_module.restore_native_window
    webview_ui_module.apply_pill_native_geometry = lambda window, width, height: geometry_calls.append(
        (window, width, height)
    )
    webview_ui_module.set_native_taskbar_presence = lambda window, visible: True
    webview_ui_module.minimize_native_window = lambda window: True
    webview_ui_module.restore_native_window = lambda window: True
    try:
        host = SecretGroundsWebViewHost()
        pill_window = FakeWindow()
        host.windows = {"pill": pill_window}  # type: ignore[dict-item]
        host.loaded = {"pill"}
        host.window_visibility = {"pill": False}
        host.last_pill_size = (132, 38)

        host.show_collapsed_control()
        assert geometry_calls == [(pill_window, 132, 38)]

        # Repeated ticks with the same (unchanged) content size: no more
        # geometry calls, and thus no more forced repaints, while the pill
        # sits minimized -- or while the customer has it open on screen.
        host.show_collapsed_control()
        host.show_collapsed_control()
        assert geometry_calls == [(pill_window, 132, 38)]

        # A genuine size change (content actually different) must still be
        # picked up.
        host.last_pill_size = (150, 38)
        host.show_collapsed_control()
        assert geometry_calls == [(pill_window, 132, 38), (pill_window, 150, 38)]
    finally:
        webview_ui_module.apply_pill_native_geometry = original_geometry
        webview_ui_module.set_native_taskbar_presence = original_taskbar_presence
        webview_ui_module.minimize_native_window = original_minimize
        webview_ui_module.restore_native_window = original_restore


def test_pill_auto_minimizes_when_it_loses_focus() -> None:
    import client.webview_ui as webview_ui_module

    class FakeEvent:
        def __init__(self) -> None:
            self.handlers: list[Any] = []

        def __iadd__(self, handler: Any) -> "FakeEvent":
            self.handlers.append(handler)
            return self

    class FakeNative:
        def __init__(self) -> None:
            self.Deactivate = FakeEvent()

    class FakeWindow:
        def __init__(self) -> None:
            self.native = FakeNative()

    minimize_calls: list[object] = []
    original_minimize = webview_ui_module.minimize_native_window
    webview_ui_module.minimize_native_window = lambda window: minimize_calls.append(window) or True
    try:
        host = SecretGroundsWebViewHost()
        pill_window = FakeWindow()
        host.windows = {"pill": pill_window}  # type: ignore[dict-item]
        host._bind_pill_auto_minimize_guard()
        assert len(pill_window.native.Deactivate.handlers) == 1

        # Simulate the customer clicking back into their game: the window
        # loses focus, .NET fires Deactivate.
        pill_window.native.Deactivate.handlers[0](pill_window.native, None)
        assert minimize_calls == [pill_window]
    finally:
        webview_ui_module.minimize_native_window = original_minimize


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
    test_webview_collapse_shows_desktop_control()
    test_collapsed_pill_gets_taskbar_presence_and_starts_minimized()
    test_pill_geometry_is_not_reapplied_every_tick()
    test_pill_auto_minimizes_when_it_loses_focus()
    test_screen_geometry_reconciliation_only_acts_on_a_real_change()
    test_native_visibility_returns_false_without_a_loaded_winforms_host()
    test_javascript_errors_are_recorded_by_the_bridge()
    print("WebView UI smoke tests passed.")


if __name__ == "__main__":
    main()
