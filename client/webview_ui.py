from __future__ import annotations

import ctypes
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread, Timer, current_thread
from typing import TYPE_CHECKING, Any, Callable

import webview

from client.config import APP_VERSION
from client.utils import get_logo_ico_path


def _webview_icon_kwargs() -> dict[str, str]:
    """Pass the Secret Grounds .ico to webview.start so the EdgeChromium
    windows (and their taskbar / Alt-Tab entries) stop showing the stock
    pywebview icon. webview.create_window has no icon param -- it can only be
    set here, once, for the whole app. Best-effort: if the packaged .ico is
    missing we just omit it and keep the default."""
    ico_path = get_logo_ico_path()
    return {"icon": str(ico_path)} if ico_path is not None else {}

if TYPE_CHECKING:
    from client.customer_overlay import CustomerOverlayController
    from client.lock_screen import LockScreenController


def _patch_pywebview_move_bug() -> None:
    """Fix a crash in pywebview's own WinForms backend (edgechromium/
    winforms.py, BrowserView.BrowserForm.move -- also resize()): they call
    ctypes.windll.user32.SetWindowPos(..., cx=None, cy=None, ...) or
    similar, relying on flags like SWP_NOSIZE to make Windows ignore some
    of the position/size arguments -- but ctypes can't marshal a Python
    None into the plain integer parameters SetWindowPos declares those as,
    so calls into it can raise ctypes.ArgumentError: "argument 5: TypeError:
    'NoneType' object cannot be interpreted as an integer". This isn't
    limited to user drag gestures -- our own move_window_exact() calls
    Window.move() too during ordinary window positioning, so the crash was
    recurring during completely normal operation, repeatedly enough to
    destabilize the whole process.

    Two layers, applied together rather than relying on either alone:

    1. Patch ctypes.windll.user32.SetWindowPos itself to coerce any None
       positional argument to 0 before the real call -- this is the actual
       Windows API entry point every caller eventually goes through
       (pywebview's move(), resize(), or anything else, present or future,
       that hits this same pattern), so it can't be bypassed by a call path
       this function doesn't already know about.
    2. Also reimplement BrowserForm.move() directly with 0 instead of None,
       in case some code path holds an earlier-bound reference to the
       now-patched ctypes function object rather than looking it up fresh
       through windll.user32 each time.

    Patched at runtime rather than editing the vendored dependency
    directly, since a future `pip install`/venv rebuild would silently
    discard a source edit there.
    """
    try:
        original_set_window_pos = ctypes.windll.user32.SetWindowPos

        def safe_set_window_pos(hwnd, hwnd_insert_after, x, y, cx, cy, flags):
            return original_set_window_pos(
                hwnd,
                hwnd_insert_after,
                0 if x is None else x,
                0 if y is None else y,
                0 if cx is None else cx,
                0 if cy is None else cy,
                flags,
            )

        ctypes.windll.user32.SetWindowPos = safe_set_window_pos
    except Exception:
        pass

    try:
        from webview.platforms import winforms
    except Exception:
        return

    def fixed_move(self, x: int, y: int) -> None:
        SWP_NOSIZE = 0x0001
        SWP_NOZORDER = 0x0004
        SWP_SHOWWINDOW = 0x0040
        scale = self._scale
        if scale != 1:
            x_phys = int(x * scale)
            y_phys = int(y * scale)
        else:
            x_phys = int(x)
            y_phys = int(y)
        ctypes.windll.user32.SetWindowPos(
            self.Handle.ToInt32(),
            None,
            x_phys,
            y_phys,
            0,
            0,
            SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW,
        )

    try:
        winforms.BrowserView.BrowserForm.move = fixed_move
    except Exception:
        pass


_patch_pywebview_move_bug()


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


def web_ui_index_uri() -> str:
    """URI for the overlay/lock HTML, with a content-hash cache-buster
    appended to the stylesheet/script links.

    Every overlay window is a separate WebView2 instance sharing one
    persistent WebView2 disk cache. After a client update replaces
    styles.css / app.js at the same file:// path, WebView2 can keep
    serving a stale (sometimes partially-cached, hence unstyled) copy to
    one or more of those windows -- observed as the collapsed pill
    rendering as a blank white box after a reinstall. Rewriting the asset
    URLs with a hash of their current bytes gives each build a distinct
    cache key, so a stale entry can never be reused.
    """
    source = web_ui_index_path()
    try:
        html = source.read_text(encoding="utf-8")
        digest = hashlib.sha1()
        for name in ("styles.css", "app.js"):
            try:
                digest.update((source.parent / name).read_bytes())
            except OSError:
                pass
        token = digest.hexdigest()[:12]
        html = html.replace('href="./styles.css"', f'href="./styles.css?v={token}"')
        html = html.replace('src="./app.js"', f'src="./app.js?v={token}"')
        generated = source.parent / "index.generated.html"
        generated.write_text(html, encoding="utf-8")
        return generated.as_uri()
    except OSError:
        # Read-only install dir or similar -- fall back to the plain file.
        return source.as_uri()


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


def current_screen_size() -> tuple[int, int]:
    """The full physical primary-display resolution (SM_CXSCREEN/SM_CYSCREEN
    -- not the work area: a kiosk fullscreen window must cover the taskbar
    strip too). Queried fresh on every call, deliberately not cached, so a
    resolution change after the window was created is always detectable."""
    user32 = ctypes.windll.user32
    return (user32.GetSystemMetrics(0), user32.GetSystemMetrics(1))


def resize_and_reposition_fullscreen(window: webview.Window, width: int, height: int) -> bool:
    """Force a window to exactly cover (0, 0)-(width, height) and stay
    topmost.

    Fixes a real kiosk-escape gap seen on freshly-imaged diskless PCs: a
    generic/basic display driver is active for the first few seconds after
    the desktop appears, at a lower resolution than the monitor's native
    one; Windows applies the real resolution moments later. The lock
    window's `fullscreen=True` sized itself against whatever resolution was
    active at window-creation time (very early in the session), so when the
    resolution later jumps up, the window keeps its stale, smaller size --
    leaving a strip of desktop, including the taskbar, exposed at the edge
    of the screen. Called from the periodic refresh tick whenever the
    detected screen size has changed since last checked."""
    try:
        from ctypes import wintypes

        hwnd_value = _taskbar_hwnd(window)
        if hwnd_value is None:
            return False
        hwnd = wintypes.HWND(hwnd_value)
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
        insert_after = wintypes.HWND(-1)  # HWND_TOPMOST
        flags = 0x0010 | 0x4000  # NOACTIVATE | ASYNCWINDOWPOS
        if not set_window_pos(hwnd, insert_after, 0, 0, width, height, flags):
            raise ctypes.WinError()
        _diagnostic(f"re-covered fullscreen window to {width}x{height} hwnd=0x{hwnd_value:X}")
        return True
    except Exception as error:
        _diagnostic(f"fullscreen re-cover failed: {error}")
        return False


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


def hud_geometry(width: int, height: int) -> tuple[int, int]:
    """Upper-middle of the primary work area, clearly above centre -- where a
    lightweight game HUD notification belongs, not dead centre."""
    left, top, right, bottom = primary_work_area()
    x = left + max(0, (right - left - width) // 2)
    y = top + int((bottom - top) * 0.18)
    return (x, y)


def apply_click_through(window: webview.Window) -> None:
    """Let mouse and keyboard fall straight through the window to the game
    underneath (Windows). WS_EX_TRANSPARENT makes hit-testing pass through;
    WS_EX_NOACTIVATE stops it ever stealing focus. Layered-window paint path
    is kept alive by apply_window_opacity(1.0) first."""
    apply_window_opacity(window, 1.0)
    try:
        from ctypes import wintypes

        hwnd = int(window.native.Handle.ToInt64())
        get_style = ctypes.windll.user32.GetWindowLongPtrW
        get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        get_style.restype = ctypes.c_ssize_t
        set_style = ctypes.windll.user32.SetWindowLongPtrW
        set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        set_style.restype = ctypes.c_ssize_t
        ws_ex_transparent = 0x00000020
        ws_ex_noactivate = 0x08000000
        extended_style = get_style(hwnd, -20)
        set_style(hwnd, -20, extended_style | ws_ex_transparent | ws_ex_noactivate)
    except Exception as error:
        _diagnostic(f"native click-through failed: {error}")


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


def apply_native_background_color(window: webview.Window, hex_color: str) -> bool:
    """Set the underlying WinForms Form's own BackColor, not just the
    WebView2 control's background_color (a create_window() param that only
    takes effect once WebView2 itself has painted). Without this, restoring
    the pill from a taskbar-click minimized state showed a brief flash of
    the Form's own default background (WinForms' light grey/white
    SystemColors.Control) before WebView2's dark content caught up."""
    try:
        native = getattr(window, "native", None)
        if native is None:
            return False
        from System.Drawing import Color

        value = hex_color.lstrip("#")
        red, green, blue = (int(value[i : i + 2], 16) for i in (0, 2, 4))
        native.BackColor = Color.FromArgb(red, green, blue)
        return True
    except Exception as error:
        _diagnostic(f"native background color failed: {error}")
        return False


def create_native_tray_icon(icon_path: str, tooltip: str, on_click: Callable[[], None]) -> Any | None:
    """A real system tray icon (System.Windows.Forms.NotifyIcon), not a
    taskbar button -- the customer's own request: check remaining time from
    the tray next to the clock, not from a window competing for space in
    the main taskbar row. Starts hidden; the caller shows/hides it as the
    session collapses/expands. Returns None (never raises) if construction
    fails for any reason, so a broken icon never blocks the collapsed
    control's actual job (the pill popup still works, just without a tray
    entry to trigger it -- callers should treat a None return as "no tray
    icon available" and fall back gracefully)."""
    try:
        import System.Windows.Forms as Forms
        from System.Drawing import Icon

        tray_icon = Forms.NotifyIcon()
        tray_icon.Icon = Icon(icon_path)
        # NotifyIcon.Text silently truncates past 63 characters in older
        # .NET but throws in newer ones -- keep it safely short regardless.
        tray_icon.Text = tooltip[:63]
        tray_icon.Visible = False

        def handle_click(_sender: Any, args: Any) -> None:
            # NotifyIcon fires Click for both left and right buttons (right
            # normally reserved for a context menu, which this icon doesn't
            # have) -- only react to the left button, matching a normal
            # left-click-to-check-time gesture.
            try:
                if getattr(args, "Button", None) == Forms.MouseButtons.Left:
                    on_click()
            except Exception as error:
                _diagnostic(f"tray icon click handler failed: {error}")

        tray_icon.MouseClick += handle_click
        return tray_icon
    except Exception as error:
        _diagnostic(f"native tray icon creation failed: {error}")
        return None


def dispose_native_tray_icon(tray_icon: Any | None) -> None:
    if tray_icon is None:
        return
    try:
        tray_icon.Visible = False
        tray_icon.Dispose()
    except Exception as error:
        _diagnostic(f"native tray icon disposal failed: {error}")


def _invoke_on_gui_thread(window: webview.Window, action: Callable[[], None]) -> bool:
    """Marshal a callable onto pywebview's single WinForms message-loop
    thread (the dedicated STA thread its winforms backend spins up for the
    master window and creates every other window on via Control.Invoke --
    see webview/platforms/winforms.py's create_window). Every callback this
    module hangs code off (window.events.loaded, the refresh-loop thread)
    runs on a throwaway Python thread instead, which is fine for the raw
    ctypes Win32 calls used elsewhere here (thread-agnostic by design), but
    not for a real WinForms component like NotifyIcon: its Click/MouseClick
    events only ever fire if the thread that constructed it is actually
    pumping Windows messages. Confirmed live -- without this, the tray icon
    rendered in the system tray but never responded to a click."""
    native = getattr(window, "native", None)
    if native is None:
        return False
    try:
        from System import Action

        native.Invoke(Action(action))
        return True
    except Exception as error:
        _diagnostic(f"invoke on gui thread failed: {error}")
        return False


def minimize_native_window(window: webview.Window) -> bool:
    """Minimize a loaded WinForms host to the taskbar without blocking on
    Control.Invoke. Used to send the collapsed-session pill back down to a
    taskbar button instead of leaving it floating over whatever the customer
    is doing."""
    try:
        from ctypes import wintypes

        hwnd_value = _taskbar_hwnd(window)
        if hwnd_value is None:
            return False
        hwnd = wintypes.HWND(hwnd_value)
        show_window = ctypes.windll.user32.ShowWindowAsync
        show_window.argtypes = [wintypes.HWND, ctypes.c_int]
        show_window.restype = wintypes.BOOL
        _diagnostic(f"native minimize before {_thread_label()}")
        show_window(hwnd, 6)  # SW_MINIMIZE
        _diagnostic(f"native minimize after {_thread_label()}")
        return True
    except Exception as error:
        _diagnostic(f"native minimize failed: {error}")
        return False


def restore_native_window(window: webview.Window) -> bool:
    """Undo a prior SW_MINIMIZE. A minimized WinForms window ignores
    SetWindowPos bounds changes until it is back in the Normal state, so this
    must run before repositioning a pill that was minimized on a previous
    collapse."""
    try:
        from ctypes import wintypes

        hwnd_value = _taskbar_hwnd(window)
        if hwnd_value is None:
            return False
        hwnd = wintypes.HWND(hwnd_value)
        show_window = ctypes.windll.user32.ShowWindowAsync
        show_window.argtypes = [wintypes.HWND, ctypes.c_int]
        show_window.restype = wintypes.BOOL
        show_window(hwnd, 9)  # SW_RESTORE
        return True
    except Exception as error:
        _diagnostic(f"native restore failed: {error}")
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
        # NOT native.ShowInTaskbar = is_visible: confirmed live, repeatedly,
        # that this managed WinForms property setter deadlocks the process,
        # regardless of which thread or how long after startup it's called
        # from. The raw SetWindowLongPtrW + SetWindowPos(SWP_FRAMECHANGED)
        # above already is the actual, complete mechanism Windows uses to
        # add/remove a window's taskbar button -- ShowInTaskbar is just a
        # higher-level WinForms wrapper around the same extended style, and
        # setting it *after* that style was already changed directly
        # underneath it (bypassing the property) is what very likely
        # triggers WinForms' internal RecreateHandle() to "reconcile" its
        # now-stale cached state, which needs the window's own UI thread
        # and safely available message pump -- neither guaranteed here.
        _describe_window_state("native taskbar presence after", hwnd_value)
        return True
    except Exception as error:
        _diagnostic(f"native taskbar presence failed: {error}")
        return False


_APP_USER_MODEL_ID = "SecretGrounds.CustomerClient"
_app_identity_applied = False


def apply_windows_app_identity() -> None:
    """Give the process its own AppUserModelID so Windows groups the taskbar
    button under the Secret Grounds app rather than lumping it in with the
    launching powershell.exe, and so the jump-list header resolves to our
    identity (and icon) instead of a generic one. Safe to call more than once;
    only the first call has any effect."""
    global _app_identity_applied
    if _app_identity_applied or sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(_APP_USER_MODEL_ID)
        _app_identity_applied = True
        _diagnostic(f"app user model id set to {_APP_USER_MODEL_ID}")
    except Exception as error:
        _diagnostic(f"app user model id set failed: {error}")


def apply_native_window_icon(window: webview.Window) -> None:
    """Force the Secret Grounds icon onto a WinForms host via WM_SETICON.

    pywebview sets Form.Icon at construction, but these windows are frameless
    and their ShowInTaskbar / ex-style gets rewritten at runtime, which can
    drop the taskbar button back to the stock application icon. Re-sending
    WM_SETICON against the live top-level handle after the page loads pins our
    icon to whatever button Windows is actually showing. Best-effort."""
    if sys.platform != "win32":
        return
    ico_path = get_logo_ico_path()
    if ico_path is None:
        return
    try:
        from ctypes import wintypes

        hwnd_value = _taskbar_hwnd(window)
        if not hwnd_value:
            return
        hwnd = wintypes.HWND(hwnd_value)

        load_image = ctypes.windll.user32.LoadImageW
        load_image.argtypes = [
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        load_image.restype = wintypes.HANDLE
        send_message = ctypes.windll.user32.SendMessageW
        send_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        send_message.restype = wintypes.LPARAM

        image_icon = 1
        lr_loadfromfile = 0x00000010
        wm_seticon = 0x0080
        get_metric = ctypes.windll.user32.GetSystemMetrics
        small_cx, small_cy = get_metric(49), get_metric(50)  # SM_CXSMICON / SM_CYSMICON
        big_cx, big_cy = get_metric(11), get_metric(12)  # SM_CXICON / SM_CYICON

        for icon_size_kind, cx, cy in ((0, small_cx, small_cy), (1, big_cx, big_cy)):
            handle = load_image(None, str(ico_path), image_icon, cx, cy, lr_loadfromfile)
            if handle:
                send_message(hwnd, wm_seticon, icon_size_kind, handle)
        _diagnostic(f"native window icon applied hwnd=0x{hwnd_value:X}")
    except Exception as error:
        _diagnostic(f"native window icon failed: {error}")


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
        # Distinct from last_pill_size: tracks what geometry was actually
        # *applied* to the native window, so show_collapsed_control can tell
        # a real content-size change (rare -- the pill's content is static)
        # from just another refresh tick with nothing new to apply.
        self._last_applied_pill_size: tuple[int, int] | None = None
        self.last_action: tuple[str, dict[str, Any]] | None = None
        self.ui_errors: list[dict[str, Any]] = []
        self.window_visibility: dict[str, bool] = {}
        self.window_on_top: dict[str, bool] = {}
        self.allow_close = False
        self.refresh_event = Event()
        self.refresh_stop = Event()
        self.pending_lock_refresh = False
        self.pending_overlay_refresh = False
        # Remaining-time reminder HUD: last token acted on, and any text
        # queued while the HUD window is still loading its content.
        self._last_hud_token = 0
        self._pending_hud_text: str | None = None
        self.refresh_thread: Thread | None = None
        self.setup_active = False
        # Last screen size the geometry-reconciliation tick observed. Seeded
        # in _on_start once the lock window's initial fullscreen has taken
        # its shape; None beforehand so the very first tick never fires an
        # unnecessary resize.
        self._last_screen_size: tuple[int, int] | None = None
        self._native_lock_deactivate_handler: Callable[..., None] | None = None
        self._native_pill_deactivate_handler: Callable[..., None] | None = None
        # The collapsed-session control lives in the system tray (a
        # NotifyIcon), not as a taskbar button. tray_icon is created once
        # the pill window first loads (see _handle_loaded); its own
        # Visible state tracks whether a session is currently collapsed.
        # _pill_popup_visible tracks the separate, click-driven question of
        # whether the small popup itself is currently on screen -- shown by
        # a tray click, hidden again when it loses focus -- independent of
        # the refresh tick loop.
        self.tray_icon: Any | None = None
        self._pill_popup_visible = False
        self._pill_popup_auto_hide_timer: Timer | None = None
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
            "presentation_token": model.presentation_token,
        }

    def create_windows(self, lock_only: bool = False) -> None:
        url = web_ui_index_uri()
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
            # pywebview defaults easy_drag to True, which wires up a global
            # window-level mousedown/mousemove listener that repositions the
            # native window by calling its move() bridge on every move. None
            # of these windows are meant to be user-draggable -- they're all
            # positioned programmatically (move_window_exact below) -- and a
            # stray click-drag anywhere on one (easy to trigger by accident
            # on a fullscreen/frameless window) can leave that listener
            # active; some edge case in it eventually computes NaN
            # coordinates, which crash the move() bridge call with a
            # ctypes.ArgumentError repeatedly (observed in client-error.log
            # as a tight loop of them). Explicitly disabling it removes the
            # whole failure mode.
            easy_drag=False,
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
            easy_drag=False,  # see the lock window's create_window call for why
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
            easy_drag=False,  # see the lock window's create_window call for why
        )
        _diagnostic("after create_window pill")
        # Remaining-time reminder HUD: a small frameless transparent window
        # near the top-centre of the screen, click-through so it never blocks
        # gameplay input. Created off-screen; shown only for the ~3s a
        # reminder is on screen.
        _diagnostic("before create_window hud")
        hud_window = webview.create_window(
            "Secret Grounds",
            url=url,
            js_api=WebViewBridge(self, "hud"),
            width=460,
            height=110,
            x=-32000,
            y=-32000,
            frameless=True,
            on_top=True,
            resizable=False,
            min_size=(1, 1),
            hidden=False,
            transparent=True,
            background_color="#000000",
            text_select=False,
            easy_drag=False,  # see the lock window's create_window call for why
        )
        _diagnostic("after create_window hud")
        self.windows = {
            "lock": lock_window,
            "overlay": overlay_window,
            "pill": pill_window,
            "hud": hud_window,
        }
        self.window_visibility = {
            "lock": lock_initially_visible,
            "overlay": False,
            "pill": False,
            "hud": False,
        }
        self._bind_window_events("overlay", overlay_window)
        self._bind_window_events("pill", pill_window)
        self._bind_window_events("hud", hud_window)

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
        apply_windows_app_identity()
        self.create_windows(lock_only=lock_only)

        def on_start() -> None:
            _diagnostic(f"inside startup callback renderer={webview.renderer}")
            self._on_start()
            if start_callback is not None:
                start_callback()
            _diagnostic("startup callback returned")

        _diagnostic("before webview.start gui=edgechromium")
        webview.start(
            on_start,
            gui="edgechromium",
            debug=debug,
            private_mode=True,
            **_webview_icon_kwargs(),
        )
        _diagnostic("after webview.start")

    def _on_start(self) -> None:
        _diagnostic("webview host on_start")
        self.started.set()
        self._bind_native_lock_guard()
        try:
            self._last_screen_size = current_screen_size()
        except Exception as error:
            _diagnostic(f"initial screen size read failed: {error}")
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
        if window is not None:
            apply_native_window_icon(window)
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
            if window is not None:
                apply_native_background_color(window, "#101a29")
                # Bound here, not from _on_start -- _on_start fires before
                # this window's native handle exists (unlike the lock
                # window's, which is ready by then), so the guard was
                # silently binding to nothing. Confirmed live: the popup
                # showed on a tray click but never hid again on click-away,
                # because nothing was ever listening for it losing focus.
                self._bind_pill_auto_hide_guard()
                # NOT set_native_taskbar_presence(..., False) here -- the
                # pill does need it suppressed (confirmed live: without it,
                # the pill still shows as an ordinary taskbar button, a
                # shown/frameless/owner-less top-level window's default),
                # but this fires on a per-window WebView2 event thread
                # while the window is still mid-load. See
                # show_collapsed_control, which does this once the pill has
                # actually settled (on first real collapse, well after
                # startup).
                if self.tray_icon is None:
                    ico_path = get_logo_ico_path()
                    _diagnostic(f"tray icon path resolved={ico_path}")
                    if ico_path is not None:

                        def create_tray_icon() -> None:
                            self.tray_icon = create_native_tray_icon(
                                str(ico_path),
                                "Secret Grounds",
                                self._show_pill_popup,
                            )
                            _diagnostic(f"tray icon created={self.tray_icon is not None}")

                        # Must be built on pywebview's own WinForms thread,
                        # not this window-loaded callback's throwaway Python
                        # thread -- see _invoke_on_gui_thread.
                        if not _invoke_on_gui_thread(window, create_tray_icon):
                            _diagnostic("tray icon creation invoke failed")
            if self.overlay_controller is not None:
                self.request_overlay_refresh()
            else:
                self.render_pill()
        elif key == "hud":
            if window is not None:
                move_window_exact(window, *hud_geometry(460, 110))
                apply_click_through(window)
            if self._pending_hud_text is not None:
                text = self._pending_hud_text
                self._pending_hud_text = None
                self._fire_hud(text)

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

    def _reconcile_screen_geometry(self) -> None:
        """Re-cover the lock window if the display resolution changed since
        last checked. See resize_and_reposition_fullscreen() for why this is
        needed on freshly-imaged diskless PCs. Runs on every refresh tick
        (every ~0.5s) but only ever acts when the size actually changed --
        same "only on a real transition" discipline as the collapsed-pill
        topmost fix, so this never fights the window on every tick."""
        try:
            width, height = current_screen_size()
        except Exception as error:
            _diagnostic(f"screen geometry check failed: {error}")
            return
        if (width, height) == self._last_screen_size:
            return
        _diagnostic(f"screen resolution changed to {width}x{height} (was {self._last_screen_size})")
        self._last_screen_size = (width, height)
        lock_window = self.windows.get("lock")
        if lock_window is not None:
            resize_and_reposition_fullscreen(lock_window, width, height)

    def _refresh_loop(self) -> None:
        while not self.refresh_stop.is_set():
            self.refresh_event.wait(0.5)
            self.refresh_event.clear()
            self._reconcile_screen_geometry()
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
        # Remaining-time reminder HUD -- independent of the session card's
        # own visible/collapsed state (the customer is playing), so fire it
        # here before any of the card branching below.
        if model.reminder_hud_token != self._last_hud_token:
            self._last_hud_token = model.reminder_hud_token
            self._fire_hud(model.reminder_hud_text)
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

    def _fire_hud(self, text: str | None) -> None:
        """Show the remaining-time reminder HUD once for a fresh crossing.
        The HUD window's own JS/CSS handles the ~3s show + fade + auto-hide
        (it then emits hud_done, and we hide the native window)."""
        if not text:
            return
        hud = self.windows.get("hud")
        if hud is None:
            return
        if "hud" not in self.loaded:
            # First use: make the window visible off-screen so WebView2
            # actually loads its content, then _handle_loaded("hud") fires
            # the queued reminder.
            self._pending_hud_text = text
            self._set_window_visible("hud", True)
            return
        self._evaluate(
            "hud",
            {
                "view": "hud",
                "logo_url": secret_grounds_logo_url(),
                "hud_text": text,
                "hud_token": self._last_hud_token,
            },
        )
        self._set_window_visible("hud", True)
        self._set_window_on_top("hud", hud, True)

    def show_collapsed_control(self) -> None:
        pill = self.windows.get("pill")
        if pill is None:
            return
        # refresh_overlay() calls collapse_overlay() -> here on every refresh
        # tick (about once a second) for as long as the session stays
        # collapsed, not just on the transition into being collapsed. Only
        # the actual transition should touch the tray icon or the popup's
        # own shown/hidden state below -- repeat ticks just keep the
        # content fresh for whenever the customer next clicks the tray icon.
        was_already_collapsed = self.window_visibility.get("pill") is True
        self.render_pill()
        width, height = self.last_pill_size
        # The pill's content is static (logo + pc_name, no countdown text),
        # so its size never actually changes tick to tick -- reapplying
        # resize/move/rounded-region/opacity every ~1s regardless (in
        # particular SetWindowRgn, which forces a repaint) was unnecessary
        # churn on a window that hadn't actually moved, resized, or changed.
        if not was_already_collapsed or (width, height) != self._last_applied_pill_size:
            apply_pill_native_geometry(pill, width, height)
            self._last_applied_pill_size = (width, height)
        with self.lock:
            self.window_visibility["pill"] = True
        if not was_already_collapsed:
            # Lives in the system tray now, not as a taskbar button: the
            # pill itself starts hidden, and only its tray icon appears.
            # The customer clicks the tray icon to check their remaining
            # time (_show_pill_popup, wired as the icon's click handler in
            # _handle_loaded), and the Deactivate guard bound in
            # _bind_pill_auto_hide_guard hides it again the moment they
            # click back into whatever they were doing.
            #
            # Called here (on first real collapse) rather than from
            # _handle_loaded because the pill's own load event fires too
            # early to matter for this -- there's no visible taskbar button
            # to hide until the customer actually collapses into it.
            if not set_native_taskbar_presence(pill, False):
                _diagnostic("pill taskbar suppression failed")
            self._hide_pill_popup()
            _diagnostic(f"tray icon show attempt, tray_icon present={self.tray_icon is not None}")
            if self.tray_icon is not None:

                def show_tray_icon() -> None:
                    try:
                        self.tray_icon.Visible = True
                        _diagnostic(f"tray icon show succeeded visible={self.tray_icon.Visible}")
                    except Exception as error:
                        _diagnostic(f"tray icon show failed: {error}")

                if not _invoke_on_gui_thread(pill, show_tray_icon):
                    _diagnostic("tray icon show invoke failed")

    def _show_pill_popup(self) -> None:
        """The tray icon's click handler. Runs on the WinForms message-loop
        thread (a .NET event, not the webview refresh thread) --
        set_native_window_visibility is explicitly safe to call cross-thread
        (ShowWindowAsync), so no marshaling back onto the refresh thread is
        needed here.

        Deliberately does NOT pass activate=True (no SetForegroundWindow).
        Confirmed live: a quick sequence of tray clicks calls
        SetForegroundWindow repeatedly in a short window, which is exactly
        what trips Windows' own anti-focus-stealing throttle -- the call
        gets silently ignored, the popup ends up visible and topmost but
        never actually the foreground window, and a window that was never
        truly foreground can't later fire Deactivate to leave. The popup
        only needs to be glanced at, not interacted with, so there's
        nothing to gain from stealing focus in the first place -- it shows
        topmost via SW_SHOWNOACTIVATE and the auto-hide timer below is the
        sole, reliable mechanism that dismisses it again."""
        pill = self.windows.get("pill")
        if pill is None:
            return
        self._pill_popup_visible = True
        set_native_window_visibility(pill, True, activate=False, on_top=True)
        self._arm_pill_popup_auto_hide_timer()

    def _hide_pill_popup(self) -> None:
        # Deliberately unconditional (no "already hidden, skip" guard):
        # confirmed live, clicking the tray icon can fire a burst of
        # rapid, genuine Activate/Deactivate pairs from Windows itself (not
        # a bug in this code -- a real WinForms/shell focus-stealing quirk
        # right after a NotifyIcon click), all landing on the same thread
        # in quick succession. A "skip if our own bookkeeping already says
        # hidden" guard here is exactly the kind of check that goes stale
        # first in a fast back-to-back sequence like that, and a stale
        # skip here means the popup never actually goes away -- it just
        # silently sits on top of whatever the customer is doing. Calling
        # SetWindowPos/ShowWindow on an already-hidden window is a cheap,
        # harmless no-op, so there's no cost to always asserting it.
        self._cancel_pill_popup_auto_hide_timer()
        pill = self.windows.get("pill")
        if pill is None:
            return
        self._pill_popup_visible = False
        set_native_window_visibility(pill, False)

    def _arm_pill_popup_auto_hide_timer(self) -> None:
        # Belt-and-braces on top of the Deactivate guard: a tray click that
        # lands as a rapid burst of Activate/Deactivate (confirmed live --
        # Windows itself can generate several in under two seconds right
        # after a NotifyIcon click) can burn through Windows' own
        # SetForegroundWindow anti-focus-stealing allowance, so the final
        # Activate silently never truly takes -- the popup ends up visible
        # and topmost forever, since a window that was never really
        # foreground can't later fire Deactivate to leave it. This timer
        # hides it regardless, a few seconds after any show, independent
        # of whether focus events ever cooperate.
        self._cancel_pill_popup_auto_hide_timer()
        timer = Timer(6.0, self._hide_pill_popup)
        timer.daemon = True
        self._pill_popup_auto_hide_timer = timer
        timer.start()

    def _cancel_pill_popup_auto_hide_timer(self) -> None:
        timer = self._pill_popup_auto_hide_timer
        if timer is not None:
            timer.cancel()
            self._pill_popup_auto_hide_timer = None

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
            self._hide_pill_popup()
            if self.tray_icon is not None:

                def hide_tray_icon() -> None:
                    try:
                        self.tray_icon.Visible = False
                    except Exception as error:
                        _diagnostic(f"tray icon hide failed: {error}")

                if not _invoke_on_gui_thread(pill, hide_tray_icon):
                    _diagnostic("tray icon hide invoke failed")
            with self.lock:
                self.window_visibility["pill"] = False
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
        if action == "hud_done" and window_key == "hud":
            # The HUD finished its show/fade; hide the native window until the
            # next reminder. A stale token (a newer reminder already replaced
            # it) means the newer animation is still running -- leave it.
            if int(payload.get("token", -1)) == self._last_hud_token:
                self._set_window_visible("hud", False)
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

    def _bind_pill_auto_hide_guard(self) -> None:
        # Mirror image of the lock guard above: the pill pops up from the
        # tray icon so a customer can check their remaining time while
        # playing a game in fullscreen, but it must not just sit floating
        # on top afterward. As soon as it loses focus again (they clicked
        # back into the game), hide it -- it's tray-triggered now, not a
        # taskbar button, so there's nothing to minimize *to*.
        pill_window = self.windows.get("pill")
        if pill_window is None:
            _diagnostic("pill auto-hide guard skipped: window missing")
            return
        native = getattr(pill_window, "native", None)
        if native is None:
            _diagnostic("pill auto-hide guard skipped: native missing")
            return

        def auto_hide(sender: Any, _args: Any) -> None:
            try:
                self._hide_pill_popup()
            except Exception as error:
                _diagnostic(f"pill auto-hide failed: {error}")

        self._native_pill_deactivate_handler = auto_hide
        try:
            native.Deactivate += auto_hide
            _diagnostic("pill auto-hide guard bound")
        except Exception as error:
            _diagnostic(f"native pill auto-hide guard failed: {error}")

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
        self._cancel_pill_popup_auto_hide_timer()
        self.refresh_stop.set()
        self.refresh_event.set()
        if (
            self.refresh_thread is not None
            and self.refresh_thread is not current_thread()
            and self.refresh_thread.is_alive()
        ):
            self.refresh_thread.join(timeout=2)
        self.detach_controllers()
        # Without this, a NotifyIcon can linger as a "ghost" tray icon
        # (visible until the taskbar/Explorer notices the owning process is
        # gone and clears it, sometimes only after the next mouse-over)
        # instead of disappearing cleanly the moment the client exits.
        dispose_native_tray_icon(self.tray_icon)
        self.tray_icon = None
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
        url=web_ui_index_uri(),
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
        easy_drag=False,  # see the lock window's create_window call for why
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
    webview.start(
        preview_started,
        gui="edgechromium",
        debug=False,
        private_mode=True,
        **_webview_icon_kwargs(),
    )


if __name__ == "__main__":
    run_webview_customer_ui_preview()
