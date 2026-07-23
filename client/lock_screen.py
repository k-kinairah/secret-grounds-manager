from __future__ import annotations

import queue
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Callable


GuiAction = Callable[[], None]


@dataclass
class LockScreenState:
    is_locked: bool = True
    is_online: bool = False
    dev_escape_active: bool = False


class LockScreenController:
    """Thread-safe boundary for the local client lock screen.

    Network and timer threads call methods on this controller. The controller
    schedules all widget changes onto the Tkinter thread so GUI objects are not
    touched directly from worker threads.
    """

    def __init__(
        self,
        pc_name: str,
        enable_gui: bool,
        enable_dev_escape: bool,
    ) -> None:
        self.pc_name = pc_name
        self.enable_gui = enable_gui
        self.enable_dev_escape = enable_dev_escape
        self.state = LockScreenState()
        self.state_lock = Lock()
        self.stop_event = Event()
        self.action_queue: queue.Queue[GuiAction] = queue.Queue()
        self.gui_thread: Thread | None = None
        self.root = None
        self.connection_label = None

    def start(self) -> None:
        if self.enable_dev_escape:
            print(
                "WARNING: Development lock-screen escape is enabled. "
                "Disable ENABLE_DEV_ESCAPE before deployment."
            )

        if not self.enable_gui:
            print("Lock screen shown.")
            return

        self.gui_thread = Thread(target=self._run_gui, daemon=True)
        self.gui_thread.start()

    def stop(self) -> None:
        self.stop_event.set()

        if self.enable_gui:
            self._schedule(self._destroy_window)

    def show_locked(self, reason: str | None = None) -> None:
        with self.state_lock:
            self.state.is_locked = True
            self.state.dev_escape_active = False

        if reason:
            print(reason)
        print("Lock screen shown.")

        if self.enable_gui:
            self._schedule(self._show_window)

    def hide_for_session(self) -> None:
        with self.state_lock:
            self.state.is_locked = False
            self.state.dev_escape_active = False

        print("Lock screen hidden.")

        if self.enable_gui:
            self._schedule(self._hide_window)

    def set_connection_online(self, is_online: bool) -> None:
        with self.state_lock:
            if self.state.is_online == is_online:
                return
            self.state.is_online = is_online

        label = "Online" if is_online else "Offline"
        print(f"WebSocket {label.lower()} state.")

        if self.enable_gui:
            self._schedule(self._update_connection_label)

    def is_locked(self) -> bool:
        with self.state_lock:
            return self.state.is_locked

    def is_online(self) -> bool:
        with self.state_lock:
            return self.state.is_online

    def use_development_escape(self) -> None:
        if not self.enable_dev_escape:
            return

        with self.state_lock:
            self.state.dev_escape_active = True

        print("Development escape used.")

        if self.enable_gui:
            self._schedule(self._hide_window)

    def _schedule(self, action: GuiAction) -> None:
        self.action_queue.put(action)

    def _run_gui(self) -> None:
        try:
            import tkinter as tk
        except ImportError:
            print("Tkinter is unavailable; lock state will be tracked without GUI.")
            return

        self.root = tk.Tk()
        self.root.title("Secret Grounds")
        self.root.configure(background="#111827")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.protocol("WM_DELETE_WINDOW", self._prevent_close)
        self.root.bind("<Unmap>", lambda _: self._ensure_visible())
        self.root.bind("<FocusOut>", lambda _: self._ensure_visible())
        self.root.bind("<Control-Shift-F12>", lambda _: self.use_development_escape())

        container = tk.Frame(self.root, background="#111827")
        container.pack(expand=True, fill="both")

        title = tk.Label(
            container,
            text="Secret Grounds",
            font=("Segoe UI", 44, "bold"),
            foreground="#F9FAFB",
            background="#111827",
        )
        title.pack(pady=(120, 24))

        unavailable = tk.Label(
            container,
            text="This computer is currently unavailable",
            font=("Segoe UI", 24),
            foreground="#F9FAFB",
            background="#111827",
        )
        unavailable.pack(pady=8)

        cashier = tk.Label(
            container,
            text="Please ask the cashier to start a session",
            font=("Segoe UI", 18),
            foreground="#D1D5DB",
            background="#111827",
        )
        cashier.pack(pady=8)

        pc_label = tk.Label(
            container,
            text=f"PC name: {self.pc_name}",
            font=("Segoe UI", 16),
            foreground="#D1D5DB",
            background="#111827",
        )
        pc_label.pack(pady=(36, 8))

        self.connection_label = tk.Label(
            container,
            text="Connection status: Offline",
            font=("Segoe UI", 16),
            foreground="#FCA5A5",
            background="#111827",
        )
        self.connection_label.pack(pady=8)

        self._update_connection_label()
        self._show_window()
        self._pump_actions()
        self.root.mainloop()

    def _pump_actions(self) -> None:
        if self.root is None:
            return

        while not self.action_queue.empty():
            action = self.action_queue.get_nowait()
            action()

        if self.stop_event.is_set():
            self._destroy_window()
            return

        self.root.after(100, self._pump_actions)

    def _show_window(self) -> None:
        if self.root is None:
            return

        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.root.focus_force()

    def _hide_window(self) -> None:
        if self.root is not None:
            self.root.withdraw()

    def _destroy_window(self) -> None:
        if self.root is not None:
            self.root.destroy()
            self.root = None

    def _prevent_close(self) -> None:
        self._ensure_visible()

    def _ensure_visible(self) -> None:
        with self.state_lock:
            should_show = self.state.is_locked and not self.state.dev_escape_active

        if should_show:
            self._show_window()

    def _update_connection_label(self) -> None:
        if self.connection_label is None:
            return

        is_online = self.is_online()
        label = "Online" if is_online else "Offline"
        color = "#86EFAC" if is_online else "#FCA5A5"
        self.connection_label.configure(
            text=f"Connection status: {label}",
            foreground=color,
        )
