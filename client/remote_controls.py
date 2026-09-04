from __future__ import annotations

import platform
import os
import subprocess
import time
from threading import Thread
from typing import Any, Callable

from client import theme
from client.tk_runtime import create_tk_root, log_tk_startup_error
from client.utils import get_logo_path


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


def default_power_executor(command: list[str]) -> None:
    if os.getenv("SECRET_GROUNDS_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError("real_power_executor_blocked_in_test_mode")
    if os.getenv("PYTEST_CURRENT_TEST"):
        raise RuntimeError("real_power_executor_blocked_in_pytest")
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class RemoteControlHandler:
    """Fixed allowlist for production-safe remote client controls."""

    def __init__(self, pc_name: str, power_executor: PowerExecutor = default_power_executor) -> None:
        self.pc_name = pc_name
        self.power_executor = power_executor
        self._brand_logo_image = None

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

        Thread(
            target=self._show_message_window,
            args=(message, severity, duration_seconds, require_acknowledgement, announcement),
            daemon=True,
        ).start()
        print(f"Displayed remote {'announcement' if announcement else 'message'} from SECRET GROUNDS.")
        return "message displayed"

    def _build_brand_mark(self, tk, container, bg: str = theme.PANEL):
        """Logo image if the packaged asset is available, else a text mark
        -- same fallback pattern as the customer overlay's compact mark."""
        logo_path = get_logo_path()
        if logo_path is not None:
            try:
                image = tk.PhotoImage(file=str(logo_path))
                target_height = 18
                if image.height() > target_height:
                    factor = max(1, round(image.height() / target_height))
                    image = image.subsample(factor, factor)
                self._brand_logo_image = image
                return tk.Label(container, image=image, background=bg)
            except Exception as error:
                log_tk_startup_error("Remote message logo image failed to load", error)

        return tk.Label(
            container,
            text="SG",
            font=("Segoe UI", 8, "bold"),
            foreground=theme.TEXT_ON_ACCENT,
            background=theme.ACCENT_CYAN,
            padx=4,
            pady=2,
        )

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
    ) -> None:
        try:
            tk, root = create_tk_root()
        except Exception as error:
            log_tk_startup_error("Remote message GUI unavailable", error)
            print(f"Message fallback: SECRET GROUNDS - {message}")
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
        self._build_brand_mark(tk, title_row, bg=header_bg).pack(side="left")
        # Plain bold label -- GlowText's offset halo passes only blend into
        # a soft glow at large hero sizes; at this small a point size the
        # offsets just show up as separate misaligned copies of the text.
        tk.Label(
            title_row,
            text="SECRET GROUNDS",
            font=("Segoe UI", 12, "bold"),
            foreground=brand_text_color,  # fixed cyan-blue, same as the dashboard nav's own wordmark -- brand identity, not tone-dependent (darkened for info, see above)
            background=header_bg,
        ).pack(side="left", padx=(8, 0))

        def dismiss(_event=None) -> None:
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
                root.after(200, lambda: tick(step + 1))

            tick()
            root.after(duration_seconds * 1000, dismiss)

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
        root.mainloop()
