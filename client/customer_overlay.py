from dataclasses import dataclass, field
from threading import Lock, Thread


DEFAULT_REMINDER_THRESHOLDS_SECONDS = [15 * 60, 5 * 60, 60, 0]


@dataclass
class CustomerOverlayState:
    is_visible: bool = False
    pc_name: str = ""
    customer_name: str | None = None
    session_type: str = "Session"
    seconds_value: int | None = None
    is_elapsed: bool = False
    connection_online: bool = True
    last_seconds_value: int | None = None
    fired_thresholds: set[int] = field(default_factory=set)


class CustomerOverlayController:
    """Thread-safe boundary for customer-facing session status.

    The first production milestone keeps this lightweight and Tkinter-free by
    default so it cannot destabilize games. The state boundary is separate from
    client.py, making it straightforward to replace with a compact always-on-top
    Tkinter window later.
    """

    def __init__(
        self,
        pc_name: str,
        thresholds_seconds: list[int] | None = None,
        position: str = "top-right",
        enable_gui: bool = True,
    ) -> None:
        self.state = CustomerOverlayState(pc_name=pc_name)
        self.thresholds_seconds = thresholds_seconds or DEFAULT_REMINDER_THRESHOLDS_SECONDS
        self.position = position
        self.enable_gui = enable_gui
        self.lock = Lock()
        self.gui_started = False
        self.gui_ready = False
        self.root = None
        self.text_var = None

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
        self.ensure_gui()
        self.schedule_gui_refresh()
        print(f"Customer overlay shown for {self.state.pc_name}: {session_type}.")

    def hide(self) -> None:
        with self.lock:
            self.state.is_visible = False
            self.state.seconds_value = None
            self.state.fired_thresholds.clear()
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
                return messages

            previous_seconds = self.state.last_seconds_value
            self.state.last_seconds_value = seconds_value
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

    def reset_reminders_after_add_time(self) -> None:
        with self.lock:
            self.state.fired_thresholds = {
                threshold
                for threshold in self.state.fired_thresholds
                if self.state.seconds_value is not None
                and self.state.seconds_value <= threshold
            }

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
            import tkinter as tk
        except Exception as error:
            print(f"Customer overlay GUI unavailable: {error}")
            return

        try:
            root = tk.Tk()
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.configure(bg="#020617")
            root.geometry(self.geometry_for_position(root, 260, 126))
            text_var = tk.StringVar(value="")
            label = tk.Label(
                root,
                textvariable=text_var,
                bg="#020617",
                fg="#e0faff",
                justify="left",
                padx=14,
                pady=12,
                font=("Segoe UI", 10, "bold"),
            )
            label.pack(fill="both", expand=True)
            self.root = root
            self.text_var = text_var
            self.gui_ready = True
            self.refresh_gui()
            root.mainloop()
        except Exception as error:
            print(f"Customer overlay GUI failed: {error}")

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
        if self.root is None or self.text_var is None:
            return
        with self.lock:
            state = CustomerOverlayState(
                is_visible=self.state.is_visible,
                pc_name=self.state.pc_name,
                customer_name=self.state.customer_name,
                session_type=self.state.session_type,
                seconds_value=self.state.seconds_value,
                is_elapsed=self.state.is_elapsed,
                connection_online=self.state.connection_online,
                fired_thresholds=set(self.state.fired_thresholds),
                last_seconds_value=self.state.last_seconds_value,
            )
        if not state.is_visible:
            self.root.withdraw()
            return
        heading = "Elapsed" if state.is_elapsed else "Time Remaining"
        warning = "" if state.connection_online else "\nConnection warning"
        self.text_var.set(
            "SECRET GROUNDS\n"
            f"{state.pc_name}\n"
            f"{state.customer_name or 'Guest'}\n"
            f"{heading}: {self.format_timer(state.seconds_value)}\n"
            f"{state.session_type}{warning}"
        )
        self.root.deiconify()
        self.root.attributes("-topmost", True)

    def format_timer(self, total_seconds: int | None) -> str:
        if total_seconds is None or total_seconds < 0:
            return "--:--"
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"
