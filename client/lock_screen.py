from __future__ import annotations

import queue
from dataclasses import dataclass
from threading import Event, Lock, Thread
from typing import Callable

from client import theme
from client.tk_runtime import (
    create_tk_root,
    log_tk_startup_error,
    show_native_startup_error,
)
from client.utils import get_logo_path


GuiAction = Callable[[], None]
MemberLoginAction = Callable[[str, str], str | None]
PresentationRefresh = Callable[[], None]


@dataclass
class LockScreenState:
    is_locked: bool = True
    is_online: bool = False
    dev_escape_active: bool = False
    shop_display_name: str = "Secret Grounds"
    customer_branding_text: str = "Ask the cashier to add more time to continue."
    mode: str = "available"
    headline: str = "Available soon."
    instruction: str = "Please ask the cashier to begin your session."
    countdown: str | None = None
    is_final_warning: bool = False
    login_message: str | None = None
    is_login_processing: bool = False
    locked_member_name: str = ""


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
    is_locked: bool = True
    dev_escape_active: bool = False
    login_message: str | None = None
    is_login_processing: bool = False
    locked_member_name: str = ""


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
        open_setup_wizard: Callable[[], object] | None = None,
        member_login_action: MemberLoginAction | None = None,
        session_unlock_action: Callable[[str], str | None] | None = None,
        presentation_refresh: PresentationRefresh | None = None,
    ) -> None:
        self.pc_name = pc_name
        self.enable_gui = enable_gui
        self.enable_dev_escape = enable_dev_escape
        self.open_setup_wizard = open_setup_wizard
        self.member_login_action = member_login_action
        self.session_unlock_action = session_unlock_action
        self.presentation_refresh = presentation_refresh
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
        self.pc_label = None
        self.username_entry = None
        self.pin_entry = None
        self.login_button = None
        self.pin_toggle_button = None
        self.login_message_label = None
        self.pin_visible = False
        self._username_field = None
        self._pin_field = None
        self._login_card_canvas = None
        self.unlock_pin_entry = None
        self.unlock_pin_toggle_button = None
        self.unlock_button = None
        self.unlock_message_label = None
        self._unlock_pin_field = None
        self._unlock_card_canvas = None
        self.gui_ready_event = Event()
        self.gui_error: BaseException | None = None
        self.pump_after_id = None
        # Tk drops a PhotoImage as soon as nothing references it from Python,
        # even while it is still assigned to a Label -- keep this reference
        # alive for as long as the controller exists so the logo doesn't
        # vanish shortly after being displayed.
        self.logo_image = None
        # Kept alive for the same reason as logo_image above -- PhotoImage
        # is dropped as soon as nothing references it from Python.
        self.watermark_image = None
        self._font_size: Callable[[int], int] | None = None

    def start(self) -> None:
        if self.enable_dev_escape:
            print(
                "WARNING: Development lock-screen escape is enabled. "
                "Disable ENABLE_DEV_ESCAPE before deployment."
            )

        if not self.enable_gui:
            print("Lock screen shown.")
            self._notify_presentation()
            return

        self.gui_thread = Thread(target=self._run_gui, daemon=True)
        self.gui_thread.start()

    def stop(self) -> None:
        self.stop_event.set()

        if self.enable_gui:
            self._schedule(self._destroy_window)

    def show_locked(self, reason: str | None = None) -> None:
        with self.state_lock:
            previous_mode = self.state.mode
            self.state.is_locked = True
            self.state.dev_escape_active = False
            self.state.mode = "available"
            self.state.headline = "Available soon."
            self.state.instruction = "Please ask the cashier to begin your session."
            self.state.countdown = None
            self.state.is_final_warning = False
            self.state.login_message = None
            self.state.is_login_processing = False

        if reason:
            print(reason)
        print("Lock screen shown.")
        self._notify_presentation()

        if self.enable_gui:
            if previous_mode != "available":
                self._schedule(self._reset_credential_fields)
            self._schedule(self._show_window)

    def show_member_locked(self, member_display_name: str) -> None:
        """Member-initiated "Lock PC": reuses the exact same is_locked=True
        window/focus/topmost machinery as the idle "Available soon" screen
        (show_locked) and the expiration screens below -- the only
        difference is which content renders inside that already-locked
        window. The active session and its timer are untouched by this."""
        with self.state_lock:
            previous_mode = self.state.mode
            self.state.is_locked = True
            self.state.dev_escape_active = False
            self.state.mode = "member_locked"
            self.state.locked_member_name = member_display_name
            self.state.headline = "Session locked."
            self.state.instruction = f"This PC is reserved for {member_display_name}."
            self.state.countdown = None
            self.state.is_final_warning = False
            self.state.login_message = None
            self.state.is_login_processing = False

        print(f"Member-locked screen shown for {member_display_name}.")
        self._notify_presentation()

        if self.enable_gui:
            if previous_mode != "member_locked":
                self._schedule(self._reset_credential_fields)
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
        self._notify_presentation()

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
        self._notify_presentation()
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
        self._notify_presentation()
        if self.enable_gui:
            self._schedule(self._show_window)

    def hide_for_session(self) -> None:
        with self.state_lock:
            self.state.is_locked = False
            self.state.dev_escape_active = False
            self.state.login_message = None
            self.state.is_login_processing = False

        print("Lock screen hidden.")
        self._notify_presentation()

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
        self._notify_presentation()
        if self.enable_gui:
            self._schedule(self._update_lock_text)

    def set_connection_online(self, is_online: bool) -> None:
        with self.state_lock:
            if self.state.is_online == is_online:
                return
            self.state.is_online = is_online

        label = "Online" if is_online else "Offline"
        print(f"WebSocket {label.lower()} state.")
        self._notify_presentation()

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
            is_locked = self.state.is_locked
            dev_escape_active = self.state.dev_escape_active
            login_message = self.state.login_message
            is_login_processing = self.state.is_login_processing
            locked_member_name = self.state.locked_member_name
        if is_final_warning:
            accent = "#F87171"
        elif mode == "grace":
            accent = "#FBBF24"
        elif mode == "member_locked":
            accent = "#F43F5E"
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
            is_locked=is_locked,
            dev_escape_active=dev_escape_active,
            login_message=login_message,
            is_login_processing=is_login_processing,
            locked_member_name=locked_member_name,
        )

    def use_development_escape(self) -> None:
        if not self.enable_dev_escape:
            return

        with self.state_lock:
            self.state.dev_escape_active = True

        print("Development escape used.")
        self._notify_presentation()

        if self.enable_gui:
            self._schedule(self._hide_window)

    def set_presentation_refresh(self, refresh: PresentationRefresh | None) -> None:
        self.presentation_refresh = refresh

    def set_pin_visible(self, is_visible: bool) -> None:
        self.pin_visible = bool(is_visible)

    def submit_member_login(self, username: str, pin: str) -> None:
        if self.member_login_action is None:
            return
        with self.state_lock:
            if self.state.is_login_processing:
                return
            self.state.login_message = None

        username = username.strip()
        pin = pin.strip()
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
        self._notify_presentation()

        def run_login() -> None:
            message = self.member_login_action(username, pin)
            if self.enable_gui:
                self._schedule(lambda: self._finish_member_login(message))
            else:
                self._finish_member_login(message)

        Thread(target=run_login, daemon=True).start()

    def submit_session_unlock(self, pin: str) -> None:
        # Deliberately PIN-only, no username: the active session already
        # identifies which member this is -- verification happens against
        # that specific member server-side (session_service.
        # clear_customer_lock_by_pin), never a general member lookup.
        if self.session_unlock_action is None:
            return
        with self.state_lock:
            if self.state.is_login_processing:
                return
            self.state.login_message = None

        pin = pin.strip()
        if not pin:
            self._set_login_message("Enter your PIN.")
            return

        with self.state_lock:
            self.state.is_login_processing = True
            self.state.login_message = "Checking your PIN..."
        self._update_login_state()
        self._notify_presentation()

        def run_unlock() -> None:
            message = self.session_unlock_action(pin)
            if self.enable_gui:
                self._schedule(lambda: self._finish_member_login(message))
            else:
                self._finish_member_login(message)

        Thread(target=run_unlock, daemon=True).start()

    def _notify_presentation(self) -> None:
        refresh = self.presentation_refresh
        if refresh is None:
            return
        try:
            refresh()
        except Exception as error:
            print(f"Lock screen presentation refresh failed: {error}")

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
        self.root.configure(background=theme.BG_DEEP)
        self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.protocol("WM_DELETE_WINDOW", self._prevent_close)
        self.root.bind("<Unmap>", lambda _: self._ensure_visible())
        self.root.bind("<FocusOut>", lambda _: self._handle_focus_out())
        self.root.bind("<Control-Shift-F12>", lambda _: self.use_development_escape())

        # Scale text/target sizes off the real screen height so the lock
        # screen reads correctly on a 1366x768 laptop panel, a 1920x1080
        # monitor, and an ultrawide alike, instead of one fixed point size
        # that only looks right at 1080p.
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        scale = max(0.8, min(1.4, screen_height / 1080))

        def font_size(base: int) -> int:
            return max(9, round(base * scale))

        self._font_size = font_size

        # Branded background: an "environment", not a wallpaper -- layered
        # per the approved browser-preview reference (docs/ui-preview):
        # gradient base -> corner ambient glows ("fog"/"volumetric light"
        # stand-in) -> two curved flowing streaks -> a bloom behind the
        # branding cluster -> the real logo as a faded watermark -> a
        # particle scatter -> a vignette darkening the edges. Tk cannot do
        # blur, alpha compositing, SVG paths, or blend modes, so every layer
        # here is the closest pure-Tk equivalent (stippled dithering for
        # "faint", a real spline for "curved", corner patches for
        # "vignette") -- see the porting-limitations note in the project
        # notes for what still can't be reproduced.
        background_canvas = tk.Canvas(
            self.root, width=screen_width, height=screen_height, highlightthickness=0, bd=0
        )
        background_canvas.place(x=0, y=0, relwidth=1, relheight=1)
        theme.draw_vertical_gradient(background_canvas, screen_width, screen_height // 2, theme.BG_DEEP, theme.BG_MID)
        theme.draw_vertical_gradient(
            background_canvas,
            screen_width,
            screen_height - screen_height // 2,
            theme.BG_MID,
            theme.BG_DEEP,
            y_offset=screen_height // 2,
        )
        # Fog: two soft, asymmetrically-placed ambient glows standing in for
        # the reference's volumetric light/fog layers. Deliberately small
        # and restrained -- an earlier pass sized these at 0.28-0.3x screen
        # height, which read as large solid cyan/blue corner shapes rather
        # than ambient texture.
        theme.draw_soft_glow(
            background_canvas,
            round(screen_width * 0.12),
            round(screen_height * 0.82),
            round(screen_height * 0.13),
            theme.ACCENT_PRIMARY,
        )
        theme.draw_soft_glow(
            background_canvas,
            round(screen_width * 0.88),
            round(screen_height * 0.18),
            round(screen_height * 0.12),
            theme.ACCENT_CYAN,
        )
        # Two flowing streaks, routed through the outer thirds so their
        # brightest stretch stays away from the login card's footprint.
        theme.draw_flowing_streak(
            background_canvas,
            [
                (-screen_width * 0.08, screen_height * 0.14),
                (screen_width * 0.14, screen_height * 0.07),
                (screen_width * 0.27, screen_height * 0.30),
                (screen_width * 0.34, screen_height * 0.42),
                (screen_width * 0.44, screen_height * 0.55),
            ],
            glow_color=theme.ACCENT_CYAN,
            core_color="#E0FBFF",
        )
        theme.draw_flowing_streak(
            background_canvas,
            [
                (screen_width * 1.08, screen_height * 0.90),
                (screen_width * 0.86, screen_height * 0.80),
                (screen_width * 0.74, screen_height * 0.62),
                (screen_width * 0.66, screen_height * 0.50),
                (screen_width * 0.56, screen_height * 0.36),
            ],
            glow_color=theme.ACCENT_PRIMARY,
            core_color="#DCEBFF",
        )
        # Branding bloom: a soft halo specifically behind the logo/title
        # cluster (roughly the top-center third), separate from the corner
        # fog glows, so the branding reads as the visual focal point.
        # Sized down alongside the fog glows above for the same reason.
        theme.draw_soft_glow(
            background_canvas,
            round(screen_width * 0.5),
            round(screen_height * 0.27),
            round(screen_height * 0.2),
            theme.ACCENT_CYAN,
        )
        # Watermark: the real logo shape (not a plain diamond), oversized,
        # faded via stippled overlay, and pushed mostly off the right edge
        # so it reads as a premium brand mark, not competing artwork.
        self._draw_lock_watermark(tk, background_canvas, screen_width, screen_height)
        theme.draw_particles(background_canvas, screen_width, screen_height, count=70, color="#CDEEFF")
        theme.draw_vignette(background_canvas, screen_width, screen_height)

        container = tk.Frame(self.root, background=theme.BG_DEEP)
        container.place(relx=0.5, rely=0.5, anchor="center")

        if self.open_setup_wizard is not None:
            # Deliberately a quiet, icon-only affordance in the corner --
            # not a labeled "Client Menu" button -- so the lock screen reads
            # as a customer-facing surface, not an admin console. The Setup
            # wizard itself is unchanged; only its entry point is de-emphasized.
            menu_button = tk.Menubutton(
                self.root,
                relief="flat",
                bd=0,
                background=theme.BG_DEEP,
                activebackground=theme.BG_DEEP,
                highlightthickness=1,
                highlightbackground=theme.LINE,
                cursor="hand2",
                padx=6,
                pady=6,
            )
            gear = theme.Icon(menu_button, tk, "gear", size=15, color=theme.TEXT_MUTED, bg=theme.BG_DEEP)
            gear.canvas.pack()
            menu = tk.Menu(menu_button, tearoff=False)
            menu.add_command(label="Setup...", command=self.open_setup_wizard)
            menu_button.configure(menu=menu)
            menu_button.place(relx=1.0, x=-18, y=18, anchor="ne")

        # Vertical rhythm is deliberately asymmetric, matching the approved
        # preview: logo/title/headline/instruction form one tight "branding
        # moment" (small gaps), then one large deliberate gap before the
        # login card, which sits lower as a secondary, supporting element.
        self._build_brand_mark(tk, container, scale).pack(pady=(round(36 * scale), round(22 * scale)))

        # The hero element of the screen: GlowText gives it a multi-layer
        # bloom and a faux-heavier weight that a plain tk.Label cannot
        # produce (Tk has no text-shadow and only a binary bold flag) --
        # see theme.GlowText's docstring for the exact technique.
        title = theme.GlowText(
            container,
            tk,
            text=theme.letter_spaced("SECRET GROUNDS"),
            font=("Segoe UI", font_size(52), "bold"),
            fill="#FFFFFF",
            glow_color=theme.ACCENT_CYAN,
            halo_color=theme.ACCENT_PRIMARY,
            bg=theme.BG_DEEP,
        )
        title.pack(pady=(0, round(14 * scale)))
        self.title_label = title

        unavailable = tk.Label(
            container,
            text="Available soon.",
            font=("Segoe UI", font_size(16), "bold"),
            foreground=theme.TEXT_PRIMARY,
            background=theme.BG_DEEP,
        )
        unavailable.pack(pady=(0, 6))
        self.headline_label = unavailable

        # The big deliberate gap: everything above is "the brand", the card
        # below is "the task".
        cashier = tk.Label(
            container,
            text="Please ask the cashier to begin your session.",
            font=("Segoe UI", font_size(11)),
            foreground=theme.TEXT_MUTED,
            background=theme.BG_DEEP,
            justify="center",
        )
        cashier.pack(pady=(0, round(54 * scale)))
        self.instruction_label = cashier

        if self.member_login_action is not None:
            # A glass-like rounded card: real rounded corners require an OS
            # window region, which only applies to top-level windows, not a
            # child Frame -- so the card is a Canvas with a rounded-rect
            # polygon background, and the actual interactive widgets are a
            # normal Frame embedded on top via create_window. The card is
            # sized in two passes: build the content first at its natural
            # size, then size/draw the canvas to match.
            card_canvas = tk.Canvas(container, highlightthickness=0, bd=0, background=theme.BG_DEEP)
            field_width = round(300 * scale)
            field_height = round(40 * scale)
            # Slightly darker than the shared PANEL token (used verbatim by
            # the overlay elsewhere) and a larger, softer corner radius --
            # local to this card only, not a change to the shared theme.
            login_card_bg = "#0A101D"
            login_card_radius = theme.RADIUS_CARD + 6

            inner = tk.Frame(
                card_canvas,
                background=login_card_bg,
                padx=round(32 * scale),
                pady=round(24 * scale),
            )

            login_title = tk.Label(
                inner,
                text="MEMBER LOGIN",
                font=("Segoe UI", font_size(12), "bold"),
                foreground=theme.ACCENT_PRIMARY,
                background=login_card_bg,
            )
            login_title.pack(pady=(0, round(16 * scale)))

            username_label = tk.Label(
                inner,
                text="username",
                font=("Segoe UI", font_size(9), "bold"),
                foreground=theme.TEXT_MUTED,
                background=login_card_bg,
                anchor="w",
            )
            username_label.pack(fill="x", pady=(0, 4))
            username_field = theme.IconField(
                inner,
                tk,
                icon="user",
                placeholder="enter username",
                width=field_width,
                height=field_height,
                font=("Segoe UI", font_size(10)),
                bg=login_card_bg,
            )
            username_field.pack(pady=(0, round(12 * scale)))
            self.username_entry = username_field.entry
            self._username_field = username_field

            pin_label = tk.Label(
                inner,
                text="pin",
                font=("Segoe UI", font_size(9), "bold"),
                foreground=theme.TEXT_MUTED,
                background=login_card_bg,
                anchor="w",
            )
            pin_label.pack(fill="x", pady=(0, 4))
            pin_field = theme.IconField(
                inner,
                tk,
                icon="lock",
                placeholder="enter pin",
                width=field_width,
                height=field_height,
                show="*",
                trailing_icon="eye",
                trailing_command=self._toggle_pin_visibility,
                font=("Segoe UI", font_size(10)),
                bg=login_card_bg,
            )
            pin_field.pack(pady=(0, round(10 * scale)))
            self.pin_entry = pin_field.entry
            # Kept as a plain attribute (not the IconField wrapper) so
            # existing callers that toggle its color and click state can
            # keep treating it as a single object, same as before.
            self.pin_toggle_button = pin_field.trailing_icon
            self._pin_field = pin_field

            self.login_message_label = tk.Label(
                inner,
                text="",
                font=("Segoe UI", font_size(10)),
                foreground=theme.WARNING,
                background=login_card_bg,
                wraplength=field_width,
                justify="left",
            )
            self.login_message_label.pack(anchor="w", pady=(0, 14))

            # Full-width gradient LOG IN button with a hover glow (built
            # into GradientButton) -- the card's single call to action.
            self.login_button = theme.GradientButton(
                inner,
                tk,
                text="LOG IN",
                command=self._submit_member_login,
                width=field_width,
                height=round(42 * scale),
                font=("Segoe UI", font_size(10), "bold"),
                bg=login_card_bg,
            )
            self.login_button.pack()

            inner.update_idletasks()
            content_width = inner.winfo_reqwidth()
            content_height = inner.winfo_reqheight()
            card_canvas.configure(width=content_width, height=content_height)
            # Softer border than the plain LINE token -- refined, not
            # redesigned, per the approved preview's "more glass" note.
            # True translucency/backdrop-blur has no Tk equivalent (see
            # porting-limitations note); a dimmer solid border/no extra
            # glow is the closest approximation.
            theme.draw_rounded_rect(
                card_canvas, 0, 0, content_width, content_height, login_card_radius,
                fill=login_card_bg, outline="#26314A", width=1,
            )
            card_canvas.create_window(0, 0, window=inner, anchor="nw")
            # Top gap is now carried by the instruction label's own large
            # bottom margin above -- this only needs a small bottom margin.
            card_canvas.pack(pady=(0, 4))
            self._login_card_canvas = card_canvas

            if self.username_entry is not None:
                self.username_entry.bind("<Return>", lambda _: self._submit_member_login())
            if self.pin_entry is not None:
                self.pin_entry.bind("<Return>", lambda _: self._submit_member_login())

        if self.session_unlock_action is not None:
            # Fallback parity for the member-locked screen: this is a
            # security-relevant path (WebView2 failing mid-lock must not
            # silently drop the lock), so it gets its own real PIN-only
            # card and UNLOCK action rather than reusing the general
            # username+PIN member-login card above. Same card styling,
            # deliberately no username field.
            unlock_card_bg = "#0A101D"
            unlock_card_radius = theme.RADIUS_CARD + 6
            unlock_field_width = round(300 * scale)
            unlock_field_height = round(40 * scale)
            unlock_canvas = tk.Canvas(container, highlightthickness=0, bd=0, background=theme.BG_DEEP)
            unlock_inner = tk.Frame(
                unlock_canvas,
                background=unlock_card_bg,
                padx=round(32 * scale),
                pady=round(24 * scale),
            )

            unlock_title = tk.Label(
                unlock_inner,
                text="UNLOCK",
                font=("Segoe UI", font_size(12), "bold"),
                foreground=theme.DANGER,
                background=unlock_card_bg,
            )
            unlock_title.pack(pady=(0, round(16 * scale)))

            unlock_pin_label = tk.Label(
                unlock_inner,
                text="pin",
                font=("Segoe UI", font_size(9), "bold"),
                foreground=theme.TEXT_MUTED,
                background=unlock_card_bg,
                anchor="w",
            )
            unlock_pin_label.pack(fill="x", pady=(0, 4))
            unlock_pin_field = theme.IconField(
                unlock_inner,
                tk,
                icon="lock",
                placeholder="enter pin",
                width=unlock_field_width,
                height=unlock_field_height,
                show="*",
                trailing_icon="eye",
                trailing_command=self._toggle_pin_visibility,
                font=("Segoe UI", font_size(10)),
                bg=unlock_card_bg,
            )
            unlock_pin_field.pack(pady=(0, round(10 * scale)))
            self.unlock_pin_entry = unlock_pin_field.entry
            self.unlock_pin_toggle_button = unlock_pin_field.trailing_icon
            self._unlock_pin_field = unlock_pin_field

            self.unlock_message_label = tk.Label(
                unlock_inner,
                text="",
                font=("Segoe UI", font_size(10)),
                foreground=theme.WARNING,
                background=unlock_card_bg,
                wraplength=unlock_field_width,
                justify="left",
            )
            self.unlock_message_label.pack(anchor="w", pady=(0, 14))

            self.unlock_button = theme.GradientButton(
                unlock_inner,
                tk,
                text="UNLOCK",
                command=self._submit_session_unlock,
                width=unlock_field_width,
                height=round(42 * scale),
                font=("Segoe UI", font_size(10), "bold"),
                bg=unlock_card_bg,
            )
            self.unlock_button.pack()

            unlock_inner.update_idletasks()
            unlock_content_width = unlock_inner.winfo_reqwidth()
            unlock_content_height = unlock_inner.winfo_reqheight()
            unlock_canvas.configure(width=unlock_content_width, height=unlock_content_height)
            theme.draw_rounded_rect(
                unlock_canvas, 0, 0, unlock_content_width, unlock_content_height, unlock_card_radius,
                fill=unlock_card_bg, outline="#26314A", width=1,
            )
            unlock_canvas.create_window(0, 0, window=unlock_inner, anchor="nw")
            self._unlock_card_canvas = unlock_canvas
            # Not packed here -- _update_lock_text() packs/unpacks this and
            # the login card so only the one matching the current mode is
            # ever visible.

            if self.unlock_pin_entry is not None:
                self.unlock_pin_entry.bind("<Return>", lambda _: self._submit_session_unlock())

        self.countdown_label = tk.Label(
            container,
            text="",
            font=("Consolas", font_size(72), "bold"),
            foreground=theme.ACCENT_CYAN,
            background=theme.BG_DEEP,
        )
        self.countdown_label.pack(pady=(20, 8))

        # Connection status only earns screen space when there is actually a
        # problem connecting to the cashier -- not shown at all otherwise.
        self.connection_label = tk.Label(
            container,
            text="Reconnecting...",
            font=("Segoe UI", font_size(13), "bold"),
            foreground=theme.WARNING,
            background=theme.BG_DEEP,
        )

        pc_row = tk.Frame(container, background=theme.BG_DEEP)
        pc_row.pack(pady=(round(34 * scale), 4))
        theme.Icon(pc_row, tk, "computer", size=11, color=theme.TEXT_MUTED, bg=theme.BG_DEEP).pack(
            side="left", padx=(0, 6)
        )
        pc_label = tk.Label(
            pc_row,
            text=self.pc_name,
            font=("Segoe UI", font_size(10)),
            foreground=theme.TEXT_MUTED,
            background=theme.BG_DEEP,
        )
        pc_label.pack(side="left")
        self.pc_label = pc_label

        self._update_connection_label()
        self._show_window()
        self.gui_ready_event.set()
        self._pump_actions()
        self.root.mainloop()

    def _draw_lock_watermark(self, tk, canvas, screen_width: int, screen_height: int) -> None:
        """Draw the real logo, oversized and faded, pushed off the right
        edge as a background watermark -- see draw_faded_watermark()'s
        docstring for why this is a stippled overlay rather than true
        opacity (Tk PhotoImage has no alpha control without PIL)."""
        logo_path = get_logo_path()
        if logo_path is None:
            return
        try:
            image = tk.PhotoImage(file=str(logo_path))
            target_height = round(screen_height * 0.62)
            if image.height() < target_height:
                factor = max(1, round(target_height / image.height()))
                image = image.zoom(factor, factor)
            elif image.height() > target_height:
                factor = max(1, round(image.height() / target_height))
                image = image.subsample(factor, factor)
            self.watermark_image = image
            theme.draw_faded_watermark(
                canvas,
                image,
                cx=round(screen_width * 0.86),
                cy=round(screen_height * 0.5),
                bg_color=theme.BG_MID,
            )
        except Exception as error:
            log_tk_startup_error("Lock screen watermark image failed to load", error)

    def _build_brand_mark(self, tk, container, scale: float = 1.0):
        """Return a Label showing the real logo, or a safe text fallback."""
        logo_path = get_logo_path()
        if logo_path is not None:
            try:
                image = tk.PhotoImage(file=str(logo_path))
                target_height = round(104 * scale)
                if image.height() > target_height:
                    factor = max(1, round(image.height() / target_height))
                    image = image.subsample(factor, factor)
                self.logo_image = image
                # Must match container's own background exactly -- a PNG's
                # transparent pixels composite against whatever color this
                # Label fills its rectangle with, so any mismatch (this was
                # a stale hardcoded near-black, not the real BG_DEEP the
                # container actually uses) shows up as a visible square
                # around the logo.
                return tk.Label(
                    container,
                    image=self.logo_image,
                    background=theme.BG_DEEP,
                )
            except Exception as error:
                log_tk_startup_error("Lock screen logo image failed to load", error)

        return tk.Label(
            container,
            text="SG",
            font=("Segoe UI", 34, "bold"),
            foreground="#020617",
            background="#22D3EE",
            padx=26,
            pady=12,
        )

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
        # Only fade in on a genuine hidden -> shown transition, not every
        # refocus reassertion (_ensure_visible() calls this on every
        # <Unmap>/<FocusOut>, which would otherwise re-fade on every
        # alt-tab back to the app).
        was_hidden = self.root.state() == "withdrawn"
        self.root.deiconify()
        if was_hidden:
            theme.fade_in(self.root)
        self.root.attributes("-topmost", True)
        self.root.lift()
        # Only steal focus back to the toplevel if nothing in our own window
        # currently holds it. Unconditionally calling focus_force() here was
        # the root cause of unusable Member Login fields: every FocusOut
        # (including the completely normal one that fires when a user clicks
        # from the toplevel into username_entry) re-ran this method, which
        # yanked keyboard focus back to the root window away from the entry
        # the user just clicked into -- so typed characters never reached it.
        if self.root.focus_get() is None:
            self.root.focus_force()

    def _handle_focus_out(self) -> None:
        if self.root is None:
            return
        # Defer the check: FocusOut on the toplevel fires for BOTH "focus left
        # this application entirely" and "focus moved to one of our own child
        # widgets" (e.g. clicking into username_entry). By the time this runs
        # on the next event-loop pass, focus_get() reflects where focus
        # actually landed -- non-None means it's still somewhere in our own
        # window (a child widget got FocusIn), so there is nothing to
        # reassert. None means focus truly left the app (e.g. Alt+Tab), which
        # is the only case the lock screen needs to reclaim visibility for.
        self.root.after(50, self._reassert_if_focus_left_app)

    def _reassert_if_focus_left_app(self) -> None:
        if self.root is None:
            return
        if self.root.focus_get() is None:
            self._ensure_visible()

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
            # Drop the PhotoImage reference synchronously, in the same
            # thread and before interpreter teardown, rather than letting
            # Python's GC collect it at some unpredictable later point --
            # PhotoImage.__del__ tries to call back into Tcl, which raises a
            # harmless but noisy "main thread is not in main loop" error if
            # that happens after mainloop has already exited (most visible
            # when a second Tk() root is created later in the same process,
            # e.g. back-to-back packaged GUI smoke checks).
            self.logo_image = None
            self.watermark_image = None

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
        if model.connection_online:
            # Customers do not need to know the connection is fine -- only
            # surfaced when there is an actual problem.
            self.connection_label.pack_forget()
        else:
            self.connection_label.configure(text="Waiting for cashier connection...")
            self.connection_label.pack(pady=(8, 0))

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
            self.title_label.configure(text=theme.letter_spaced(model.brand.upper()))
        if self.countdown_label is not None:
            self.countdown_label.configure(
                text=model.countdown or "",
                foreground=model.accent,
            )
        # Exactly one of the two cards is ever packed: the member-locked
        # screen shows PIN-only UNLOCK, everything else shows the general
        # username+PIN MEMBER LOGIN card (if enabled).
        show_unlock = model.mode == "member_locked"
        if self._unlock_card_canvas is not None:
            if show_unlock:
                self._unlock_card_canvas.pack(pady=(0, 4))
            else:
                self._unlock_card_canvas.pack_forget()
        if self._login_card_canvas is not None:
            if show_unlock:
                self._login_card_canvas.pack_forget()
            else:
                self._login_card_canvas.pack(pady=(0, 4))
        self._update_login_state()
        self._update_connection_label()

    def _submit_member_login(self) -> None:
        # Read through the IconField wrapper, not the raw Entry: an
        # unfocused field still holds its placeholder text as literal
        # widget content ("Username"/"PIN"), which get_value() knows to
        # treat as empty.
        username = self._username_field.get_value().strip() if self._username_field is not None else ""
        pin = self._pin_field.get_value().strip() if self._pin_field is not None else ""
        self.submit_member_login(username, pin)

    def _submit_session_unlock(self) -> None:
        pin = self._unlock_pin_field.get_value().strip() if self._unlock_pin_field is not None else ""
        self.submit_session_unlock(pin)

    def _finish_member_login(self, message: str | None) -> None:
        with self.state_lock:
            self.state.is_login_processing = False
            self.state.login_message = message
        self._update_login_state()
        self._notify_presentation()

    def _set_login_message(self, message: str) -> None:
        with self.state_lock:
            self.state.login_message = message
            self.state.is_login_processing = False
        self._update_login_state()
        self._notify_presentation()

    def _reset_credential_fields(self) -> None:
        """Wipe any lingering username/PIN text and turn the reveal toggle
        back off, so returning to a locked screen never shows the previous
        member's PIN already filled in -- masked, or in plain text if they
        had tapped the eye. Runs only on a real transition into the
        available / member-locked screen, never on a plain re-render, so it
        cannot clear a PIN the customer is mid-way through typing."""
        for field in (self._username_field, self._pin_field, self._unlock_pin_field):
            if field is not None:
                field.reset()
        self.pin_visible = False
        for toggle in (self.pin_toggle_button, self.unlock_pin_toggle_button):
            if toggle is not None:
                toggle.set_color(theme.TEXT_MUTED)

    def _toggle_pin_visibility(self) -> None:
        # Shared by the login card's PIN field and the member-locked
        # screen's unlock PIN field -- only one of the two cards is ever
        # packed/visible at a time, so a single shared reveal state is
        # simpler than tracking them independently.
        self.pin_visible = not self.pin_visible
        if self.pin_entry is not None:
            self.pin_entry.configure(show="" if self.pin_visible else "*")
        if self.pin_toggle_button is not None:
            # Same eye icon either way (exactly like a modern login form's
            # single toggle icon) -- only its color signals state, matching
            # the accent used for a focused field.
            self.pin_toggle_button.set_color(theme.ACCENT_PRIMARY if self.pin_visible else theme.TEXT_MUTED)
        if self.unlock_pin_entry is not None:
            self.unlock_pin_entry.configure(show="" if self.pin_visible else "*")
        if self.unlock_pin_toggle_button is not None:
            self.unlock_pin_toggle_button.set_color(theme.DANGER if self.pin_visible else theme.TEXT_MUTED)

    def _update_login_state(self) -> None:
        with self.state_lock:
            is_processing = self.state.is_login_processing
            message = self.state.login_message
            is_locked = self.state.is_locked
        state = "disabled" if is_processing or not is_locked else "normal"
        if self.login_button is not None:
            self.login_button.configure(
                state=state,
                text="LOGGING IN..." if is_processing else "LOG IN",
            )
        if self.username_entry is not None:
            self.username_entry.configure(state=state)
        if self.pin_entry is not None:
            self.pin_entry.configure(state=state)
        if self.pin_toggle_button is not None:
            if is_processing:
                self.pin_toggle_button.set_color(theme.LINE)
            else:
                self.pin_toggle_button.set_color(theme.ACCENT_PRIMARY if self.pin_visible else theme.TEXT_MUTED)
        if self.login_message_label is not None:
            self.login_message_label.configure(text=message or "")
        if self.unlock_button is not None:
            self.unlock_button.configure(
                state=state,
                text="UNLOCKING..." if is_processing else "UNLOCK",
            )
        if self.unlock_pin_entry is not None:
            self.unlock_pin_entry.configure(state=state)
        if self.unlock_pin_toggle_button is not None:
            if is_processing:
                self.unlock_pin_toggle_button.set_color(theme.LINE)
            else:
                self.unlock_pin_toggle_button.set_color(theme.DANGER if self.pin_visible else theme.TEXT_MUTED)
        if self.unlock_message_label is not None:
            self.unlock_message_label.configure(text=message or "")


def run_lock_screen_gui_smoke(timeout_seconds: float = 6.0) -> None:
    login_calls: list[tuple[str, str]] = []
    login_release = Event()

    def login_action(username: str, pin: str) -> str:
        login_calls.append((username, pin))
        login_release.wait(timeout=2)
        return "That username or PIN did not match. Please try again."

    def unlock_action(_pin: str) -> str:
        return "Incorrect PIN. Please try again."

    controller = LockScreenController(
        "TEST-PC-01",
        enable_gui=True,
        enable_dev_escape=True,
        member_login_action=login_action,
        session_unlock_action=unlock_action,
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

                if controller.logo_image is None:
                    raise AssertionError(
                        "Lock screen fell back to the text mark instead of "
                        "loading the packaged Secret Grounds logo."
                    )

                expected_title = theme.letter_spaced("SECRET GROUNDS")
                if controller.title_label.cget("text") != expected_title:
                    raise AssertionError(
                        f"Branding must be ALL CAPS with letter spacing: got {controller.title_label.cget('text')!r}."
                    )
                if controller.pc_label.cget("text") != controller.pc_name:
                    raise AssertionError(
                        "PC label must show just the station name, no 'PC name:' prefix: "
                        f"got {controller.pc_label.cget('text')!r}."
                    )

                # set_connection_online() only enqueues the label update for
                # the next _pump_actions() tick; this code is itself running
                # from inside that same pump's call stack, so waiting on a
                # future tick here is unreliable. Call the render step
                # directly (what the pump would eventually do anyway) for a
                # deterministic check, since it is the actual widget logic
                # under test, not the scheduling plumbing around it.
                controller.set_connection_online(True)
                controller._update_connection_label()
                controller.root.update_idletasks()
                if controller.connection_label.winfo_ismapped():
                    raise AssertionError("Connection status must be hidden while online.")

                controller.set_connection_online(False)
                controller._update_connection_label()
                controller.root.update_idletasks()
                if not controller.connection_label.winfo_ismapped():
                    raise AssertionError("Connection warning did not appear when offline.")

                controller.set_connection_online(True)
                controller._update_connection_label()
                controller.root.update_idletasks()
                if controller.connection_label.winfo_ismapped():
                    raise AssertionError("Connection warning did not clear once back online.")

                # Responsive scaling: font sizes must move with screen size
                # (within sane bounds), not stay a single fixed point size
                # regardless of the monitor.
                if controller._font_size is None:
                    raise AssertionError("Font scaling helper was not set up.")
                scaled_title = controller._font_size(52)
                if not (9 <= scaled_title <= round(52 * 1.4)):
                    raise AssertionError(f"Title font size out of expected range: {scaled_title}")

                # Mouse click focuses the field: focus_set() is the
                # programmatic equivalent of the click-to-focus Tk already
                # gives Entry widgets for free once nothing is stealing focus
                # back out from under it. Focusing also clears the fake
                # placeholder text (IconField's own FocusIn handler).
                controller.username_entry.focus_set()
                controller.root.update_idletasks()
                controller.root.update()
                if controller.root.focus_get() is not controller.username_entry:
                    raise AssertionError("Clicking the username field did not focus it.")
                if controller._username_field.get_value() != "":
                    raise AssertionError("Focusing an empty field must clear its placeholder text.")
                if controller.username_entry.cget("fg") != theme.TEXT_PRIMARY:
                    raise AssertionError("Focused field text color did not switch away from the placeholder color.")

                # Leaving a field empty must restore its placeholder rather
                # than submitting the placeholder text as real input.
                controller.pin_entry.focus_set()
                controller.root.update_idletasks()
                controller.root.update()
                if controller._username_field.get_value() != "":
                    raise AssertionError("Blurring an empty field must restore its placeholder, not real input.")

                controller.username_entry.focus_set()
                controller.root.update_idletasks()
                controller.root.update()

                # Regression check for the actual reported bug: re-showing the
                # lock screen (what every <Unmap>/<FocusOut> re-assertion used
                # to do unconditionally) must not steal focus away from a
                # field the user is actively using.
                controller._show_window()
                controller.root.update_idletasks()
                controller.root.update()
                if controller.root.focus_get() is not controller.username_entry:
                    raise AssertionError(
                        "Re-showing the lock screen stole keyboard focus away "
                        "from the focused username field."
                    )

                controller.username_entry.insert(0, "test_member")
                controller.pin_entry.focus_set()
                controller.root.update_idletasks()
                controller.root.update()
                controller.pin_entry.insert(0, "1234")
                if controller.pin_entry.cget("show") != "*":
                    raise AssertionError("PIN entry is not masked once real text is entered.")
                controller._toggle_pin_visibility()
                if controller.pin_entry.cget("show") != "":
                    raise AssertionError("PIN show toggle did not reveal the PIN.")
                controller._toggle_pin_visibility()
                if controller.pin_entry.cget("show") != "*":
                    raise AssertionError("PIN hide toggle did not mask the PIN.")
                if controller._username_field.get_value() != "test_member":
                    raise AssertionError("Username field did not retain the typed value.")
                if controller._pin_field.get_value() != "1234":
                    raise AssertionError("PIN field did not retain the typed value.")

                # Enter submits from either field, and a second submission
                # attempt while the first is still processing must be a no-op
                # (the processing guard finish_login_widget_checks verifies
                # below via login_calls).
                controller.pin_entry.event_generate("<Return>")
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
                controller.root.after(300, verify_friendly_error_shown)
            except Exception as error:
                errors.append(str(error))
                login_release.set()
                finish_smoke()

        def verify_friendly_error_shown() -> None:
            try:
                expected = "That username or PIN did not match. Please try again."
                if controller.state.login_message != expected:
                    raise AssertionError(
                        "Invalid login did not show the friendly error message: "
                        f"got {controller.state.login_message!r}."
                    )
                if controller.login_message_label is not None:
                    displayed = controller.login_message_label.cget("text")
                    if displayed != expected:
                        raise AssertionError(
                            f"Login message label did not render the error: got {displayed!r}."
                        )
                # The typed username/PIN are still in the fields, and the
                # customer has tapped the eye to reveal the PIN.
                if controller._pin_field.get_value() != "1234":
                    raise AssertionError("Setup for the relock check is wrong: PIN was already cleared.")
                controller._toggle_pin_visibility()
                if controller.pin_entry.cget("show") != "":
                    raise AssertionError("Could not reveal the PIN for the relock check.")
                # Transitioning back to a locked screen (member Lock PC, or
                # idle) must wipe every credential field and turn the reveal
                # toggle back off -- the reported bug was the previous PIN
                # sitting there, in plain text.
                controller.show_member_locked("Someone Else")
                controller.root.after(250, verify_credential_reset_on_relock)
            except Exception as error:
                errors.append(str(error))
                finish_smoke()

        def verify_credential_reset_on_relock() -> None:
            try:
                if controller._unlock_pin_field.get_value() != "":
                    raise AssertionError(
                        "Member-locked screen showed a pre-filled PIN: "
                        f"{controller._unlock_pin_field.get_value()!r}."
                    )
                if controller._pin_field.get_value() != "" or controller._username_field.get_value() != "":
                    raise AssertionError("Login-card credentials were carried over into the relock.")
                if controller.pin_visible is not False:
                    raise AssertionError("Reveal toggle stayed on after returning to a locked screen.")
                finish_smoke()
            except Exception as error:
                errors.append(str(error))
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
