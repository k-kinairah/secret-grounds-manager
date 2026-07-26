import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class NoGuiLockScreen:
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def hide_for_session(self) -> None:
        pass

    def show_locked(self, reason: str | None = None) -> None:
        pass

    def set_connection_online(self, is_online: bool) -> None:
        pass


def main() -> None:
    import client.remote_controls as remote_controls_module
    from client.client import SecretGroundsClient
    from client.remote_controls import RemoteControlHandler

    original_platform_system = remote_controls_module.platform.system
    remote_controls_module.platform.system = lambda: "Windows"  # type: ignore[assignment]
    try:
        executed: list[list[str]] = []
        handler = RemoteControlHandler("PC-TEST", power_executor=lambda command: executed.append(command))
        handler._show_message_window = lambda *args, **kwargs: None  # type: ignore[method-assign]
        message = handler.handle_power_command("shutdown", {})
        assert "expected disconnect" in message
        assert handler.show_message(
            {
                "title": "Secret Grounds",
                "message": "Please return to the cashier.",
                "severity": "info",
                "duration_seconds": 5,
            }
        ) == "message displayed"
        try:
            handler.show_message({"title": "", "message": "", "severity": "bad"})
        except RuntimeError:
            pass
        else:
            raise AssertionError("Invalid message payload should fail")

        client = SecretGroundsClient(
            server_url="http://127.0.0.1:8000",
            pc_name="PC-TEST",
            heartbeat_interval=5,
            request_timeout=2,
            lock_screen=NoGuiLockScreen(),  # type: ignore[arg-type]
            enable_lock_screen=False,
        )
        calls: list[str] = []
        client.remote_controls = RemoteControlHandler(
            "PC-TEST",
            power_executor=lambda command: calls.append(" ".join(command)),
        )
        command = {
            "command_id": "remote-1",
            "type": "sleep",
            "payload": {},
        }
        first = client.execute_command(command, websocket=None)  # type: ignore[arg-type]
        second = client.execute_command(command, websocket=None)  # type: ignore[arg-type]
        assert first["status"] == "completed"
        assert second["message"] == "Duplicate command ignored"

        print("Client remote controls smoke test passed.")
    finally:
        remote_controls_module.platform.system = original_platform_system  # type: ignore[assignment]


if __name__ == "__main__":
    main()
