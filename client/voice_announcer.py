"""Best-effort spoken announcements for customer-facing reminders.

Uses Windows' built-in System.Speech (SAPI) via a short, hidden PowerShell
one-liner -- no extra pip dependency, no native COM interop from Python,
nothing new for PyInstaller to bundle.

Fire-and-forget and silent-on-failure by design: a missing/broken
PowerShell, no audio device, Speech platform components not installed, or
anything else going wrong here must never affect a real customer session --
worst case is simply no sound.
"""

from __future__ import annotations

import subprocess
import sys

# CREATE_NO_WINDOW: passed straight into the CreateProcess call subprocess
# makes, so no console is ever allocated for the child -- not even for an
# instant. This is a different (and here, sufficient) mechanism from the
# -WindowStyle Hidden flash fixed for the client's own autostart task (see
# scripts/install-client-autostart.ps1): that flash came from Task
# Scheduler's own process creation, which this project does not control;
# spawning our own child via subprocess with this flag does.
_CREATE_NO_WINDOW = 0x08000000


def announce(text: str) -> None:
    """Speak text aloud once, asynchronously. Returns immediately; never
    raises. No-op on a falsy text or off Windows."""
    if not text or sys.platform != "win32":
        return
    # Embedded in a PowerShell single-quoted string -- '' is that language's
    # own escape for a literal single quote.
    escaped = text.replace("'", "''")
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$voice = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$voice.Speak('{escaped}')"
    )
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            creationflags=_CREATE_NO_WINDOW,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as error:
        print(f"Reminder announcement unavailable: {error}")
