from __future__ import annotations

import queue
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Callable

from client.tk_runtime import (
    create_tk_root,
    log_tk_startup_error,
    show_native_startup_error,
)


GuiAction = Callable[[], None]
MemberLoginAction = Callable[[str, str], str | None]


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
    login_message: str | None = None
    is_login_processing: bool = False


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
        member_login_action: MemberLoginAction | None = None,
    ) -> None:
        self.pc_name = pc_name
        self.enable_gui = enable_gui
        self.enable_dev_escape = enable_dev_escape
        self.open_setup_wizard = open_setup_wizard
        self.member_login_action = member_login_action
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
        self.username_entry = None
        self.pin_entry = None
        self.login_button = None
        self.pin_toggle_button = None
        self.login_message_label = None
        self.pin_visible = False
        self.gui_ready_event = Event()
        self.gui_error: BaseException | None = None
        self.pump_after_id = None

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
            self.state.login_message = None
            self.state.is_login_processing = False

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
            self.state.login_message = None
            self.state.is_login_processing = False

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
            tk, self.root = create_tk_root()
        except Exception as error:
            self.gui_error = error
            self.gui_ready_event.set()
            log_tk_startup_error("Lock screen Tk root creation failed", error)
            show_native_startup_error("Lock screen Tk root creation failed", error)
            return
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

        if self.member_login_action is not None:
            login_frame = tk.Frame(
                container,
                background="#0B1220",
                highlightthickness=1,
                highlightbackground="#1F2937",
                padx=18,
                pady=16,
            )
            login_frame.pack(pady=(22, 4))

            login_title = tk.Label(
                login_frame,
                text="Member Login",
                font=("Segoe UI", 15, "bold"),
                foreground="#F9FAFB",
                background="#0B1220",
            )
            login_title.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))

            username_label = tk.Label(
                login_frame,
                text="Username",
                font=("Segoe UI", 10, "bold"),
                foreground="#CBD5E1",
                background="#0B1220",
            )
            username_label.grid(row=1, column=0, sticky="w")
            self.username_entry = tk.Entry(
                login_frame,
                width=24,
                font=("Segoe UI", 13),
                foreground="#F8FAFC",
                background="#111827",
                insertbackground="#F8FAFC",
                relief="flat",
            )
            self.username_entry.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 10))

            pin_label = tk.Label(
                login_frame,
                text="PIN",
                font=("Segoe UI", 10, "bold"),
                foreground="#CBD5E1",
                background="#0B1220",
            )
            pin_label.grid(row=3, column=0, sticky="w")
            self.pin_entry = tk.Entry(
                login_frame,
                width=14,
                font=("Segoe UI", 13),
                foreground="#F8FAFC",
                background="#111827",
                insertbackground="#F8FAFC",
                relief="flat",
                show="*",
            )
            self.pin_entry.grid(row=4, column=0, sticky="ew", pady=(4, 10))
            self.pin_toggle_button = tk.Button(
                login_frame,
                text="Show PIN",
                command=self._toggle_pin_visibility,
                font=("Segoe UI", 9, "bold"),
                foreground="#E0FAFF",
                background="#164E63",
                activeforeground="#FFFFFF",
                activebackground="#155E75",
                relief="flat",
                padx=10,
            )
            self.pin_toggle_button.grid(row=4, column=1, sticky="ew", padx=(8, 0), pady=(4, 10))
            self.login_button = tk.Button(
                login_frame,
                text="Log in",
                command=self._submit_member_login,
                font=("Segoe UI", 10, "bold"),
                foreground="#020617",
                background="#22D3EE",
                activeforeground="#020617",
                activebackground="#67E8F9",
                relief="flat",
                padx=14,
            )
            self.login_button.grid(row=4, column=2, sticky="ew", padx=(8, 0), pady=(4, 10))
            self.login_message_label = tk.Label(
                login_frame,
                text="",
                font=("Segoe UI", 10),
                foreground="#FBBF24",
                background="#0B1220",
                wraplength=360,
                justify="left",
            )
            self.login_message_label.grid(row=5, column=0, columnspan=3, sticky="w")
            login_frame.columnconfigure(0, weight=1)
            if self.username_entry is not None:
                self.username_entry.bind("<Return>", lambda _: self._submit_member_login())
            if self.pin_entry is not None:
                self.pin_entry.bind("<Return>", lambda _: self._submit_member_login())

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
        self.gui_ready_event.set()
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

        self.pump_after_id = self.root.after(100, self._pump_actions)

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
            root = self.root
            if self.pump_after_id is not None:
                try:
                    root.after_cancel(self.pump_after_id)
                except Exception:
                    pass
                self.pump_after_id = None
            self.root = None
            root.quit()
            root.destroy()

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
        self._update_login_state()
        self._update_connection_label()

    def _submit_member_login(self) -> None:
        if self.member_login_action is None:
            return
        with self.state_lock:
            if self.state.is_login_processing:
                return
            self.state.login_message = None

        username = self.username_entry.get().strip() if self.username_entry is not None else ""
        pin = self.pin_entry.get().strip() if self.pin_entry is not None else ""
        if not username:
            self._set_login_message("Enter your username.")
            return
        if not pin:
            self._set_login_message("Enter your PIN.")
            return

        with self.state_lock:
            self.state.is_login_processing = True
            self.state.login_message = "Checking your account..."
        self._update_login_state()

        def run_login() -> None:
            message = self.member_login_action(username, pin)
            self._schedule(lambda: self._finish_member_login(message))

        Thread(target=run_login, daemon=True).start()

    def _finish_member_login(self, message: str | None) -> None:
        with self.state_lock:
            self.state.is_login_processing = False
            self.state.login_message = message
        self._update_login_state()

    def _set_login_message(self, message: str) -> None:
        with self.state_lock:
            self.state.login_message = message
            self.state.is_login_processing = False
        self._update_login_state()

    def _toggle_pin_visibility(self) -> None:
        self.pin_visible = not self.pin_visible
        if self.pin_entry is not None:
            self.pin_entry.configure(show="" if self.pin_visible else "*")
        if self.pin_toggle_button is not None:
            self.pin_toggle_button.configure(text="Hide PIN" if self.pin_visible else "Show PIN")

    def _update_login_state(self) -> None:
        with self.state_lock:
            is_processing = self.state.is_login_processing
            message = self.state.login_message
            is_locked = self.state.is_locked
        state = "disabled" if is_processing or not is_locked else "normal"
        if self.login_button is not None:
            self.login_button.configure(
                state=state,
                text="Logging in..." if is_processing else "Log in",
            )
        if self.username_entry is not None:
            self.username_entry.configure(state=state)
        if self.pin_entry is not None:
            self.pin_entry.configure(state=state)
        if self.pin_toggle_button is not None:
            self.pin_toggle_button.configure(state="disabled" if is_processing else "normal")
        if self.login_message_label is not None:
            self.login_message_label.configure(text=message or "")


def run_lock_screen_gui_smoke(timeout_seconds: float = 6.0) -> None:
    login_calls: list[tuple[str, str]] = []
    login_release = Event()

    def login_action(username: str, pin: str) -> str:
        login_calls.append((username, pin))
        login_release.wait(timeout=2)
        return "That username or PIN did not match. Please try again."

    controller = LockScreenController(
        "TEST-PC-01",
        enable_gui=True,
        enable_dev_escape=True,
        member_login_action=login_action,
    )
    print(
        "WARNING: Development lock-screen escape is enabled. "
        "Disable ENABLE_DEV_ESCAPE before deployment."
    )
    try:
        finished = Event()
        errors: list[str] = []

        def exercise_login_widgets() -> None:
            try:
                required_widgets = [
                    controller.root,
                    controller.username_entry,
                    controller.pin_entry,
                    controller.pin_toggle_button,
                    controller.login_button,
                ]
                if any(widget is None for widget in required_widgets):
                    raise AssertionError("Member login widgets were not created.")

                controller.username_entry.insert(0, "test_member")
                controller.pin_entry.insert(0, "1234")
                if controller.pin_entry.cget("show") != "*":
                    raise AssertionError("PIN entry is not masked by default.")
                controller._toggle_pin_visibility()
                if controller.pin_entry.cget("show") != "":
                    raise AssertionError("PIN show toggle did not reveal the PIN.")
                controller._toggle_pin_visibility()
                if controller.pin_entry.cget("show") != "*":
                    raise AssertionError("PIN hide toggle did not mask the PIN.")

                controller._submit_member_login()
                controller._submit_member_login()
                controller.root.after(200, finish_login_widget_checks)
            except Exception as error:
                errors.append(str(error))
                login_release.set()
                if controller.root is not None:
                    controller.root.after(0, controller._destroy_window)
                finished.set()

        def finish_login_widget_checks() -> None:
            try:
                if len(login_calls) != 1:
                    raise AssertionError("Processing guard allowed duplicate login submission.")
                login_release.set()
                controller.root.after(300, finish_smoke)
            except Exception as error:
                errors.append(str(error))
                login_release.set()
                finish_smoke()

        def finish_smoke() -> None:
            if controller.root is not None:
                controller._destroy_window()
                finished.set()

        controller._schedule(exercise_login_widgets)
        controller._run_gui()
        if controller.gui_error is not None:
            raise RuntimeError(f"Lock screen GUI failed: {controller.gui_error}") from controller.gui_error
        if not finished.is_set():
            raise RuntimeError("Lock screen GUI closed before smoke checks completed.")
        if errors:
            raise RuntimeError(errors[0])
    finally:
        controller.stop()
