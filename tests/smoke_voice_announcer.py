"""client/voice_announcer.py: spoken reminder announcements.

Pure unit tests -- subprocess.Popen is monkeypatched throughout, so no
PowerShell process (and no audio) is ever actually spawned by this file.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import client.voice_announcer as voice_announcer


class FakePopen:
    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs


def test_announce_spawns_a_hidden_powershell_speech_command() -> None:
    calls = []
    original = voice_announcer.subprocess.Popen
    voice_announcer.subprocess.Popen = lambda *a, **kw: calls.append((a, kw)) or FakePopen(*a, **kw)
    try:
        voice_announcer.announce("5 minutes left")
    finally:
        voice_announcer.subprocess.Popen = original

    assert len(calls) == 1
    args, kwargs = calls[0]
    command = args[0]
    assert command[0] == "powershell.exe"
    assert "-NoProfile" in command
    joined = " ".join(command)
    assert "System.Speech" in joined
    assert "SpeechSynthesizer" in joined
    assert "5 minutes left" in joined
    # No console window, ever, for this child process.
    assert kwargs.get("creationflags") == voice_announcer._CREATE_NO_WINDOW
    assert kwargs.get("stdout") is voice_announcer.subprocess.DEVNULL
    assert kwargs.get("stderr") is voice_announcer.subprocess.DEVNULL


def test_announce_escapes_embedded_single_quotes_for_powershell() -> None:
    calls = []
    original = voice_announcer.subprocess.Popen
    voice_announcer.subprocess.Popen = lambda *a, **kw: calls.append(a) or FakePopen(*a, **kw)
    try:
        voice_announcer.announce("Your session will end in 1 minute. Please save your work.")
    finally:
        voice_announcer.subprocess.Popen = original

    # No literal PC-name-with-apostrophe case in practice, but any embedded
    # single quote must become '' (PowerShell's own escape), never break the
    # generated -Command string.
    calls_two = []
    original = voice_announcer.subprocess.Popen
    voice_announcer.subprocess.Popen = lambda *a, **kw: calls_two.append(a) or FakePopen(*a, **kw)
    try:
        voice_announcer.announce("O'Brien's PC")
    finally:
        voice_announcer.subprocess.Popen = original
    joined = " ".join(calls_two[0][0])
    assert "O''Brien''s PC" in joined


def test_announce_is_a_no_op_for_empty_text() -> None:
    calls = []
    original = voice_announcer.subprocess.Popen
    voice_announcer.subprocess.Popen = lambda *a, **kw: calls.append(a)
    try:
        voice_announcer.announce("")
        voice_announcer.announce(None)  # type: ignore[arg-type]
    finally:
        voice_announcer.subprocess.Popen = original
    assert calls == []


def test_announce_never_raises_even_if_popen_fails() -> None:
    original = voice_announcer.subprocess.Popen

    def boom(*a, **kw):
        raise OSError("no powershell here")

    voice_announcer.subprocess.Popen = boom
    try:
        voice_announcer.announce("1 minute left")  # must not raise
    finally:
        voice_announcer.subprocess.Popen = original


def main() -> None:
    for test in (
        test_announce_spawns_a_hidden_powershell_speech_command,
        test_announce_escapes_embedded_single_quotes_for_powershell,
        test_announce_is_a_no_op_for_empty_text,
        test_announce_never_raises_even_if_popen_fails,
    ):
        test()
        print(f"{test.__name__} passed.")
    print("voice announcer smoke tests passed.")


if __name__ == "__main__":
    main()
