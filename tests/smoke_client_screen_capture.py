"""Client-side PC screen-monitoring capture prototype smoke tests.

Deliberately never calls capture_primary_display() (real mss screen grab)
or runs an unbounded loop -- capture_fn and the WebSocket are always
injected fakes, and every worker thread this file starts is joined with a
bounded timeout before the test function returns.
"""

import io
import json
import sys
import tempfile
import threading
import time
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


class FakeCaptureWebSocket:
    """Fake SimpleWebSocketClient for the /ws/screens/{pc_name} channel.

    Records send_json/send_bytes calls into one ordered `events` list so
    tests can assert relative ordering (frame_meta before binary bytes),
    and replays a scripted list of server responses from receive_json().
    """

    def __init__(self, responses: list[dict] | None = None, fail_on_connect: bool = False) -> None:
        self.fail_on_connect = fail_on_connect
        self.events: list[tuple[str, object]] = []
        self.responses = list(responses) if responses is not None else [{"type": "frame_accepted"}]
        self.closed = False

    def __enter__(self) -> "FakeCaptureWebSocket":
        if self.fail_on_connect:
            raise ConnectionError("simulated connect failure")
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.closed = True

    def send_json(self, payload: dict) -> None:
        self.events.append(("json", payload))

    def send_bytes(self, payload: bytes) -> None:
        self.events.append(("bytes", payload))

    def receive_json(self, timeout: float | None = None) -> dict:
        if self.responses:
            return self.responses.pop(0)
        return {"type": "frame_accepted"}


def make_fake_image(width: int = 64, height: int = 48):
    from PIL import Image

    return Image.new("RGB", (width, height), color=(12, 34, 56))


def test_disabled_monitoring_creates_no_worker() -> None:
    import client.client as client_module

    original = client_module.ENABLE_SCREEN_MONITORING
    client_module.ENABLE_SCREEN_MONITORING = False
    try:
        client = client_module.SecretGroundsClient(
            server_url="http://127.0.0.1:8000",
            pc_name="PC-CAP-1",
            heartbeat_interval=5,
            request_timeout=2,
            lock_screen=NoGuiLockScreen(),  # type: ignore[arg-type]
            enable_lock_screen=False,
        )
        assert client.screen_capture_worker is None
    finally:
        client_module.ENABLE_SCREEN_MONITORING = original


def test_enabled_monitoring_builds_worker_with_runtime_pc_name() -> None:
    import client.client as client_module

    original = client_module.ENABLE_SCREEN_MONITORING
    client_module.ENABLE_SCREEN_MONITORING = True
    try:
        client = client_module.SecretGroundsClient(
            server_url="http://127.0.0.1:8000",
            pc_name="PC-CAP-2",
            heartbeat_interval=5,
            request_timeout=2,
            lock_screen=NoGuiLockScreen(),  # type: ignore[arg-type]
            enable_lock_screen=False,
        )
        worker = client.screen_capture_worker
        assert worker is not None
        assert not worker.is_running()
        # Uses the same runtime-resolved pc_name the rest of the client was
        # constructed with -- never re-resolves the station itself.
        assert worker.pc_name == "PC-CAP-2" == client.pc_name
        assert worker.websocket_url == "ws://127.0.0.1:8000/ws/screens/PC-CAP-2"
    finally:
        client_module.ENABLE_SCREEN_MONITORING = original


def test_enabled_monitoring_starts_exactly_one_thread() -> None:
    from client.screen_capture import ScreenCaptureWorker

    worker = ScreenCaptureWorker(
        server_url="http://127.0.0.1:8000",
        pc_name="PC-CAP-3",
        request_timeout=2,
        capture_interval_seconds=1,
        max_width=480,
        max_height=270,
        jpeg_quality=50,
        capture_fn=make_fake_image,
        websocket_factory=lambda url, timeout: FakeCaptureWebSocket(),
    )
    try:
        worker.start()
        assert worker.is_running()
        first_thread = worker._thread
        # A redundant start() (e.g. a caller invoking it twice) must not
        # spin up a second thread/socket.
        worker.start()
        assert worker._thread is first_thread
    finally:
        worker.stop()
        worker._thread.join(timeout=5)
        assert not worker._thread.is_alive()


def test_runtime_pc_name_never_written_to_config_json(temp_directory: str) -> None:
    from client.config import save_client_config
    from client.screen_capture import ScreenCaptureWorker

    config_path = Path(temp_directory) / "config.json"
    save_client_config(
        {
            "server_host": "127.0.0.1",
            "server_port": 18001,
            "pc_name": "",
            "auto_connect": True,
        },
        config_path,
        require_pc_name=False,
    )
    before = config_path.read_text(encoding="utf-8")

    worker = ScreenCaptureWorker(
        server_url="http://127.0.0.1:18001",
        # Simulates a diskless station whose pc_name was resolved at
        # runtime via /stations/resolve -- config.json above deliberately
        # still has a blank pc_name, matching automatic mode.
        pc_name="PC-07",
        request_timeout=2,
        capture_interval_seconds=1,
        max_width=480,
        max_height=270,
        jpeg_quality=50,
        capture_fn=make_fake_image,
        websocket_factory=lambda url, timeout: FakeCaptureWebSocket(),
    )
    fake_ws = FakeCaptureWebSocket()
    worker._send_one_frame(fake_ws)
    worker._send_one_frame(fake_ws)

    after = config_path.read_text(encoding="utf-8")
    assert after == before
    assert json.loads(after)["pc_name"] == ""


def test_frame_resized_without_changing_aspect_ratio() -> None:
    from client.screen_capture import encode_frame
    from PIL import Image

    wide_image = Image.new("RGB", (1920, 1080), color=(10, 20, 30))
    encoded = encode_frame(wide_image, max_width=480, max_height=270, quality=50)
    decoded = Image.open(io.BytesIO(encoded))
    assert decoded.width <= 480
    assert decoded.height <= 270
    original_ratio = 1920 / 1080
    resized_ratio = decoded.width / decoded.height
    assert abs(original_ratio - resized_ratio) < 0.02

    tall_image = Image.new("RGB", (1080, 1920), color=(30, 20, 10))
    tall_encoded = encode_frame(tall_image, max_width=480, max_height=270, quality=50)
    tall_decoded = Image.open(io.BytesIO(tall_encoded))
    assert tall_decoded.width <= 480
    assert tall_decoded.height <= 270
    tall_original_ratio = 1080 / 1920
    tall_resized_ratio = tall_decoded.width / tall_decoded.height
    assert abs(tall_original_ratio - tall_resized_ratio) < 0.02


def test_jpeg_output_is_valid() -> None:
    from client.screen_capture import encode_frame
    from PIL import Image

    encoded = encode_frame(make_fake_image(800, 600), max_width=480, max_height=270, quality=50)
    assert encoded[:3] == b"\xff\xd8\xff"
    decoded = Image.open(io.BytesIO(encoded))
    decoded.load()
    assert decoded.format == "JPEG"


def test_sequence_metadata_sent_before_binary_bytes() -> None:
    from client.screen_capture import ScreenCaptureWorker

    worker = ScreenCaptureWorker(
        server_url="http://127.0.0.1:8000",
        pc_name="PC-CAP-4",
        request_timeout=2,
        capture_interval_seconds=1,
        max_width=480,
        max_height=270,
        jpeg_quality=50,
        capture_fn=make_fake_image,
        websocket_factory=lambda url, timeout: FakeCaptureWebSocket(),
    )
    fake_ws = FakeCaptureWebSocket()
    worker._send_one_frame(fake_ws)

    assert len(fake_ws.events) == 2
    kind, payload = fake_ws.events[0]
    assert kind == "json"
    assert payload == {"type": "frame_meta", "sequence": 1}
    kind, payload = fake_ws.events[1]
    assert kind == "bytes"
    assert payload[:3] == b"\xff\xd8\xff"


def test_rejected_frame_does_not_crash_capture_loop() -> None:
    from client.screen_capture import ScreenCaptureWorker

    log_messages: list[str] = []
    worker = ScreenCaptureWorker(
        server_url="http://127.0.0.1:8000",
        pc_name="PC-CAP-5",
        request_timeout=2,
        capture_interval_seconds=1,
        max_width=480,
        max_height=270,
        jpeg_quality=50,
        capture_fn=make_fake_image,
        websocket_factory=lambda url, timeout: FakeCaptureWebSocket(),
        log=log_messages.append,
    )
    fake_ws = FakeCaptureWebSocket(responses=[{"type": "frame_rejected", "reason": "frame too large"}])
    worker._send_one_frame(fake_ws)  # must not raise
    assert any("rejected" in message.lower() for message in log_messages)

    # The worker is still usable for the next cycle after a rejection.
    fake_ws.responses = [{"type": "frame_accepted"}]
    worker._send_one_frame(fake_ws)  # must not raise
    assert worker._sequence == 2


def test_reconnection_does_not_create_duplicate_threads() -> None:
    from client.screen_capture import ScreenCaptureWorker

    factory_calls: list[int] = []

    def flaky_factory(url: str, timeout: int) -> FakeCaptureWebSocket:
        factory_calls.append(1)
        if len(factory_calls) == 1:
            return FakeCaptureWebSocket(fail_on_connect=True)
        return FakeCaptureWebSocket(responses=[{"type": "frame_accepted"}] * 10)

    worker = ScreenCaptureWorker(
        server_url="http://127.0.0.1:8000",
        pc_name="PC-CAP-6",
        request_timeout=2,
        capture_interval_seconds=1,
        max_width=480,
        max_height=270,
        jpeg_quality=50,
        capture_fn=make_fake_image,
        websocket_factory=flaky_factory,
        log=lambda message: None,
    )
    try:
        worker.start()
        first_thread = worker._thread
        deadline = time.monotonic() + 8
        while len(factory_calls) < 2 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert len(factory_calls) >= 2
        # Still the exact same thread object that reconnected internally --
        # never a second thread spun up for the retry.
        assert worker._thread is first_thread
        assert threading.active_count() >= 1
    finally:
        worker.stop()
        first_thread.join(timeout=5)
        assert not first_thread.is_alive()


def test_old_frames_are_not_queued_while_disconnected() -> None:
    from client.screen_capture import ScreenCaptureWorker

    capture_calls: list[int] = []

    def counting_capture_fn():
        capture_calls.append(1)
        return make_fake_image()

    worker = ScreenCaptureWorker(
        server_url="http://127.0.0.1:8000",
        pc_name="PC-CAP-7",
        request_timeout=2,
        capture_interval_seconds=1,
        max_width=480,
        max_height=270,
        jpeg_quality=50,
        capture_fn=counting_capture_fn,
        websocket_factory=lambda url, timeout: FakeCaptureWebSocket(fail_on_connect=True),
        log=lambda message: None,
    )
    try:
        worker.start()
        # Give the worker a couple of failed-reconnect cycles.
        time.sleep(1.5)
        # Nothing is ever captured before a connection succeeds -- the
        # server being unreachable means frames are never even produced,
        # let alone buffered.
        assert capture_calls == []
    finally:
        worker.stop()
        worker._thread.join(timeout=5)
        assert not worker._thread.is_alive()


def test_clean_shutdown_stops_the_worker() -> None:
    from client.screen_capture import ScreenCaptureWorker

    worker = ScreenCaptureWorker(
        server_url="http://127.0.0.1:8000",
        pc_name="PC-CAP-8",
        request_timeout=2,
        capture_interval_seconds=1,
        max_width=480,
        max_height=270,
        jpeg_quality=50,
        capture_fn=make_fake_image,
        websocket_factory=lambda url, timeout: FakeCaptureWebSocket(),
        log=lambda message: None,
    )
    worker.start()
    assert worker.is_running()
    worker.stop()
    worker._thread.join(timeout=5)
    assert not worker.is_running()
    assert worker._thread.is_alive() is False
    # A stopped worker's daemon thread must never block process exit --
    # daemon=True is asserted directly since we cannot exit the interpreter
    # from inside a test.
    assert worker._thread.daemon is True


def test_receive_json_default_call_still_blocks_and_returns_normally() -> None:
    """Regression guard: receive_json() with no timeout argument (what
    client.py's command channel calls) must behave exactly as before --
    only an explicit timeout=... opts into the new bounded-wait path."""
    import socket

    from client.websocket_transport import SimpleWebSocketClient

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    client_socket = socket.create_connection(("127.0.0.1", port), timeout=2)
    server_socket, _ = listener.accept()
    listener.close()
    try:
        ws_client = SimpleWebSocketClient(url=f"ws://127.0.0.1:{port}/", timeout=2)
        ws_client._socket = client_socket

        # A minimal unmasked text frame carrying {"type": "ping"}.
        payload = b'{"type": "ping"}'
        frame = bytes([0x81, len(payload)]) + payload
        server_socket.sendall(frame)

        message = ws_client.receive_json()
        assert message == {"type": "ping"}
    finally:
        client_socket.close()
        server_socket.close()


def test_receive_json_with_timeout_does_not_hang_forever() -> None:
    """Proves the fix for requirement 14: a server that accepts a frame but
    never responds must not stall the caller indefinitely."""
    import socket

    from client.websocket_transport import SimpleWebSocketClient

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    client_socket = socket.create_connection(("127.0.0.1", port), timeout=2)
    server_socket, _ = listener.accept()
    listener.close()
    try:
        ws_client = SimpleWebSocketClient(url=f"ws://127.0.0.1:{port}/", timeout=2)
        ws_client._socket = client_socket
        # server_socket deliberately never sends a reply.

        started = time.monotonic()
        try:
            ws_client.receive_json(timeout=0.3)
            raise AssertionError("expected a timeout-triggered ConnectionError")
        except ConnectionError:
            pass
        elapsed = time.monotonic() - started
        assert elapsed < 2.0, f"receive_json(timeout=0.3) took {elapsed}s -- did not bound the wait"
    finally:
        client_socket.close()
        server_socket.close()


def main() -> None:
    tests_without_temp_dir = [
        test_disabled_monitoring_creates_no_worker,
        test_enabled_monitoring_builds_worker_with_runtime_pc_name,
        test_enabled_monitoring_starts_exactly_one_thread,
        test_frame_resized_without_changing_aspect_ratio,
        test_jpeg_output_is_valid,
        test_sequence_metadata_sent_before_binary_bytes,
        test_rejected_frame_does_not_crash_capture_loop,
        test_reconnection_does_not_create_duplicate_threads,
        test_old_frames_are_not_queued_while_disconnected,
        test_clean_shutdown_stops_the_worker,
        test_receive_json_default_call_still_blocks_and_returns_normally,
        test_receive_json_with_timeout_does_not_hang_forever,
    ]
    for test in tests_without_temp_dir:
        test()
        print(f"{test.__name__} passed.")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_runtime_pc_name_never_written_to_config_json(temp_directory)
    print("test_runtime_pc_name_never_written_to_config_json passed.")

    print("Client screen capture smoke tests passed.")


if __name__ == "__main__":
    main()
