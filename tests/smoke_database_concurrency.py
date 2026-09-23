import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def test_connections_use_wal_mode_and_a_busy_timeout() -> None:
    # Pre-deployment audit finding: every request opens its own SQLite
    # connection (no pooling) in the default rollback-journal mode, where a
    # writer and a reader block each other database-wide. With ~15 client
    # PCs heartbeating every 5s and the dashboard polling sales every 4s,
    # this is a real "database is locked" risk as payments/sessions grow,
    # not a hypothetical one. WAL mode lets readers and a writer proceed
    # concurrently; busy_timeout makes a genuine collision wait briefly
    # instead of raising immediately.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "wal.sqlite3")
        from server.database import get_connection, initialize_database

        initialize_database()
        with get_connection() as connection:
            journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        assert journal_mode.lower() == "wal"
        assert busy_timeout > 0


def test_payments_has_an_index_covering_the_operator_sale_query() -> None:
    # The dashboard's Operator's Sale card (GET /dashboard/operator-sale)
    # polls shift_repository.calculate_operator_sale every 4 seconds,
    # filtering "WHERE recorded_by_user_id = ? [AND created_at >= ?]" with
    # no index on either column -- a full table scan on every poll, forever,
    # that gets slower as `payments` grows. Confirmed via
    # sqlite_master/index_list, not just that *an* index exists.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "index.sqlite3")
        from server.database import get_connection, initialize_database

        initialize_database()
        with get_connection() as connection:
            indexes = connection.execute("PRAGMA index_list(payments)").fetchall()
            covering_index = None
            for index in indexes:
                columns = [
                    row["name"]
                    for row in connection.execute(f"PRAGMA index_info({index['name']})").fetchall()
                ]
                if columns[:1] == ["recorded_by_user_id"]:
                    covering_index = columns
                    break
        assert covering_index is not None, "no index leads with recorded_by_user_id"
        assert "created_at" in covering_index


def main() -> None:
    test_connections_use_wal_mode_and_a_busy_timeout()
    test_payments_has_an_index_covering_the_operator_sale_query()
    print("Database concurrency smoke tests passed.")


if __name__ == "__main__":
    main()
