import os
import sqlite3
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        root = Path(temp_directory)
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(root / "system.sqlite3")
        os.environ["SECRET_GROUNDS_BACKUP_DIR"] = str(root / "backups")
        os.environ["SECRET_GROUNDS_BACKUP_RETENTION"] = "2"
        os.environ["SECRET_GROUNDS_TEST_MODE"] = "true"

        from fastapi import HTTPException

        from server.database import get_connection, initialize_database
        from server.main import (
            delete_system_backup,
            download_system_backup,
            get_system_backups,
            get_system_database,
            get_system_health,
            get_system_logs,
            get_system_storage,
            require_admin_operator,
            restore_system_backup,
            system_service,
            user_repository,
        )

        initialize_database()
        admin = user_repository.get_by_username("admin")
        mama = user_repository.get_by_username("operator_mama")
        assert admin is not None and mama is not None
        assert require_admin_operator(admin).user_id == admin.user_id
        try:
            require_admin_operator(mama)
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Non-admin operator must not access System")

        database_health = get_system_database(admin)
        assert database_health["status"] == "Healthy"
        assert database_health["integrity"] == "Healthy"
        assert database_health["estimated_row_counts"]["users"] >= 1

        backup_response = system_service.create_backup()
        assert backup_response["message"] == "Backup verified successfully."
        first_backup = backup_response["backup"]
        assert first_backup["verified"] is True
        assert first_backup["filename"].startswith("secretgrounds_")

        backups = get_system_backups(admin)
        assert len(backups) == 1
        assert backups[0]["filename"] == first_backup["filename"]

        validation = system_service.validate_backup(first_backup["filename"])
        assert validation["valid"] is True
        assert validation["missing_tables"] == []

        corrupt_backup = Path(os.environ["SECRET_GROUNDS_BACKUP_DIR"]) / "secretgrounds_corrupt.db"
        corrupt_backup.write_bytes(b"not sqlite")
        corrupt_validation = system_service.validate_backup(corrupt_backup.name)
        assert corrupt_validation["valid"] is False
        try:
            restore_system_backup({"filename": corrupt_backup.name, "confirm": True}, admin)
        except HTTPException as error:
            assert error.status_code == 400
        else:
            raise AssertionError("Corrupted backups must not restore")

        try:
            restore_system_backup({"filename": first_backup["filename"], "confirm": False}, admin)
        except HTTPException as error:
            assert error.status_code == 400
        else:
            raise AssertionError("Restore must require confirmation")

        with get_connection() as connection:
            connection.execute("CREATE TABLE restore_marker (value TEXT)")
            connection.execute("INSERT INTO restore_marker VALUES ('after-backup')")
        restored = restore_system_backup({"filename": first_backup["filename"], "confirm": True}, admin)
        assert restored["restored_from"] == first_backup["filename"]
        with sqlite3.connect(os.environ["SECRET_GROUNDS_DB_PATH"]) as connection:
            marker = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'restore_marker'"
            ).fetchone()
        assert marker is None

        for _ in range(3):
            system_service.create_backup()
        retained = [backup for backup in get_system_backups(admin) if backup["verified"]]
        assert len(retained) >= 2
        assert retained[0]["created_at"] >= retained[1]["created_at"]
        download_response = download_system_backup(retained[0]["filename"], admin)
        assert download_response.filename == retained[0]["filename"]
        del download_response

        storage = get_system_storage(admin)
        assert storage["writable"] is True
        assert storage["available_bytes"] > 0

        health = get_system_health(admin)
        assert health["database_status"] == "Healthy"
        assert "server_status" in health

        logs = get_system_logs(admin)
        assert len(logs) <= 100
        assert all("password" not in log["message"].lower() for log in logs)

        try:
            delete_system_backup(first_backup["filename"], False, admin)
        except HTTPException as error:
            assert error.status_code == 400
        else:
            raise AssertionError("Delete must require confirmation")

        delete_candidate = Path(os.environ["SECRET_GROUNDS_BACKUP_DIR"]) / "secretgrounds_delete_me.db"
        delete_candidate.write_bytes(b"delete me")
        delete_system_backup(delete_candidate.name, True, admin)
        assert not delete_candidate.exists()

        print("Admin system smoke test passed.")


if __name__ == "__main__":
    main()
