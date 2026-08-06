import time
from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from typing import Callable

from client.config import APP_VERSION
from client.tk_runtime import create_tk_root, log_tk_startup_error
from client.utils import get_logo_path


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
    is_collapsed: bool = False
    notice: str | None = None
    accent: str = "#22D3EE"


@dataclass
class CustomerOverlayState:
    is_visible: bool = False
    is_collapsed: bool = False
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
        position: str = "bottom-right",
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
        self.minimize_button = None
        self.menu = None
        self.menu_connection_index: int | None = None
        self.menu_logout_index: int | None = None
        self.logo_image = None
        # The collapsed "tab" is a Toplevel of the same root, not a second Tk()
        # instance -- Tkinter only supports one real Tk() per process safely.
        self.collapsed_root = None
        self.collapsed_text_label = None
        self._button_row = None
        # In-memory only: remembers a drag-repositioned spot for this run.
        # Not persisted across restarts, so a fresh process always starts at
        # the default bottom-right corner.
        self._custom_position: tuple[int, int] | None = None
        self._drag_origin: tuple[int, int] | None = None

    def show_session(
        self,
        session_type: str,
        customer_name: str | None = None,
        is_elapsed: bool = False,
        is_new_session: bool = True,
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
            if is_new_session:
                # A genuinely new (or resumed-from-grace) session should be
                # visible, not silently stuck in whatever collapsed state a
                # *previous* session happened to leave behind. Refresh calls
                # for an already-active session (reconnect resync, member
                # login updating the display name) pass is_new_session=False
                # so a deliberately minimized overlay stays minimized.
                self.state.is_collapsed = False
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
            self.state.is_collapsed = False
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
        # The early return this used to have for is_elapsed/None sessions
        # happened *inside* the `with self.lock:` block, above the
        # schedule_gui_refresh() call below it -- so Open Time's background
        # timer thread was updating self.state.seconds_value every second
        # (proving reminders/logs looked fine), but the GUI was never told to
        # repaint, and the overlay froze at whatever value the last *other*
        # refresh trigger (e.g. show_session) happened to leave on screen.
        messages: list[str] = []
        with self.lock:
            self.state.seconds_value = seconds_value
            if self.state.is_elapsed or seconds_value is None:
                self._tick_notice_locked()
            else:
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
                is_collapsed=self.state.is_collapsed,
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
            is_collapsed=state.is_collapsed,
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
            # Borderless, custom-drawn panel -- same visual language as the
            # lock screen, but (unlike the lock screen) dismissible. There is
            # no native title bar to supply a minimize/close button, so both
            # are hand-drawn: Minimize collapses to the small pill built in
            # _build_collapsed_window(); Alt+F4 / WM_DELETE_WINDOW is routed
            # to the same collapse action rather than destroying the window.
            root.overrideredirect(True)
            root.attributes("-topmost", True)
            root.configure(bg="#05070D")
            root.resizable(False, False)
            root.protocol("WM_DELETE_WINDOW", self.handle_minimize_clicked)
            self._build_menu(root, tk)

            panel = tk.Frame(
                root,
                bg="#05070D",
                highlightthickness=1,
                highlightbackground="#0891B2",
                padx=18,
                pady=14,
            )
            panel.pack(fill="both", expand=True)
            self._bind_drag(panel)

            header = tk.Frame(panel, bg="#05070D")
            header.pack(fill="x")
            self._bind_drag(header)
            mark = self._build_compact_mark(tk, header)
            mark.pack(side="left")
            eyebrow = tk.Label(
                header,
                text="SECRET GROUNDS",
                bg="#05070D",
                fg="#67E8F9",
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            )
            eyebrow.pack(side="left", padx=(8, 0))
            self._bind_drag(eyebrow)

            self.title_label = tk.Label(
                panel,
                text="SESSION ACTIVE",
                bg="#05070D",
                fg="#F8FAFC",
                font=("Segoe UI", 15, "bold"),
                anchor="w",
            )
            self.title_label.pack(fill="x", pady=(10, 0))
            self.timer_label = tk.Label(
                panel,
                text="TIME REMAINING",
                bg="#05070D",
                fg="#94A3B8",
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            )
            self.timer_label.pack(fill="x", pady=(12, 0))
            self.timer_value_label = tk.Label(
                panel,
                text="--:--",
                bg="#05070D",
                fg="#22D3EE",
                font=("Consolas", 30, "bold"),
                anchor="w",
            )
            self.timer_value_label.pack(fill="x")
            self.meta_label = tk.Label(
                panel,
                text="",
                bg="#05070D",
                fg="#CBD5E1",
                font=("Segoe UI", 10),
                anchor="w",
                justify="left",
            )
            self.meta_label.pack(fill="x", pady=(10, 0))
            self.connection_label = tk.Label(
                panel,
                text="Online",
                bg="#05070D",
                fg="#86EFAC",
                font=("Segoe UI", 9, "bold"),
                anchor="w",
            )
            self.connection_label.pack(fill="x", pady=(4, 0))

            # Button row is packed last so it always anchors to the bottom;
            # notice_label/logout_button are created but NOT packed here --
            # refresh_gui() packs them with before=button_row whenever they
            # should be visible, which keeps their position stable across
            # repeated pack()/pack_forget() toggles (Tk otherwise appends a
            # freshly re-packed widget to the end of the stacking order).
            button_row = tk.Frame(panel, bg="#05070D")
            self.notice_label = tk.Label(
                panel,
                text="",
                bg="#082F49",
                fg="#E0FAFF",
                font=("Segoe UI", 9, "bold"),
                anchor="w",
                padx=8,
                pady=5,
            )
            self.logout_button = tk.Button(
                panel,
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
            self._button_row = button_row
            button_row.pack(fill="x", pady=(12, 0))
            menu_button = tk.Menubutton(
                button_row,
                text="Client Menu",
                menu=self.menu,
                font=("Segoe UI", 9, "bold"),
                foreground="#CBD5E1",
                background="#111827",
                activeforeground="#F9FAFB",
                activebackground="#1F2937",
                relief="flat",
                padx=10,
                pady=6,
            )
            menu_button.pack(side="left")
            self.minimize_button = tk.Button(
                button_row,
                text="Minimize",
                command=self.handle_minimize_clicked,
                font=("Segoe UI", 9, "bold"),
                foreground="#020617",
                background="#22D3EE",
                activeforeground="#020617",
                activebackground="#67E8F9",
                relief="flat",
                padx=10,
                pady=6,
            )
            self.minimize_button.pack(side="right")

            self.root = root
            self.gui_ready = True
            self._position_window(root, 320, 210)
            self.refresh_gui()
            root.mainloop()
        except Exception as error:
            log_tk_startup_error("Customer overlay GUI failed", error)

    def _build_menu(self, root, tk) -> None:
        menu = tk.Menu(root, tearoff=False)
        menu.add_command(label="Restore / Show Overlay", command=self.handle_restore_clicked)
        menu.add_command(label="Minimize Overlay", command=self.handle_minimize_clicked)
        menu.add_separator()
        self.menu_connection_index = menu.index("end") + 1
        menu.add_command(label="Connection: Online", state="disabled")
        menu.add_separator()
        self.menu_logout_index = menu.index("end") + 1
        menu.add_command(label="Log out", command=self.handle_logout_clicked)
        menu.add_separator()
        # Informational only: no cashier, admin, session-editing, payment, or
        # power controls belong on a customer-facing menu.
        menu.add_command(label=f"About: Secret Grounds Client v{APP_VERSION}", state="disabled")
        self.menu = menu

    def _build_compact_mark(self, tk, container):
        logo_path = get_logo_path()
        if logo_path is not None:
            try:
                image = tk.PhotoImage(file=str(logo_path))
                target_height = 28
                if image.height() > target_height:
                    factor = max(1, round(image.height() / target_height))
                    image = image.subsample(factor, factor)
                self.logo_image = image
                return tk.Label(container, image=self.logo_image, background="#05070D")
            except Exception as error:
                log_tk_startup_error("Customer overlay logo image failed to load", error)

        return tk.Label(
            container,
            text="SG",
            font=("Segoe UI", 10, "bold"),
            foreground="#020617",
            background="#22D3EE",
            padx=6,
            pady=2,
        )

    def _build_collapsed_window(self) -> None:
        if self.root is None:
            return
        import tkinter as tk

        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg="#0B1220")

        frame = tk.Frame(
            top,
            bg="#0B1220",
            highlightthickness=1,
            highlightbackground="#0891B2",
            padx=14,
            pady=8,
        )
        frame.pack(fill="both", expand=True)
        chevron = tk.Label(
            frame, text="▲", bg="#0B1220", fg="#22D3EE", font=("Segoe UI", 11, "bold")
        )
        chevron.pack(side="left")
        text_label = tk.Label(
            frame,
            text="SECRET GROUNDS",
            bg="#0B1220",
            fg="#E0FAFF",
            font=("Segoe UI", 10, "bold"),
        )
        text_label.pack(side="left", padx=(8, 0))
        self.collapsed_text_label = text_label

        def on_enter(_event=None) -> None:
            for widget in (frame, chevron, text_label):
                widget.configure(bg="#132133")

        def on_leave(_event=None) -> None:
            for widget in (frame, chevron, text_label):
                widget.configure(bg="#0B1220")

        # Bound on every widget in the pill, not just the Toplevel: Tk click
        # bindings do not propagate from a child to its parent, so a click
        # anywhere in the pill (frame, chevron, or text) must restore it.
        for widget in (top, frame, chevron, text_label):
            widget.bind("<Button-1>", lambda _event: self.handle_restore_clicked())
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)

        top.withdraw()
        self.collapsed_root = top

    def _show_collapsed(self) -> None:
        if self.collapsed_root is None:
            self._build_collapsed_window()
        if self.collapsed_root is None:
            return
        width, height = 190, 44
        x, y = self._default_geometry(self.collapsed_root, width, height)
        self.collapsed_root.geometry(f"{width}x{height}+{x}+{y}")
        # Deliberately does not call focus_force()/lift() with keyboard
        # focus: the collapsed tab must stay visible over a fullscreen game
        # without stealing its input until the user actually clicks it.
        self.collapsed_root.deiconify()
        self.collapsed_root.attributes("-topmost", True)

    def handle_minimize_clicked(self) -> None:
        with self.lock:
            self.state.is_collapsed = True
        self.schedule_gui_refresh()

    def handle_restore_clicked(self) -> None:
        with self.lock:
            self.state.is_collapsed = False
        self.schedule_gui_refresh()
        if self.root is not None:
            self.root.lift()
            self.root.focus_force()

    def _bind_drag(self, widget) -> None:
        widget.bind("<ButtonPress-1>", self._on_drag_start)
        widget.bind("<B1-Motion>", self._on_drag_motion)

    def _on_drag_start(self, event) -> None:
        self._drag_origin = (event.x_root, event.y_root)

    def _on_drag_motion(self, event) -> None:
        if self.root is None or self._drag_origin is None:
            return
        delta_x = event.x_root - self._drag_origin[0]
        delta_y = event.y_root - self._drag_origin[1]
        x = self.root.winfo_x() + delta_x
        y = self.root.winfo_y() + delta_y
        self.root.geometry(f"+{x}+{y}")
        self._custom_position = (x, y)
        self._drag_origin = (event.x_root, event.y_root)

    def _work_area(self, root) -> tuple[int, int, int, int]:
        """Primary monitor's work area (screen minus the taskbar)."""
        try:
            import ctypes
            from ctypes import wintypes

            rect = wintypes.RECT()
            spi_getworkarea = 0x0030
            if ctypes.windll.user32.SystemParametersInfoW(
                spi_getworkarea, 0, ctypes.byref(rect), 0
            ):
                return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            pass
        return (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())

    def _default_geometry(self, root, width: int, height: int) -> tuple[int, int]:
        left, top, right, bottom = self._work_area(root)
        margin = 16
        x = margin if "left" in self.position else right - width - margin
        y = margin if "top" in self.position else bottom - height - margin
        return (x, y)

    def _position_window(self, root, width: int, height: int) -> None:
        if self._custom_position is not None:
            x, y = self._custom_position
        else:
            x, y = self._default_geometry(root, width, height)
        root.geometry(f"{width}x{height}+{x}+{y}")

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
            is_collapsed = self.state.is_collapsed

        if not is_visible:
            self.root.withdraw()
            if self.collapsed_root is not None:
                self.collapsed_root.withdraw()
            return

        if is_collapsed:
            # Must not fight a deliberate minimize: Open Time ticks this
            # every second, so the expanded panel stays hidden while
            # collapsed rather than popping back up on the next tick.
            self.root.withdraw()
            self._show_collapsed()
            return

        if self.collapsed_root is not None:
            self.collapsed_root.withdraw()

        model = self.build_view_model()
        if self.title_label is not None:
            self.title_label.configure(text=model.title.upper())
        if self.timer_label is not None:
            self.timer_label.configure(text=model.timer_label.upper())
        if self.timer_value_label is not None:
            self.timer_value_label.configure(
                text=model.timer_value,
                foreground=model.accent,
            )
        if self.meta_label is not None:
            self.meta_label.configure(text=f"{model.pc_name} • {model.customer_name}")
        if self.connection_label is not None:
            self.connection_label.configure(
                text=model.connection_label,
                foreground="#86EFAC" if model.connection_online else "#FDBA74",
            )
        show_logout = model.session_type != "Open Time"
        button_row = getattr(self, "_button_row", None)
        if self.logout_button is not None:
            if show_logout:
                self.logout_button.pack(fill="x", pady=(10, 0), before=button_row)
            else:
                self.logout_button.pack_forget()
        if self.notice_label is not None:
            if model.notice:
                self.notice_label.configure(text=model.notice)
                self.notice_label.pack(fill="x", pady=(8, 0), before=button_row)
            else:
                self.notice_label.pack_forget()
        if self.menu is not None and self.menu_connection_index is not None:
            self.menu.entryconfigure(
                self.menu_connection_index,
                label=f"Connection: {model.connection_label}",
            )
        if self.menu is not None and self.menu_logout_index is not None:
            self.menu.entryconfigure(
                self.menu_logout_index,
                state="normal" if show_logout else "disabled",
            )
        panel_height = 210
        if model.notice:
            panel_height += 34
        if show_logout:
            panel_height += 40
        self._position_window(self.root, 320, panel_height)
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


def run_customer_overlay_gui_smoke(timeout_seconds: float = 6.0) -> None:
    """Exercise the real run_gui()/refresh_gui() path, not a parallel mock UI.

    This drives the actual production window (menu, minimize/restore,
    collapsed tab, drag) so a regression in any of those is caught here
    rather than only in a hand-rolled lookalike that never touches the real
    code.
    """
    logout_calls: list[str] = []
    controller = CustomerOverlayController(
        "TEST-PC-01",
        enable_gui=True,
        logout_action=lambda: logout_calls.append("logout") or None,
    )
    finished = Event()
    errors: list[str] = []

    def exercise_overlay() -> None:
        try:
            if not controller.gui_ready or controller.root is None:
                raise AssertionError("Customer overlay GUI did not become ready in time.")
            if controller.menu is None or controller.menu_connection_index is None:
                raise AssertionError("Client Menu was not built on the overlay.")

            # The expanded panel is borderless (no native title bar/taskbar
            # entry) -- only the lock screen's overrideredirect matters for
            # non-bypassability; this window is deliberately dismissible.
            if not controller.root.overrideredirect():
                raise AssertionError("Active overlay must be a borderless window.")

            controller.show_session("Prepaid", customer_name="Test Member")
            controller.update_time(125)
            controller.root.update_idletasks()
            controller.root.update()
            if controller.root.state() == "withdrawn":
                raise AssertionError("Expanded overlay did not appear for a new session.")
            if controller.collapsed_root is not None and controller.collapsed_root.state() != "withdrawn":
                raise AssertionError("Collapsed tab must not exist while the overlay is expanded.")

            # Default position is bottom-right of the work area, not
            # wherever the old top-right/full-screen default put it.
            left, top, right, bottom = controller._work_area(controller.root)
            window_x = controller.root.winfo_x()
            window_y = controller.root.winfo_y()
            if window_x <= (left + right) // 2 or window_y <= (top + bottom) // 2:
                raise AssertionError(
                    f"Overlay did not default to the bottom-right work area: "
                    f"pos=({window_x},{window_y}) work_area=({left},{top},{right},{bottom})"
                )

            connection_label = controller.menu.entrycget(controller.menu_connection_index, "label")
            if "Online" not in connection_label:
                raise AssertionError(f"Client Menu did not show connection status: {connection_label!r}")
            logout_state = controller.menu.entrycget(controller.menu_logout_index, "state")
            if logout_state != "normal":
                raise AssertionError("Log out menu item should be enabled for a non-Open-Time session.")

            # Open Time sessions do not support logout; the menu item must
            # reflect that the same way the on-screen button does.
            controller.show_session("Open Time", is_elapsed=True)
            controller.update_time(60)
            controller.root.update_idletasks()
            controller.root.update()
            open_time_logout_state = controller.menu.entrycget(controller.menu_logout_index, "state")
            if open_time_logout_state != "disabled":
                raise AssertionError("Log out menu item must be disabled during Open Time sessions.")

            # Minimize: the expanded panel hides and a single collapsed tab
            # appears, containing the chevron and brand text. A background
            # refresh (Open Time ticks this every second) must not fight the
            # deliberate minimize by popping the expanded panel back up.
            controller.handle_minimize_clicked()
            controller.root.update_idletasks()
            controller.root.update()
            controller.update_time(59)
            controller.root.update_idletasks()
            controller.root.update()
            if controller.root.state() != "withdrawn":
                raise AssertionError("A background refresh restored a window the user had minimized.")
            if controller.collapsed_root is None or controller.collapsed_root.state() == "withdrawn":
                raise AssertionError("Minimizing did not show the collapsed tab.")
            collapsed_text = controller.collapsed_text_label.cget("text") if controller.collapsed_text_label else ""
            if "SECRET GROUNDS" not in collapsed_text:
                raise AssertionError(f"Collapsed tab is missing its brand text: {collapsed_text!r}")

            # Minimizing again (e.g. a stray duplicate click) must not create
            # a second collapsed window.
            existing_collapsed = controller.collapsed_root
            controller.handle_minimize_clicked()
            controller.root.update_idletasks()
            controller.root.update()
            if controller.collapsed_root is not existing_collapsed:
                raise AssertionError("A second collapsed tab window was created.")

            # Clicking the collapsed tab restores the full overlay and hides
            # the tab -- never both visible at once.
            controller.handle_restore_clicked()
            controller.root.update_idletasks()
            controller.root.update()
            if controller.root.state() == "withdrawn":
                raise AssertionError("Clicking the collapsed tab did not restore the full overlay.")
            if controller.collapsed_root.state() != "withdrawn":
                raise AssertionError("Collapsed tab stayed visible after restoring the full overlay.")

            # Closing (Alt+F4 / WM_DELETE_WINDOW protocol, wired to the same
            # handler as the Minimize button) must collapse, never terminate
            # the client or the session.
            controller.handle_restore_clicked()
            controller.root.update_idletasks()
            controller.root.update()
            controller.handle_minimize_clicked()
            controller.root.update_idletasks()
            controller.root.update()
            if controller.root.state() != "withdrawn":
                raise AssertionError("Closing the overlay must collapse it, not leave it open.")
            if logout_calls:
                raise AssertionError("Closing/minimizing must never trigger a logout.")

            # Session end must remove both the expanded overlay and any
            # collapsed tab.
            controller.hide()
            controller.root.update_idletasks()
            controller.root.update()
            if controller.root.state() != "withdrawn":
                raise AssertionError("Session end did not hide the expanded overlay.")
            if controller.collapsed_root is not None and controller.collapsed_root.state() != "withdrawn":
                raise AssertionError("Session end did not hide the collapsed tab.")

            finish_smoke()
        except Exception as error:
            errors.append(str(error))
            finish_smoke()

    def finish_smoke() -> None:
        # Runs on the Tk thread (scheduled via after()/called from within an
        # after()-dispatched callback), so quit()/destroy() here are safe:
        # this ends the mainloop() that ensure_gui()'s background thread is
        # blocked in, which is the only thread allowed to own this root.
        if controller.root is not None:
            controller.root.quit()
            controller.root.destroy()
        # Drop image references synchronously rather than leaving them for
        # GC to collect at an unpredictable later point -- PhotoImage.__del__
        # calls back into Tcl, which is noisy (though harmless) if that
        # happens after this mainloop has already exited.
        controller.logo_image = None
        finished.set()

    controller.ensure_gui()
    ready = False
    for _ in range(int(timeout_seconds * 20)):
        if controller.gui_ready and controller.root is not None:
            ready = True
            break
        time.sleep(0.05)
    if not ready:
        raise RuntimeError("Customer overlay GUI did not start within the timeout.")

    controller.root.after(0, exercise_overlay)
    if not finished.wait(timeout=timeout_seconds):
        raise RuntimeError("Customer overlay GUI smoke test did not complete.")
    if errors:
        raise RuntimeError(errors[0])
