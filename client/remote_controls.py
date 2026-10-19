from __future__ import annotations

import platform
import os
import subprocess
import time
from threading import Event, Lock, Thread, Timer
from typing import Any, Callable

from client import theme
from client.tk_runtime import create_tk_root, log_tk_startup_error


PowerExecutor = Callable[[list[str]], None]

# Severity is communicated entirely through this accent (border, divider,
# eyebrow, button) -- no separate "URGENT"/"WARNING" text label and no icon
# glyph competing with the Secret Grounds mark for "the" icon in the popup,
# the same way the dashboard's own confirm-dialog tones work (color alone
# carries the tone). Blue/pink/red rather than the dashboard's own
# cyan/amber/rose set, per direct request, so the three read distinctly
# apart even to someone who never opens the dashboard.
_SEVERITY_STYLES: dict[str, dict[str, str]] = {
    "info": {"accent": "#38BDF8", "label": "INFO"},  # sky-400 -- the cyan-leaning middle stop of the dashboard's own brand gradient, not flat blue
    "warning": {"accent": "#EC4899", "label": "WARNING"},
    "urgent": {"accent": "#EF4444", "label": "URGENT"},
}


def _test_mode_enabled() -> bool:
    return os.getenv("SECRET_GROUNDS_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}


# A message/announcement is only acknowledged as "message displayed" once its
# pop-up window is really on screen. The server waits 5 seconds for the final
# acknowledgement (COMMAND_ACK_TIMEOUT_SECONDS), so give up a bit before that
# and say plainly that it didn't appear.
POPUP_CONFIRM_TIMEOUT_SECONDS = 3.0
POPUP_TIMEOUT_REASON = "the pop-up didn't appear within 3 seconds"


def _test_popup_failure_enabled() -> bool:
    """Test mode only (SECRET_GROUNDS_TEST_POPUP_FAILURE=true): every pop-up
    fails, so a demo can show what an honest failure looks like."""
    return _test_mode_enabled() and os.getenv("SECRET_GROUNDS_TEST_POPUP_FAILURE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _popup_failure_reason(error: BaseException) -> str:
    """A short, readable reason (Tcl errors can be long multi-line lists)."""
    first_line = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
    return f"the pop-up couldn't open: {first_line[:160]}"


class _PopupOutcome:
    """First result wins (shown, failed or timed out); later ones are ignored."""

    def __init__(self, on_result: Callable[[bool, str], None]) -> None:
        self._on_result = on_result
        self._lock = Lock()
        self._done = False
        self._cleanups: list[Callable[[], None]] = []

    def on_finish(self, cleanup: Callable[[], None]) -> None:
        self._cleanups.append(cleanup)

    def finish(self, shown: bool, text: str) -> bool:
        with self._lock:
            if self._done:
                return False
            self._done = True
        for cleanup in self._cleanups:
            try:
                cleanup()
            except Exception:
                pass
        self._on_result(shown, text)
        return True


def default_power_executor(command: list[str]) -> None:
    if _test_mode_enabled():
        raise RuntimeError("real_power_executor_blocked_in_test_mode")
    if os.getenv("PYTEST_CURRENT_TEST"):
        raise RuntimeError("real_power_executor_blocked_in_pytest")
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class RemoteControlHandler:
    """Fixed allowlist for production-safe remote client controls."""

    def __init__(self, pc_name: str, power_executor: PowerExecutor = default_power_executor) -> None:
        self.pc_name = pc_name
        self.power_executor = power_executor
        # Set by client.py once both controllers exist (this handler is
        # constructed before either). Used so message popups can be parented
        # onto whichever one of these already has a live, running Tk
        # interpreter -- see show_message()/_get_active_gui_root().
        self.lock_screen: Any = None
        self.customer_overlay: Any = None
        # Lazily-created, then permanently reused, dedicated Tk interpreter
        # for message popups -- see _ensure_popup_root(). Needed because on
        # a PC where the WebView2 UI (webview_ui.py) is the active customer
        # UI, lock_screen/customer_overlay never start a Tk GUI at all, so
        # there was nothing for popups to reuse and every single message
        # was still spinning up (and tearing down) its own brand-new Tk
        # interpreter -- the exact instability this whole reuse scheme was
        # meant to eliminate, just not caught by the lock_screen/
        # customer_overlay check alone.
        self._popup_tk = None
        self._popup_root = None
        self._popup_root_lock = Lock()

    def handle_power_command(self, command_type: str, payload: dict[str, Any]) -> str:
        if platform.system().lower() != "windows":
            raise RuntimeError("unsupported_operating_system")
        if not isinstance(payload, dict):
            raise RuntimeError("invalid_payload")

        commands = {
            "shutdown": ["shutdown", "/s", "/t", "3"],
            "restart": ["shutdown", "/r", "/t", "3"],
            "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
        }
        command = commands.get(command_type)
        if command is None:
            raise RuntimeError("unsupported_power_command")

        print(f"Remote {command_type} command accepted for {self.pc_name}.")
        Thread(target=self._execute_after_ack, args=(command_type, command), daemon=True).start()
        if self.power_executor is default_power_executor and _test_mode_enabled():
            # default_power_executor refuses to run in test mode, so say so in
            # the acknowledgement instead of implying the PC will power off.
            return "accepted (test mode: not powered off)"
        if command_type in {"shutdown", "restart"}:
            return "accepted; expected disconnect may follow"
        return "accepted"

    def _execute_after_ack(self, command_type: str, command: list[str]) -> None:
        time.sleep(0.25)
        try:
            print(f"Executing remote {command_type} command.")
            self.power_executor(command)
        except Exception as error:
            print(f"Remote {command_type} execution failed: {error}")

    def show_message(self, payload: dict[str, Any], announcement: bool = False) -> str:
        """Show the pop-up and wait (briefly) until it is really on screen.
        Returns "message displayed", or raises RuntimeError with the reason.
        The client's command loop uses show_message_async() instead, so it
        never waits."""
        done = Event()
        outcome: dict[str, Any] = {}

        def on_result(shown: bool, text: str) -> None:
            outcome.update(shown=shown, text=text)
            done.set()

        self.show_message_async(payload, announcement, on_result)
        done.wait(POPUP_CONFIRM_TIMEOUT_SECONDS + 1)
        if not outcome.get("shown"):
            raise RuntimeError(outcome.get("text") or POPUP_TIMEOUT_REASON)
        return str(outcome["text"])

    def show_message_async(
        self,
        payload: dict[str, Any],
        announcement: bool,
        on_result: Callable[[bool, str], None],
    ) -> None:
        """Validate now (raises on a bad payload), then show the pop-up and
        call on_result(True, "message displayed") once it is on screen, or
        on_result(False, reason) if it failed or didn't appear within
        POPUP_CONFIRM_TIMEOUT_SECONDS -- exactly once, from another thread,
        without blocking the caller."""
        # title is intentionally ignored here -- the server already
        # overrides it to the canonical "SECRET GROUNDS" brand regardless
        # of what the dashboard sent (see MessageCommandRequest), and the
        # popup below never reads it either, so a stale/rogue caller can
        # never spoof the sender the customer sees.
        message = self._clean_text(payload.get("message"), 500)
        severity = payload.get("severity", "info")
        duration_seconds = payload.get("duration_seconds")
        require_acknowledgement = bool(payload.get("require_acknowledgement", False))
        if severity not in {"info", "warning", "urgent"}:
            raise RuntimeError("invalid_severity")
        if duration_seconds is not None and (
            not isinstance(duration_seconds, int) or duration_seconds < 5 or duration_seconds > 3600
        ):
            raise RuntimeError("invalid_duration")

        kind = "announcement" if announcement else "message"
        outcome = _PopupOutcome(on_result)

        def on_shown(error: str | None) -> None:
            if error is None:
                if outcome.finish(True, "message displayed"):
                    print(f"Displayed remote {kind} from SECRET GROUNDS.")
            elif outcome.finish(False, error):
                print(f"Remote {kind} was not shown: {error}")

        timer = Timer(POPUP_CONFIRM_TIMEOUT_SECONDS, lambda: on_shown(POPUP_TIMEOUT_REASON))
        timer.daemon = True
        timer.start()
        outcome.on_finish(timer.cancel)
        try:
            self._present_message_window(
                message, severity, duration_seconds, require_acknowledgement, announcement, on_shown=on_shown
            )
        except Exception as error:
            on_shown(_popup_failure_reason(error))

    def _get_active_gui_root(self):
        """The Tk root of whichever of lock_screen/customer_overlay is
        already alive and running its own mainloop, or None if neither has
        started its GUI yet (very early in client startup)."""
        for controller in (self.customer_overlay, self.lock_screen):
            root = getattr(controller, "root", None) if controller is not None else None
            if root is not None:
                return root
        return None

    def _ensure_popup_root(self):
        """Create this handler's own dedicated Tk interpreter the first
        time it's needed, and reuse that same one (with its own thread
        running mainloop() forever) for every message after that. Returns
        (tk, root), or (None, None) if it could not be started."""
        with self._popup_root_lock:
            if self._popup_root is not None:
                return self._popup_tk, self._popup_root

            ready = Event()
            holder: dict[str, Any] = {}

            def run() -> None:
                try:
                    tk, root = create_tk_root()
                except Exception as error:
                    holder["error"] = error
                    ready.set()
                    return
                # This root itself is never shown -- it only exists to own
                # an interpreter/mainloop that popups attach to as
                # Toplevels, exactly like lock_screen's/customer_overlay's
                # own root would if either were running one.
                root.withdraw()
                holder["tk"] = tk
                holder["root"] = root
                ready.set()
                root.mainloop()

            Thread(target=run, daemon=True, name="secret-grounds-popup-gui").start()
            if not ready.wait(timeout=5) or "root" not in holder:
                log_tk_startup_error(
                    "Dedicated popup GUI thread failed to start", holder.get("error", RuntimeError("timed out"))
                )
                return None, None

            self._popup_tk = holder["tk"]
            self._popup_root = holder["root"]
            return self._popup_tk, self._popup_root

    def _present_message_window(
        self,
        message: str,
        severity: str,
        duration_seconds: int | None,
        require_acknowledgement: bool,
        announcement: bool,
        on_shown: Callable[[str | None], None] | None = None,
    ) -> None:
        # on_shown(None) once the window is on screen, on_shown(reason) if it
        # could not be shown (see _show_message_window_reporting).
        # The popup is built as a Toplevel of some already-running Tk
        # interpreter, scheduled via that root's own after(0, ...) -- the
        # same cross-thread-safe pattern customer_overlay already uses for
        # schedule_gui_refresh(). Previously this spun up a brand-new
        # tk.Tk() interpreter on a brand-new thread for every single
        # message; repeatedly creating and tearing down a whole second/
        # third Tcl interpreter -- especially alongside the WebView2engine
        # that's actually the primary customer UI on most PCs -- corrupts
        # Tcl's internal state (Tcl_AsyncDelete: async handler deleted by
        # the wrong thread) badly enough to eventually crash the process.
        # Prefer reusing lock_screen's/customer_overlay's own root if
        # either is running a Tk GUI (the Tk-fallback case); otherwise fall
        # back to this handler's own dedicated, permanently-reused root
        # (the WebView2-primary case, which is the normal one in
        # production) -- either way, exactly one interpreter ever gets
        # created for the whole lifetime of the process, not one per
        # message.
        parent_root = self._get_active_gui_root()
        if parent_root is None:
            _, parent_root = self._ensure_popup_root()

        if parent_root is not None:
            try:
                parent_root.after(
                    0,
                    lambda: self._show_message_window_reporting(
                        on_shown, message, severity, duration_seconds, require_acknowledgement, announcement,
                        parent=parent_root,
                    ),
                )
                return
            except Exception as error:
                log_tk_startup_error("Scheduling remote message on the existing GUI thread failed", error)

        # Last-resort fallback if even the dedicated popup root couldn't be
        # started -- better to show it standalone than drop it.
        Thread(
            target=self._show_message_window_reporting,
            args=(on_shown, message, severity, duration_seconds, require_acknowledgement, announcement),
            daemon=True,
        ).start()

    def _show_message_window_reporting(self, on_shown, *args, **kwargs) -> None:
        """Run _show_message_window and make sure on_shown hears how it went:
        the window reports itself once it is on screen, and every failure
        path (including an exception) reports the reason."""
        reported: list[str | None] = []

        def report(error: str | None) -> None:
            if reported:
                return
            reported.append(error)
            if on_shown is not None:
                on_shown(error)

        if _test_popup_failure_enabled():
            # Test mode only: lets a demo show what an honest failure looks like.
            report("the pop-up couldn't open (test mode: forced failure)")
            print("Remote message pop-up skipped: test-mode forced failure.")
            return
        try:
            self._show_message_window(*args, on_shown=report, **kwargs)
        except Exception as error:
            log_tk_startup_error("Remote message pop-up failed", error)
            report(_popup_failure_reason(error))
            return
        # Every real path reports before returning (or before its own
        # mainloop); this only covers a window builder that returned quietly.
        report(None)

    def _build_brand_mark(self, tk, container, bg: str = theme.PANEL):
        """A small vector diamond mark, drawn on a Canvas -- deliberately
        NOT a loaded tk.PhotoImage. show_message() spins up a brand-new Tk
        interpreter on a brand-new thread for every single popup, and a
        PhotoImage loaded there was getting garbage-collected later from
        whatever thread Python's GC happened to run on -- collecting a Tk
        image tied to a different (often already-exited) interpreter
        corrupts Tcl's internal async state process-wide, which was
        destabilizing the customer overlay's own separate, long-running Tk
        interpreter (its session timer) after a second broadcast message.
        A vector draw has no image object to mistime, so the hazard is
        gone entirely rather than just made less likely.
        """
        size = 18
        canvas = tk.Canvas(container, width=size, height=size, highlightthickness=0, bd=0, background=bg)
        mid = size / 2
        canvas.create_polygon(
            mid, 1, size - 1, mid, mid, size - 1, 1, mid,
            outline="#00DEFF", fill="", width=1.6, joinstyle="round",
        )
        return canvas

    def _build_pill_button(
        self, tk, container, bg: str, accent: str, text: str, command, width: int = 220, height: int = 46
    ) -> None:
        """A solid-fill stadium button: two end-cap ovals plus a middle
        rectangle, all one flat color. GradientButton's per-column gradient
        fill plus its separate corner-arc masking pass leaves a faint seam
        right where the two techniques meet, which is exactly what read as
        "blurry" on a button this size -- a flat fill has no seam to show.
        Hover lightens the fill, a press darkens it, for the same feedback
        without needing a second gradient.
        """
        canvas = tk.Canvas(container, width=width, height=height, highlightthickness=0, bd=0, background=bg, cursor="hand2")
        state = {"hover": False, "pressed": False}

        def redraw() -> None:
            canvas.delete("all")
            if state["pressed"]:
                fill = theme.blend(accent, "#000000", 0.18)
            elif state["hover"]:
                fill = theme.lighten(accent, 0.16)
            else:
                fill = accent
            canvas.create_oval(0, 0, height, height, fill=fill, outline="")
            canvas.create_oval(width - height, 0, width, height, fill=fill, outline="")
            canvas.create_rectangle(height / 2, 0, width - height / 2, height, fill=fill, outline="")
            canvas.create_text(width / 2, height / 2, text=text, font=("Segoe UI", 11, "bold"), fill=theme.TEXT_ON_ACCENT)

        redraw()
        canvas.bind("<Enter>", lambda _e: (state.update(hover=True), redraw()))
        canvas.bind("<Leave>", lambda _e: (state.update(hover=False, pressed=False), redraw()))
        canvas.bind("<ButtonPress-1>", lambda _e: (state.update(pressed=True), redraw()))

        def on_release(_event) -> None:
            was_pressed = state["pressed"]
            state["pressed"] = False
            redraw()
            if was_pressed and command is not None:
                command()

        canvas.bind("<ButtonRelease-1>", on_release)
        canvas.pack()

    def _round_card_corners(
        self, tk, root, top_bg: str, bottom_bg: str, card_border: str, width: int, height: int, radius: int,
        offset: tuple[int, int] = (0, 0),
    ) -> None:
        """Overlay a small Canvas patch at each of the card's four corners
        that erases the straight border's square corner stub (by painting
        the surrounding fill color over it) and draws a real quarter-circle
        arc in its place -- see the call site for why. Corner bounding
        boxes mirror the corner-masking math GradientButton already uses,
        just each cropped to its own small radius x radius canvas instead
        of one big one. `top_bg`/`bottom_bg` are separate because the
        header sits on a lighter tint than the rest of the card -- each
        patch has to match whatever's actually behind it, or the mask
        itself becomes a visible seam. `offset` shifts every patch by
        (x, y) -- for when the card itself isn't flush against the
        window's own top-left corner (e.g. sitting inside a glow margin).
        """
        offset_x, offset_y = offset
        corners = [
            (0, 0, (0, 0, radius * 2, radius * 2), 90, top_bg),  # top-left
            (width - radius, 0, (-radius, 0, radius, radius * 2), 0, top_bg),  # top-right
            (0, height - radius, (0, -radius, radius * 2, radius), 180, bottom_bg),  # bottom-left
            (width - radius, height - radius, (-radius, -radius, radius, radius), 270, bottom_bg),  # bottom-right
        ]
        for place_x, place_y, bbox, start, fill_bg in corners:
            patch = tk.Canvas(root, width=radius, height=radius, highlightthickness=0, bd=0, background=fill_bg)
            patch.create_arc(*bbox, start=start, extent=90, outline=card_border, width=2, style="arc")
            patch.place(x=place_x + offset_x, y=place_y + offset_y)

    def _bind_drag(self, root, widget) -> None:
        """Let the customer drag the popup out of the way -- borderless
        windows have no OS title bar to grab. State lives in this closure
        (not on self) so two message popups shown at once from different
        threads can never clobber each other's drag origin."""
        origin: dict[str, tuple[int, int] | None] = {"point": None}

        def start(event) -> None:
            origin["point"] = (event.x_root, event.y_root)

        def motion(event) -> None:
            point = origin["point"]
            if point is None:
                return
            dx, dy = event.x_root - point[0], event.y_root - point[1]
            root.geometry(f"+{root.winfo_x() + dx}+{root.winfo_y() + dy}")
            origin["point"] = (event.x_root, event.y_root)

        widget.bind("<ButtonPress-1>", start)
        widget.bind("<B1-Motion>", motion)

    def _clean_text(self, value: object, max_length: int) -> str:
        if not isinstance(value, str):
            raise RuntimeError("invalid_message_text")
        cleaned = " ".join(value.replace("\x00", "").split())
        if not cleaned:
            raise RuntimeError("blank_message_text")
        return cleaned[:max_length]

    def _show_message_window(
        self,
        message: str,
        severity: str,
        duration_seconds: int | None,
        require_acknowledgement: bool,
        announcement: bool,
        parent=None,
        on_shown: Callable[[str | None], None] | None = None,
    ) -> None:
        # `parent` is an existing, already-running Tk root (see
        # _present_message_window) -- build this window as a Toplevel of it
        # instead of a whole new interpreter. The standalone create_tk_root()
        # path only runs in the rare fallback case (see call site).
        # on_shown(None) is called once the window is on screen, on_shown(reason)
        # if it never will be -- so the acknowledgement can say which.
        if parent is not None:
            try:
                import tkinter as tk

                root = tk.Toplevel(parent)
            except Exception as error:
                log_tk_startup_error("Remote message Toplevel creation failed", error)
                print(f"Message fallback: SECRET GROUNDS - {message}")
                if on_shown is not None:
                    on_shown(_popup_failure_reason(error))
                return
        else:
            try:
                tk, root = create_tk_root()
            except Exception as error:
                log_tk_startup_error("Remote message GUI unavailable", error)
                print(f"Message fallback: SECRET GROUNDS - {message}")
                if on_shown is not None:
                    on_shown(_popup_failure_reason(error))
                return

        style = _SEVERITY_STYLES[severity]
        accent = style["accent"]
        width = 400
        radius = 6

        # Same recipe as the dashboard's confirmDialog[data-tone] tokens --
        # a dark card tinted a little toward the accent (PANEL, not the
        # near-black BG_DEEP, so it reads as premium dark navy rather than
        # flat black), and near-white text tinted the same way for the
        # title/lede -- one formula reused for every severity instead of a
        # hand-picked palette per tone.
        card_bg = theme.blend(theme.PANEL, accent, 0.22)
        card_border = theme.blend(card_bg, accent, 0.85)
        title_color = theme.blend("#ffffff", accent, 0.14)
        note_color = theme.blend(theme.TEXT_MUTED, accent, 0.12)
        header_bg = theme.blend(card_bg, accent, 0.32)  # noticeably brighter band up top, not just a faint tint
        # Same bright saturated cyan as the logo mark itself (sampled from
        # the PNG) -- a softer pastel blue blended into info's own bright
        # blue header, but this reads by saturation contrast, not just
        # darkness, so it holds up against every severity's header.
        brand_text_color = "#00DEFF"

        root.title("Secret Grounds Message")
        root.configure(background=card_bg)
        root.attributes("-topmost", True)
        root.resizable(False, False)
        # Borderless, custom-drawn card -- same visual language as the
        # dashboard's own confirm dialogs (Sign Out / Clear Customer Lock /
        # Register a station): rounded corners, a tinted accent border, a
        # soft shadow, and a touch of real window transparency, faded in
        # once its final size is known, rather than a plain OS dialog frame.
        root.overrideredirect(True)
        theme.apply_soft_shadow(root)

        card = tk.Frame(root, background=card_bg, highlightthickness=2, highlightbackground=card_border)
        card.pack(fill="both", expand=True)

        # -- Header: the fixed "SECRET GROUNDS" wordmark (never a
        # caller-supplied logo/name, so a rogue caller can never make this
        # popup claim to be from someone/something else), centered -- no
        # close button in the header at all now; Dismiss/Acknowledge in the
        # footer is the only way to close it (Escape is still bound as a
        # silent keyboard fallback when dismissible). This whole block
        # (and its divider below) is a direct child of `card`, not the
        # padded `body` that holds everything else, so the divider can
        # reach edge to edge.
        header = tk.Frame(card, background=header_bg)
        header.pack(fill="x")
        self._bind_drag(root, header)

        title_row = tk.Frame(header, background=header_bg)
        title_row.pack(pady=(16, 0))
        self._bind_drag(root, title_row)
        # Plain bold label -- GlowText's offset halo passes only blend into
        # a soft glow at large hero sizes; at this small a point size the
        # offsets just show up as separate misaligned copies of the text.
        tk.Label(
            title_row,
            text="SECRET GROUNDS",
            font=("Segoe UI", 12, "bold"),
            foreground=accent,
            background=header_bg,
        ).pack()

        # Every root.after() scheduled below (the countdown ticks, the
        # auto-dismiss timer) gets its id recorded here so dismiss() can
        # cancel whatever's still pending -- on a manual Dismiss/Escape,
        # both chains would otherwise still be scheduled when destroy()
        # runs, leaving stale callbacks in Tcl's queue for an interpreter
        # that no longer exists. That dangling state is what was corrupting
        # the *other*, still-running Tk interpreter (the customer overlay's
        # own window) after a second broadcast message closed early.
        pending_after_ids: list[str] = []

        def dismiss(_event=None) -> None:
            for after_id in pending_after_ids:
                try:
                    root.after_cancel(after_id)
                except Exception:
                    pass
            pending_after_ids.clear()
            theme.fade_out(root, on_complete=root.destroy)

        if not require_acknowledgement:
            root.bind("<Escape>", dismiss)
        else:
            # Alt+F4 / a taskbar close request is swallowed here too --
            # otherwise "require_acknowledgement" would be advisory only.
            root.protocol("WM_DELETE_WINDOW", lambda: None)

        # Full-width solid underline right under the title -- same flat
        # treatment as the line above the footer button, just packed
        # inside the header itself (tight gap under the title row) rather
        # than as a separate row below the whole header block.
        tk.Frame(header, background=accent, height=2).pack(fill="x", pady=(10, 0))

        content = tk.Frame(card, background=card_bg, padx=24)
        content.pack(fill="both", expand=True, pady=(18, 20))

        # Kind eyebrow (what this is) in the accent color -- severity
        # itself is never spelled out as text, only carried by that color,
        # the border, the divider, and the button below.
        tk.Label(
            content,
            text=theme.letter_spaced(style["label"], gap=" "),
            font=("Segoe UI", 10, "bold"),
            foreground=accent,
            background=card_bg,
        ).pack()
        tk.Label(
            content,
            text=message,
            font=("Segoe UI", 16, "bold"),
            foreground=title_color,
            background=card_bg,
            wraplength=width - 72,
            justify="center",
        ).pack(pady=(10, 0))

        caption = ""
        if require_acknowledgement:
            caption = "Acknowledgement required to continue"
        elif duration_seconds is not None:
            caption = f"Closes automatically in {duration_seconds}s"
        if caption:
            tk.Label(
                content,
                text=caption,
                font=("Segoe UI", 9),
                foreground=note_color,
                background=card_bg,
                wraplength=width - 72,
                justify="center",
            ).pack(pady=(14, 0))

        tk.Frame(card, background=accent, height=2).pack(fill="x")

        footer = tk.Frame(card, background=card_bg, pady=16)
        footer.pack(fill="x")

        self._build_pill_button(
            tk, footer, card_bg, accent, "ACKNOWLEDGE" if require_acknowledgement else "DISMISS", dismiss
        )

        # A draining bar doubles as a literal countdown and as the "lively"
        # motion cue that this will close itself -- only relevant when it
        # actually will (dismissible + timed).
        if not require_acknowledgement and duration_seconds is not None:
            track = tk.Frame(card, background=theme.LINE, height=2)
            track.pack(fill="x", side="bottom")
            fill = tk.Frame(track, background=accent, height=2)
            fill.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)

            total_ticks = max(1, duration_seconds * 5)

            def tick(step: int = 0) -> None:
                if step >= total_ticks:
                    return
                try:
                    fill.place_configure(relwidth=max(0.0, 1 - step / total_ticks))
                except Exception:
                    return
                pending_after_ids.append(root.after(200, lambda: tick(step + 1)))

            tick()
            pending_after_ids.append(root.after(duration_seconds * 1000, dismiss))

        root.update_idletasks()
        height = root.winfo_reqheight()
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y_end = 24  # upper-middle, like a notification banner dropping in from the top -- not vertically centered
        y_start = y_end - 46  # starts a bit further up, drops into place -- the "pop from up there"
        root.geometry(f"{width}x{height}+{x}+{y_start}")
        # `card`'s highlightthickness border above draws perfectly straight
        # edges, but it's a plain rectangle -- at each corner it would just
        # get truncated by the window's rounded GDI clip below rather than
        # following the curve, which is what actually reads as "fake/
        # blurry" edges. Patch each corner: paint over that straight stub
        # and redraw a real anti-aliased arc there instead, matching the
        # exact radius the window clip uses.
        self._round_card_corners(tk, root, header_bg, card_bg, card_border, width, height, radius)
        theme.apply_rounded_corners(root, width, height, radius)
        # A touch of real window translucency -- the closest pure-Tk
        # equivalent of the dashboard's glassy dialog backgrounds, since Tk
        # has no per-pixel/backdrop-blur alpha to fake it with instead.
        theme.fade_in(root, target_alpha=0.96)
        theme.slide_in(root, x, y_start, y_end, width, height)
        if on_shown is not None:
            on_shown(None)  # the window exists and is on its way in
        # Only the standalone fallback path owns its own interpreter and
        # needs to pump it; a Toplevel is already serviced by its parent's
        # mainloop, which is running on this exact thread right now (we got
        # here via that root's own after(0, ...) callback).
        if parent is None:
            root.mainloop()
