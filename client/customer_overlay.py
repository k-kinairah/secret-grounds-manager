import time
from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from typing import Callable

from client import theme
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
    used_time_value: str
    pc_name: str
    customer_name: str
    session_type: str
    is_member_session: bool
    is_elapsed: bool
    connection_label: str
    connection_online: bool
    is_collapsed: bool = False
    notice: str | None = None
    accent: str = "#22D3EE"
    is_visible: bool = False
    seconds_value: int | None = None
    used_seconds: int = 0


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
    # Wall-clock elapsed since this controller last saw a *new* session
    # start (client.py's start_local_session with is_new_session=True), used
    # purely for the "USED TIME" stat. Deliberately separate from
    # seconds_value/is_elapsed (the primary remaining/elapsed timer), which
    # already has its own server-anchored, reconnect-safe derivation --
    # reusing that logic here isn't necessary since "how long you've been
    # using this station" only needs to survive a GUI collapse/reconnect
    # within the same client process, not a full process restart.
    session_started_monotonic: float | None = None


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
        presentation_refresh: Callable[[], None] | None = None,
    ) -> None:
        self.state = CustomerOverlayState(pc_name=pc_name)
        self.thresholds_seconds = thresholds_seconds or DEFAULT_REMINDER_THRESHOLDS_SECONDS
        self.position = position
        self.enable_gui = enable_gui
        self.external_presentation_active = False
        self.logout_action = logout_action
        self.presentation_refresh = presentation_refresh
        self.lock = Lock()
        self.gui_started = False
        self.gui_ready = False
        self.root = None
        self.title_label = None
        self.timer_label = None
        self.timer_value_label = None
        self.used_time_value_label = None
        self.pc_label = None
        self.meta_label = None
        self.session_type_label = None
        self.connection_label = None
        self.notice_label = None
        self.logout_button = None
        self.minimize_button = None
        self.menu = None
        self.menu_logout_index: int | None = None
        self.logo_image = None
        # The collapsed "tab" is a Toplevel of the same root, not a second Tk()
        # instance -- Tkinter only supports one real Tk() per process safely.
        self.collapsed_root = None
        self.collapsed_text_label = None
        self._button_row = None
        self._identity_row = None
        self._used_time_row = None
        self._divider_row = None
        self._pc_row = None
        self._footer_anchor = None
        self._panel = None
        # In-memory only: remembers a drag-repositioned spot for this run.
        # Not persisted across restarts, so a fresh process always starts at
        # the default bottom-right corner.
        self._custom_position: tuple[int, int] | None = None
        self._drag_origin: tuple[int, int] | None = None

    def set_external_presentation_active(self, is_active: bool) -> None:
        with self.lock:
            self.external_presentation_active = is_active
            if is_active:
                self.enable_gui = False

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
                self.state.session_started_monotonic = time.monotonic()
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
            if enable_gui is not None and not self.external_presentation_active:
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
                session_started_monotonic=self.state.session_started_monotonic,
            )
        timer_label = "Elapsed Time" if state.is_elapsed else "Remaining Time"
        title = "Session Active" if not state.is_elapsed else "Open Time Active"
        is_member_session = state.session_type == "Member Time"
        if is_member_session:
            title = "Member Session Active"

        if state.session_started_monotonic is None:
            used_seconds = 0
        else:
            used_seconds = max(0, int(time.monotonic() - state.session_started_monotonic))

        return CustomerOverlayViewModel(
            eyebrow=state.shop_display_name.upper(),
            title=title,
            timer_label=timer_label,
            timer_value=self.format_timer(state.seconds_value),
            used_time_value=self.format_used_time(used_seconds),
            pc_name=state.pc_name,
            customer_name=state.customer_name or "Guest",
            session_type=state.session_type,
            is_member_session=is_member_session,
            is_elapsed=state.is_elapsed,
            connection_label="Online" if state.connection_online else "Reconnecting",
            connection_online=state.connection_online,
            is_collapsed=state.is_collapsed,
            notice=state.notice,
            # Session accent is about *what kind* of session this is (an
            # Open Time countdown reads orange, a member/timed session reads
            # cyan/blue) -- deliberately independent of connection_online,
            # which only ever affects the separate connection-warning line.
            accent=theme.OPEN_TIME_ACCENT if state.is_elapsed else theme.ACCENT_CYAN,
            is_visible=state.is_visible,
            seconds_value=state.seconds_value,
            used_seconds=used_seconds,
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
            root.configure(bg=theme.PANEL)
            root.resizable(False, False)
            root.protocol("WM_DELETE_WINDOW", self.handle_minimize_clicked)
            theme.apply_soft_shadow(root)
            self._build_menu(root, tk)

            # Compact, notification-sized card (closer to a Discord/Riot
            # Client toast than a full settings panel) -- tight padding, no
            # spacer rows, so the card's height is exactly what its content
            # needs rather than padded out with empty space.
            panel = tk.Frame(
                root,
                bg=theme.PANEL,
                highlightthickness=1,
                highlightbackground=theme.LINE,
                padx=12,
                pady=7,
            )
            panel.pack(fill="both", expand=True)
            self._bind_drag(panel)

            header = tk.Frame(panel, bg=theme.PANEL)
            header.pack(fill="x")
            self._bind_drag(header)
            mark = self._build_compact_mark(tk, header)
            mark.pack(side="left")
            eyebrow = tk.Label(
                header,
                text="SECRET GROUNDS",
                bg=theme.PANEL,
                fg=theme.ACCENT_CYAN,
                font=("Segoe UI", 8, "bold"),
                anchor="w",
            )
            eyebrow.pack(side="left", padx=(8, 0))
            self._bind_drag(eyebrow)

            minimize_icon = self._build_icon_button(tk, header, "chevron-down", self.handle_minimize_clicked)
            minimize_icon.pack(side="right")
            menu_button = self._build_icon_menu_button(header, tk)
            menu_button.pack(side="right", padx=(0, 2))

            # Not shown in the reference (the card goes straight from the
            # header to the timer) -- kept as a widget so the rest of the
            # code and model stay uniform, but never packed.
            self.title_label = tk.Label(
                panel,
                text="SESSION ACTIVE",
                bg=theme.PANEL,
                fg=theme.TEXT_PRIMARY,
                font=("Segoe UI", 11, "bold"),
                anchor="w",
            )

            # The timer is the single most important number on the card --
            # large and first, with its caption reading like a subtitle
            # underneath rather than a label announcing it from above.
            self.timer_value_label = tk.Label(
                panel,
                text="--:--",
                bg=theme.PANEL,
                fg=theme.ACCENT_CYAN,
                font=("Consolas", 31, "bold"),
                anchor="w",
            )
            self.timer_value_label.pack(fill="x", pady=(3, 0))
            self.timer_label = tk.Label(
                panel,
                text="Remaining Time",
                bg=theme.PANEL,
                fg=theme.TEXT_MUTED,
                font=("Segoe UI", 8),
                anchor="w",
            )
            self.timer_label.pack(fill="x")

            self._build_divider(tk, panel).pack(fill="x", pady=(5, 3))

            # Only relevant for a Remaining-Time session (Package/Member):
            # for Open Time, "Used Time" and the primary "Elapsed Time"
            # timer above are the same duration, so showing both is a
            # redundant second representation of one number. refresh_gui()
            # hides this row entirely when the session is elapsed-based.
            self._used_time_row, _, self.used_time_value_label = self._build_stat_stack(
                tk, panel, "clock", "Used Time", "0s"
            )

            # Identity block, Discord-profile-style: an icon-prefixed name
            # (bold and large for a member, quieter for a guest) with a
            # small caption underneath -- this replaces the old separate
            # "PC-01 / name / session type" stack, which buried the name
            # the customer actually cares about among smaller, equally-
            # weighted lines.
            self._identity_row, self.meta_label, self.session_type_label = self._build_stat_stack(
                tk, panel, "user", "", "", top_pady=(6, 0), name_first=True
            )

            # PC name: small, quiet, bottom-left -- an identifier, not a
            # stat that deserves the same visual weight as the timer. Tk
            # has no per-widget opacity, so "quieter" is approximated with
            # a dimmer pre-blended color (roughly TEXT_MUTED at 75% over
            # the panel background) plus a smaller, non-bold font rather
            # than a true alpha reduction.
            pc_row = tk.Frame(panel, bg=theme.PANEL)
            pc_row.pack(fill="x", pady=(5, 0))
            theme.Icon(pc_row, tk, "computer", size=11, color="#6B7A8F", bg=theme.PANEL).pack(side="left")
            self.pc_label = tk.Label(
                pc_row, text="", bg=theme.PANEL, fg="#6B7A8F", font=("Segoe UI", 8), anchor="w"
            )
            self.pc_label.pack(side="left", padx=(5, 0))
            self._pc_row = pc_row

            # Connection warning and notice are created but NOT packed here
            # -- refresh_gui() packs them (with before=self._divider_row)
            # only when relevant, which keeps their position stable across
            # repeated pack()/pack_forget() toggles (Tk otherwise appends a
            # freshly re-packed widget to the end of the stacking order).
            self.connection_label = tk.Label(
                panel,
                text="Reconnecting...",
                bg=theme.PANEL,
                fg=theme.WARNING,
                font=("Segoe UI", 8, "bold"),
                anchor="w",
            )
            self.notice_label = tk.Label(
                panel,
                text="",
                bg=theme.PANEL_STRONG,
                fg=theme.TEXT_PRIMARY,
                font=("Segoe UI", 8, "bold"),
                anchor="w",
                padx=10,
                pady=5,
            )

            self._divider_row = self._build_divider(tk, panel)
            button_row = tk.Frame(panel, bg=theme.PANEL)
            self._button_row = button_row
            # Log Out is a quiet secondary action (dark fill, thin outline,
            # glow on hover) below a divider -- not a filled gradient CTA
            # competing with the timer for attention. Uses a muted teal
            # rather than the full-brightness ACCENT_CYAN so it reads as
            # even less visually dominant against the timer/identity above.
            self.logout_button = theme.OutlineButton(
                button_row,
                tk,
                text="Log Out",
                command=self.handle_logout_clicked,
                width=104,
                height=28,
                accent="#197E96",
                font=("Segoe UI", 8, "bold"),
                bg=theme.PANEL,
            )
            self.logout_button.pack()

            # A stable, always-packed, zero-height anchor at the true
            # bottom of the panel. connection_label/notice_label/divider_row/
            # button_row are all shown and hidden independently via
            # pack()/pack_forget(); Tk's `before=` option requires its
            # target to already be a managed slave, so each of those needs
            # something that is *always* packed to anchor against -- this is
            # that anchor.
            footer_anchor = tk.Frame(panel, bg=theme.PANEL, height=0)
            footer_anchor.pack(fill="x")
            self._footer_anchor = footer_anchor
            self._panel = panel

            self.root = root
            self.gui_ready = True
            self._position_window(root, 276, 210)
            self.refresh_gui()
            root.mainloop()
        except Exception as error:
            log_tk_startup_error("Customer overlay GUI failed", error)

    def _build_divider(self, tk, container):
        return tk.Frame(container, bg=theme.LINE, height=1)

    def _build_menu(self, root, tk) -> None:
        # No "Connection: Online" entry: a customer assumes the connection
        # is fine when everything is working, so surfacing that fact is
        # noise. A real problem is already shown inline on the card itself
        # (self.connection_label) -- the menu doesn't need to repeat it.
        menu = tk.Menu(root, tearoff=False)
        menu.add_command(label="Restore / Show Overlay", command=self.handle_restore_clicked)
        menu.add_command(label="Minimize Overlay", command=self.handle_minimize_clicked)
        menu.add_separator()
        self.menu_logout_index = menu.index("end") + 1
        menu.add_command(label="Log out", command=self.handle_logout_clicked)
        menu.add_separator()
        # Informational only: no cashier, admin, session-editing, payment, or
        # power controls belong on a customer-facing menu.
        menu.add_command(label=f"About: Secret Grounds Client v{APP_VERSION}", state="disabled")
        self.menu = menu

    def _build_icon_button(self, tk, container, icon_name: str, command):
        """A small, unobtrusive outlined-icon control (minimize, etc).

        Deliberately not a labeled button: the customer should never read
        this panel as offering administrative controls, just a couple of
        quiet icon affordances in the header, the way a modern consumer app
        would present them.
        """
        icon = theme.Icon(container, tk, icon_name, size=12, color=theme.TEXT_MUTED, bg=theme.PANEL)
        icon.canvas.configure(cursor="hand2")

        def on_enter(_event=None) -> None:
            icon.set_color(theme.TEXT_PRIMARY)

        def on_leave(_event=None) -> None:
            icon.set_color(theme.TEXT_MUTED)

        icon.canvas.bind("<Button-1>", lambda _event: command())
        icon.canvas.bind("<Enter>", on_enter)
        icon.canvas.bind("<Leave>", on_leave)
        return icon

    def _build_icon_menu_button(self, container, tk):
        button = tk.Menubutton(
            container,
            menu=self.menu,
            bg=theme.PANEL,
            activebackground=theme.PANEL,
            relief="flat",
            bd=0,
            padx=6,
            pady=1,
            cursor="hand2",
        )
        theme.Icon(button, tk, "dots", size=12, color=theme.TEXT_MUTED, bg=theme.PANEL).pack()
        return button

    def _build_stat_stack(
        self, tk, container, icon_name: str, top_text: str, bottom_text: str,
        top_pady=(0, 0), name_first: bool = False,
    ):
        """An icon-prefixed two-line text stack -- the reference's pattern
        for Used Time (caption above value) and the member identity block
        (name above caption, larger and brighter since it's the more
        important line there). Returns (row, top_label, bottom_label) so
        callers can show/hide the whole row and update either line.
        """
        row = tk.Frame(container, bg=theme.PANEL)
        row.pack(fill="x", pady=top_pady)
        theme.Icon(row, tk, icon_name, size=12, color=theme.TEXT_MUTED, bg=theme.PANEL).pack(side="left", anchor="n")
        text_col = tk.Frame(row, bg=theme.PANEL)
        text_col.pack(side="left", padx=(6, 0), fill="x", expand=True)
        if name_first:
            top_label = tk.Label(
                text_col, text=top_text, bg=theme.PANEL, fg=theme.TEXT_PRIMARY, font=("Segoe UI", 12, "bold"),
                anchor="w",
            )
            bottom_label = tk.Label(
                text_col, text=bottom_text, bg=theme.PANEL, fg=theme.TEXT_MUTED, font=("Segoe UI", 8), anchor="w"
            )
        else:
            top_label = tk.Label(
                text_col, text=top_text, bg=theme.PANEL, fg=theme.TEXT_MUTED, font=("Segoe UI", 7, "bold"),
                anchor="w",
            )
            bottom_label = tk.Label(
                text_col, text=bottom_text, bg=theme.PANEL, fg=theme.TEXT_PRIMARY, font=("Segoe UI", 10, "bold"),
                anchor="w",
            )
        top_label.pack(fill="x")
        bottom_label.pack(fill="x")
        return row, top_label, bottom_label

    def _build_compact_mark(self, tk, container, bg: str = theme.PANEL):
        logo_path = get_logo_path()
        if logo_path is not None:
            try:
                image = tk.PhotoImage(file=str(logo_path))
                target_height = 18
                if image.height() > target_height:
                    factor = max(1, round(image.height() / target_height))
                    image = image.subsample(factor, factor)
                self.logo_image = image
                return tk.Label(container, image=self.logo_image, background=bg)
            except Exception as error:
                log_tk_startup_error("Customer overlay logo image failed to load", error)

        return tk.Label(
            container,
            text="SG",
            font=("Segoe UI", 9, "bold"),
            foreground=theme.TEXT_ON_ACCENT,
            background=theme.ACCENT_CYAN,
            padx=5,
            pady=2,
        )

    def _build_collapsed_window(self) -> None:
        if self.root is None:
            return
        import tkinter as tk

        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=theme.PANEL_STRONG)
        theme.apply_soft_shadow(top)

        # Exactly the logo mark + "SECRET GROUNDS" -- no arrow, no other
        # text, per the reference: "No arrow, no text other than 'SECRET
        # GROUNDS'." Border is a pre-blended cyan (Tk highlightbackground
        # has no alpha, so ~35% ACCENT_CYAN over PANEL_STRONG is baked into
        # a solid hex instead of a true translucent rgba border), brightening
        # on hover as the nearest Tk equivalent of a glow.
        _PILL_BORDER = "#1F657A"
        _PILL_BORDER_HOVER = "#20879D"
        frame = tk.Frame(
            top,
            bg=theme.PANEL_STRONG,
            highlightthickness=1,
            highlightbackground=_PILL_BORDER,
            padx=13,
            pady=7,
        )
        frame.pack(fill="both", expand=True)
        mark = self._build_compact_mark(tk, frame, bg=theme.PANEL_STRONG)
        mark.pack(side="left")
        text_label = tk.Label(
            frame,
            text="SECRET GROUNDS",
            bg=theme.PANEL_STRONG,
            fg=theme.TEXT_PRIMARY,
            font=("Segoe UI", 8, "bold"),
        )
        text_label.pack(side="left", padx=(6, 0))
        self.collapsed_text_label = text_label

        def on_enter(_event=None) -> None:
            for widget in (frame, mark, text_label):
                widget.configure(bg=theme.PANEL)
            frame.configure(highlightbackground=_PILL_BORDER_HOVER)

        def on_leave(_event=None) -> None:
            for widget in (frame, mark, text_label):
                widget.configure(bg=theme.PANEL_STRONG)
            frame.configure(highlightbackground=_PILL_BORDER)

        # Bound on every widget in the pill, not just the Toplevel: Tk click
        # bindings do not propagate from a child to its parent, so a click
        # anywhere in the pill (frame, mark, or text) must restore it.
        for widget in (top, frame, mark, text_label):
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
        width, height = 174, 38
        x, y = self._default_geometry(self.collapsed_root, width, height)
        self.collapsed_root.geometry(f"{width}x{height}+{x}+{y}")
        theme.apply_rounded_corners(self.collapsed_root, width, height, theme.RADIUS_PILL)
        # Deliberately does not call focus_force()/lift() with keyboard
        # focus: the collapsed tab must stay visible over a fullscreen game
        # without stealing its input until the user actually clicks it.
        was_hidden = self.collapsed_root.state() == "withdrawn"
        self.collapsed_root.deiconify()
        if was_hidden:
            theme.fade_in(self.collapsed_root, duration_ms=100, steps=6)
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
        # The panel's height changes as optional rows (notice, connection
        # warning) show/hide, so the rounded-corner window region has to be
        # re-applied to match every time the geometry changes -- a region
        # sized for the old height would clip or leave square corners on
        # whatever no longer lines up.
        theme.apply_rounded_corners(root, width, height)

    def schedule_gui_refresh(self) -> None:
        refresh = self.presentation_refresh
        if refresh is not None:
            try:
                refresh()
            except Exception as error:
                print(f"Customer overlay presentation refresh failed: {error}")
        if not self.gui_ready or self.root is None:
            return
        try:
            self.root.after(0, self.refresh_gui)
        except Exception:
            pass

    def set_presentation_refresh(self, refresh: Callable[[], None] | None) -> None:
        self.presentation_refresh = refresh

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
        if self.timer_value_label is not None:
            self.timer_value_label.configure(
                text=model.timer_value,
                foreground=model.accent,
            )
        if self.timer_label is not None:
            # Title Case, matching how the timer's own value reads -- not
            # ALL CAPS, which fought the timer for attention as the loudest
            # thing on the card.
            self.timer_label.configure(text=model.timer_label)
        if self.used_time_value_label is not None:
            self.used_time_value_label.configure(text=model.used_time_value)
        if self.pc_label is not None:
            self.pc_label.configure(text=model.pc_name)

        # Open Time has no customer identity to show (it's anonymous, and
        # "OPEN TIME ACTIVE" already says what this is) and no logout --
        # both the identity block and the divider/LOG OUT row below it are
        # only relevant for a timed or member session.
        show_identity_and_logout = model.session_type != "Open Time"
        identity_row = getattr(self, "_identity_row", None)
        used_time_row = getattr(self, "_used_time_row", None)
        divider_row = getattr(self, "_divider_row", None)
        button_row = getattr(self, "_button_row", None)

        if self.meta_label is not None:
            # The customer's own identity is the single most important line
            # on the panel -- large and bright for a member session; still
            # present but visually quieter ("Guest") for an anonymous one.
            # Member names display lowercase (e.g. "kinairah"), matching
            # the approved reference; not uppercased either.
            display_name = model.customer_name.lower() if model.is_member_session else model.customer_name
            self.meta_label.configure(
                text=display_name,
                foreground=theme.TEXT_PRIMARY if model.is_member_session else theme.TEXT_MUTED,
                font=("Segoe UI", 12 if model.is_member_session else 10, "bold"),
            )
        if self.session_type_label is not None:
            self.session_type_label.configure(text="Member" if model.is_member_session else model.session_type)

        # Used Time is only shown for a Remaining-Time session (Package /
        # Member). For Open Time, it would be a second, redundant display
        # of the exact same duration as the primary "Elapsed Time" timer
        # above -- packed before identity_row (both anchor against the
        # always-packed pc_row) so the two conditional rows land in the
        # right order regardless of which combination is currently visible.
        if used_time_row is not None:
            if not model.is_elapsed:
                used_time_row.pack(fill="x", pady=(0, 0), before=self._pc_row)
            else:
                used_time_row.pack_forget()
        if identity_row is not None:
            if show_identity_and_logout:
                identity_row.pack(fill="x", pady=(5, 0), before=self._pc_row)
            else:
                identity_row.pack_forget()

        # Each of these re-packs against self._footer_anchor (always packed,
        # zero-height, at the true bottom of the panel) rather than against
        # each other -- `before=` requires its target to already be a
        # managed slave, which divider_row/button_row are not when a
        # session hides them (Open Time). Packing in this order keeps the
        # visual stacking connection -> notice -> divider -> button.
        footer_anchor = self._footer_anchor

        # Connection status only earns screen space when there is actually a
        # problem -- otherwise it is a wasted line customers do not need.
        # Never shows "Online": the customer already assumes it's working.
        if self.connection_label is not None:
            if model.connection_online:
                self.connection_label.pack_forget()
            else:
                self.connection_label.configure(text=f"{model.connection_label}...")
                self.connection_label.pack(fill="x", pady=(4, 0), before=footer_anchor)

        if self.notice_label is not None:
            if model.notice:
                self.notice_label.configure(text=model.notice)
                self.notice_label.pack(fill="x", pady=(4, 0), before=footer_anchor)
            else:
                self.notice_label.pack_forget()

        if divider_row is not None:
            if show_identity_and_logout:
                divider_row.pack(fill="x", pady=(5, 3), before=footer_anchor)
            else:
                divider_row.pack_forget()
        if self.logout_button is not None:
            if show_identity_and_logout:
                button_row.pack(fill="x", pady=(0, 0), before=footer_anchor)
            else:
                button_row.pack_forget()
        if self.menu is not None and self.menu_logout_index is not None:
            self.menu.entryconfigure(
                self.menu_logout_index,
                state="normal" if show_identity_and_logout else "disabled",
            )

        # The panel's height changes as optional rows (identity block,
        # connection warning, notice, divider/logout) show and hide -- ask
        # Tk what the content actually needs rather than hand-maintaining a
        # magic-number estimate per combination of visible rows, which is
        # exactly the kind of thing that silently drifts out of sync with
        # the layout and clips/un-maps whatever no longer fits.
        if self._panel is not None:
            self._panel.update_idletasks()
            panel_height = self._panel.winfo_reqheight()
        else:
            panel_height = 210
        self._position_window(self.root, 276, panel_height)
        # Fade in only on a genuine hidden -> shown transition (a new
        # session appearing, or restoring from the collapsed pill), not on
        # every per-second timer refresh that happens to pass through here
        # while already visible.
        was_hidden = self.root.state() == "withdrawn"
        self.root.deiconify()
        if was_hidden:
            theme.fade_in(self.root)
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

    def format_used_time(self, total_seconds: int) -> str:
        """Human-readable "how long have I been here" -- "1h 12m", "45m",
        "32s" -- deliberately not the HH:MM:SS clock format used for the
        primary timer, so the two stats read as visually distinct at a
        glance rather than looking like a typo/duplicate of each other."""
        total_seconds = max(0, total_seconds)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes:02d}m"
        if minutes:
            return f"{minutes}m {seconds:02d}s"
        return f"{seconds}s"


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
            if controller.menu is None or controller.menu_logout_index is None:
                raise AssertionError("Client Menu was not built on the overlay.")
            # The menu must never claim "Online" -- a customer already
            # assumes the connection is fine when nothing is wrong, so a
            # standing "Connection: Online" entry was pure noise.
            menu_labels = [
                controller.menu.entrycget(index, "label")
                for index in range(controller.menu.index("end") + 1)
                if controller.menu.type(index) == "command"
            ]
            if any("online" in label.lower() for label in menu_labels):
                raise AssertionError(f"Client Menu must never show an 'Online' entry: {menu_labels!r}")

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

            # Compact, notification-sized card -- not the old full settings
            # panel width.
            if controller.root.winfo_width() > 276:
                raise AssertionError(f"Expanded overlay is not compact: width={controller.root.winfo_width()}")

            # LOG OUT is a quiet outline button (dark fill, thin accent
            # border), not a filled gradient CTA competing with the timer.
            if not isinstance(controller.logout_button, theme.OutlineButton):
                raise AssertionError("LOG OUT must use the outline button style.")

            # A timed/member session reads cyan, not the Open Time orange.
            if controller.timer_value_label.cget("foreground") != theme.ACCENT_CYAN:
                raise AssertionError(
                    "Timed/member session timer must use the cyan accent, got "
                    f"{controller.timer_value_label.cget('foreground')!r}."
                )

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

            logout_state = controller.menu.entrycget(controller.menu_logout_index, "state")
            if logout_state != "normal":
                raise AssertionError("Log out menu item should be enabled for a non-Open-Time session.")

            # Connection status must stay hidden while actually online, and
            # only appear once there is a real problem -- called directly
            # (what schedule_gui_refresh's root.after(0, ...) would do
            # anyway) since this code is itself running from inside that
            # same after-callback call stack.
            if controller.connection_label.winfo_ismapped():
                raise AssertionError("Connection status must be hidden while online.")
            controller.set_connection_online(False)
            controller.refresh_gui()
            controller.root.update_idletasks()
            if not controller.connection_label.winfo_ismapped():
                raise AssertionError("Connection warning did not appear when offline.")
            controller.set_connection_online(True)
            controller.refresh_gui()
            controller.root.update_idletasks()
            if controller.connection_label.winfo_ismapped():
                raise AssertionError("Connection warning did not clear once back online.")

            # A non-member session's identity line ("Guest") must read
            # quieter than a member's own name.
            non_member_font = controller.meta_label.cget("font")
            controller.show_session("Member Time", customer_name="Kinairah")
            controller.update_time(300)
            controller.root.update_idletasks()
            controller.root.update()
            if controller.meta_label.cget("text") != "kinairah":
                raise AssertionError("Member name must display lowercase, matching the approved reference.")
            member_font = controller.meta_label.cget("font")
            if member_font == non_member_font:
                raise AssertionError("Member name must be visually more prominent than a guest name.")
            if not controller.build_view_model().is_member_session:
                raise AssertionError("Member Time session was not flagged as a member session.")

            # Remaining-Time sessions (Package/Member) show the Used Time
            # stat -- it's meaningfully different from the countdown above.
            if not controller._used_time_row.winfo_ismapped():
                raise AssertionError("Used Time row must be visible for a Remaining-Time session.")

            # Open Time sessions do not support logout; the menu item must
            # reflect that the same way the on-screen button does.
            controller.show_session("Open Time", is_elapsed=True)
            controller.update_time(60)
            controller.root.update_idletasks()
            controller.root.update()
            open_time_logout_state = controller.menu.entrycget(controller.menu_logout_index, "state")
            if open_time_logout_state != "disabled":
                raise AssertionError("Log out menu item must be disabled during Open Time sessions.")

            # Open Time's primary timer already shows "Elapsed Time" -- a
            # separate Used Time stat would be a second, redundant display
            # of the exact same duration, so it must be hidden here.
            if controller._used_time_row.winfo_ismapped():
                raise AssertionError("Used Time row must be hidden for Open Time (redundant with Elapsed Time).")

            # Open Time reads orange, distinct from a timed/member session's
            # cyan -- independent of connection state.
            if controller.timer_value_label.cget("foreground") != theme.OPEN_TIME_ACCENT:
                raise AssertionError(
                    "Open Time timer must use the orange accent, got "
                    f"{controller.timer_value_label.cget('foreground')!r}."
                )

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
