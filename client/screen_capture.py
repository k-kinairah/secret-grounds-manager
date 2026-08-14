"""PC screen-monitoring capture prototype.

Dedicated background worker, deliberately isolated from Station Assignment,
registration, heartbeat, the command WebSocket, session/timer handling, and
the Tkinter lock screen -- it owns nothing those systems depend on and
never blocks the main thread. It takes the runtime-resolved pc_name exactly
as the caller (client/client.py) already resolved it once at startup; it
never calls station_resolver itself and never touches config.json.

mss and Pillow (server/requirements.txt) are imported lazily inside
capture_primary_display()/encode_frame() rather than at module load time,
so a client build without screen-monitoring dependencies installed still
starts normally with ENABLE_SCREEN_MONITORING left at its default of False
-- only actually enabling monitoring can ever require them.
"""

from __future__ import annotations

from threading import Event, Thread
from typing import Any, Callable
from urllib.parse import quote

from client.websocket_transport import SimpleWebSocketClient


RECONNECT_INITIAL_SECONDS = 2
RECONNECT_MAX_SECONDS = 30
SUMMARY_LOG_EVERY_N_FRAMES = 20

CaptureFn = Callable[[], Any]  # returns a PIL.Image.Image
WebSocketFactory = Callable[[str, int], SimpleWebSocketClient]
LogFn = Callable[[str], None]


def capture_primary_display() -> Any:
    """Grab the primary display only, as a PIL Image. Never touches disk."""
    import mss
    from PIL import Image

    with mss.mss() as screen:
        # monitors[0] is the union of every display; monitors[1] is the
        # first physical monitor. Capturing only [1] is what keeps this
        # prototype to "primary display only," per scope.
        primary_monitor = screen.monitors[1]
        raw_frame = screen.grab(primary_monitor)
        return Image.frombytes("RGB", raw_frame.size, raw_frame.rgb)


def encode_frame(image: Any, max_width: int, max_height: int, quality: int) -> bytes:
    """Resize (aspect-ratio preserved, never upscaled, no letterboxing) and
    JPEG-encode a captured frame. Returns bytes only -- never writes to disk."""
    import io

    from PIL import Image

    resized = image.copy()
    resized.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    resized.convert("RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


class ScreenCaptureWorker:
    """Runs one capture-and-upload cycle roughly every capture_interval_seconds
    on its own daemon thread. At most one thread/socket is ever active at a
    time -- start() is a no-op while a previous thread is still alive, and
    the connect/capture/send/reconnect cycle is fully serial.

    Frames are never queued: each cycle captures fresh, sends it, and moves
    on. If the server is unreachable the frame captured for that cycle is
    simply discarded (never buffered), so a prolonged outage costs no more
    memory than a single frame at any moment. Backoff between reconnect
    attempts is bounded (RECONNECT_INITIAL_SECONDS..RECONNECT_MAX_SECONDS)
    and independent of client.py's own reconnect_interval_seconds -- this
    worker never touches the command WebSocket or its retry state.
    """

    def __init__(
        self,
        server_url: str,
        pc_name: str,
        request_timeout: int,
        capture_interval_seconds: int,
        max_width: int,
        max_height: int,
        jpeg_quality: int,
        stop_event: Event | None = None,
        capture_fn: CaptureFn = capture_primary_display,
        websocket_factory: WebSocketFactory | None = None,
        log: LogFn = print,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.pc_name = pc_name
        self.request_timeout = request_timeout
        self.capture_interval_seconds = max(1, capture_interval_seconds)
        self.max_width = max_width
        self.max_height = max_height
        self.jpeg_quality = jpeg_quality
        self.stop_event = stop_event if stop_event is not None else Event()
        self._capture_fn = capture_fn
        self._websocket_factory = websocket_factory or (
            lambda url, timeout: SimpleWebSocketClient(url=url, timeout=timeout)
        )
        self._log = log
        self._thread: Thread | None = None
        self._sequence = 0
        self._accepted_since_summary = 0

    @property
    def websocket_url(self) -> str:
        websocket_base_url = self.server_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        return f"{websocket_base_url}/ws/screens/{quote(self.pc_name)}"

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = Thread(target=self._run, name="secret-grounds-screen-capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stop_event.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        backoff_seconds = RECONNECT_INITIAL_SECONDS
        while not self.stop_event.is_set():
            try:
                self._capture_loop()
                backoff_seconds = RECONNECT_INITIAL_SECONDS
            except (ConnectionError, OSError, ValueError) as error:
                self._log(f"Screen capture WebSocket disconnected: {error}")
            except Exception as error:
                self._log(f"Unexpected error in screen capture loop: {error}")

            if self.stop_event.is_set():
                return

            self._log(f"Screen capture reconnecting in {backoff_seconds}s.")
            if self.stop_event.wait(backoff_seconds):
                return
            backoff_seconds = min(backoff_seconds * 2, RECONNECT_MAX_SECONDS)

    def _capture_loop(self) -> None:
        with self._websocket_factory(self.websocket_url, self.request_timeout) as websocket:
            self._log(f"Screen capture WebSocket connected: pc_name={self.pc_name}")
            while not self.stop_event.is_set():
                self._send_one_frame(websocket)
                if self.stop_event.wait(self.capture_interval_seconds):
                    return

    def _send_one_frame(self, websocket: SimpleWebSocketClient) -> None:
        try:
            image = self._capture_fn()
            frame_bytes = encode_frame(image, self.max_width, self.max_height, self.jpeg_quality)
        except Exception as error:
            # Capture/encode failures never take down the loop or the
            # connection -- just skip this cycle and try again next time.
            self._log(f"Screen capture failed to produce a frame: {error}")
            return

        self._sequence += 1
        websocket.send_json({"type": "frame_meta", "sequence": self._sequence})
        websocket.send_bytes(frame_bytes)

        # Bounded wait: a hung/unresponsive server must never stall this
        # thread forever waiting for an ack (see SimpleWebSocketClient.
        # receive_json's timeout parameter). A timeout here is treated
        # exactly like any other disconnect -- caught by _run()'s retry
        # loop, not raised out of this method.
        response = websocket.receive_json(timeout=self.request_timeout)
        response_type = response.get("type") if isinstance(response, dict) else None
        if response_type == "frame_rejected":
            self._log(f"Screen frame rejected: {response.get('reason')}")
        elif response_type != "frame_accepted":
            self._log(f"Unexpected screen capture response: {response}")
        else:
            self._accepted_since_summary += 1
            if self._accepted_since_summary >= SUMMARY_LOG_EVERY_N_FRAMES:
                self._log(
                    f"Screen capture: {self._accepted_since_summary} frames accepted "
                    f"(pc_name={self.pc_name})."
                )
                self._accepted_since_summary = 0
