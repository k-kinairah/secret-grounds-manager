from __future__ import annotations

import platform
import subprocess
import time
from threading import Thread
from typing import Any, Callable


PowerExecutor = Callable[[list[str]], None]


def default_power_executor(command: list[str]) -> None:
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class RemoteControlHandler:
    """Fixed allowlist for production-safe remote client controls."""

    def __init__(self, pc_name: str, power_executor: PowerExecutor = default_power_executor) -> None:
        self.pc_name = pc_name
        self.power_executor = power_executor

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
        title = self._clean_text(payload.get("title"), 80)
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
            args=(title, message, severity, duration_seconds, require_acknowledgement, announcement),
            daemon=True,
        ).start()
        print(f"Displayed remote {'announcement' if announcement else 'message'}: {title}")
        return "message displayed"

    def _clean_text(self, value: object, max_length: int) -> str:
        if not isinstance(value, str):
            raise RuntimeError("invalid_message_text")
        cleaned = " ".join(value.replace("\x00", "").split())
        if not cleaned:
            raise RuntimeError("blank_message_text")
        return cleaned[:max_length]

    def _show_message_window(
        self,
        title: str,
        message: str,
        severity: str,
        duration_seconds: int | None,
        require_acknowledgement: bool,
        announcement: bool,
    ) -> None:
        try:
            import tkinter as tk
        except ImportError:
            print(f"Tkinter unavailable. Message fallback: {title} - {message}")
            return

        colors = {
            "info": ("#0EA5E9", "#E0FAFF"),
            "warning": ("#F97316", "#FFF7ED"),
            "urgent": ("#F43F5E", "#FFF1F2"),
        }
        accent, foreground = colors[severity]
        root = tk.Tk()
        root.title("Secret Grounds Message")
        root.configure(background="#030712")
        root.attributes("-topmost", True)
        root.resizable(False, False)

        frame = tk.Frame(root, background="#030712", padx=28, pady=24)
        frame.pack(fill="both", expand=True)
        tk.Label(
            frame,
            text="Secret Grounds Announcement" if announcement else "Secret Grounds Message",
            font=("Segoe UI", 11, "bold"),
            foreground=accent,
            background="#030712",
        ).pack(anchor="w")
        tk.Label(
            frame,
            text=title,
            font=("Segoe UI", 24, "bold"),
            foreground=foreground,
            background="#030712",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(10, 8))
        tk.Label(
            frame,
            text=message,
            font=("Segoe UI", 14),
            foreground="#CBD5E1",
            background="#030712",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(0, 18))

        if require_acknowledgement:
            tk.Button(frame, text="Acknowledge", command=root.destroy).pack(anchor="e")
        else:
            tk.Button(frame, text="Dismiss", command=root.destroy).pack(anchor="e")
            if duration_seconds is not None:
                root.after(duration_seconds * 1000, root.destroy)

        root.update_idletasks()
        width = 600
        height = root.winfo_reqheight()
        x = max(0, (root.winfo_screenwidth() - width) // 2)
        y = max(0, (root.winfo_screenheight() - height) // 3)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.mainloop()
