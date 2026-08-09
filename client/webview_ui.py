from __future__ import annotations

import ctypes
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread, current_thread
from typing import TYPE_CHECKING, Any, Callable

import webview

from client.config import APP_VERSION

if TYPE_CHECKING:
    from client.customer_overlay import CustomerOverlayController
    from client.lock_screen import LockScreenController


MemberLoginAction = Callable[[str, str], str | None]


def _diagnostic(message: str) -> None:
    print(f"[webview-ui] {message}", flush=True)
    try:
        log_path = Path(sys.executable).resolve().with_name("client-webview-startup.log")
        timestamp = datetime.now(timezone.utc).isoformat()
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{timestamp} [webview-ui] {message}\n")
    except Exception:
        pass


def _thread_label() -> str:
    thread = current_thread()
    return f"thread={thread.name}/{thread.ident}"


def _handle_value(handle: Any) -> int:
    if handle is None:
        return 0
    try:
        return int(handle)
    except TypeError:
        value = getattr(handle, "value", None)
        return int(value or 0)


def web_ui_index_path() -> Path:
    return Path(__file__).resolve().parent / "web_ui" / "index.html"


def bundled_root_path() -> Path:
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        return Path(frozen_root)
    return Path(__file__).resolve().parents[1]


def secret_grounds_logo_url() -> str:
    candidates = [
        bundled_root_path() / "assets" / "secret-grounds-logo.png",
        Path(__file__).resolve().parent / "assets" / "secret-grounds-logo.png",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.as_uri()
    return ""


def primary_work_area() -> tuple[int, int, int, int]:
    try:
        from ctypes import wintypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return (rect.left, rect.top, rect.right, rect.bottom)
    except Exception:
        pass
    user32 = ctypes.windll.user32
    return (0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def bottom_right_geometry(width: int, height: int, margin: int = 16) -> tuple[int, int]:
    left, top, right, bottom = primary_work_area()
    return (right - width - margin, bottom - height - margin)


def _window_hwnd(window: webview.Window) -> int | None:
    native = getattr(window, "native", None)
    if native is None:
        return None
    try:
        return int(native.Handle.ToInt64())
    except Exception as error:
        _diagnostic(f"native hwnd unavailable: {error}")
        return None


def _taskbar_hwnd(window: webview.Window) -> int | None:
    hwnd = _window_hwnd(window)
    if not hwnd:
        return None
    try:
        from ctypes import wintypes

        get_ancestor = ctypes.windll.user32.GetAncestor
        get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
        get_ancestor.restype = wintypes.HWND
        root = get_ancestor(wintypes.HWND(hwnd), 2)  # GA_ROOT
        root_value = _handle_value(root)
        if root_value:
            return root_value
    except Exception as error:
        _diagnostic(f"native root hwnd lookup failed: {error}")
    return hwnd


def _describe_window_state(label: str, hwnd: int) -> None:
    try:
        from ctypes import wintypes

        handle = wintypes.HWND(hwnd)
        get_style = ctypes.windll.user32.GetWindowLongPtrW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        get_window = ctypes.windll.user32.GetWindow
        get_window.argtypes = [wintypes.HWND, wintypes.UINT]
        get_window.restype = wintypes.HWND
        get_parent = ctypes.windll.user32.GetParent
        get_parent.argtypes = [wintypes.HWND]
        get_parent.restype = wintypes.HWND
        get_ancestor = ctypes.windll.user32.GetAncestor
        get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
        get_ancestor.restype = wintypes.HWND
        is_visible = ctypes.windll.user32.IsWindowVisible
        is_visible.argtypes = [wintypes.HWND]
        is_visible.restype = wintypes.BOOL
        is_iconic = ctypes.windll.user32.IsIconic
        is_iconic.argtypes = [wintypes.HWND]
        is_iconic.restype = wintypes.BOOL
        root = get_ancestor(handle, 2)
        root_owner = get_ancestor(handle, 3)
        owner = get_window(handle, 4)  # GW_OWNER
        parent = get_parent(handle)
        style = get_style(handle, -16)
        exstyle = get_style(handle, -20)
        _diagnostic(
            f"{label} hwnd=0x{hwnd:X} root=0x{_handle_value(root):X} root_owner=0x{_handle_value(root_owner):X} "
            f"owner=0x{_handle_value(owner):X} parent=0x{_handle_value(parent):X} style=0x{style:X} "
            f"exstyle=0x{exstyle:X} appwindow={bool(exstyle & 0x00040000)} "
            f"toolwindow={bool(exstyle & 0x00000080)} visible={bool(is_visible(handle))} "
            f"minimized={bool(is_iconic(handle))} {_thread_label()}"
        )
    except Exception as error:
        _diagnostic(f"{label} window state diagnostics failed: {error}")


def move_window_exact(window: webview.Window, x: int, y: int) -> None:
    """Move a WinForms host without pywebview's Python 3.14 None/ctypes bug."""
    try:
        from ctypes import wintypes

        hwnd = wintypes.HWND(int(window.native.Handle.ToInt64()))
        set_window_pos = ctypes.windll.user32.SetWindowPos
        set_window_pos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        set_window_pos.restype = wintypes.BOOL
        flags = 0x0001 | 0x0004 | 0x0010  # NOSIZE | NOZORDER | NOACTIVATE
        if not set_window_pos(hwnd, None, x, y, 0, 0, flags):
            raise ctypes.WinError()

        rect = wintypes.RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise ctypes.WinError()
        actual_x, actual_y = rect.left, rect.top
        if abs(actual_x - x) > 1 or abs(actual_y - y) > 1:
            if not set_window_pos(
                hwnd,
                None,
                x + (x - actual_x),
                y + (y - actual_y),
                0,
                0,
                flags,
            ):
                raise ctypes.WinError()
    except Exception as error:
        _diagnostic(f"exact native move failed: {error}")


def apply_rounded_window_region(
    window: webview.Window,
    width: int,
    height: int,
    radius: int,
) -> None:
    """Clip the native WinForms host so CSS-transparent corners reveal the desktop."""
    try:
        from ctypes import wintypes

        hwnd = int(window.native.Handle.ToInt64())
        dpi = ctypes.windll.user32.GetDpiForWindow(hwnd) or 96
        scale = dpi / 96.0
        physical_width = max(1, int(round(width * scale)))
        physical_height = max(1, int(round(height * scale)))
        diameter = max(2, int(round(radius * 2 * scale)))
        create_region = ctypes.windll.gdi32.CreateRoundRectRgn
        create_region.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        create_region.restype = wintypes.HANDLE
        set_window_region = ctypes.windll.user32.SetWindowRgn
        set_window_region.argtypes = [wintypes.HWND, wintypes.HANDLE, wintypes.BOOL]
        set_window_region.restype = ctypes.c_int
        region = create_region(
            0,
            0,
            physical_width + 1,
            physical_height + 1,
            diameter,
            diameter,
        )
        if not region:
            raise ctypes.WinError()
        if not set_window_region(hwnd, region, True):
            ctypes.windll.gdi32.DeleteObject(region)
            raise ctypes.WinError()
    except Exception as error:
        _diagnostic(f"native rounded region failed: {error}")


def apply_window_opacity(window: webview.Window, opacity: float) -> None:
    """Apply uniform native alpha while preserving the window hit-test region."""
    try:
        from ctypes import wintypes

        hwnd = int(window.native.Handle.ToInt64())
        get_style = ctypes.windll.user32.GetWindowLongPtrW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style = ctypes.windll.user32.SetWindowLongPtrW
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        set_alpha = ctypes.windll.user32.SetLayeredWindowAttributes
        set_alpha.argtypes = [wintypes.HWND, wintypes.COLORREF, ctypes.c_ubyte, wintypes.DWORD]
        set_alpha.restype = wintypes.BOOL
        extended_style = get_style(hwnd, -20)
        set_style(hwnd, -20, extended_style | 0x00080000)
        alpha = max(1, min(255, round(opacity * 255)))
        if not set_alpha(hwnd, 0, alpha, 0x00000002):
            raise ctypes.WinError()
    except Exception as error:
        _diagnostic(f"native window opacity failed: {error}")


def apply_pill_native_geometry(pill: webview.Window, content_width: int, content_height: int) -> None:
    """Size/position the pill host snug to its CSS content, clipped to match."""
    x, y = bottom_right_geometry(content_width, content_height)
    pill.resize(content_width, content_height)
    move_window_exact(pill, x, y)
    apply_rounded_window_region(pill, content_width, content_height, 19)
    apply_window_opacity(pill, 1.0)


def set_native_window_visibility(
    window: webview.Window,
    is_visible: bool,
    *,
    activate: bool = False,
    on_top: bool = True,
) -> bool:
    """Show or hide a loaded WinForms host without blocking on Control.Invoke."""
    try:
        from ctypes import wintypes

        hwnd_value = _taskbar_hwnd(window)
        if hwnd_value is None:
            return False
        hwnd = wintypes.HWND(hwnd_value)
        _describe_window_state("native visibility before", hwnd_value)
        show_window = ctypes.windll.user32.ShowWindowAsync
        show_window.argtypes = [wintypes.HWND, ctypes.c_int]
        show_window.restype = wintypes.BOOL
        if not is_visible:
            _diagnostic(f"native visibility before hide {_thread_label()}")
            show_window(hwnd, 0)  # SW_HIDE
            _diagnostic(f"native visibility after hide {_thread_label()}")
            return True

        _diagnostic(f"native visibility before show visible={is_visible} activate={activate} on_top={on_top} {_thread_label()}")
        show_window(hwnd, 5 if activate else 4)  # SW_SHOW / SW_SHOWNOACTIVATE
        _diagnostic(f"native visibility after show visible={is_visible} {_thread_label()}")
        set_window_pos = ctypes.windll.user32.SetWindowPos
        set_window_pos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        set_window_pos.restype = wintypes.BOOL
        insert_after = wintypes.HWND(-1 if on_top else -2)  # HWND_TOPMOST / HWND_NOTOPMOST
        flags = 0x0001 | 0x0002 | 0x0010 | 0x4000  # NOSIZE | NOMOVE | NOACTIVATE | ASYNCWINDOWPOS
        _diagnostic(f"native visibility before set_window_pos on_top={on_top} {_thread_label()}")
        if not set_window_pos(hwnd, insert_after, 0, 0, 0, 0, flags):
            raise ctypes.WinError()
        _diagnostic(f"native visibility after set_window_pos on_top={on_top} {_thread_label()}")
        if activate:
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        _describe_window_state("native visibility after", hwnd_value)
        return True
    except Exception as error:
        _diagnostic(f"native window visibility failed: {error}")
        return False


def set_native_window_z_order(window: webview.Window, on_top: bool) -> bool:
    try:
        from ctypes import wintypes

        hwnd_value = _taskbar_hwnd(window)
        if hwnd_value is None:
            return False
        hwnd = wintypes.HWND(hwnd_value)
        _describe_window_state("native z-order before", hwnd_value)
        set_window_pos = ctypes.windll.user32.SetWindowPos
        set_window_pos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        set_window_pos.restype = wintypes.BOOL
        insert_after = wintypes.HWND(-1 if on_top else -2)  # HWND_TOPMOST / HWND_NOTOPMOST
        flags = 0x0001 | 0x0002 | 0x0010 | 0x4000  # NOSIZE | NOMOVE | NOACTIVATE | ASYNCWINDOWPOS
        _diagnostic(f"native z-order before set_window_pos on_top={on_top} {_thread_label()}")
        if not set_window_pos(hwnd, insert_after, 0, 0, 0, 0, flags):
            raise ctypes.WinError()
        _diagnostic(f"native z-order after set_window_pos on_top={on_top} {_thread_label()}")
        _describe_window_state("native z-order after", hwnd_value)
        return True
    except Exception as error:
        _diagnostic(f"native z-order failed: {error}")
        return False


def set_native_taskbar_presence(window: webview.Window, is_visible: bool) -> bool:
    try:
        from ctypes import wintypes

        native = getattr(window, "native", None)
        if native is None:
            _diagnostic(f"native taskbar presence skipped: native missing visible={is_visible}")
            return False
        hwnd_value = _taskbar_hwnd(window)
        if hwnd_value is None:
            _diagnostic(f"native taskbar presence skipped: hwnd missing visible={is_visible}")
            return False
        hwnd = wintypes.HWND(hwnd_value)
        get_style = ctypes.windll.user32.GetWindowLongPtrW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style = ctypes.windll.user32.SetWindowLongPtrW
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        get_window = ctypes.windll.user32.GetWindow
        get_window.argtypes = [wintypes.HWND, wintypes.UINT]
        get_window.restype = wintypes.HWND
        set_owner = ctypes.windll.user32.SetWindowLongPtrW
        set_window_pos = ctypes.windll.user32.SetWindowPos
        set_window_pos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        set_window_pos.restype = wintypes.BOOL
        _describe_window_state("native taskbar presence before", hwnd_value)
        extended_style = get_style(hwnd, -20)
        if is_visible:
            extended_style = (extended_style | 0x00040000) & ~0x00000080  # APPWINDOW, not TOOLWINDOW
            owner = get_window(hwnd, 4)  # GW_OWNER
            if owner:
                _diagnostic(
                    f"native taskbar presence clearing owner owner=0x{int(owner):X} {_thread_label()}"
                )
                set_owner(hwnd, -8, 0)  # GWLP_HWNDPARENT on a top-level window
        else:
            extended_style = (extended_style & ~0x00040000) | 0x00000080
        _diagnostic(f"native taskbar presence before exstyle visible={is_visible} {_thread_label()}")
        set_style(hwnd, -20, extended_style)
        _diagnostic(f"native taskbar presence after exstyle visible={is_visible} {_thread_label()}")
        flags = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020 | 0x4000
        _diagnostic(f"native taskbar presence before frame refresh visible={is_visible} {_thread_label()}")
        set_window_pos(hwnd, None, 0, 0, 0, 0, flags)
        _diagnostic(f"native taskbar presence after frame refresh visible={is_visible} {_thread_label()}")
        _diagnostic(f"native taskbar presence before ShowInTaskbar visible={is_visible} {_thread_label()}")
        native.ShowInTaskbar = is_visible
        _diagnostic(f"native taskbar presence after ShowInTaskbar visible={is_visible} {_thread_label()}")
        _describe_window_state("native taskbar presence after", hwnd_value)
        return True
    except Exception as error:
        _diagnostic(f"native taskbar presence failed: {error}")
        return False


def set_native_window_minimized(window: webview.Window, is_minimized: bool) -> bool:
    try:
        from ctypes import wintypes

        hwnd_value = _taskbar_hwnd(window)
        if hwnd_value is None:
            _diagnostic(f"native minimize skipped: native missing minimized={is_minimized}")
            return False
        hwnd = wintypes.HWND(hwnd_value)
        _describe_window_state("native minimize before", hwnd_value)
        show_window = ctypes.windll.user32.ShowWindowAsync
        show_window.argtypes = [wintypes.HWND, ctypes.c_int]
        show_window.restype = wintypes.BOOL
        _diagnostic(f"native minimize before ShowWindowAsync minimized={is_minimized} {_thread_label()}")
        if not show_window(hwnd, 6 if is_minimized else 9):  # SW_MINIMIZE / SW_RESTORE
            raise ctypes.WinError()
        _diagnostic(f"native minimize after ShowWindowAsync minimized={is_minimized} {_thread_label()}")
        _describe_window_state("native minimize after", hwnd_value)
        return True
    except Exception as error:
        _diagnostic(f"native minimize state failed: {error}")
        try:
            from ctypes import wintypes

            native = getattr(window, "native", None)
            if native is None:
                return False
            hwnd = wintypes.HWND(int(native.Handle.ToInt64()))
            show_window = ctypes.windll.user32.ShowWindow
            show_window.argtypes = [wintypes.HWND, ctypes.c_int]
            show_window.restype = wintypes.BOOL
            show_window(hwnd, 6 if is_minimized else 9)  # SW_MINIMIZE / SW_RESTORE
            return True
        except Exception as fallback_error:
            _diagnostic(f"native minimize fallback failed: {fallback_error}")
            return False


def js_set_state_script(state: dict[str, Any]) -> str:
    return f"window.SecretGroundsUI && window.SecretGroundsUI.setState({json.dumps(state)});"


class WebViewBridge:
    def __init__(self, host: "SecretGroundsWebViewHost", window_key: str) -> None:
        self.host = host
        self.window_key = window_key

    def emitAction(self, action: str, payload: dict[str, Any] | None = None) -> None:
        self.host.handle_action(self.window_key, action, payload or {})


@dataclass
class WebLockState:
    pc_name: str
    shop_display_name: str = "Secret Grounds"
    headline: str = "Available soon."
    instruction: str = "Please ask the cashier to begin your session."
    connection_state: str = "offline"
    login_message: str = ""
    is_login_processing: bool = False


@dataclass
class WebOverlayState:
    pc_name: str
    shop_display_name: str = "Secret Grounds"
    session_type: str = "Open Time"
    customer_name: str = "Guest"
    remaining_seconds: int | None = None
    elapsed_seconds: int = 0
    used_seconds: int = 0
    connection_state: str = "online"
    can_logout: bool = False


class SecretGroundsWebViewHost:
    """Local WebView2 presentation host.

    Python remains the controller: callers update dataclass state and the web
    layer only renders it. User actions cross back through WebViewBridge.
    """

    def __init__(
        self,
        pc_name: str = "PC-01",
        member_login_action: MemberLoginAction | None = None,
        logout_action: Callable[[], str | None] | None = None,
        open_setup_action: Callable[[], object] | None = None,
        lock_controller: LockScreenController | None = None,
        overlay_controller: CustomerOverlayController | None = None,
        enable_lock_window: bool = True,
        enable_overlay_window: bool = True,
        initial_lock_ready: Callable[[], bool] | None = None,
    ) -> None:
        self.pc_name = pc_name
        self.member_login_action = member_login_action
        self.logout_action = logout_action
        self.open_setup_action = open_setup_action
        self.lock_controller = lock_controller
        self.overlay_controller = overlay_controller
        self.enable_lock_window = enable_lock_window
        self.enable_overlay_window = enable_overlay_window
        self.initial_lock_ready = initial_lock_ready
        self.lock_state = WebLockState(pc_name=pc_name)
        self.overlay_state = WebOverlayState(pc_name=pc_name)
        self.windows: dict[str, webview.Window] = {}
        self.loaded: set[str] = set()
        self.lock = Lock()
        self.started = Event()
        self.ready = Event()
        self.lock_loaded = Event()
        self.last_overlay_size = (0, 0)
        self.last_pill_size = (132, 38)
        self.last_action: tuple[str, dict[str, Any]] | None = None
        self.ui_errors: list[dict[str, Any]] = []
        self.window_visibility: dict[str, bool] = {}
        self.window_on_top: dict[str, bool] = {}
        self.allow_close = False
        self.refresh_event = Event()
        self.refresh_stop = Event()
        self.pending_lock_refresh = False
        self.pending_overlay_refresh = False
        self.refresh_thread: Thread | None = None
        self.setup_active = False
        self._native_lock_deactivate_handler: Callable[..., None] | None = None
        if self.lock_controller is not None:
            self.lock_controller.set_presentation_refresh(self.request_lock_refresh)
        if self.overlay_controller is not None:
            self.overlay_controller.set_external_presentation_active(True)
            self.overlay_controller.set_presentation_refresh(self.request_overlay_refresh)

    def detach_controllers(self) -> None:
        if self.lock_controller is not None:
            self.lock_controller.set_presentation_refresh(None)
        if self.overlay_controller is not None:
            self.overlay_controller.set_presentation_refresh(None)

    def lock_payload(self) -> dict[str, Any]:
        if self.lock_controller is None:
            with self.lock:
                return {
                    "view": "lock",
                    "logo_url": secret_grounds_logo_url(),
                    "pc_name": self.lock_state.pc_name,
                    "shop_display_name": self.lock_state.shop_display_name,
                    "headline": self.lock_state.headline,
                    "instruction": self.lock_state.instruction,
                    "connection_state": self.lock_state.connection_state,
                    "login_message": self.lock_state.login_message,
                    "is_login_processing": self.lock_state.is_login_processing,
                }
        model = self.lock_controller.build_view_model()
        return {
            "view": "lock",
            "logo_url": secret_grounds_logo_url(),
            "pc_name": model.pc_name,
            "shop_display_name": model.brand,
            "mode": model.mode,
            "headline": model.headline,
            "instruction": model.instruction,
            "countdown": model.countdown,
            "accent": model.accent,
            "connection_state": "online" if model.connection_online else "offline",
            "login_message": model.login_message or "",
            "is_login_processing": model.is_login_processing,
            "locked_member_name": model.locked_member_name,
        }

    def overlay_payload(self) -> dict[str, Any]:
        if self.overlay_controller is None:
            with self.lock:
                state = self.overlay_state
                return {
                    "view": "overlay",
                    "logo_url": secret_grounds_logo_url(),
                    "pc_name": state.pc_name,
                    "shop_display_name": state.shop_display_name,
                    "session_type": state.session_type,
                    "customer_name": state.customer_name,
                    "remaining_seconds": state.remaining_seconds,
                    "elapsed_seconds": state.elapsed_seconds,
                    "used_seconds": state.used_seconds,
                    "connection_state": state.connection_state,
                    "can_logout": state.can_logout,
                }
        model = self.overlay_controller.build_view_model()
        return {
            "view": "overlay",
            "logo_url": secret_grounds_logo_url(),
            "pc_name": model.pc_name,
            "shop_display_name": model.eyebrow.title(),
            "session_type": model.session_type,
            "customer_name": model.customer_name,
            "remaining_seconds": None if model.is_elapsed else model.seconds_value,
            "elapsed_seconds": model.seconds_value if model.is_elapsed else 0,
            "used_seconds": model.used_seconds,
            "connection_state": "online" if model.connection_online else "offline",
            "can_logout": self.overlay_controller.logout_action is not None,
            "can_lock_pc": model.can_lock_pc,
            "notice": model.notice or "",
        }

    def create_windows(self, lock_only: bool = False) -> None:
        url = web_ui_index_path().as_uri()
        # The lock window is fullscreen and can't use the overlay/pill
        # off-screen trick, so it must actually start hidden until the
        # initial-reconciliation gate is satisfied -- otherwise it appears
        # on screen (unstyled/pre-content) the instant it's created, then
        # gets hidden again a moment later by refresh_lock()'s readiness
        # check, which is a visible flash. refresh_lock() already knows how
        # to show it once ready (even before WebView2 has loaded content),
        # so gating the initial visibility here just makes that the only
        # time it ever becomes visible.
        lock_initially_visible = self.enable_lock_window and self._initial_lock_is_ready()
        _diagnostic(f"before create_window lock url={url}")
        lock_window = webview.create_window(
            "Secret Grounds",
            url=url,
            js_api=WebViewBridge(self, "lock"),
            fullscreen=True,
            frameless=True,
            on_top=True,
            resizable=False,
            background_color="#0B1220",
            text_select=False,
            hidden=not lock_initially_visible,
        )
        _diagnostic("after create_window lock")
        self.windows = {"lock": lock_window}
        self.window_visibility = {"lock": lock_initially_visible}
        self._bind_window_events("lock", lock_window)
        if lock_only or not self.enable_overlay_window:
            return

        overlay_x, overlay_y = (-32000, -32000)
        _diagnostic("before create_window overlay")
        overlay_window = webview.create_window(
            "Secret Grounds Session",
            url=url,
            js_api=WebViewBridge(self, "overlay"),
            width=276,
            height=220,
            x=overlay_x,
            y=overlay_y,
            frameless=True,
            on_top=True,
            resizable=False,
            min_size=(1, 1),
            hidden=False,
            transparent=True,
            background_color="#000000",
            text_select=False,
        )
        _diagnostic("after create_window overlay")
        pill_x, pill_y = (-32000, -32000)
        _diagnostic("before create_window pill")
        pill_window = webview.create_window(
            "Secret Grounds",
            url=url,
            js_api=WebViewBridge(self, "pill"),
            width=178,
            height=38,
            x=pill_x,
            y=pill_y,
            frameless=True,
            on_top=False,
            resizable=False,
            min_size=(1, 1),
            hidden=False,
            transparent=True,
            background_color="#101a29",
            text_select=False,
        )
        _diagnostic("after create_window pill")
        self.windows = {"lock": lock_window, "overlay": overlay_window, "pill": pill_window}
        self.window_visibility = {
            "lock": lock_initially_visible,
            "overlay": False,
            "pill": False,
        }
        self._bind_window_events("overlay", overlay_window)
        self._bind_window_events("pill", pill_window)

    def _bind_window_events(self, key: str, window: webview.Window) -> None:
        window.events.loaded += lambda key=key: self._handle_loaded(key)
        window.events.closing += self._handle_closing
        if key == "lock":
            window.events.minimized += self._restore_locked_window

    def _initial_lock_is_ready(self) -> bool:
        return self.initial_lock_ready is None or self.initial_lock_ready()

    def _handle_closing(self) -> bool | None:
        return None if self.allow_close else False

    def start(
        self,
        debug: bool = False,
        start_callback: Callable[[], None] | None = None,
        lock_only: bool = False,
    ) -> None:
        self.create_windows(lock_only=lock_only)

        def on_start() -> None:
            _diagnostic(f"inside startup callback renderer={webview.renderer}")
            self._on_start()
            if start_callback is not None:
                start_callback()
            _diagnostic("startup callback returned")

        _diagnostic("before webview.start gui=edgechromium")
        webview.start(on_start, gui="edgechromium", debug=debug, private_mode=True)
        _diagnostic("after webview.start")

    def _on_start(self) -> None:
        _diagnostic("webview host on_start")
        self.started.set()
        self._bind_native_lock_guard()
        if self.lock_controller is not None or self.overlay_controller is not None:
            self.refresh_thread = Thread(
                target=self._refresh_loop,
                name="secret-grounds-webview-refresh",
                daemon=True,
            )
            self.refresh_thread.start()
            self.request_lock_refresh()
            self.request_overlay_refresh()

    def _handle_loaded(self, key: str) -> None:
        _diagnostic(f"page-loaded event key={key}")
        self.loaded.add(key)
        window = self.windows.get(key)
        if window is not None and self.window_visibility.get(key) is False:
            set_native_window_visibility(window, False)
        if key == "lock":
            self.lock_loaded.set()
            if self.lock_controller is not None:
                self.request_lock_refresh()
            else:
                self.refresh_lock()
        elif key == "overlay":
            if self.overlay_controller is not None:
                self.request_overlay_refresh()
            else:
                self.refresh_overlay()
        elif key == "pill":
            if self.overlay_controller is not None:
                self.request_overlay_refresh()
            else:
                self.render_pill()

    def request_lock_refresh(self) -> None:
        _diagnostic("request lock refresh")
        with self.lock:
            self.pending_lock_refresh = True
        self.refresh_event.set()

    def request_overlay_refresh(self) -> None:
        _diagnostic("request overlay refresh")
        with self.lock:
            self.pending_overlay_refresh = True
        self.refresh_event.set()

    def _refresh_loop(self) -> None:
        while not self.refresh_stop.is_set():
            self.refresh_event.wait(0.5)
            self.refresh_event.clear()
            with self.lock:
                refresh_lock = self.pending_lock_refresh
                refresh_overlay = self.pending_overlay_refresh
                self.pending_lock_refresh = False
                self.pending_overlay_refresh = False
            try:
                if refresh_lock:
                    self.refresh_lock()
                if refresh_overlay:
                    self.refresh_overlay()
                lock_ready = not self.enable_lock_window or "lock" in self.loaded
                overlay_ready = (
                    not self.enable_overlay_window
                    or ("overlay" in self.loaded and "pill" in self.loaded)
                )
                if lock_ready and overlay_ready:
                    self.ready.set()
            except Exception as error:
                _diagnostic(f"presentation refresh failed: {error}")

    def _evaluate(self, key: str, state: dict[str, Any]) -> None:
        window = self.windows.get(key)
        if window is None or key not in self.loaded:
            _diagnostic(f"skip evaluate_js key={key} loaded={key in self.loaded}")
            return
        try:
            _diagnostic(f"before evaluate_js key={key}")
            window.evaluate_js(js_set_state_script(state))
            _diagnostic(f"after evaluate_js key={key}")
        except Exception as error:
            _diagnostic(f"evaluate_js failed key={key}: {error}")

    def show_lock(self) -> None:
        self._evaluate("lock", self.lock_payload())
        for key in ("overlay", "pill"):
            if key in self.loaded:
                self._set_window_visible(key, False)
        lock_window = self.windows.get("lock")
        if lock_window is not None:
            self._set_window_visible("lock", True)
            self._set_window_on_top("lock", lock_window, True)
            self._focus_lock_inputs()

    def refresh_lock(self) -> None:
        _diagnostic(
            "refresh_lock "
            f"loaded={'lock' in self.loaded} initial_ready={self._initial_lock_is_ready()} "
            f"visibility={self.window_visibility.get('lock')}"
        )
        if "lock" not in self.loaded:
            if self.enable_lock_window and self._initial_lock_is_ready():
                _diagnostic("lock ready before load; showing hidden lock to trigger WebView load")
                self._set_window_visible("lock", True)
            return
        if self.enable_lock_window and not self._initial_lock_is_ready():
            self.hide_lock()
            return
        if self.lock_controller is None:
            self.show_lock()
            return
        model = self.lock_controller.build_view_model()
        if self.enable_lock_window and model.is_locked and not model.dev_escape_active:
            self.show_lock()
        else:
            self.hide_lock()

    def hide_lock(self) -> None:
        self._set_window_visible("lock", False)

    def show_overlay(self, overlay_state: WebOverlayState) -> None:
        _diagnostic(f"show_overlay direct state={overlay_state}")
        with self.lock:
            self.overlay_state = overlay_state
        self.hide_lock()
        self.render_overlay()
        overlay = self.windows.get("overlay")
        pill = self.windows.get("pill")
        if pill is not None:
            self._set_window_visible("pill", False)
        if overlay is not None:
            self._set_window_visible("overlay", True)
            self._set_window_on_top("overlay", overlay, True)

    def render_overlay(self) -> None:
        self._evaluate("overlay", self.overlay_payload())

    def refresh_overlay(self) -> None:
        _diagnostic(
            "refresh_overlay "
            f"loaded_overlay={'overlay' in self.loaded} loaded_pill={'pill' in self.loaded}"
        )
        if self.overlay_controller is None:
            if "overlay" not in self.loaded:
                return
            self.render_overlay()
            return
        model = self.overlay_controller.build_view_model()
        _diagnostic(
            "refresh_overlay model "
            f"visible={model.is_visible} collapsed={model.is_collapsed} "
            f"seconds={model.seconds_value} elapsed={model.is_elapsed}"
        )
        overlay = self.windows.get("overlay")
        pill = self.windows.get("pill")
        if not self.enable_overlay_window or not model.is_visible:
            if overlay is not None:
                self._set_window_visible("overlay", False)
            if pill is not None:
                self._set_window_visible("pill", False)
            return
        self.hide_lock()
        if overlay is not None and "overlay" not in self.loaded:
            _diagnostic("overlay visible before load; showing hidden overlay to trigger WebView load")
            self._set_window_visible("overlay", True)
            return
        if "overlay" not in self.loaded:
            _diagnostic("overlay visible but overlay window missing/not loaded")
            return
        if model.is_collapsed:
            self.collapse_overlay()
        else:
            self.restore_overlay()

    def render_pill(self) -> None:
        self._evaluate("pill", {"view": "pill", "logo_url": secret_grounds_logo_url(), "pc_name": self.pc_name})

    def show_collapsed_control(self) -> None:
        pill = self.windows.get("pill")
        if pill is None:
            return
        self.render_pill()
        width, height = self.last_pill_size
        apply_pill_native_geometry(pill, width, height)
        self._set_window_visible("pill", True)
        self._set_window_on_top("pill", pill, False)

    def collapse_overlay(self) -> None:
        _diagnostic("collapse_overlay invoked")
        overlay = self.windows.get("overlay")
        pill = self.windows.get("pill")
        if overlay is not None:
            self._set_window_on_top("overlay", overlay, False)
            self._set_window_visible("overlay", False)
        if pill is not None:
            self.show_collapsed_control()

    def restore_overlay(self) -> None:
        _diagnostic("restore_overlay invoked")
        pill = self.windows.get("pill")
        overlay = self.windows.get("overlay")
        if pill is not None:
            self._set_window_visible("pill", False)
        if overlay is not None:
            self.render_overlay()
            self._set_window_visible("overlay", True)
            self._set_window_on_top("overlay", overlay, True)

    def _set_window_visible(self, key: str, is_visible: bool) -> None:
        window = self.windows.get(key)
        if window is None:
            _diagnostic(f"set_window_visible skipped key={key} missing visible={is_visible}")
            return
        with self.lock:
            if self.window_visibility.get(key) == is_visible:
                _diagnostic(f"set_window_visible noop key={key} visible={is_visible}")
                return
            self.window_visibility[key] = is_visible
        try:
            _diagnostic(f"set_window_visible key={key} visible={is_visible}")
            if not set_native_window_visibility(
                window,
                is_visible,
                activate=is_visible and key == "lock",
                on_top=is_visible and key != "pill",
            ):
                if is_visible:
                    window.show()
                else:
                    window.hide()
        except Exception:
            with self.lock:
                self.window_visibility[key] = not is_visible
            raise

    def _set_window_on_top(self, key: str, window: webview.Window, on_top: bool) -> None:
        # SetWindowPos(HWND_NOTOPMOST) does not just clear the topmost flag --
        # it re-inserts the window just below the topmost band, i.e. above
        # every other regular window currently in the z-order (a documented
        # Win32 quirk). The collapsed pill's on-top state gets reasserted
        # here on every per-second refresh tick while collapsed, so without
        # this no-op guard that reassertion kept yanking the pill back above
        # other apps (e.g. Discord) even though it was already correctly
        # non-topmost -- the window's z-order should only actually change on
        # a real state transition, not on every refresh.
        if self.window_on_top.get(key) == on_top:
            _diagnostic(f"set_window_on_top noop key={key} on_top={on_top}")
            return
        self.window_on_top[key] = on_top
        _diagnostic(f"set_window_on_top key={key} on_top={on_top}")
        if not set_native_window_z_order(window, on_top):
            window.on_top = on_top
        _diagnostic(f"set_window_on_top complete key={key} on_top={on_top}")

    def update_overlay_size(self, width: int, height: int) -> None:
        width = max(120, min(360, width))
        height = max(38, min(420, height))
        if (width, height) == self.last_overlay_size:
            return
        self.last_overlay_size = (width, height)
        overlay = self.windows.get("overlay")
        if overlay is None:
            return
        x, y = bottom_right_geometry(width, height)
        overlay.resize(width, height)
        move_window_exact(overlay, x, y)
        apply_rounded_window_region(overlay, width, height, 18)

    def geometry_snapshot(self, key: str) -> tuple[int, int, int, int, int, int] | None:
        window = self.windows.get(key)
        if window is None:
            return None
        left, top, right, bottom = primary_work_area()
        try:
            from ctypes import wintypes

            hwnd = wintypes.HWND(int(window.native.Handle.ToInt64()))
            rect = wintypes.RECT()
            if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                raise ctypes.WinError()
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            x = rect.left
            y = rect.top
        except Exception:
            return None
        return (width, height, x, y, right - x - width, bottom - y - height)

    def handle_action(self, window_key: str, action: str, payload: dict[str, Any]) -> None:
        self.last_action = (action, payload)
        if action == "ui_error":
            error = {"window": window_key, **payload}
            self.ui_errors.append(error)
            _diagnostic(f"JavaScript error: {error}")
            return
        if action == "content_size" and window_key == "overlay":
            self.update_overlay_size(int(payload.get("width", 276)), int(payload.get("height", 220)))
            return
        if action == "content_size" and window_key == "pill":
            pill = self.windows.get("pill")
            if pill is not None:
                width = max(110, min(220, int(payload.get("width", 130))))
                height = max(32, min(60, int(payload.get("height", 38))))
                self.last_pill_size = (width, height)
                apply_pill_native_geometry(pill, width, height)
            return
        if action == "collapse_overlay":
            if self.overlay_controller is not None:
                self.overlay_controller.handle_minimize_clicked()
                return
            self.collapse_overlay()
            return
        if action == "restore_overlay":
            if self.overlay_controller is not None:
                self.overlay_controller.handle_restore_clicked()
                return
            self.restore_overlay()
            return
        if action == "open_setup":
            self._open_setup()
            return
        if action == "development_escape" and self.lock_controller is not None:
            self.lock_controller.use_development_escape()
            return
        if action == "toggle_pin" and self.lock_controller is not None:
            self.lock_controller.set_pin_visible(bool(payload.get("revealed")))
            return
        if action == "logout":
            if self.overlay_controller is not None:
                self.overlay_controller.handle_logout_clicked()
                return
            if self.logout_action is not None:
                message = self.logout_action()
                if message:
                    print(message)
            return
        if action == "lock_pc":
            # The confirmation dialog ("Lock this computer? ...") already
            # happened client-side in the overlay UI before this fires.
            if self.overlay_controller is not None:
                self.overlay_controller.handle_lock_pc_clicked()
            return
        if action == "member_login":
            username = str(payload.get("username", "")).strip()
            pin = str(payload.get("pin", "")).strip()
            if self.lock_controller is not None:
                self.lock_controller.submit_member_login(username, pin)
                return
            if self.member_login_action is not None:
                Thread(target=self._run_member_login, args=(username, pin), daemon=True).start()
            return
        if action == "unlock_session":
            pin = str(payload.get("pin", "")).strip()
            if self.lock_controller is not None:
                self.lock_controller.submit_session_unlock(pin)
            return

    def _focus_lock_inputs(self) -> None:
        if "lock" not in self.loaded:
            return
        lock_window = self.windows.get("lock")
        if lock_window is None:
            return
        try:
            lock_window.evaluate_js(
                "window.SecretGroundsUI && window.SecretGroundsUI.focusLock && "
                "window.SecretGroundsUI.focusLock();"
            )
        except Exception as error:
            _diagnostic(f"lock input focus failed: {error}")

    def _restore_locked_window(self) -> None:
        if self.lock_controller is not None and not self.lock_controller.is_locked():
            return
        self.show_lock()

    def _bind_native_lock_guard(self) -> None:
        lock_window = self.windows.get("lock")
        if lock_window is None or not self.enable_lock_window:
            return
        native = getattr(lock_window, "native", None)
        if native is None:
            return

        def reassert(sender: Any, _args: Any) -> None:
            if self.allow_close or self.setup_active:
                return
            if self.lock_controller is not None:
                model = self.lock_controller.build_view_model()
                if not model.is_locked or model.dev_escape_active:
                    return
            try:
                sender.TopMost = True
                sender.Show()
                sender.Activate()
            except Exception as error:
                _diagnostic(f"lock focus reassertion failed: {error}")

        self._native_lock_deactivate_handler = reassert
        try:
            native.Deactivate += reassert
        except Exception as error:
            _diagnostic(f"native lock focus guard failed: {error}")

    def _open_setup(self) -> None:
        action = (
            self.lock_controller.open_setup_wizard
            if self.lock_controller is not None
            else self.open_setup_action
        )
        if action is None:
            return
        lock_window = self.windows.get("lock")
        self.setup_active = True
        if lock_window is not None:
            self._set_window_on_top("lock", lock_window, False)
        try:
            result = action()
        except Exception:
            self.setup_active = False
            self.refresh_lock()
            raise

        def restore_after_setup() -> None:
            if isinstance(result, Thread):
                result.join()
            self.setup_active = False
            self.refresh_lock()

        if isinstance(result, Thread):
            Thread(target=restore_after_setup, daemon=True).start()
        else:
            self.setup_active = False
            self.refresh_lock()

    def shutdown(self) -> None:
        self.allow_close = True
        self.refresh_stop.set()
        self.refresh_event.set()
        if (
            self.refresh_thread is not None
            and self.refresh_thread is not current_thread()
            and self.refresh_thread.is_alive()
        ):
            self.refresh_thread.join(timeout=2)
        self.detach_controllers()
        for window in tuple(self.windows.values()):
            try:
                window.destroy()
            except Exception:
                pass

    def _run_member_login(self, username: str, pin: str) -> None:
        with self.lock:
            self.lock_state.is_login_processing = True
            self.lock_state.login_message = ""
        self.show_lock()
        message = self.member_login_action(username, pin) if self.member_login_action is not None else None
        with self.lock:
            self.lock_state.is_login_processing = False
            self.lock_state.login_message = message or ""
        self.show_lock()


def run_webview_customer_ui_preview() -> None:
    host = SecretGroundsWebViewHost(
        pc_name="PC-01",
        member_login_action=lambda username, pin: print(f"member_login action: {username}/{len(pin) * '*'}") or None,
        logout_action=lambda: print("logout action") or None,
        open_setup_action=lambda: print("open_setup action"),
    )

    def preview_started() -> None:
        _diagnostic("single-window lock preview ready; no demo-state worker started")

    print(f"Secret Grounds WebView UI preview v{APP_VERSION}. Close the window after visual review.", flush=True)
    host.start(debug=False, start_callback=preview_started, lock_only=True)


def run_webview_customer_overlay_preview(view: str, right_offset: int = 16) -> None:
    states: dict[str, dict[str, Any]] = {
        "open": {
            "view": "overlay",
            "pc_name": "PC-01",
            "session_type": "Open Time",
            "customer_name": "Guest",
            "elapsed_seconds": 1 * 3600 + 23 * 60 + 45,
            "used_seconds": 1 * 3600 + 23 * 60 + 45,
            "connection_state": "online",
            "can_logout": True,
        },
        "package": {
            "view": "overlay",
            "pc_name": "PC-01",
            "session_type": "Package",
            "customer_name": "Guest",
            "remaining_seconds": 44 * 60 + 12,
            "used_seconds": 15 * 60 + 48,
            "connection_state": "online",
            "can_logout": True,
        },
        "member": {
            "view": "overlay",
            "pc_name": "PC-01",
            "session_type": "Member Time",
            "customer_name": "Kinairah",
            "remaining_seconds": 5 * 3600 + 59 * 60 + 34,
            "used_seconds": 25 * 60 + 16,
            "connection_state": "online",
            "can_logout": True,
        },
        "pill": {"view": "pill", "pc_name": "PC-01"},
    }
    if view not in states:
        raise ValueError(f"Unknown overlay preview: {view}")

    is_pill = view == "pill"
    initial_width, initial_height = ((132, 38) if is_pill else (276, 286))
    _, _, right, bottom = primary_work_area()
    _diagnostic(f"before create_window overlay-preview view={view}")
    window = webview.create_window(
        f"Secret Grounds {view.title()} Preview",
        url=web_ui_index_path().as_uri(),
        width=initial_width,
        height=initial_height,
        x=right - initial_width - right_offset,
        y=bottom - initial_height - 16,
        frameless=True,
        on_top=True,
        resizable=False,
        min_size=(1, 1),
        hidden=True,
        transparent=True,
        background_color="#000000",
        text_select=False,
    )
    _diagnostic(f"after create_window overlay-preview view={view}")

    def page_loaded() -> None:
        _diagnostic(f"page-loaded event overlay-preview view={view}")
        _diagnostic(f"before evaluate_js overlay-preview view={view}")
        window.evaluate_js(js_set_state_script(states[view]))
        _diagnostic(f"after evaluate_js overlay-preview view={view}")
        size = window.evaluate_js(
            "(() => { const target = document.querySelector('.overlay-card, .overlay-pill'); "
            "const rect = target.getBoundingClientRect(); "
            "return { width: Math.ceil(rect.width), height: Math.ceil(rect.height) }; })();"
        )
        width = max(110, min(360, int(size["width"])))
        height = max(38, min(420, int(size["height"])))
        _diagnostic(f"measured overlay-preview view={view} size={width}x{height}")
        window.resize(width, height)
        move_window_exact(window, right - width - right_offset, bottom - height - 16)
        apply_rounded_window_region(window, width, height, 19 if is_pill else 18)
        if is_pill:
            apply_window_opacity(window, 0.74)
        window.show()
        window.on_top = True

    window.events.loaded += page_loaded
    window.events.closing += lambda: False

    def preview_started() -> None:
        _diagnostic(f"overlay preview startup callback view={view} renderer={webview.renderer}")

    print(f"Secret Grounds WebView overlay preview: {view}", flush=True)
    webview.start(preview_started, gui="edgechromium", debug=False, private_mode=True)


if __name__ == "__main__":
    run_webview_customer_ui_preview()
