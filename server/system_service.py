import os
import platform
import shutil
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from server.database import get_connection, get_database_path
from server.time_utils import current_utc_time


BACKUP_RETENTION_DEFAULT = 30
CORE_TABLES = {
    "users",
    "sessions",
    "payments",
    "pc_layouts",
    "admin_settings",
}


class SystemService:
    def __init__(
        self,
        backup_directory: Path | None = None,
        retention_count: int | None = None,
    ) -> None:
        database_path = get_database_path()
        configured_backup_path = os.getenv("SECRET_GROUNDS_BACKUP_DIR")
        self.backup_directory = (
            backup_directory
            if backup_directory is not None
            else Path(configured_backup_path) if configured_backup_path else database_path.parent.parent / "backups"
        )
        self.retention_count = retention_count or int(
            os.getenv("SECRET_GROUNDS_BACKUP_RETENTION", str(BACKUP_RETENTION_DEFAULT))
        )
        self._operation_lock = threading.Lock()

    def get_health(
        self,
        *,
        app_version: str,
        server_started_at: datetime,
        pc_registry: dict[str, Any],
        active_websockets: list[str],
    ) -> dict[str, Any]:
        now = current_utc_time()
        database = self.get_database_health()
        storage = self.get_storage()
        backups = self.list_backups()
        connected_clients = len([pc for pc in pc_registry.values() if pc.status == "online"])
        offline_clients = len([pc for pc in pc_registry.values() if pc.status != "online"])
        websocket_status = "Healthy" if active_websockets or not pc_registry else "Warning"
        server_status = "Warning" if storage["status"] == "Warning" else "Healthy"

        return {
            "server_status": server_status,
            "database_status": database["status"],
            "websocket_status": websocket_status,
            "connected_clients": connected_clients,
            "offline_clients": offline_clients,
            "current_version": app_version,
            "database_size_bytes": database["file_size_bytes"],
            "last_backup": backups[0] if backups else None,
            "system_uptime_seconds": max(0, int((now - server_started_at).total_seconds())),
            "storage_status": storage["status"],
        }

    def get_database_health(self) -> dict[str, Any]:
        database_path = get_database_path()
        exists = database_path.exists()
        stat = database_path.stat() if exists else None
        integrity_ok = False
        user_version = 0
        row_counts: dict[str, int] = {}

        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            integrity_ok = self._integrity_check(connection)
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            for table in sorted(self._table_names(connection) & CORE_TABLES):
                row_counts[table] = int(
                    connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                )
        except sqlite3.Error:
            integrity_ok = False
        finally:
            if connection is not None:
                connection.close()

        return {
            "status": "Healthy" if exists and integrity_ok else "Error",
            "file_size_bytes": stat.st_size if stat else 0,
            "last_modified_at": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat() if stat else None,
            "estimated_row_counts": row_counts,
            "integrity": "Healthy" if integrity_ok else "Database requires attention",
            "database_version": user_version,
            "path": str(database_path),
        }

    def create_backup(self) -> dict[str, Any]:
        if not self._operation_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="A backup or restore is already running")

        try:
            self._ensure_backup_directory()
            self._flush_database()
            database_path = get_database_path()
            timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
            backup_path = self.backup_directory / f"secretgrounds_{timestamp}.db"
            counter = 1
            while backup_path.exists():
                backup_path = self.backup_directory / f"secretgrounds_{timestamp}_{counter}.db"
                counter += 1

            with sqlite3.connect(database_path) as source, sqlite3.connect(backup_path) as target:
                source.backup(target)

            validation = self.validate_backup(backup_path.name)
            if not validation["valid"]:
                backup_path.unlink(missing_ok=True)
                raise HTTPException(status_code=500, detail="Backup validation failed")

            self._record_event("backup_created", f"Created verified backup {backup_path.name}")
            self.cleanup_retention()
            return {
                "message": "Backup verified successfully.",
                "backup": self._backup_record(backup_path),
                "validation": validation,
            }
        finally:
            self._operation_lock.release()

    def list_backups(self) -> list[dict[str, Any]]:
        if not self.backup_directory.exists():
            return []
        backups = [
            self._backup_record(path)
            for path in self.backup_directory.glob("*.db")
            if path.is_file()
        ]
        return sorted(backups, key=lambda backup: backup["created_at"], reverse=True)

    def validate_backup(self, filename: str) -> dict[str, Any]:
        backup_path = self.resolve_backup_path(filename)
        missing_tables: list[str] = []
        valid = False
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(f"file:{backup_path}?mode=ro", uri=True)
            integrity_ok = self._integrity_check(connection)
            tables = self._table_names(connection)
            missing_tables = sorted(CORE_TABLES - tables)
            valid = integrity_ok and not missing_tables
        except sqlite3.Error:
            integrity_ok = False
        finally:
            if connection is not None:
                connection.close()

        return {
            "valid": valid,
            "status": "Healthy" if valid else "Database requires attention",
            "missing_tables": missing_tables,
            "integrity": "Healthy" if integrity_ok else "Database requires attention",
        }

    def restore_backup(self, filename: str, confirm: bool) -> dict[str, Any]:
        if not confirm:
            raise HTTPException(status_code=400, detail="Restore confirmation is required")
        if not self._operation_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="A backup or restore is already running")

        try:
            backup_path = self.resolve_backup_path(filename)
            validation = self.validate_backup(filename)
            if not validation["valid"]:
                raise HTTPException(status_code=400, detail="Backup is not a valid Secret Grounds database")

            database_path = get_database_path()
            if backup_path.resolve() == database_path.resolve():
                raise HTTPException(status_code=400, detail="Cannot restore the active database file")

            temporary_restore = database_path.with_suffix(".restore.tmp")
            rollback_copy = database_path.with_suffix(".before-restore.tmp")
            shutil.copy2(backup_path, temporary_restore)
            with sqlite3.connect(temporary_restore) as connection:
                if not self._integrity_check(connection):
                    temporary_restore.unlink(missing_ok=True)
                    raise HTTPException(status_code=400, detail="Backup failed restore validation")

            if database_path.exists():
                shutil.copy2(database_path, rollback_copy)
            self._atomic_replace_with_retry(temporary_restore, database_path)
            rollback_copy.unlink(missing_ok=True)
            self._record_event("restore_completed", f"Restored database from {backup_path.name}")
            return {
                "message": "Restore completed. Restart the server if clients do not reconnect cleanly.",
                "restored_from": backup_path.name,
                "validation": validation,
            }
        finally:
            self._operation_lock.release()

    def delete_backup(self, filename: str, confirm: bool) -> dict[str, str]:
        if not confirm:
            raise HTTPException(status_code=400, detail="Delete confirmation is required")
        backup_path = self.resolve_backup_path(filename)
        if backup_path.resolve() == get_database_path().resolve():
            raise HTTPException(status_code=400, detail="Cannot delete the active database")
        self._unlink_with_retry(backup_path)
        self._record_event("backup_deleted", f"Deleted backup {backup_path.name}")
        return {"message": "Backup deleted"}

    def resolve_backup_path(self, filename: str) -> Path:
        if Path(filename).name != filename or not filename.endswith(".db"):
            raise HTTPException(status_code=400, detail="Invalid backup filename")
        backup_path = (self.backup_directory / filename).resolve()
        backup_root = self.backup_directory.resolve()
        if backup_root not in backup_path.parents:
            raise HTTPException(status_code=400, detail="Invalid backup filename")
        if not backup_path.exists() or not backup_path.is_file():
            raise HTTPException(status_code=404, detail="Backup not found")
        return backup_path

    def cleanup_retention(self) -> None:
        if self.retention_count <= 0:
            return
        verified_backups = [
            self.resolve_backup_path(backup["filename"])
            for backup in self.list_backups()
            if backup["verified"]
        ]
        for backup_path in verified_backups[self.retention_count:]:
            try:
                self._unlink_with_retry(backup_path, attempts=3)
            except PermissionError:
                print(f"Retention cleanup skipped locked backup: {backup_path.name}")

    def get_storage(self) -> dict[str, Any]:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        usage = shutil.disk_usage(self.backup_directory)
        backups = self.list_backups()
        folder_size = sum(backup["size_bytes"] for backup in backups)
        largest = max(backups, key=lambda backup: backup["size_bytes"], default=None)
        writable = os.access(self.backup_directory, os.W_OK)
        free_ratio = usage.free / usage.total if usage.total else 0
        status = "Healthy"
        if not writable or free_ratio < 0.10:
            status = "Warning"
        return {
            "status": status,
            "available_bytes": usage.free,
            "total_bytes": usage.total,
            "backup_folder_size_bytes": folder_size,
            "largest_backup": largest,
            "backup_folder": str(self.backup_directory),
            "writable": writable,
        }

    def get_info(self, *, app_version: str, server_started_at: datetime) -> dict[str, Any]:
        now = current_utc_time()
        return {
            "secret_grounds_version": app_version,
            "python_version": sys.version.split()[0],
            "operating_system": f"{platform.system()} {platform.release()}",
            "database_version": self.get_database_health()["database_version"],
            "server_start_time": server_started_at.isoformat(),
            "server_uptime_seconds": max(0, int((now - server_started_at).total_seconds())),
            "application_mode": "TEST MODE" if os.getenv("SECRET_GROUNDS_TEST_MODE", "false").lower() == "true" else "Production",
        }

    def get_logs(self, limit: int = 100) -> list[dict[str, str]]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT event_type, created_at, performed_by_user_id
                FROM admin_settings_audit_events
                UNION ALL
                SELECT event_type, created_at, selected_by_user_id
                FROM operator_audit_events
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (min(max(limit, 1), 100),),
            ).fetchall()
        return [
            {
                "level": self._log_level(row["event_type"]),
                "timestamp": row["created_at"],
                "event": row["event_type"],
                "message": self._sanitize_log_message(f"{row['event_type']} by {row['performed_by_user_id'] or 'system'}"),
            }
            for row in rows
        ]

    def _backup_record(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        created_at = datetime.fromtimestamp(stat.st_mtime).astimezone()
        validation = self.validate_backup(path.name)
        return {
            "filename": path.name,
            "created_at": created_at.isoformat(),
            "size_bytes": stat.st_size,
            "age_seconds": max(0, int((datetime.now().astimezone() - created_at).total_seconds())),
            "verified": validation["valid"],
        }

    def _ensure_backup_directory(self) -> None:
        self.backup_directory.mkdir(parents=True, exist_ok=True)
        if not os.access(self.backup_directory, os.W_OK):
            raise HTTPException(status_code=500, detail="Backup folder is not writable")

    def _flush_database(self) -> None:
        with get_connection() as connection:
            connection.execute("PRAGMA wal_checkpoint(FULL)")

    def _integrity_check(self, connection: sqlite3.Connection) -> bool:
        row = connection.execute("PRAGMA integrity_check").fetchone()
        return row is not None and row[0] == "ok"

    def _atomic_replace_with_retry(self, source: Path, target: Path) -> None:
        last_error: PermissionError | None = None
        for _ in range(5):
            try:
                os.replace(source, target)
                return
            except PermissionError as error:
                last_error = error
                time.sleep(0.15)
        if last_error is not None:
            with sqlite3.connect(source) as source_connection, sqlite3.connect(target) as target_connection:
                source_connection.backup(target_connection)
            try:
                source.unlink(missing_ok=True)
            except PermissionError:
                pass
            return

    def _unlink_with_retry(self, path: Path, attempts: int = 30) -> None:
        last_error: PermissionError | None = None
        for _ in range(attempts):
            try:
                path.unlink(missing_ok=True)
                return
            except PermissionError as error:
                last_error = error
                time.sleep(0.5)
        if last_error is not None:
            raise last_error

    def _table_names(self, connection: sqlite3.Connection) -> set[str]:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        return {str(row[0]) for row in rows}

    def _record_event(self, event_type: str, message: str) -> None:
        print(f"System event: {self._sanitize_log_message(message)}")
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO operator_audit_events (
                    event_id,
                    event_type,
                    operator_user_id,
                    operator_display_name,
                    selected_by_user_id,
                    created_at
                )
                VALUES (?, ?, NULL, 'System', NULL, ?)
                """,
                (str(uuid4()), event_type, current_utc_time().isoformat()),
            )

    def _sanitize_log_message(self, message: str) -> str:
        redacted_words = ("password", "token", "cookie", "secret", "key")
        sanitized = message
        for word in redacted_words:
            sanitized = sanitized.replace(word, "[redacted]")
            sanitized = sanitized.replace(word.upper(), "[redacted]")
            sanitized = sanitized.replace(word.title(), "[redacted]")
        return sanitized[:240]

    def _log_level(self, event_type: str) -> str:
        lower = event_type.lower()
        if "error" in lower or "failed" in lower:
            return "Error"
        if "warning" in lower or "restore" in lower:
            return "Warning"
        return "Info"
