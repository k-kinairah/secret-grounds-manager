import ctypes
import os
import sys
import time
from pathlib import Path
from threading import Event, Thread


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import client.customer_overlay as customer_overlay_module
from client.customer_overlay import CustomerOverlayController
from client.lock_screen import LockScreenController
from client.webview_ui import SecretGroundsWebViewHost

# Reminder crossings would otherwise spawn a real, audible PowerShell
# text-to-speech process (client/voice_announcer.py) -- silence it for the
# whole file regardless of REMINDER_VOICE_ENABLED's default.
customer_overlay_module.announce_reminder = lambda *_a, **_kw: None


def main() -> None:
    returned = Event()
    flow_finished = Event()

    def logout() -> None:
        overlay.hide()
        lock.show_locked("Production WebView flow logout.")
        return None

    def login(username: str, pin: str) -> str | None:
        if username != "kinairah" or pin != "1234":
            return "Username or PIN is incorrect."
        lock.hide_for_session()
        overlay.show_session("Member Time", customer_name="Kinairah", is_elapsed=False)
        overlay.update_time(5 * 3600 + 59 * 60 + 34)
        return None

    lock = LockScreenController(
        "PC-01",
        enable_gui=False,
        enable_dev_escape=False,
        member_login_action=login,
    )
    overlay = CustomerOverlayController(
        "PC-01",
        enable_gui=False,
        logout_action=logout,
    )
    host = SecretGroundsWebViewHost(
        pc_name="PC-01",
        lock_controller=lock,
        overlay_controller=overlay,
    )

    def flow() -> None:
        try:
            print("FLOW waiting for lock page", flush=True)
            if not host.lock_loaded.wait(8):
                raise RuntimeError("lock page did not load within 8 seconds")
            print("FLOW lock", flush=True)
            lock.set_connection_online(True)
            lock.hide_for_session()
            overlay.show_session("Open Time", is_elapsed=True)
            overlay.update_time(5025)
            time.sleep(0.5)
            overlay.handle_minimize_clicked()
            # Regression: pywebview's window.resize() calls SetWindowPos with
            # SWP_SHOWWINDOW, so re-applying the pill's geometry once its real
            # content size is measured used to leave it visible on screen for
            # the whole collapsed session -- it must only ever be on screen
            # while the customer has deliberately opened it from the tray.
            time.sleep(2.5)
            from client.webview_ui import _taskbar_hwnd

            pill_hwnd = _taskbar_hwnd(host.windows["pill"])
            if pill_hwnd is None:
                raise RuntimeError("pill has no native window handle")
            if ctypes.windll.user32.IsWindowVisible(pill_hwnd):
                raise RuntimeError("collapsed pill is visible on screen without a tray click")
            print("FLOW pill hidden while collapsed", flush=True)
            # A tray click must show it, and it must go away again.
            host._show_pill_popup()
            time.sleep(0.6)
            if not ctypes.windll.user32.IsWindowVisible(pill_hwnd):
                raise RuntimeError("tray click did not show the pill popup")
            host._hide_pill_popup()
            time.sleep(0.6)
            if ctypes.windll.user32.IsWindowVisible(pill_hwnd):
                raise RuntimeError("pill popup did not hide again")
            print("FLOW pill popup shows and hides", flush=True)
            overlay.handle_restore_clicked()
            time.sleep(0.3)
            host.handle_action("overlay", "logout", {})
            print("FLOW open complete", flush=True)

            lock.hide_for_session()
            overlay.show_session("Package", is_elapsed=False)
            overlay.update_time(2652)
            overlay.show_time_added_confirmation(600, 3252)
            time.sleep(0.4)
            overlay.handle_minimize_clicked()
            time.sleep(0.3)
            overlay.handle_restore_clicked()
            time.sleep(0.3)
            overlay.update_time(0)
            overlay.hide()
            lock.show_expiration_grace(120)
            time.sleep(0.4)
            lock.show_locked("Package flow expired.")
            print("FLOW package complete", flush=True)

            host.handle_action(
                "lock",
                "member_login",
                {"username": "kinairah", "pin": "1234"},
            )
            time.sleep(0.7)
            overlay.handle_minimize_clicked()
            time.sleep(0.3)
            overlay.handle_restore_clicked()
            time.sleep(0.3)
            host.handle_action("overlay", "logout", {})
            print("FLOW member complete", flush=True)
            overlay_geometry = host.geometry_snapshot("overlay")
            pill_geometry = host.geometry_snapshot("pill")
            print(f"FLOW overlay geometry={overlay_geometry}", flush=True)
            print(f"FLOW pill geometry={pill_geometry}", flush=True)
            if overlay_geometry is None or overlay_geometry[-2:] != (16, 16):
                raise RuntimeError(f"overlay is not anchored at 16px: {overlay_geometry}")
            if pill_geometry is None or pill_geometry[-2:] != (16, 16):
                raise RuntimeError(f"pill is not anchored at 16px: {pill_geometry}")
            if host.ui_errors:
                raise RuntimeError(f"JavaScript errors were reported: {host.ui_errors}")
            print("PRODUCTION_WEBVIEW_FLOW_OK", flush=True)
            flow_finished.set()
        except Exception as error:
            print(f"PRODUCTION_WEBVIEW_FLOW_FAILED: {error}", flush=True)
        finally:
            print("FLOW before host shutdown", flush=True)
            host.shutdown()
            print("FLOW after host shutdown", flush=True)

    def watchdog() -> None:
        if not flow_finished.wait(20):
            print("PRODUCTION_WEBVIEW_FLOW_TIMEOUT", flush=True)
            host.shutdown()
        if not returned.wait(5):
            print("PRODUCTION_WEBVIEW_EVENT_LOOP_DID_NOT_RETURN", flush=True)
            os._exit(2)

    Thread(target=watchdog, daemon=True).start()
    print("PRODUCTION_WEBVIEW before host.start", flush=True)
    host.start(start_callback=lambda: Thread(target=flow, daemon=True).start())
    print("PRODUCTION_WEBVIEW after host.start", flush=True)
    returned.set()


if __name__ == "__main__":
    main()
