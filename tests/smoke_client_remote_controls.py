import sys
import os
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

    def show_expiration_grace(self, remaining_seconds: int, final_warning: bool = False) -> None:
        pass

    def set_connection_online(self, is_online: bool) -> None:
        pass


def main() -> None:
    import client.remote_controls as remote_controls_module
    from client.client import SecretGroundsClient
    from client.remote_controls import RemoteControlHandler

    original_platform_system = remote_controls_module.platform.system
    original_test_mode = os.environ.get("SECRET_GROUNDS_TEST_MODE")
    remote_controls_module.platform.system = lambda: "Windows"  # type: ignore[assignment]
    try:
        os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"
        try:
            remote_controls_module.default_power_executor(["shutdown", "/s", "/t", "3"])
        except RuntimeError as error:
            assert "blocked_in_test_mode" in str(error)
        else:
            raise AssertionError("Real power executor must be blocked in test mode")

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
        if original_test_mode is None:
            os.environ.pop("SECRET_GROUNDS_TEST_MODE", None)
        else:
            os.environ["SECRET_GROUNDS_TEST_MODE"] = original_test_mode


if __name__ == "__main__":
    main()
