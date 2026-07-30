from dataclasses import dataclass, field
from threading import Lock, Thread
from typing import Callable

from client.tk_runtime import create_tk_root, log_tk_startup_error


DEFAULT_REMINDER_THRESHOLDS_SECONDS = [15 * 60, 5 * 60, 60, 0]
TIME_ADDED_CONFIRMATION_TICKS = 6


@dataclass(frozen=True)
class CustomerOverlayViewModel:
    eyebrow: str
    title: str
    timer_label: str
    timer_value: str
    pc_name: str
    customer_name: str
    session_type: str
    connection_label: str
    connection_online: bool
    notice: str | None = None
    accent: str = "#22D3EE"


@dataclass
class CustomerOverlayState:
    is_visible: bool = False
    pc_name: str = ""
    shop_display_name: str = "Secret Grounds"
    customer_name: str | None = None
    session_type: str = "Session"
    seconds_value: int | None = None
    is_elapsed: bool = False
    connection_online: bool = True
    last_seconds_value: int | None = None
    fired_thresholds: set[int] = field(default_factory=set)
    notice: str | None = None
    notice_ticks_remaining: int = 0


class CustomerOverlayController:
    """Thread-safe boundary for customer-facing session status.

    Command, heartbeat, and timer threads update this controller instead of
    touching Tkinter widgets. The controller stores pure state and schedules all
    GUI refreshes on the Tkinter thread, which keeps customer UI changes isolated
    from the networking and recovery loops.
    """

    def __init__(
        self,
        pc_name: str,
        thresholds_seconds: list[int] | None = None,
        position: str = "top-right",
        enable_gui: bool = True,
        logout_action: Callable[[], str | None] | None = None,
    ) -> None:
        self.state = CustomerOverlayState(pc_name=pc_name)
        self.thresholds_seconds = thresholds_seconds or DEFAULT_REMINDER_THRESHOLDS_SECONDS
        self.position = position
        self.enable_gui = enable_gui
        self.logout_action = logout_action
        self.lock = Lock()
        self.gui_started = False
        self.gui_ready = False
        self.root = None
        self.title_label = None
        self.timer_label = None
        self.timer_value_label = None
        self.meta_label = None
        self.connection_label = None
        self.notice_label = None
        self.logout_button = None

    def show_session(
        self,
        session_type: str,
        customer_name: str | None = None,
        is_elapsed: bool = False,
    ) -> None:
        with self.lock:
            self.state.is_visible = True
            self.state.session_type = session_type
            self.state.customer_name = customer_name
            self.state.is_elapsed = is_elapsed
            self.state.fired_thresholds.clear()
            self.state.last_seconds_value = None
            self.state.notice = None
            self.state.notice_ticks_remaining = 0
        self.ensure_gui()
        self.schedule_gui_refresh()
        print(f"Customer overlay shown for {self.state.pc_name}: {session_type}.")

    def apply_settings(
        self,
        shop_display_name: str | None = None,
        thresholds_seconds: list[int] | None = None,
        position: str | None = None,
        enable_gui: bool | None = None,
    ) -> None:
        with self.lock:
            if shop_display_name:
                self.state.shop_display_name = shop_display_name
            if thresholds_seconds:
                self.thresholds_seconds = thresholds_seconds
            if position:
                self.position = position
            if enable_gui is not None:
                self.enable_gui = enable_gui
        self.schedule_gui_refresh()

    def hide(self) -> None:
        with self.lock:
            self.state.is_visible = False
            self.state.seconds_value = None
            self.state.fired_thresholds.clear()
            self.state.notice = None
            self.state.notice_ticks_remaining = 0
        self.schedule_gui_refresh()
        print("Customer overlay hidden.")

    def set_connection_online(self, is_online: bool) -> None:
        with self.lock:
            self.state.connection_online = is_online
        if not is_online:
            print("Customer overlay connection warning enabled.")
        self.schedule_gui_refresh()

    def update_time(self, seconds_value: int | None) -> list[str]:
        messages: list[str] = []
        with self.lock:
            self.state.seconds_value = seconds_value
            if self.state.is_elapsed or seconds_value is None:
                self._tick_notice_locked()
                return messages

            previous_seconds = self.state.last_seconds_value
            self.state.last_seconds_value = seconds_value
            self._tick_notice_locked()
            for threshold in self.thresholds_seconds:
                crossed_threshold = (
                    threshold == 0 and seconds_value <= 0
                ) or (
                    previous_seconds is not None
                    and previous_seconds > threshold
                    and seconds_value <= threshold
                )
                if crossed_threshold and threshold not in self.state.fired_thresholds:
                    self.state.fired_thresholds.add(threshold)
                    messages.append(self.build_reminder_message(threshold))
        for message in messages:
            print(f"Customer reminder: {message}")
        self.schedule_gui_refresh()
        return messages

    def show_time_added_confirmation(
        self,
        added_seconds: int,
        remaining_seconds: int | None,
    ) -> None:
        with self.lock:
            self.state.seconds_value = remaining_seconds
            self.state.notice = (
                f"Time added: {self.format_timer(added_seconds)}. "
                "Your session has been extended."
            )
            self.state.notice_ticks_remaining = TIME_ADDED_CONFIRMATION_TICKS
        print(
            "Customer time-added confirmation shown: "
            f"added_seconds={added_seconds}, remaining_seconds={remaining_seconds}."
        )
        self.schedule_gui_refresh()

    def reset_reminders_after_add_time(self) -> None:
        with self.lock:
            self.state.fired_thresholds = {
                threshold
                for threshold in self.state.fired_thresholds
                if self.state.seconds_value is not None
                and self.state.seconds_value <= threshold
            }

    def build_view_model(self) -> CustomerOverlayViewModel:
        with self.lock:
            state = CustomerOverlayState(
                is_visible=self.state.is_visible,
                pc_name=self.state.pc_name,
                shop_display_name=self.state.shop_display_name,
                customer_name=self.state.customer_name,
                session_type=self.state.session_type,
                seconds_value=self.state.seconds_value,
                is_elapsed=self.state.is_elapsed,
                connection_online=self.state.connection_online,
                fired_thresholds=set(self.state.fired_thresholds),
                last_seconds_value=self.state.last_seconds_value,
                notice=self.state.notice,
                notice_ticks_remaining=self.state.notice_ticks_remaining,
            )
        timer_label = "Elapsed Time" if state.is_elapsed else "Time Remaining"
        title = "Session Active" if not state.is_elapsed else "Open Time Active"
        if state.session_type == "Member Time":
            title = "Member Session Active"
        return CustomerOverlayViewModel(
            eyebrow=state.shop_display_name.upper(),
            title=title,
            timer_label=timer_label,
            timer_value=self.format_timer(state.seconds_value),
            pc_name=state.pc_name,
            customer_name=state.customer_name or "Guest",
            session_type=state.session_type,
            connection_label="Online" if state.connection_online else "Reconnecting",
            connection_online=state.connection_online,
            notice=state.notice,
            accent="#38BDF8" if state.connection_online else "#F97316",
        )

    def _tick_notice_locked(self) -> None:
        if self.state.notice_ticks_remaining <= 0:
            self.state.notice = None
            return
        self.state.notice_ticks_remaining -= 1
        if self.state.notice_ticks_remaining <= 0:
            self.state.notice = None

    def build_reminder_message(self, threshold: int) -> str:
        if threshold == 15 * 60:
            return "You have 15 minutes remaining. Please visit the cashier if you would like to add more time."
        if threshold == 5 * 60:
            return "You have 5 minutes remaining."
        if threshold == 60:
            return "Your session will end in 1 minute. Please save your work."
        return "Your time has ended. Please proceed to the cashier."

    def ensure_gui(self) -> None:
        if not self.enable_gui or self.gui_started:
            return
        self.gui_started = True
        Thread(target=self.run_gui, daemon=True).start()

    def run_gui(self) -> None:
        try:
            tk, root = create_tk_root()
        except Exception as error:
            log_tk_startup_error("Customer overlay GUI unavailable", error)
            return

        try:
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.configure(bg="#05070D")
            root.geometry(self.geometry_for_position(root, 360, 182))
            root.resizable(False, False)
            container = tk.Frame(
                root,
                bg="#05070D",
                highlightthickness=1,
                highlightbackground="#0891B2",
                padx=18,
                pady=14,
            )
            container.pack(fill="both", expand=True)
            eyebrow = tk.Label(
                container,
                text="SECRET GROUNDS",
                bg="#05070D",
                fg="#67E8F9",
                font=("Segoe UI", 8, "bold"),
                anchor="w",
            )
            eyebrow.pack(fill="x")
            self.title_label = tk.Label(
                container,
                text="Session Active",
                bg="#05070D",
                fg="#F8FAFC",
                font=("Segoe UI", 14, "bold"),
                anchor="w",
            )
            self.title_label.pack(fill="x", pady=(5, 0))
            self.timer_label = tk.Label(
                container,
                text="Time Remaining",
                bg="#05070D",
                fg="#94A3B8",
                font=("Segoe UI", 8, "bold"),
                anchor="w",
            )
            self.timer_label.pack(fill="x", pady=(12, 0))
            label = tk.Label(
                container,
                text="--:--",
                bg="#05070D",
                fg="#22D3EE",
                font=("Consolas", 28, "bold"),
                anchor="w",
            )
            label.pack(fill="x")
            self.timer_value_label = label
            self.meta_label = tk.Label(
                container,
                text="",
                bg="#05070D",
                fg="#CBD5E1",
                font=("Segoe UI", 9),
                anchor="w",
                justify="left",
            )
            self.meta_label.pack(fill="x", pady=(2, 0))
            self.connection_label = tk.Label(
                container,
                text="Online",
                bg="#05070D",
                fg="#86EFAC",
                font=("Segoe UI", 8, "bold"),
                anchor="w",
            )
            self.connection_label.pack(fill="x", pady=(8, 0))
            self.notice_label = tk.Label(
                container,
                text="",
                bg="#082F49",
                fg="#E0FAFF",
                font=("Segoe UI", 8, "bold"),
                anchor="w",
                padx=8,
                pady=5,
            )
            self.logout_button = tk.Button(
                container,
                text="Log out",
                command=self.handle_logout_clicked,
                font=("Segoe UI", 9, "bold"),
                foreground="#020617",
                background="#FBBF24",
                activeforeground="#020617",
                activebackground="#FDE68A",
                relief="flat",
                padx=10,
                pady=5,
            )
            self.root = root
            self.gui_ready = True
            self.refresh_gui()
            root.mainloop()
        except Exception as error:
            log_tk_startup_error("Customer overlay GUI failed", error)

    def geometry_for_position(self, root, width: int, height: int) -> str:
        screen_width = root.winfo_screenwidth()
        screen_height = root.winfo_screenheight()
        margin = 18
        left = margin if "left" in self.position else screen_width - width - margin
        top = margin if "top" in self.position else screen_height - height - margin
        return f"{width}x{height}+{left}+{top}"

    def schedule_gui_refresh(self) -> None:
        if not self.gui_ready or self.root is None:
            return
        try:
            self.root.after(0, self.refresh_gui)
        except Exception:
            pass

    def refresh_gui(self) -> None:
        if self.root is None:
            return
        with self.lock:
            is_visible = self.state.is_visible
        if not is_visible:
            self.root.withdraw()
            return
        model = self.build_view_model()
        if self.title_label is not None:
            self.title_label.configure(text=model.title)
        if self.timer_label is not None:
            self.timer_label.configure(text=model.timer_label.upper())
        if self.timer_value_label is not None:
            self.timer_value_label.configure(
                text=model.timer_value,
                foreground=model.accent,
            )
        if self.meta_label is not None:
            self.meta_label.configure(
                text=(
                    f"{model.pc_name}  |  {model.customer_name}  |  "
                    f"{model.session_type}"
                )
            )
        if self.connection_label is not None:
            self.connection_label.configure(
                text=f"Connection: {model.connection_label}",
                foreground="#86EFAC" if model.connection_online else "#FDBA74",
            )
        if self.notice_label is not None:
            if model.notice:
                self.notice_label.configure(text=model.notice)
                self.notice_label.pack(fill="x", pady=(8, 0))
            else:
                self.notice_label.pack_forget()
        if self.logout_button is not None:
            if model.session_type != "Open Time":
                self.logout_button.pack(fill="x", pady=(8, 0))
            else:
                self.logout_button.pack_forget()
        panel_height = 182
        if model.notice:
            panel_height += 36
        if model.session_type != "Open Time":
            panel_height += 42
        self.root.geometry(self.geometry_for_position(self.root, 360, panel_height))
        self.root.deiconify()
        self.root.attributes("-topmost", True)

    def handle_logout_clicked(self) -> None:
        if self.logout_action is None:
            return
        message = self.logout_action()
        if message:
            with self.lock:
                self.state.notice = message
                self.state.notice_ticks_remaining = TIME_ADDED_CONFIRMATION_TICKS
            self.schedule_gui_refresh()

    def format_timer(self, total_seconds: int | None) -> str:
        if total_seconds is None:
            return "--:--"
        total_seconds = max(0, total_seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"


def run_customer_overlay_gui_smoke() -> None:
    tk, root = create_tk_root()

    controller = CustomerOverlayController(
        "TEST-PC-01",
        enable_gui=False,
        logout_action=lambda: "Logout is disabled during the GUI smoke test.",
    )
    controller.show_session("Member Time", customer_name="Test Member")
    controller.update_time(180)
    model = controller.build_view_model()

    try:
        root.withdraw()
        root.overrideredirect(True)
        root.attributes("-topmost", True)
        root.configure(bg="#05070D")
        frame = tk.Frame(root, bg="#05070D", padx=18, pady=14)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text=model.title, bg="#05070D", fg="#F8FAFC").pack(anchor="w")
        tk.Label(frame, text=model.timer_value, bg="#05070D", fg=model.accent).pack(anchor="w")
        tk.Label(frame, text=model.customer_name, bg="#05070D", fg="#CBD5E1").pack(anchor="w")
        root.update_idletasks()
        root.update()
    finally:
        root.destroy()
