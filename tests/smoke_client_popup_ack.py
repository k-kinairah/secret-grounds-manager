"""Remote message/announcement acknowledgements are honest and never block.

- "completed / message displayed" only once the pop-up is really on screen;
- "failed" with the reason when it can't open, when it doesn't appear within
  the time limit, or (test mode only) when a failure is forced;
- the client's command loop never waits for the pop-up: other commands are
  handled and acknowledged straight away.
No real window is opened: the window builder is stubbed.
"""

import os
import sys
import time
from pathlib import Path
from threading import Event

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import client.remote_controls as remote_controls  # noqa: E402
from client.remote_controls import RemoteControlHandler  # noqa: E402

PAYLOAD = {"title": "SECRET GROUNDS", "message": "We close in 15 minutes", "severity": "info", "duration_seconds": 20}


def handler_with(builder) -> RemoteControlHandler:
    handler = RemoteControlHandler("PC-TEST", power_executor=lambda command: None)
    # No Tk at all: no running GUI root and no dedicated pop-up root, so the
    # pop-up goes through the standalone path straight into the stub builder.
    handler._get_active_gui_root = lambda: None  # type: ignore[method-assign]
    handler._ensure_popup_root = lambda: (None, None)  # type: ignore[method-assign]
    handler._show_message_window = builder  # type: ignore[method-assign]
    return handler


def wait_result(handler, payload=PAYLOAD, announcement=True, timeout=5.0):
    done = Event()
    result: dict = {}

    def on_result(shown, text):
        result.update(shown=shown, text=text, at=time.monotonic())
        done.set()

    started = time.monotonic()
    handler.show_message_async(payload, announcement, on_result)
    returned_after = time.monotonic() - started
    assert done.wait(timeout), "no result reported"
    return result, returned_after


def test_shown_only_when_the_window_reports_it() -> None:
    def builder(*args, on_shown=None, **kwargs):
        time.sleep(0.3)  # building the window takes a moment
        on_shown(None)

    result, returned_after = wait_result(handler_with(builder))
    assert result["shown"] is True and result["text"] == "message displayed"
    assert returned_after < 0.2, f"show_message_async must not wait ({returned_after:.2f}s)"


def test_window_error_is_a_failure_with_the_reason() -> None:
    def builder(*args, on_shown=None, **kwargs):
        raise RuntimeError("Could not find Tcl/Tk startup file init.tcl. Checked:\n - C:\\somewhere")

    result, _ = wait_result(handler_with(builder))
    assert result["shown"] is False
    assert result["text"] == "the pop-up couldn't open: Could not find Tcl/Tk startup file init.tcl. Checked:"


def test_reported_failure_from_inside_the_builder() -> None:
    def builder(*args, on_shown=None, **kwargs):
        on_shown("the pop-up couldn't open: no display")

    result, _ = wait_result(handler_with(builder))
    assert (result["shown"], result["text"]) == (False, "the pop-up couldn't open: no display")


def test_no_answer_in_time_is_a_failure() -> None:
    original = remote_controls.POPUP_CONFIRM_TIMEOUT_SECONDS
    remote_controls.POPUP_CONFIRM_TIMEOUT_SECONDS = 0.3
    release = Event()
    try:
        def builder(*args, on_shown=None, **kwargs):
            release.wait(5)  # never gets on screen while we wait

        result, _ = wait_result(handler_with(builder))
        assert result["shown"] is False and "didn't appear" in result["text"]
    finally:
        release.set()
        remote_controls.POPUP_CONFIRM_TIMEOUT_SECONDS = original


def test_only_the_first_result_counts() -> None:
    results = []

    def builder(*args, on_shown=None, **kwargs):
        on_shown(None)
        on_shown("late failure")

    handler = handler_with(builder)
    done = Event()
    handler.show_message_async(PAYLOAD, False, lambda shown, text: (results.append((shown, text)), done.set()))
    assert done.wait(3)
    time.sleep(0.2)
    assert results == [(True, "message displayed")]


def test_forced_failure_only_in_test_mode() -> None:
    saved = {name: os.environ.get(name) for name in ("SECRET_GROUNDS_TEST_MODE", "SECRET_GROUNDS_TEST_POPUP_FAILURE")}

    def builder(*args, on_shown=None, **kwargs):
        on_shown(None)

    try:
        os.environ["SECRET_GROUNDS_TEST_POPUP_FAILURE"] = "true"
        os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"
        result, _ = wait_result(handler_with(builder))
        assert (result["shown"], result["text"]) == (False, "the pop-up couldn't open (test mode: forced failure)")
        os.environ["SECRET_GROUNDS_TEST_MODE"] = "false"  # ignored outside test mode
        result, _ = wait_result(handler_with(builder))
        assert result["shown"] is True
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_bad_payload_is_refused_at_once() -> None:
    handler = handler_with(lambda *args, **kwargs: None)
    try:
        handler.show_message_async({"message": "hi", "severity": "bad"}, False, lambda shown, text: None)
    except RuntimeError as error:
        assert "invalid_severity" in str(error)
    else:
        raise AssertionError("a bad payload must be refused straight away")


class FakeWebSocket:
    def __init__(self, commands: list[dict]) -> None:
        self.commands = list(commands)
        self.sent: list[tuple[float, dict]] = []

    def receive_json(self, timeout=None) -> dict:
        return self.commands.pop(0)

    def send_json(self, payload: dict) -> None:
        self.sent.append((time.monotonic(), payload))


class NoGuiLockScreen:
    def start(self): pass
    def stop(self): pass
    def hide_for_session(self): pass
    def show_locked(self, reason=None): pass
    def show_expiration_grace(self, remaining_seconds, final_warning=False): pass
    def set_connection_online(self, is_online): pass


def test_client_loop_is_not_blocked_and_acks_honestly() -> None:
    from client.client import SecretGroundsClient

    client = SecretGroundsClient(
        server_url="http://127.0.0.1:8000", pc_name="PC-TEST", heartbeat_interval=5, request_timeout=2,
        lock_screen=NoGuiLockScreen(), enable_lock_screen=False,  # type: ignore[arg-type]
    )
    slow_shown = Event()

    def builder(*args, on_shown=None, **kwargs):
        time.sleep(0.8)  # a slow pop-up
        on_shown(None)
        slow_shown.set()

    client.remote_controls = handler_with(builder)
    websocket = FakeWebSocket([
        {"command_id": "announce-1", "type": "show_announcement", "payload": PAYLOAD},
        {"command_id": "ping-1", "type": "ping", "payload": {}},
    ])
    started = time.monotonic()
    client.handle_command(websocket)  # the announcement
    client.handle_command(websocket)  # the ping, while the pop-up is still opening
    loop_time = time.monotonic() - started
    assert loop_time < 0.5, f"the command loop waited for the pop-up ({loop_time:.2f}s)"
    assert slow_shown.wait(3)
    time.sleep(0.1)
    acks = [(payload["command_id"], payload["status"], payload.get("message")) for _, payload in websocket.sent]
    assert acks == [
        ("announce-1", "received", "Command received"),
        ("ping-1", "received", "Command received"),
        ("ping-1", "completed", "pong"),
        ("announce-1", "completed", "message displayed"),  # only once it was on screen
    ], acks

    # A pop-up that fails gives a "failed" acknowledgement with the reason.
    def broken(*args, on_shown=None, **kwargs):
        raise RuntimeError("no display")

    client.remote_controls = handler_with(broken)
    websocket = FakeWebSocket([{"command_id": "message-1", "type": "show_message", "payload": PAYLOAD}])
    client.handle_command(websocket)
    deadline = time.monotonic() + 3
    while len(websocket.sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert websocket.sent[-1][1] == {"command_id": "message-1", "status": "failed",
                                     "message": "the pop-up couldn't open: no display"}, websocket.sent

    # A bad payload is still refused at once, in the command loop.
    websocket = FakeWebSocket([{"command_id": "bad-1", "type": "show_message", "payload": {"message": "x", "severity": "nope"}}])
    client.handle_command(websocket)
    assert websocket.sent[-1][1]["status"] == "failed" and websocket.sent[-1][1]["message"] == "invalid_severity"


def main() -> None:
    test_shown_only_when_the_window_reports_it()
    test_window_error_is_a_failure_with_the_reason()
    test_reported_failure_from_inside_the_builder()
    test_no_answer_in_time_is_a_failure()
    test_only_the_first_result_counts()
    test_forced_failure_only_in_test_mode()
    test_bad_payload_is_refused_at_once()
    test_client_loop_is_not_blocked_and_acks_honestly()
    print("Client pop-up acknowledgement smoke tests passed.")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)
