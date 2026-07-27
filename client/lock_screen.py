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
    shop_display_name: str = "Secret Grounds"
    customer_branding_text: str = "Ask the cashier to add more time to continue."
    mode: str = "available"
    headline: str = "This computer is currently unavailable"
    instruction: str = "Please ask the cashier to start a session"
    countdown: str | None = None
    is_final_warning: bool = False


@dataclass(frozen=True)
class LockScreenViewModel:
    brand: str
    pc_name: str
    mode: str
    headline: str
    instruction: str
    countdown: str | None
    connection_label: str
    connection_online: bool
    is_final_warning: bool
    accent: str


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
        open_setup_wizard: Callable[[], None] | None = None,
    ) -> None:
        self.pc_name = pc_name
        self.enable_gui = enable_gui
        self.enable_dev_escape = enable_dev_escape
        self.open_setup_wizard = open_setup_wizard
        self.state = LockScreenState()
        self.state_lock = Lock()
        self.stop_event = Event()
        self.action_queue: queue.Queue[GuiAction] = queue.Queue()
        self.gui_thread: Thread | None = None
        self.root = None
        self.connection_label = None
        self.headline_label = None
        self.instruction_label = None
        self.countdown_label = None
        self.title_label = None

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
            self.state.mode = "available"
            self.state.headline = "This computer is currently unavailable"
            self.state.instruction = "Please ask the cashier to start a session"
            self.state.countdown = None
            self.state.is_final_warning = False

        if reason:
            print(reason)
        print("Lock screen shown.")

        if self.enable_gui:
            self._schedule(self._show_window)

    def show_expiration_grace(self, remaining_seconds: int, final_warning: bool = False) -> None:
        minutes = max(0, remaining_seconds) // 60
        seconds = max(0, remaining_seconds) % 60
        with self.state_lock:
            self.state.is_locked = True
            self.state.dev_escape_active = False
            self.state.mode = "grace"
            self.state.headline = (
                f"PC shutting down in {max(0, remaining_seconds)} seconds."
                if final_warning
                else "Your time has ended."
            )
            self.state.instruction = (
                "This PC will shut down in 2 minutes.\n"
                f"{self.state.customer_branding_text}\n"
                "Please save and close your applications if you are finished."
            )
            self.state.countdown = f"{minutes:02d}:{seconds:02d}"
            self.state.is_final_warning = final_warning

        print(
            "Expiration grace screen shown: "
            f"shutdown in {minutes}:{seconds:02d}."
        )

        if self.enable_gui:
            self._schedule(self._show_window)

    def show_shutdown_transition(self) -> None:
        with self.state_lock:
            self.state.is_locked = True
            self.state.dev_escape_active = False
            self.state.mode = "shutting_down"
            self.state.headline = "Session complete. This PC is shutting down."
            self.state.instruction = (
                "Please leave the computer on this screen.\n"
                "The cashier will handle the next session."
            )
            self.state.countdown = None
            self.state.is_final_warning = False
        print("Shutdown transition screen shown.")
        if self.enable_gui:
            self._schedule(self._show_window)

    def show_shutdown_failure(self) -> None:
        with self.state_lock:
            self.state.is_locked = True
            self.state.dev_escape_active = False
            self.state.mode = "shutdown_failed"
            self.state.headline = "The session has ended."
            self.state.instruction = (
                "Please ask the cashier for assistance.\n"
                "This computer is locked until the shop resolves shutdown."
            )
            self.state.countdown = None
            self.state.is_final_warning = True
        print("Shutdown failure screen shown.")
        if self.enable_gui:
            self._schedule(self._show_window)

    def hide_for_session(self) -> None:
        with self.state_lock:
            self.state.is_locked = False
            self.state.dev_escape_active = False

        print("Lock screen hidden.")

        if self.enable_gui:
            self._schedule(self._hide_window)

    def apply_settings(
        self,
        shop_display_name: str | None = None,
        customer_branding_text: str | None = None,
    ) -> None:
        with self.state_lock:
            if shop_display_name:
                self.state.shop_display_name = shop_display_name
            if customer_branding_text:
                self.state.customer_branding_text = customer_branding_text
        if self.enable_gui:
            self._schedule(self._update_lock_text)

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

    def build_view_model(self) -> LockScreenViewModel:
        with self.state_lock:
            connection_online = self.state.is_online
            is_final_warning = self.state.is_final_warning
            mode = self.state.mode
            headline = self.state.headline
            instruction = self.state.instruction
            countdown = self.state.countdown
            brand = self.state.shop_display_name
        if is_final_warning:
            accent = "#F87171"
        elif mode == "grace":
            accent = "#FBBF24"
        else:
            accent = "#22D3EE"
        return LockScreenViewModel(
            brand=brand,
            pc_name=self.pc_name,
            mode=mode,
            headline=headline,
            instruction=instruction,
            countdown=countdown,
            connection_label="Online" if connection_online else "Offline",
            connection_online=connection_online,
            is_final_warning=is_final_warning,
            accent=accent,
        )

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
        self.root.configure(background="#05070D")
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.protocol("WM_DELETE_WINDOW", self._prevent_close)
        self.root.bind("<Unmap>", lambda _: self._ensure_visible())
        self.root.bind("<FocusOut>", lambda _: self._ensure_visible())
        self.root.bind("<Control-Shift-F12>", lambda _: self.use_development_escape())

        container = tk.Frame(self.root, background="#05070D")
        container.pack(expand=True, fill="both")

        if self.open_setup_wizard is not None:
            menu_button = tk.Menubutton(
                self.root,
                text="Client Menu",
                font=("Segoe UI", 10),
                foreground="#CBD5E1",
                background="#111827",
                activeforeground="#F9FAFB",
                activebackground="#1F2937",
                relief="flat",
                padx=12,
                pady=6,
            )
            menu = tk.Menu(menu_button, tearoff=False)
            menu.add_command(label="Setup...", command=self.open_setup_wizard)
            menu_button.configure(menu=menu)
            menu_button.place(relx=1.0, x=-18, y=18, anchor="ne")

        mark = tk.Label(
            container,
            text="SG",
            font=("Segoe UI", 34, "bold"),
            foreground="#020617",
            background="#22D3EE",
            padx=26,
            pady=12,
        )
        mark.pack(pady=(84, 22))

        title = tk.Label(
            container,
            text="Secret Grounds",
            font=("Segoe UI", 52, "bold"),
            foreground="#E0FAFF",
            background="#05070D",
        )
        title.pack(pady=(0, 22))
        self.title_label = title

        unavailable = tk.Label(
            container,
            text="This computer is currently unavailable",
            font=("Segoe UI", 28, "bold"),
            foreground="#F9FAFB",
            background="#05070D",
        )
        unavailable.pack(pady=8)
        self.headline_label = unavailable

        cashier = tk.Label(
            container,
            text="Please ask the cashier to start a session",
            font=("Segoe UI", 18),
            foreground="#A7B8D4",
            background="#05070D",
            justify="center",
        )
        cashier.pack(pady=8)
        self.instruction_label = cashier

        self.countdown_label = tk.Label(
            container,
            text="",
            font=("Consolas", 72, "bold"),
            foreground="#22D3EE",
            background="#05070D",
        )
        self.countdown_label.pack(pady=(20, 8))

        pc_label = tk.Label(
            container,
            text=f"PC name: {self.pc_name}",
            font=("Segoe UI", 18, "bold"),
            foreground="#67E8F9",
            background="#05070D",
        )
        pc_label.pack(pady=(36, 8))

        self.connection_label = tk.Label(
            container,
            text="Connection status: Offline",
            font=("Segoe UI", 16),
            foreground="#FCA5A5",
            background="#05070D",
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

        self._update_lock_text()
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

        model = self.build_view_model()
        color = "#86EFAC" if model.connection_online else "#FCA5A5"
        self.connection_label.configure(
            text=f"Connection status: {model.connection_label}",
            foreground=color,
        )

    def _update_lock_text(self) -> None:
        model = self.build_view_model()

        if self.headline_label is not None:
            self.headline_label.configure(
                text=model.headline,
                foreground="#FCA5A5" if model.is_final_warning else "#F9FAFB",
            )
        if self.instruction_label is not None:
            self.instruction_label.configure(text=model.instruction)
        if self.title_label is not None:
            self.title_label.configure(text=model.brand)
        if self.countdown_label is not None:
            self.countdown_label.configure(
                text=model.countdown or "",
                foreground=model.accent,
            )
        self._update_connection_label()
