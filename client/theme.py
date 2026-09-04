"""Shared visual identity for the customer-facing client UI (lock screen,
active-session overlay). Palette and component values match the approved
"SECRET GROUNDS - CLIENT UI REDESIGN SPECIFICATION" reference exactly (exact
hex values, radii, icon style) so the client matches that reference as
closely as Tkinter allows, rather than improvising a different dark theme.
"""

from __future__ import annotations

import ctypes
import math
import sys

# -- Palette (exact values from the reference spec) -------------------------
BG_DEEP = "#0B1220"  # Background (Primary)
BG_MID = "#0F172A"  # Background (Secondary)
BACKGROUND = "#0B1220"
SURFACE = "#1E293B"  # Surface / Card (used for input fills, sitting on a card)
PANEL = "#0F172A"  # Card background (component spec)
PANEL_STRONG = "#1E293B"
TEXT_PRIMARY = "#E5E7EB"
TEXT_MUTED = "#94A3B8"
LINE = "#334155"  # Border (Subtle) / Card border
ACCENT_PRIMARY = "#3B82F6"  # Primary Blue
ACCENT_ACTIVE = "#3B82F6"
ACCENT_CYAN = "#22D3EE"  # Accent Cyan
SUCCESS = "#10B981"
WARNING = "#FB923C"
OPEN_TIME_ACCENT = "#F59E0B"  # Open Time Accent (Orange)
DANGER = "#F43F5E"
FOCUS = "#3B82F6"  # Focus glow -- blue, per component spec
INPUT_BG = "#1E293B"
INPUT_BORDER = "#334155"
TEXT_ON_ACCENT = "#04101F"

RADIUS_CARD = 18
RADIUS_CONTROL = 14
RADIUS_PILL = 999


def letter_spaced(text: str, gap: str = " ") -> str:
    """Fake CSS letter-spacing by inserting `gap` between every character.

    Tk has no letter-spacing option, so wide-tracked headline text (as
    used throughout the reference for "SECRET GROUNDS") has to be built
    into the string itself.
    """
    return gap.join(text)


def apply_rounded_corners(root, width: int, height: int, radius: int = RADIUS_CARD) -> None:
    """Clip a borderless top-level Tk window to a rounded-rectangle region.

    Real OS-level window shape (GDI region), not a drawn illusion -- only
    valid for genuine top-level windows (Tk/Toplevel), not child widgets.
    Best-effort: failures are swallowed so a GDI/API problem on some
    Windows configuration degrades to square corners rather than crashing
    the client.
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = root.winfo_id()
        region = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, width + 1, height + 1, radius, radius)
        if region:
            ctypes.windll.user32.SetWindowRgn(hwnd, region, True)
    except Exception:
        pass


def apply_soft_shadow(root) -> None:
    """Ask DWM for a native drop shadow on an otherwise borderless window.

    Best-effort: on platforms/configurations without DWM composition this
    silently does nothing, which just means no shadow -- never a crash.
    """
    if sys.platform != "win32":
        return
    try:
        hwnd = root.winfo_id()
        margins = (ctypes.c_int * 4)(1, 1, 1, 1)
        ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
    except Exception:
        pass


def fade_in(root, duration_ms: int = 140, steps: int = 10, target_alpha: float = 1.0) -> None:
    """Step a borderless window's opacity from 0 to target_alpha.

    Best-effort: `-alpha` is a real Windows Tk window attribute (not
    simulated), but not guaranteed on every platform/configuration, so
    failures are swallowed -- the window still shows, just without the
    fade, rather than crashing the client.
    """
    try:
        root.attributes("-alpha", 0.0)
    except Exception:
        return
    step_delay = max(1, duration_ms // steps)

    def _step(i: int = 1) -> None:
        try:
            root.attributes("-alpha", target_alpha * (i / steps))
        except Exception:
            return
        if i < steps:
            root.after(step_delay, lambda: _step(i + 1))

    _step()


def fade_out(root, duration_ms: int = 120, steps: int = 8, on_complete=None) -> None:
    """Step a borderless window's opacity down to 0, then call on_complete
    (typically withdraw()/pack_forget()) -- the reverse of fade_in()."""
    try:
        current = float(root.attributes("-alpha"))
    except Exception:
        if on_complete is not None:
            on_complete()
        return
    step_delay = max(1, duration_ms // steps)

    def _step(i: int = 1) -> None:
        try:
            root.attributes("-alpha", current * (1 - i / steps))
        except Exception:
            pass
        if i < steps:
            root.after(step_delay, lambda: _step(i + 1))
        elif on_complete is not None:
            on_complete()

    _step()


def slide_in(root, x: int, y_start: int, y_end: int, width: int, height: int, duration_ms: int = 220, steps: int = 14) -> None:
    """Animate a window's y position from y_start to y_end -- the vertical
    counterpart to fade_in, for a "drop/pop in" entrance (e.g. a
    notification-style popup landing from just above its resting spot)
    instead of a plain fade in place. Ease-out cubic: fast start, gentle
    settle, which reads as a "pop" rather than a linear slide.
    """
    step_delay = max(1, duration_ms // steps)

    def _step(i: int = 1) -> None:
        eased = 1 - (1 - i / steps) ** 3
        y = round(y_start + (y_end - y_start) * eased)
        try:
            root.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            return
        if i < steps:
            root.after(step_delay, lambda: _step(i + 1))

    _step()


def draw_vertical_gradient(
    canvas, width: int, height: int, top_color: str, bottom_color: str, y_offset: int = 0
) -> None:
    """Fill a Canvas region with a smooth vertical gradient using thin strips.

    Pure-Tk technique (no PIL): interpolate RGB between the two endpoints
    per horizontal strip. y_offset lets multiple gradients be stacked (a
    3-stop gradient built from two 2-stop calls) without each one
    overwriting the last.
    """
    top_rgb = canvas.winfo_rgb(top_color)
    bottom_rgb = canvas.winfo_rgb(bottom_color)
    steps = max(1, height)
    for step in range(steps):
        fraction = step / steps
        r = int(top_rgb[0] + (bottom_rgb[0] - top_rgb[0]) * fraction) >> 8
        g = int(top_rgb[1] + (bottom_rgb[1] - top_rgb[1]) * fraction) >> 8
        b = int(top_rgb[2] + (bottom_rgb[2] - top_rgb[2]) * fraction) >> 8
        color = f"#{r:02x}{g:02x}{b:02x}"
        y = step + y_offset
        canvas.create_line(0, y, width, y, fill=color)


def draw_horizontal_gradient(canvas, x1: int, y1: int, x2: int, y2: int, left_color: str, right_color: str) -> None:
    """Same technique as draw_vertical_gradient, but left-to-right -- used
    for the gradient buttons the reference calls for (LOG IN)."""
    left_rgb = canvas.winfo_rgb(left_color)
    right_rgb = canvas.winfo_rgb(right_color)
    width = max(1, x2 - x1)
    for step in range(width):
        fraction = step / width
        r = int(left_rgb[0] + (right_rgb[0] - left_rgb[0]) * fraction) >> 8
        g = int(left_rgb[1] + (right_rgb[1] - left_rgb[1]) * fraction) >> 8
        b = int(left_rgb[2] + (right_rgb[2] - left_rgb[2]) * fraction) >> 8
        color = f"#{r:02x}{g:02x}{b:02x}"
        x = x1 + step
        canvas.create_line(x, y1, x, y2, fill=color)


def blend(color_a: str, color_b: str, amount: float) -> str:
    """Mix two hex colors -- amount 0 keeps color_a, 1 gives color_b, values
    between interpolate. Tk fills/outlines take one flat color, so this is
    the pure-Tk stand-in for the dashboard's `rgb(accent / 0.4)`-style
    translucent borders: mix the accent toward the surface color it sits on
    instead of laying down a real alpha-blended one.
    """
    a = color_a.lstrip("#")
    b = color_b.lstrip("#")
    ar, ag, ab = (int(a[i : i + 2], 16) for i in (0, 2, 4))
    br, bg, bb = (int(b[i : i + 2], 16) for i in (0, 2, 4))
    r = round(ar + (br - ar) * amount)
    g = round(ag + (bg - ag) * amount)
    b_ = round(ab + (bb - ab) * amount)
    return f"#{r:02x}{g:02x}{b_:02x}"


def lighten(hex_color: str, amount: float) -> str:
    """Blend a hex color toward white by `amount` (0-1) -- lets a two-stop
    gradient (GradientButton, etc.) be derived from a single accent color
    instead of hand-picking a second stop for every accent it's used with.
    """
    return blend(hex_color, "#ffffff", amount)


def draw_soft_glow(canvas, center_x: int, center_y: int, max_radius: int, color: str) -> None:
    """Approximate a faint ambient glow with two sparse stippled ovals.

    Tk cannot alpha-blend, so true blur/opacity isn't available -- a sparse
    stipple pattern (a dithered fill) is the pure-Tk stand-in for "barely
    there". Deliberately just two rings at the sparsest stipple Tk offers
    ("gray12", ~12% dot fill) -- more rings or a denser stipple reads as a
    solid colored blob rather than an ambient glow, especially at the large
    radii an "ambient light behind the UI" effect needs.
    """
    rings = [
        (max_radius, "gray12"),
        (int(max_radius * 0.55), "gray12"),
    ]
    for radius, stipple in rings:
        if radius <= 0:
            continue
        canvas.create_oval(
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
            fill=color,
            outline="",
            stipple=stipple,
        )


def draw_particles(canvas, width: int, height: int, count: int, color: str, seed: int = 7) -> None:
    """Scatter tiny sparse dots across a region -- pure-Tk stand-in for the
    reference's faint background "particles". Uses a fixed, seeded PRNG
    (not true randomness) so the pattern is stable across runs/screen sizes
    rather than jittering on every restart. Mixed sizes/stipple density
    read as a starfield rather than a uniform dot grid.
    """
    import random

    rng = random.Random(seed)
    for _ in range(count):
        x = rng.uniform(0, width)
        y = rng.uniform(0, height)
        r = rng.uniform(0.5, 1.8)
        stipple = "gray25" if r < 1.2 else "gray50"
        canvas.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="", stipple=stipple)


def draw_flowing_streak(
    canvas, points: list[tuple[float, float]], glow_color: str, core_color: str | None = None
) -> None:
    """A curved, glowing "light trail" -- the pure-Tk equivalent of the
    reference's SVG bezier streaks. `create_line(..., smooth=True)` draws a
    real spline through the given control points (not a straight segment),
    and layering a thick stippled pass under a thin bright pass approximates
    a soft-edged glowing core, the same two-pass trick draw_soft_glow uses
    for radial glow.
    """
    core_color = core_color or glow_color
    flat_points = [coord for point in points for coord in point]
    canvas.create_line(
        flat_points, smooth=True, splinesteps=36, width=22, fill=glow_color,
        capstyle="round", joinstyle="round", stipple="gray12",
    )
    canvas.create_line(
        flat_points, smooth=True, splinesteps=36, width=10, fill=glow_color,
        capstyle="round", joinstyle="round", stipple="gray25",
    )
    canvas.create_line(
        flat_points, smooth=True, splinesteps=36, width=2, fill=core_color,
        capstyle="round", joinstyle="round",
    )


def draw_vignette(canvas, width: int, height: int, color: str = BG_DEEP) -> None:
    """Darken the four corners/edges of a Canvas -- Tk cannot alpha-blend a
    true radial vignette (darker at the edges, clear in the middle), so
    this layers stippled corner patches of the background color instead;
    denser near the corner, sparser as it reaches toward center.
    """
    corner = min(width, height) * 0.42
    for cx, cy in ((0, 0), (width, 0), (0, height), (width, height)):
        for radius, stipple in ((corner, "gray25"), (corner * 0.6, "gray50")):
            canvas.create_oval(
                cx - radius, cy - radius, cx + radius, cy + radius,
                fill=color, outline="", stipple=stipple,
            )


def draw_faded_watermark(canvas, image, cx: float, cy: float, bg_color: str) -> None:
    """Place a PhotoImage as a barely-there background watermark.

    Tk's PhotoImage has no global-alpha/opacity control (that needs PIL,
    which this project deliberately doesn't depend on), so "5-8% opacity"
    is faked by drawing the image at full strength and then washing it out
    with stippled background-color overlays on top -- the same dithering
    trick used everywhere else in this module for "faint", just applied to
    a bitmap instead of a vector shape.
    """
    canvas.create_image(cx, cy, image=image, anchor="center")
    width, height = image.width(), image.height()
    # Escalating stipple density (each pass hits more pixels than the
    # last) washes the image down toward "barely noticeable" rather than
    # the lighter 2xgray25+gray50 fade an earlier version used, which
    # still read as a fairly visible logo, not a faint watermark.
    for stipple in ("gray25", "gray50", "gray50", "gray75", "gray75"):
        canvas.create_rectangle(
            cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2,
            fill=bg_color, outline="", stipple=stipple,
        )


class GlowText:
    """Canvas-based text with a layered glow and faux-heavy weight -- Tk
    labels have no text-shadow and only a binary bold/not-bold weight, so a
    "hero" glowing title needs the same trick real renderers use under the
    hood: draw dim, stippled, offset copies of the text in the glow color
    first (wide pass then tight pass), then thicken the crisp text itself by
    stamping it at a few 1px offsets before the final centered draw.
    Exposes cget("text")/configure(text=...) so call sites and tests written
    against a plain tk.Label keep working unchanged.
    """

    def __init__(
        self,
        parent,
        tk,
        text: str,
        font,
        fill: str = "#FFFFFF",
        glow_color: str = ACCENT_CYAN,
        halo_color: str = ACCENT_PRIMARY,
        bg: str = BG_DEEP,
        pad: int = 46,
    ) -> None:
        self._tk = tk
        self._font = font
        self._fill = fill
        self._glow_color = glow_color
        self._halo_color = halo_color
        self._bg = bg
        self._text = text
        self._pad = pad
        self.canvas = tk.Canvas(parent, highlightthickness=0, bd=0, bg=bg)
        self._redraw()

    def _redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        probe = canvas.create_text(0, 0, text=self._text, font=self._font, anchor="nw")
        bbox = canvas.bbox(probe)
        canvas.delete(probe)
        pad = self._pad
        width = (bbox[2] - bbox[0]) + pad * 2
        height = (bbox[3] - bbox[1]) + pad * 2
        canvas.configure(width=width, height=height)
        cx, cy = width / 2, height / 2

        # Wide soft halo (blue), then a tighter bloom (cyan) -- two glow
        # radii read as a real bloom rather than a single flat ring.
        # Deliberately restrained: half the passes and half the offset
        # distance of an earlier version, which read as overwhelming the
        # text rather than framing it.
        for dx, dy in ((-2.5, 0), (2.5, 0), (0, -2.5), (0, 2.5)):
            canvas.create_text(
                cx + dx, cy + dy, text=self._text, font=self._font, fill=self._halo_color, anchor="center"
            )
        for dx, dy in ((-1.2, 0), (1.2, 0), (0, -1.2), (0, 1.2)):
            canvas.create_text(
                cx + dx, cy + dy, text=self._text, font=self._font, fill=self._glow_color, anchor="center"
            )
        # Faux-heavier weight: stamp the final color at four 1px offsets
        # before the crisp centered draw, thickening every stroke slightly
        # beyond what the font's own "bold" flag gives.
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            canvas.create_text(cx + dx, cy + dy, text=self._text, font=self._font, fill=self._fill, anchor="center")
        canvas.create_text(cx, cy, text=self._text, font=self._font, fill=self._fill, anchor="center")

    def configure(self, text: str | None = None, **_ignored) -> None:
        if text is not None and text != self._text:
            self._text = text
            self._redraw()

    def cget(self, key: str):
        if key == "text":
            return self._text
        raise KeyError(key)

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self


class BoldText:
    """GlowText's faux-bold stamped-offset pass, without the stippled glow
    halo underneath it -- a stipple pattern reads fine as ambient light
    behind a large hero headline, but at small (label-sized) text it just
    reads as blurry/soft. This is for small brand/eyebrow text that needs
    to look genuinely thicker than Tk's own "bold" flag renders it, while
    staying crisp. Same cget/configure/pack surface as GlowText.
    """

    def __init__(self, parent, tk, text: str, font, fill: str, bg: str) -> None:
        self._tk = tk
        self._font = font
        self._fill = fill
        self._bg = bg
        self._text = text
        self.canvas = tk.Canvas(parent, highlightthickness=0, bd=0, bg=bg)
        self._redraw()

    def _redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        probe = canvas.create_text(0, 0, text=self._text, font=self._font, anchor="nw")
        bbox = canvas.bbox(probe)
        canvas.delete(probe)
        pad = 2
        width = (bbox[2] - bbox[0]) + pad * 2
        height = (bbox[3] - bbox[1]) + pad * 2
        canvas.configure(width=width, height=height)
        cx, cy = width / 2, height / 2
        for dx, dy in ((-0.6, 0), (0.6, 0), (0, -0.6), (0, 0.6)):
            canvas.create_text(cx + dx, cy + dy, text=self._text, font=self._font, fill=self._fill, anchor="center")
        canvas.create_text(cx, cy, text=self._text, font=self._font, fill=self._fill, anchor="center")

    def configure(self, text: str | None = None, **_ignored) -> None:
        if text is not None and text != self._text:
            self._text = text
            self._redraw()

    def cget(self, key: str):
        if key == "text":
            return self._text
        raise KeyError(key)

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self


def rounded_rect_points(x1: float, y1: float, x2: float, y2: float, radius: float) -> list[float]:
    """Point list for canvas.create_polygon(..., smooth=True) approximating
    a rounded rectangle -- the standard pure-Tk way to get real rounded
    corners on a Canvas-drawn shape (as opposed to an OS window region,
    which only applies to top-level windows)."""
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]


def draw_rounded_rect(canvas, x1: float, y1: float, x2: float, y2: float, radius: float, **kwargs):
    return canvas.create_polygon(
        rounded_rect_points(x1, y1, x2, y2, radius), smooth=True, splinesteps=24, **kwargs
    )


# -- Icon set -----------------------------------------------------------
# Consistent outlined vector icons drawn with plain Canvas primitives
# (lines/arcs/ovals) -- the reference explicitly calls for a real outlined
# icon set, not emoji. No image assets or icon-font dependency needed.

_ICON_NAMES = frozenset(
    {
        "user", "lock", "eye", "clock", "computer", "logout", "chevron-down", "chevron-right", "dots", "gear",
        "close", "info", "warning", "urgent",
    }
)


def draw_icon(canvas, name: str, cx: float, cy: float, size: float, color: str, width: float = 1.6) -> None:
    r = size / 2
    if name == "user":
        head_r = size * 0.19
        canvas.create_oval(
            cx - head_r, cy - r * 0.68, cx + head_r, cy - r * 0.68 + head_r * 2, outline=color, width=width
        )
        canvas.create_arc(
            cx - r * 0.55, cy - r * 0.05, cx + r * 0.55, cy + r * 0.95,
            start=0, extent=180, outline=color, width=width, style="arc",
        )
    elif name == "lock":
        body_w, body_h = size * 0.6, size * 0.48
        top = cy - body_h * 0.05
        canvas.create_rectangle(cx - body_w / 2, top, cx + body_w / 2, top + body_h, outline=color, width=width)
        canvas.create_arc(
            cx - body_w * 0.32, top - body_h * 0.62, cx + body_w * 0.32, top + body_h * 0.12,
            start=0, extent=180, outline=color, width=width, style="arc",
        )
        canvas.create_oval(cx - 1.3, cy + body_h * 0.12, cx + 1.3, cy + body_h * 0.12 + 2.6, fill=color, outline="")
    elif name == "eye":
        canvas.create_oval(cx - r * 0.85, cy - r * 0.48, cx + r * 0.85, cy + r * 0.48, outline=color, width=width)
        canvas.create_oval(cx - r * 0.17, cy - r * 0.17, cx + r * 0.17, cy + r * 0.17, fill=color, outline="")
    elif name == "clock":
        canvas.create_oval(cx - r * 0.82, cy - r * 0.82, cx + r * 0.82, cy + r * 0.82, outline=color, width=width)
        canvas.create_line(cx, cy, cx, cy - r * 0.5, fill=color, width=width, capstyle="round")
        canvas.create_line(cx, cy, cx + r * 0.35, cy + r * 0.15, fill=color, width=width, capstyle="round")
    elif name == "computer":
        w, h = size * 0.86, size * 0.52
        canvas.create_rectangle(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2 - 2, outline=color, width=width)
        canvas.create_line(cx - w * 0.22, cy + h / 2 + 2, cx + w * 0.22, cy + h / 2 + 2, fill=color, width=width)
    elif name == "logout":
        bw, bh = size * 0.45, size * 0.8
        canvas.create_line(cx + bw * 0.2, cy - bh / 2, cx - bw / 2, cy - bh / 2, fill=color, width=width)
        canvas.create_line(cx - bw / 2, cy - bh / 2, cx - bw / 2, cy + bh / 2, fill=color, width=width)
        canvas.create_line(cx - bw / 2, cy + bh / 2, cx + bw * 0.2, cy + bh / 2, fill=color, width=width)
        canvas.create_line(cx - bw * 0.15, cy, cx + bw * 0.85, cy, fill=color, width=width)
        canvas.create_line(cx + bw * 0.55, cy - bh * 0.25, cx + bw * 0.85, cy, fill=color, width=width)
        canvas.create_line(cx + bw * 0.55, cy + bh * 0.25, cx + bw * 0.85, cy, fill=color, width=width)
    elif name == "chevron-down":
        canvas.create_line(
            cx - r * 0.5, cy - r * 0.25, cx, cy + r * 0.3, cx + r * 0.5, cy - r * 0.25,
            fill=color, width=width, joinstyle="round", capstyle="round", smooth=False,
        )
    elif name == "chevron-right":
        canvas.create_line(
            cx - r * 0.25, cy - r * 0.5, cx + r * 0.3, cy, cx - r * 0.25, cy + r * 0.5,
            fill=color, width=width, joinstyle="round", capstyle="round", smooth=False,
        )
    elif name == "dots":
        for dx in (-r * 0.5, 0, r * 0.5):
            canvas.create_oval(cx + dx - 1.4, cy - 1.4, cx + dx + 1.4, cy + 1.4, fill=color, outline="")
    elif name == "gear":
        canvas.create_oval(cx - r * 0.35, cy - r * 0.35, cx + r * 0.35, cy + r * 0.35, outline=color, width=width)
        for i in range(8):
            angle = i * (math.pi / 4)
            x1 = cx + math.cos(angle) * r * 0.4
            y1 = cy + math.sin(angle) * r * 0.4
            x2 = cx + math.cos(angle) * r * 0.85
            y2 = cy + math.sin(angle) * r * 0.85
            canvas.create_line(x1, y1, x2, y2, fill=color, width=width)
    elif name == "close":
        offset = r * 0.55
        canvas.create_line(
            cx - offset, cy - offset, cx + offset, cy + offset, fill=color, width=width, capstyle="round"
        )
        canvas.create_line(
            cx - offset, cy + offset, cx + offset, cy - offset, fill=color, width=width, capstyle="round"
        )
    elif name == "info":
        canvas.create_oval(cx - r * 0.82, cy - r * 0.82, cx + r * 0.82, cy + r * 0.82, outline=color, width=width)
        canvas.create_oval(cx - 1.4, cy - r * 0.42, cx + 1.4, cy - r * 0.42 + 2.8, fill=color, outline="")
        canvas.create_line(cx, cy - r * 0.05, cx, cy + r * 0.45, fill=color, width=width, capstyle="round")
    elif name == "warning":
        canvas.create_polygon(
            cx, cy - r * 0.85, cx + r * 0.82, cy + r * 0.6, cx - r * 0.82, cy + r * 0.6,
            outline=color, fill="", width=width, joinstyle="round",
        )
        canvas.create_line(cx, cy - r * 0.25, cx, cy + r * 0.15, fill=color, width=width, capstyle="round")
        canvas.create_oval(cx - 1.4, cy + r * 0.35, cx + 1.4, cy + r * 0.35 + 2.8, fill=color, outline="")
    elif name == "urgent":
        # A second, tighter ring around the same exclamation reads as more
        # alarming than the plain "info" circle without needing a filled
        # (alpha-blend-dependent) fill Tk can't do.
        canvas.create_oval(cx - r * 0.85, cy - r * 0.85, cx + r * 0.85, cy + r * 0.85, outline=color, width=width)
        canvas.create_oval(
            cx - r * 0.66, cy - r * 0.66, cx + r * 0.66, cy + r * 0.66, outline=color, width=max(1.0, width * 0.7)
        )
        canvas.create_line(cx, cy - r * 0.32, cx, cy + r * 0.12, fill=color, width=width, capstyle="round")
        canvas.create_oval(cx - 1.5, cy + r * 0.32, cx + 1.5, cy + r * 0.32 + 3, fill=color, outline="")
    else:
        raise ValueError(f"Unknown icon name: {name!r}")


class Icon:
    """A small Canvas showing one outlined vector icon -- pack()/grid() like
    a Label, recolor via set_color() (used for hover/focus/toggle states
    without needing an image asset)."""

    def __init__(
        self, parent, tk, name: str, size: int = 16, color: str = TEXT_MUTED, bg: str = PANEL, width: float = 1.6
    ) -> None:
        self.canvas = tk.Canvas(parent, width=size, height=size, highlightthickness=0, bd=0, bg=bg)
        self._name = name
        self._size = size
        self._stroke_width = width
        self.color = color
        self._redraw()

    def _redraw(self) -> None:
        self.canvas.delete("all")
        draw_icon(self.canvas, self._name, self._size / 2, self._size / 2, self._size, self.color, self._stroke_width)

    def set_color(self, color: str) -> None:
        self.color = color
        self._redraw()

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    def grid(self, **kwargs):
        self.canvas.grid(**kwargs)
        return self


class GradientButton:
    """A canvas-drawn button with a left-to-right gradient fill, rounded
    corners, hover glow and press feedback -- the closest pure-Tk
    approximation of the reference's gradient CTA button (LOG IN).

    Exposes configure(state=..., text=...) so call sites written against a
    plain tk.Button keep working unchanged.
    """

    def __init__(
        self,
        parent,
        tk,
        text: str,
        command,
        width: int = 320,
        height: int = 48,
        color_left: str = ACCENT_PRIMARY,
        color_right: str = ACCENT_CYAN,
        radius: int = RADIUS_CONTROL,
        text_color: str = TEXT_ON_ACCENT,
        font=("Segoe UI", 11, "bold"),
        bg: str = BG_DEEP,
    ) -> None:
        self._tk = tk
        self._command = command
        self._width = width
        self._height = height
        self._color_left = color_left
        self._color_right = color_right
        self._radius = radius
        self._text_color = text_color
        self._font = font
        self._text = text
        self._state = "normal"
        self._pressed = False
        self._bg = bg

        self.canvas = tk.Canvas(
            parent, width=width, height=height, highlightthickness=0, bd=0, bg=bg, cursor="hand2"
        )
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self._redraw()

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    def grid(self, **kwargs):
        self.canvas.grid(**kwargs)
        return self

    def configure(self, state: str | None = None, text: str | None = None) -> None:
        changed = False
        if state is not None and state != self._state:
            self._state = state
            changed = True
        if text is not None and text != self._text:
            self._text = text
            changed = True
        if changed:
            self._redraw()

    def _redraw(self, brighten: bool = False) -> None:
        canvas = self.canvas
        canvas.delete("all")
        left = self._color_left
        right = self._color_right
        if self._state == "disabled":
            left = right = LINE
        elif self._pressed:
            left, right = right, left
        elif brighten:
            left, right = ACCENT_PRIMARY, ACCENT_CYAN

        width, height, radius, bg = self._width, self._height, self._radius, self._bg
        draw_horizontal_gradient(canvas, 0, 0, width, height, left, right)
        # A gradient fill is drawn as a plain rectangle (create_line strips
        # can't follow a rounded silhouette), so its sharp corners poke past
        # the rounded shape. Mask each corner back to the canvas's own
        # background, then re-draw just that corner as a solid quarter-circle
        # in the nearest gradient color -- the standard Tk "rounded rect via
        # corner masking" technique.
        r = radius
        canvas.create_rectangle(0, 0, r, r, fill=bg, outline="")
        canvas.create_arc(0, 0, r * 2, r * 2, start=90, extent=90, fill=left, outline="", style="pieslice")
        canvas.create_rectangle(width - r, 0, width, r, fill=bg, outline="")
        canvas.create_arc(width - r * 2, 0, width, r * 2, start=0, extent=90, fill=right, outline="", style="pieslice")
        canvas.create_rectangle(0, height - r, r, height, fill=bg, outline="")
        canvas.create_arc(0, height - r * 2, r * 2, height, start=180, extent=90, fill=left, outline="", style="pieslice")
        canvas.create_rectangle(width - r, height - r, width, height, fill=bg, outline="")
        canvas.create_arc(
            width - r * 2, height - r * 2, width, height, start=270, extent=90, fill=right, outline="", style="pieslice"
        )
        canvas.create_text(
            width / 2,
            height / 2,
            text=self._text,
            fill=self._text_color if self._state != "disabled" else TEXT_MUTED,
            font=self._font,
        )

    def _on_enter(self, _event=None) -> None:
        if self._state != "disabled":
            self._redraw(brighten=True)

    def _on_leave(self, _event=None) -> None:
        self._pressed = False
        self._redraw()

    def _on_press(self, _event=None) -> None:
        if self._state == "disabled":
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, event) -> None:
        if self._state == "disabled":
            return
        was_pressed = self._pressed
        self._pressed = False
        self._redraw(brighten=True)
        inside = 0 <= event.x <= self._width and 0 <= event.y <= self._height
        if was_pressed and inside and self._command is not None:
            self._command()


class OutlineButton:
    """A quiet secondary-action button: dark fill, thin colored outline,
    text in the accent color -- for actions that shouldn't compete with a
    screen's one gradient CTA (Log Out next to LOG IN). Thickens and
    brightens its outline on hover as the nearest pure-Tk stand-in for a
    glow. Exposes the same configure(state=..., text=...) surface as
    GradientButton so call sites can treat either interchangeably.
    """

    def __init__(
        self,
        parent,
        tk,
        text: str,
        command,
        width: int = 140,
        height: int = 40,
        accent: str = ACCENT_CYAN,
        radius: int = RADIUS_CONTROL,
        font=("Segoe UI", 10, "bold"),
        bg: str = PANEL,
    ) -> None:
        self._tk = tk
        self._command = command
        self._width = width
        self._height = height
        self._accent = accent
        self._radius = radius
        self._font = font
        self._text = text
        self._state = "normal"
        self._bg = bg
        self._pressed = False
        self._hover = False

        self.canvas = tk.Canvas(
            parent, width=width, height=height, highlightthickness=0, bd=0, bg=bg, cursor="hand2"
        )
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self._redraw()

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self

    def configure(self, state: str | None = None, text: str | None = None) -> None:
        changed = False
        if state is not None and state != self._state:
            self._state = state
            changed = True
        if text is not None and text != self._text:
            self._text = text
            changed = True
        if changed:
            self._redraw()

    def _redraw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        disabled = self._state == "disabled"
        outline = LINE if disabled else self._accent
        outline_width = 2 if (self._hover and not disabled) else 1
        fill = PANEL_STRONG if (self._pressed and not disabled) else self._bg
        draw_rounded_rect(
            canvas, 1, 1, self._width - 1, self._height - 1, self._radius,
            fill=fill, outline=outline, width=outline_width,
        )
        canvas.create_text(
            self._width / 2, self._height / 2, text=self._text,
            fill=TEXT_MUTED if disabled else self._accent, font=self._font,
        )

    def _on_enter(self, _event=None) -> None:
        if self._state != "disabled":
            self._hover = True
            self._redraw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _event=None) -> None:
        if self._state == "disabled":
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, event) -> None:
        if self._state == "disabled":
            return
        was_pressed = self._pressed
        self._pressed = False
        self._redraw()
        inside = 0 <= event.x <= self._width and 0 <= event.y <= self._height
        if was_pressed and inside and self._command is not None:
            self._command()


class IconField:
    """A rounded, icon-prefixed input -- the reference's "icon inside the
    textbox" style -- built by embedding a real tk.Entry inside a Canvas
    via create_window, so typing/focus/selection all behave like a normal
    Entry while the Canvas supplies the rounded background and the leading
    (and optional trailing) outlined vector icon.

    A fake placeholder (shown when the field is empty and unfocused) is
    implemented directly on the Entry since Tk has no native placeholder
    support; get_value() strips it back out so callers never see it.
    """

    def __init__(
        self,
        parent,
        tk,
        icon: str,
        placeholder: str,
        width: int = 360,
        height: int = 48,
        show: str | None = None,
        trailing_icon: str | None = None,
        trailing_command=None,
        font=("Segoe UI", 12),
        bg: str = BG_DEEP,
    ) -> None:
        self._tk = tk
        self._width = width
        self._height = height
        self._placeholder = placeholder
        self._real_show = show
        self._showing_placeholder = True
        self._focus_color = FOCUS

        self.canvas = tk.Canvas(parent, width=width, height=height, highlightthickness=0, bd=0, bg=bg)
        # Passive border is barely-there; focus swaps it to the accent color
        # and thickens it slightly -- the closest a flat Tk outline can get
        # to a soft focus glow without the canvas clipping a true outer ring.
        self._bg_shape = draw_rounded_rect(
            self.canvas, 0, 0, width, height, RADIUS_CONTROL, fill=INPUT_BG, outline=INPUT_BORDER, width=1
        )

        self.leading_icon = Icon(self.canvas, tk, icon, size=16, color=TEXT_MUTED, bg=INPUT_BG)
        self.canvas.create_window(18, height / 2, window=self.leading_icon.canvas, anchor="w")

        entry_width_chars = max(8, int((width - 90) / 9))
        self.entry = tk.Entry(
            self.canvas,
            width=entry_width_chars,
            font=font,
            fg=TEXT_MUTED,
            bg=INPUT_BG,
            insertbackground=TEXT_PRIMARY,
            relief="flat",
            bd=0,
            highlightthickness=0,
        )
        self.entry.insert(0, placeholder)
        right_margin = 44 if trailing_icon else 18
        self.canvas.create_window(46, height / 2, window=self.entry, anchor="w", width=width - 46 - right_margin)

        self.trailing_icon = None
        if trailing_icon is not None:
            self.trailing_icon = Icon(self.canvas, tk, trailing_icon, size=16, color=TEXT_MUTED, bg=INPUT_BG)
            trailing_widget = self.trailing_icon.canvas
            # takefocus + Return/space keep this keyboard-accessible (Tab to
            # it, activate with Enter/Space) the same way the Entry beside
            # it already is -- a bare Canvas isn't focusable by default.
            trailing_widget.configure(cursor="hand2", takefocus=True, highlightthickness=0)
            self.canvas.create_window(width - 18, height / 2, window=trailing_widget, anchor="e")
            if trailing_command is not None:
                trailing_widget.bind("<Button-1>", lambda _e: trailing_command())
                trailing_widget.bind("<Return>", lambda _e: trailing_command())
                trailing_widget.bind("<space>", lambda _e: trailing_command())

        self.entry.bind("<FocusIn>", self._on_focus_in)
        self.entry.bind("<FocusOut>", self._on_focus_out)

    def _on_focus_in(self, _event=None) -> None:
        if self._showing_placeholder:
            self.entry.delete(0, "end")
            self.entry.configure(fg=TEXT_PRIMARY, show=self._real_show or "")
            self._showing_placeholder = False
        self.canvas.itemconfigure(self._bg_shape, outline=self._focus_color, width=2)

    def _on_focus_out(self, _event=None) -> None:
        if not self.entry.get():
            self.entry.configure(fg=TEXT_MUTED, show="")
            self.entry.insert(0, self._placeholder)
            self._showing_placeholder = True
        self.canvas.itemconfigure(self._bg_shape, outline=INPUT_BORDER, width=1)

    def get_value(self) -> str:
        return "" if self._showing_placeholder else self.entry.get()

    def reset(self) -> None:
        """Clear any typed value and return to the unfocused placeholder
        state -- get_value() becomes "" and the placeholder text shows
        unmasked, exactly as on a freshly built field."""
        self.entry.delete(0, "end")
        self.entry.configure(fg=TEXT_MUTED, show="")
        self.entry.insert(0, self._placeholder)
        self._showing_placeholder = True

    def set_masked(self, masked: bool) -> None:
        if not self._showing_placeholder:
            self.entry.configure(show=(self._real_show or "*") if masked else "")

    def pack(self, **kwargs):
        self.canvas.pack(**kwargs)
        return self
