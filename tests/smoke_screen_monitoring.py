"""Screen monitoring backend foundation smoke tests.

Covers the diskless-safe, one-PC prototype: the dedicated /ws/screens/{pc_name}
upload channel, the in-memory-only latest-frame cache, and the read-only
GET /monitor/pcs + GET /monitor/pcs/{pc_name}/frame endpoints. Deliberately
does not touch Station Assignment, heartbeat, or the existing /ws/{pc_name}
command channel -- those are covered by their own smoke tests.
"""

import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


# Minimal valid magic-byte payloads. Content past the signature is
# irrelevant to sniff_image_content_type -- these are not real, decodable
# images, only enough bytes to exercise the server's format detection.
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 60
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 40


class FakeScreenWebSocket:
    """Stand-in for a Starlette WebSocket, driven by a queue of raw ASGI
    receive messages. Mirrors the accept()/receive()/send_json() surface
    server/main.py's screen_upload_websocket actually calls."""

    def __init__(self, incoming: list[dict]) -> None:
        self._incoming = list(incoming)
        self.accepted = False
        self.closed_code: int | None = None
        self.sent: list[dict] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.closed_code = code

    async def receive(self) -> dict:
        if not self._incoming:
            return {"type": "websocket.disconnect", "code": 1000}
        return self._incoming.pop(0)

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


def binary_message(data: bytes) -> dict:
    return {"type": "websocket.receive", "bytes": data}


def text_message(payload: dict) -> dict:
    return {"type": "websocket.receive", "text": json.dumps(payload)}


def create_env(database_path: Path):
    import os

    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module
    from server.schemas import PCRecord
    from server.time_utils import current_utc_time

    main_module.pc_registry.clear()
    main_module.screen_monitor_service._frames.clear()
    main_module.screen_monitor_service._last_upload_monotonic.clear()
    main_module.screen_monitor_service._owner_tokens.clear()

    main_module.pc_registry["PC-01"] = PCRecord(
        pc_name="PC-01",
        ip_address="192.168.0.10",
        status="online",
        last_seen=current_utc_time(),
    )

    from server.schemas import UserCreateRequest

    cashier_public = main_module.user_repository.create_user(
        UserCreateRequest(username="sm-cashier", display_name="SM Cashier", password="password123", role="cashier")
    )
    cashier = main_module.user_repository.get_user(cashier_public.user_id)
    assert cashier is not None
    return main_module, cashier


def test_valid_station_can_upload_binary_frame(temp_directory: str) -> None:
    main_module, cashier = create_env(Path(temp_directory) / "valid-upload.sqlite3")

    fake_ws = FakeScreenWebSocket([binary_message(JPEG_BYTES)])
    asyncio.run(main_module.screen_upload_websocket(fake_ws, "PC-01"))  # type: ignore[arg-type]

    assert fake_ws.accepted
    assert fake_ws.closed_code is None
    assert [message["type"] for message in fake_ws.sent] == ["frame_accepted"]
    # The websocket handler clears the frame on disconnect (cleanup path),
    # so acceptance is proven via the frame_accepted response above rather
    # than by inspecting post-disconnect cache state.
    assert main_module.screen_monitor_service.get_frame("PC-01") is None


def test_invalid_station_is_rejected(temp_directory: str) -> None:
    main_module, _ = create_env(Path(temp_directory) / "invalid-station.sqlite3")

    fake_ws = FakeScreenWebSocket([binary_message(JPEG_BYTES)])
    asyncio.run(main_module.screen_upload_websocket(fake_ws, "PC-99-UNKNOWN"))  # type: ignore[arg-type]

    assert not fake_ws.accepted
    assert fake_ws.closed_code == 1008
    assert fake_ws.sent == []
    assert main_module.screen_monitor_service.get_frame("PC-99-UNKNOWN") is None


def test_oversized_frame_is_rejected(temp_directory: str) -> None:
    main_module, _ = create_env(Path(temp_directory) / "oversized.sqlite3")
    from server.settings import SCREEN_MAX_FRAME_BYTES

    oversized = b"\xff\xd8\xff\xe0" + b"\x00" * (SCREEN_MAX_FRAME_BYTES + 10)
    fake_ws = FakeScreenWebSocket([binary_message(oversized)])
    asyncio.run(main_module.screen_upload_websocket(fake_ws, "PC-01"))  # type: ignore[arg-type]

    assert fake_ws.accepted
    assert [message["type"] for message in fake_ws.sent] == ["frame_rejected"]
    assert main_module.screen_monitor_service.get_frame("PC-01") is None


def test_upload_rate_limit_works(temp_directory: str) -> None:
    main_module, _ = create_env(Path(temp_directory) / "rate-limit.sqlite3")
    service = main_module.screen_monitor_service

    first = service.store_frame("PC-01", "image/jpeg", JPEG_BYTES)
    assert first.byte_size == len(JPEG_BYTES)

    try:
        service.store_frame("PC-01", "image/jpeg", JPEG_BYTES)
        raise AssertionError("expected a 429 rate-limit rejection")
    except HTTPException as error:
        assert error.status_code == 429

    # The first frame must still be the one on record -- a rejected
    # upload never overwrites the last accepted frame.
    assert service.get_frame("PC-01").byte_size == len(JPEG_BYTES)


def test_latest_frame_replaces_previous(temp_directory: str) -> None:
    main_module, _ = create_env(Path(temp_directory) / "latest-replaces.sqlite3")
    from server.settings import SCREEN_MIN_FRAME_INTERVAL_MS

    service = main_module.screen_monitor_service
    service.store_frame("PC-01", "image/jpeg", JPEG_BYTES, sequence=1)
    time.sleep((SCREEN_MIN_FRAME_INTERVAL_MS / 1000) + 0.05)
    service.store_frame("PC-01", "image/webp", WEBP_BYTES, sequence=2)

    latest = service.get_frame("PC-01")
    assert latest.content_type == "image/webp"
    assert latest.data == WEBP_BYTES
    assert latest.sequence == 2


def test_frames_are_held_only_in_memory(temp_directory: str) -> None:
    main_module, _ = create_env(Path(temp_directory) / "memory-only.sqlite3")
    from server.database import get_connection

    main_module.screen_monitor_service.store_frame("PC-01", "image/jpeg", JPEG_BYTES)

    with get_connection() as connection:
        table_names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    # No screen/frame/image table exists anywhere in the schema -- frames
    # never touch SQLite at all.
    assert not any("frame" in name.lower() or "screen" in name.lower() for name in table_names)


def test_frame_endpoint_returns_cache_control_no_store(temp_directory: str) -> None:
    main_module, cashier = create_env(Path(temp_directory) / "cache-control.sqlite3")
    main_module.screen_monitor_service.store_frame("PC-01", "image/jpeg", JPEG_BYTES)

    response = main_module.get_monitored_pc_frame("PC-01", cashier)
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate"
    assert response.media_type == "image/jpeg"
    assert response.body == JPEG_BYTES


def test_missing_frame_returns_expected_error(temp_directory: str) -> None:
    main_module, cashier = create_env(Path(temp_directory) / "missing-frame.sqlite3")

    try:
        main_module.get_monitored_pc_frame("PC-01", cashier)
        raise AssertionError("expected a 404 for a station with no cached frame")
    except HTTPException as error:
        assert error.status_code == 404


def test_status_changes_from_live_to_stale(temp_directory: str) -> None:
    main_module, cashier = create_env(Path(temp_directory) / "live-to-stale.sqlite3")
    from datetime import timedelta

    from server.settings import SCREEN_STALE_AFTER_SECONDS
    from server.time_utils import current_utc_time

    service = main_module.screen_monitor_service
    service.store_frame("PC-01", "image/jpeg", JPEG_BYTES)

    live_statuses = main_module.list_monitored_pcs(cashier)
    live_status = next(status for status in live_statuses if status.pc_name == "PC-01")
    assert live_status.has_frame is True
    assert live_status.frame_status == "live"

    # Backdate the cached frame's received_at past the staleness window
    # without waiting in real time.
    stored_frame = service.get_frame("PC-01")
    stored_frame.received_at = current_utc_time() - timedelta(seconds=SCREEN_STALE_AFTER_SECONDS + 1)

    stale_statuses = main_module.list_monitored_pcs(cashier)
    stale_status = next(status for status in stale_statuses if status.pc_name == "PC-01")
    assert stale_status.has_frame is True
    assert stale_status.frame_status == "stale"


def set_pc_status(main_module, pc_name: str, status: str) -> None:
    from server.schemas import PCRecord
    from server.time_utils import current_utc_time

    existing = main_module.pc_registry[pc_name]
    main_module.pc_registry[pc_name] = PCRecord(
        pc_name=pc_name,
        ip_address=existing.ip_address,
        status=status,
        last_seen=current_utc_time(),
        mac_address=existing.mac_address,
    )


def test_online_station_with_no_frame_reports_no_image(temp_directory: str) -> None:
    main_module, cashier = create_env(Path(temp_directory) / "online-no-frame.sqlite3")

    statuses = main_module.list_monitored_pcs(cashier)
    status = next(item for item in statuses if item.pc_name == "PC-01")
    assert status.has_frame is False
    assert status.frame_status == "no_image"
    assert status.frame_received_at is None
    assert status.frame_age_seconds is None


def test_offline_station_with_no_frame_reports_offline(temp_directory: str) -> None:
    """Offline must take precedence over no_image: a station that went
    offline (whether or not it ever uploaded, or its frame was already
    cleared) is 'offline', never 'no_image'."""
    main_module, cashier = create_env(Path(temp_directory) / "offline-no-frame.sqlite3")
    set_pc_status(main_module, "PC-01", "offline")

    statuses = main_module.list_monitored_pcs(cashier)
    status = next(item for item in statuses if item.pc_name == "PC-01")
    assert status.has_frame is False
    assert status.frame_status == "offline"
    assert status.frame_received_at is None
    assert status.frame_age_seconds is None


def test_offline_station_with_cached_frame_reports_offline(temp_directory: str) -> None:
    main_module, cashier = create_env(Path(temp_directory) / "offline-cached-frame.sqlite3")
    main_module.screen_monitor_service.store_frame("PC-01", "image/jpeg", JPEG_BYTES)
    set_pc_status(main_module, "PC-01", "offline")

    offline_statuses = main_module.list_monitored_pcs(cashier)
    offline_status = next(item for item in offline_statuses if item.pc_name == "PC-01")
    assert offline_status.frame_status == "offline"
    assert offline_status.has_frame is True


def test_unsupported_content_type_is_rejected(temp_directory: str) -> None:
    main_module, _ = create_env(Path(temp_directory) / "unsupported-type.sqlite3")

    garbage = b"not-an-image" + b"\x00" * 40
    fake_ws = FakeScreenWebSocket([binary_message(garbage)])
    asyncio.run(main_module.screen_upload_websocket(fake_ws, "PC-01"))  # type: ignore[arg-type]

    assert [message["type"] for message in fake_ws.sent] == ["frame_rejected"]
    assert main_module.screen_monitor_service.get_frame("PC-01") is None


def test_frame_meta_sequence_is_applied_to_next_binary_frame(temp_directory: str) -> None:
    main_module, _ = create_env(Path(temp_directory) / "sequence-meta.sqlite3")

    fake_ws = FakeScreenWebSocket(
        [
            text_message({"type": "frame_meta", "sequence": 7}),
            binary_message(JPEG_BYTES),
        ]
    )
    asyncio.run(main_module.screen_upload_websocket(fake_ws, "PC-01"))  # type: ignore[arg-type]

    assert fake_ws.sent[-1]["type"] == "frame_accepted"
    assert fake_ws.sent[-1]["sequence"] == 7


class GatedFakeScreenWebSocket(FakeScreenWebSocket):
    """Like FakeScreenWebSocket, but blocks on an asyncio.Event instead of
    disconnecting immediately once its queued messages run out -- lets a
    test hold one connection "open" while driving a second, real
    screen_upload_websocket() coroutine concurrently, then release the
    first on command. Only used to prove the reconnect race is fixed at
    the actual handler level (server/main.py), not just the service."""

    def __init__(self, incoming: list[dict], disconnect_gate) -> None:
        super().__init__(incoming)
        self._disconnect_gate = disconnect_gate

    async def receive(self) -> dict:
        if not self._incoming:
            await self._disconnect_gate.wait()
            return {"type": "websocket.disconnect", "code": 1000}
        return self._incoming.pop(0)


def test_superseded_connection_cannot_overwrite_newer_frame(temp_directory: str) -> None:
    """The exact reconnect race this feature fixes, at the ScreenMonitorService
    level: an old connection's late frame or disconnect cleanup must never
    be able to clobber a newer connection's already-uploaded frame."""
    from server.settings import SCREEN_MIN_FRAME_INTERVAL_MS

    main_module, _ = create_env(Path(temp_directory) / "supersede.sqlite3")
    service = main_module.screen_monitor_service
    rate_limit_gap = (SCREEN_MIN_FRAME_INTERVAL_MS / 1000) + 0.05

    # Connection A connects and uploads frame A.
    token_a = service.acquire_connection("PC-01")
    frame_a = service.store_frame_if_owner("PC-01", token_a, "image/jpeg", JPEG_BYTES, sequence=1)
    assert service.get_frame("PC-01") is frame_a

    # Connection B connects (superseding A) and uploads frame B.
    time.sleep(rate_limit_gap)
    token_b = service.acquire_connection("PC-01")
    assert token_b != token_a
    frame_b = service.store_frame_if_owner("PC-01", token_b, "image/webp", WEBP_BYTES, sequence=2)
    assert service.get_frame("PC-01") is frame_b

    # Connection A disconnects -- its cleanup must be a no-op now.
    service.release_connection("PC-01", token_a)
    assert service.get_frame("PC-01") is frame_b, "an old connection's disconnect must never clear a newer frame"

    # A late/stray frame from the superseded connection A is rejected,
    # never overwrites frame B.
    try:
        service.store_frame_if_owner("PC-01", token_a, "image/jpeg", JPEG_BYTES, sequence=3)
        raise AssertionError("expected a 409 rejection for a superseded connection")
    except HTTPException as error:
        assert error.status_code == 409
    assert service.get_frame("PC-01") is frame_b

    # Connection B disconnects normally -- its own frame is cleared.
    service.release_connection("PC-01", token_b)
    assert service.get_frame("PC-01") is None


def test_offline_cleanup_force_clears_regardless_of_ownership(temp_directory: str) -> None:
    """mark_stale_pcs_offline's unconditional clear_frame() must still work
    even while a connection is (in the service's bookkeeping) the current
    owner -- offline always wins over connection ownership."""
    main_module, _ = create_env(Path(temp_directory) / "offline-force-clear.sqlite3")
    service = main_module.screen_monitor_service

    token = service.acquire_connection("PC-01")
    service.store_frame_if_owner("PC-01", token, "image/jpeg", JPEG_BYTES)
    assert service.get_frame("PC-01") is not None

    # Simulates mark_stale_pcs_offline's own call: unconditional, no token.
    service.clear_frame("PC-01")
    assert service.get_frame("PC-01") is None


def test_connections_for_different_pcs_are_independent(temp_directory: str) -> None:
    main_module, _ = create_env(Path(temp_directory) / "independent-pcs.sqlite3")
    from server.schemas import PCRecord
    from server.time_utils import current_utc_time

    main_module.pc_registry["PC-02"] = PCRecord(
        pc_name="PC-02", ip_address="192.168.0.11", status="online", last_seen=current_utc_time()
    )
    service = main_module.screen_monitor_service

    from server.settings import SCREEN_MIN_FRAME_INTERVAL_MS

    token_1 = service.acquire_connection("PC-01")
    token_2 = service.acquire_connection("PC-02")
    service.store_frame_if_owner("PC-01", token_1, "image/jpeg", JPEG_BYTES)
    frame_2 = service.store_frame_if_owner("PC-02", token_2, "image/webp", WEBP_BYTES)

    # A second connection superseding PC-01 must not affect PC-02 at all.
    time.sleep((SCREEN_MIN_FRAME_INTERVAL_MS / 1000) + 0.05)
    token_1b = service.acquire_connection("PC-01")
    service.store_frame_if_owner("PC-01", token_1b, "image/webp", WEBP_BYTES, sequence=9)
    assert service.get_frame("PC-02") is frame_2

    # PC-01's original (now-superseded) connection disconnecting must not
    # touch PC-02's frame or ownership.
    service.release_connection("PC-01", token_1)
    assert service.get_frame("PC-02") is frame_2
    assert service.get_frame("PC-01") is not None, "PC-01's current (token_1b) frame must survive token_1's release"

    # PC-02 disconnecting only clears PC-02.
    service.release_connection("PC-02", token_2)
    assert service.get_frame("PC-02") is None
    assert service.get_frame("PC-01") is not None


def test_handler_level_reconnect_race_is_safe(temp_directory: str) -> None:
    """End-to-end through the real screen_upload_websocket() coroutine
    (not just the service): an old connection left "open" past a newer
    connection's upload must not be able to clear the newer frame when it
    finally disconnects."""
    from server.settings import SCREEN_MIN_FRAME_INTERVAL_MS

    main_module, _ = create_env(Path(temp_directory) / "handler-race.sqlite3")

    async def run_race() -> None:
        disconnect_gate_a = asyncio.Event()
        ws_a = GatedFakeScreenWebSocket([binary_message(JPEG_BYTES)], disconnect_gate_a)
        task_a = asyncio.create_task(main_module.screen_upload_websocket(ws_a, "PC-01"))
        # Let connection A run until it has uploaded frame A and is
        # blocked waiting on the gate for its next message (i.e. still
        # "connected" from the server's point of view).
        for _ in range(3):
            await asyncio.sleep(0)
        assert ws_a.sent and ws_a.sent[-1]["type"] == "frame_accepted"

        # Stay clear of the per-pc_name upload rate limit before B uploads.
        await asyncio.sleep((SCREEN_MIN_FRAME_INTERVAL_MS / 1000) + 0.05)

        # Connection B connects and uploads frame B, also staying
        # "connected" (gated) afterward -- exactly the live scenario this
        # fix targets: B is now current, A is a stale, still-open socket.
        disconnect_gate_b = asyncio.Event()
        ws_b = GatedFakeScreenWebSocket([binary_message(WEBP_BYTES)], disconnect_gate_b)
        task_b = asyncio.create_task(main_module.screen_upload_websocket(ws_b, "PC-01"))
        for _ in range(3):
            await asyncio.sleep(0)
        assert ws_b.sent and ws_b.sent[-1]["type"] == "frame_accepted"
        frame_after_b = main_module.screen_monitor_service.get_frame("PC-01")
        assert frame_after_b is not None
        assert frame_after_b.content_type == "image/webp"

        # Now let connection A finally disconnect -- must not clear B's frame.
        disconnect_gate_a.set()
        await task_a
        frame_after_a_disconnects = main_module.screen_monitor_service.get_frame("PC-01")
        assert frame_after_a_disconnects is not None, "superseded connection A's disconnect must not clear B's frame"
        assert frame_after_a_disconnects.content_type == "image/webp"

        # Finally, B disconnecting normally does clear its own frame.
        disconnect_gate_b.set()
        await task_b
        assert main_module.screen_monitor_service.get_frame("PC-01") is None

    asyncio.run(run_race())


def main() -> None:
    tests = [
        test_valid_station_can_upload_binary_frame,
        test_invalid_station_is_rejected,
        test_oversized_frame_is_rejected,
        test_upload_rate_limit_works,
        test_latest_frame_replaces_previous,
        test_frames_are_held_only_in_memory,
        test_frame_endpoint_returns_cache_control_no_store,
        test_missing_frame_returns_expected_error,
        test_status_changes_from_live_to_stale,
        test_online_station_with_no_frame_reports_no_image,
        test_offline_station_with_no_frame_reports_offline,
        test_offline_station_with_cached_frame_reports_offline,
        test_unsupported_content_type_is_rejected,
        test_frame_meta_sequence_is_applied_to_next_binary_frame,
        test_superseded_connection_cannot_overwrite_newer_frame,
        test_offline_cleanup_force_clears_regardless_of_ownership,
        test_connections_for_different_pcs_are_independent,
        test_handler_level_reconnect_race_is_safe,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Screen monitoring smoke tests passed.")


if __name__ == "__main__":
    main()
