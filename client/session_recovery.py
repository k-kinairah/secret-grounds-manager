import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Literal


RecoveryState = Literal["active", "pending_completion"]


def current_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RecoverableSessionState:
    session_id: str
    pc_name: str
    scheduled_end_at: str
    saved_at: str
    state: RecoveryState
    session_mode: str = "prepaid"


class SessionRecoveryStore:
    """Thread-safe atomic JSON store for recoverable local session state."""

    def __init__(self, file_path: str | Path = "client_session_recovery.json") -> None:
        self.file_path = Path(file_path)
        self.lock = Lock()

    def load(self) -> RecoverableSessionState | None:
        with self.lock:
            if not self.file_path.exists():
                return None

            try:
                with self.file_path.open("r", encoding="utf-8") as recovery_file:
                    payload = json.load(recovery_file)
                return RecoverableSessionState(
                    session_id=str(payload["session_id"]),
                    pc_name=str(payload["pc_name"]),
                    scheduled_end_at=str(payload["scheduled_end_at"]),
                    saved_at=str(payload["saved_at"]),
                    state=payload["state"],
                    session_mode=str(payload.get("session_mode", "prepaid")),
                )
            except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
                print(f"Invalid recovery record ignored: {error}")
                return None

    def save(
        self,
        session_id: str,
        pc_name: str,
        scheduled_end_at: str,
        state: RecoveryState = "active",
        session_mode: str = "prepaid",
    ) -> RecoverableSessionState:
        record = RecoverableSessionState(
            session_id=session_id,
            pc_name=pc_name,
            scheduled_end_at=scheduled_end_at,
            saved_at=current_utc_iso(),
            state=state,
            session_mode=session_mode,
        )
        self.save_record(record)
        return record

    def save_record(self, record: RecoverableSessionState) -> None:
        with self.lock:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")

            with temporary_path.open("w", encoding="utf-8") as recovery_file:
                json.dump(asdict(record), recovery_file, indent=2)
                recovery_file.write("\n")
                recovery_file.flush()
                os.fsync(recovery_file.fileno())

            os.replace(temporary_path, self.file_path)

    def mark_pending_completion(
        self,
        session_id: str,
        pc_name: str,
        scheduled_end_at: str,
        session_mode: str = "prepaid",
    ) -> RecoverableSessionState:
        return self.save(
            session_id=session_id,
            pc_name=pc_name,
            scheduled_end_at=scheduled_end_at,
            state="pending_completion",
            session_mode=session_mode,
        )

    def clear(self) -> None:
        with self.lock:
            temporary_path = self.file_path.with_suffix(f"{self.file_path.suffix}.tmp")
            if temporary_path.exists():
                temporary_path.unlink()
            if self.file_path.exists():
                self.file_path.unlink()
