import time
from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException

from server.settings import (
    SCREEN_MAX_FRAME_BYTES,
    SCREEN_MIN_FRAME_INTERVAL_MS,
    SCREEN_STALE_AFTER_SECONDS,
)
from server.time_utils import current_utc_time


ALLOWED_FRAME_CONTENT_TYPES = {"image/jpeg", "image/webp"}


def sniff_image_content_type(data: bytes) -> str | None:
    """Identify a compressed frame's format from its magic bytes.

    Deliberately signature-based instead of trusting a client-supplied
    label: the wire format is a raw binary WebSocket message (see
    server/main.py's /ws/screens/{pc_name}), so the server itself is the
    only source of truth for what content type gets stored and served.
    """
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


@dataclass
class StoredFrame:
    pc_name: str
    content_type: str
    data: bytes
    byte_size: int
    received_at: datetime
    sequence: int | None


class ScreenMonitorService:
    """In-memory-only latest-frame cache for PC screen monitoring.

    Only the newest frame per pc_name is ever kept, and only in process
    memory -- there is deliberately no write path to SQLite, disk, logs,
    or backups anywhere in this class.
    """

    def __init__(self) -> None:
        self._frames: dict[str, StoredFrame] = {}
        self._last_upload_monotonic: dict[str, float] = {}

    def validate_upload(self, pc_name: str, content_type: str, byte_size: int) -> None:
        if content_type not in ALLOWED_FRAME_CONTENT_TYPES:
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported frame content type: {content_type}",
            )
        if byte_size > SCREEN_MAX_FRAME_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Frame exceeds maximum size of {SCREEN_MAX_FRAME_BYTES} bytes",
            )

        last_upload = self._last_upload_monotonic.get(pc_name)
        min_interval_seconds = SCREEN_MIN_FRAME_INTERVAL_MS / 1000
        if last_upload is not None and (time.monotonic() - last_upload) < min_interval_seconds:
            raise HTTPException(status_code=429, detail="Frame upload rate limit exceeded")

    def store_frame(
        self,
        pc_name: str,
        content_type: str,
        data: bytes,
        sequence: int | None = None,
    ) -> StoredFrame:
        self.validate_upload(pc_name, content_type, len(data))
        self._last_upload_monotonic[pc_name] = time.monotonic()
        frame = StoredFrame(
            pc_name=pc_name,
            content_type=content_type,
            data=data,
            byte_size=len(data),
            received_at=current_utc_time(),
            sequence=sequence,
        )
        self._frames[pc_name] = frame
        return frame

    def get_frame(self, pc_name: str) -> StoredFrame | None:
        return self._frames.get(pc_name)

    def clear_frame(self, pc_name: str) -> None:
        self._frames.pop(pc_name, None)
        self._last_upload_monotonic.pop(pc_name, None)

    def frame_status(self, pc_name: str, is_online: bool) -> tuple[StoredFrame | None, str]:
        """Compute (frame, status) for a station without mutating anything.

        Priority: offline always wins, whether or not a frame happens to
        still be cached (has_frame reflects the frame, not the status) --
        callers are expected to clear the frame on disconnect/offline
        expiration (server/main.py), so a cached frame surviving to this
        check is only the brief window before that cleanup runs. Only an
        online station with no cached frame is 'no_image'.
        """
        frame = self._frames.get(pc_name)
        if not is_online:
            return frame, "offline"
        if frame is None:
            return None, "no_image"
        age_seconds = (current_utc_time() - frame.received_at).total_seconds()
        if age_seconds > SCREEN_STALE_AFTER_SECONDS:
            return frame, "stale"
        return frame, "live"
