import os
import sqlite3
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_old_database(database_path: Path) -> None:
    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE sessions (
                session_id TEXT PRIMARY KEY,
                pc_name TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'active',
                        'completed',
                        'ended_manually',
                        'cancelled',
                        'failed'
                    )
                ),
                started_at TEXT NOT NULL,
                scheduled_end_at TEXT NOT NULL,
                ended_at TEXT,
                original_seconds INTEGER NOT NULL,
                added_seconds INTEGER NOT NULL DEFAULT 0,
                total_seconds INTEGER NOT NULL,
                amount_paid REAL,
                ended_reason TEXT,
                created_at TEXT NOT NULL,
                rate_id TEXT,
                rate_name_snapshot TEXT,
                base_amount TEXT NOT NULL DEFAULT '0.00',
                additional_amount TEXT NOT NULL DEFAULT '0.00',
                total_amount TEXT NOT NULL DEFAULT '0.00',
                session_mode TEXT NOT NULL DEFAULT 'prepaid',
                actual_elapsed_seconds INTEGER,
                billed_seconds INTEGER,
                final_computed_amount TEXT,
                metered_rate_price_snapshot TEXT,
                metered_rate_duration_seconds_snapshot INTEGER,
                settlement_pending INTEGER NOT NULL DEFAULT 0,
                created_by_user_id TEXT,
                ended_by_user_id TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX idx_sessions_old_pc_status
            ON sessions (pc_name, status)
            """
        )
        connection.execute(
            """
            CREATE TABLE payments (
                payment_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                amount TEXT NOT NULL,
                payment_method TEXT NOT NULL CHECK (
                    payment_method IN ('cash', 'gcash', 'maya', 'other')
                ),
                cash_received TEXT,
                change_given TEXT,
                reference_number TEXT,
                notes TEXT,
                action_type TEXT NOT NULL DEFAULT 'manual',
                related_command_id TEXT,
                review_required INTEGER NOT NULL DEFAULT 0,
                recorded_by_user_id TEXT,
                voided_by_user_id TEXT,
                voided_at TEXT,
                void_reason TEXT,
                status TEXT NOT NULL CHECK (status IN ('completed', 'voided')),
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE session_events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK (
                    event_type IN (
                        'session_started',
                        'time_added',
                        'session_completed',
                        'session_ended_manually',
                        'session_cancelled',
                        'command_failed'
                    )
                ),
                seconds INTEGER,
                message TEXT,
                created_at TEXT NOT NULL,
                rate_id TEXT,
                rate_name_snapshot TEXT,
                amount TEXT,
                payment_id TEXT,
                payment_method TEXT,
                performed_by_user_id TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
            """
        )
        now = "2026-07-25T00:00:00+00:00"
        rows = [
            (
                "session-active",
                "PC-01",
                "active",
                now,
                "2026-07-25T01:00:00+00:00",
                None,
                3600,
                0,
                3600,
                None,
                None,
                now,
                None,
                None,
                "20.00",
                "0.00",
                "20.00",
                "prepaid",
                None,
                None,
                None,
                None,
                None,
                0,
                "owner-1",
                None,
            ),
            (
                "session-stranded",
                "PC-02",
                "ended_manually",
                now,
                now,
                now,
                0,
                0,
                0,
                None,
                "manual_end",
                now,
                None,
                "Regular Rate",
                "0.00",
                "0.00",
                "0.00",
                "open_time",
                301,
                600,
                "2.00",
                "1.00",
                300,
                1,
                "owner-1",
                "admin-1",
            ),
            (
                "session-completed",
                "PC-03",
                "completed",
                now,
                "2026-07-25T00:30:00+00:00",
                "2026-07-25T00:30:00+00:00",
                1800,
                0,
                1800,
                None,
                "timer_expired",
                now,
                "rate-30-minutes",
                "30 Minutes",
                "50.00",
                "0.00",
                "50.00",
                "prepaid",
                None,
                None,
                None,
                None,
                None,
                0,
                "cashier-1",
                None,
            ),
        ]
        connection.executemany(
            """
            INSERT INTO sessions (
                session_id,
                pc_name,
                status,
                started_at,
                scheduled_end_at,
                ended_at,
                original_seconds,
                added_seconds,
                total_seconds,
                amount_paid,
                ended_reason,
                created_at,
                rate_id,
                rate_name_snapshot,
                base_amount,
                additional_amount,
                total_amount,
                session_mode,
                actual_elapsed_seconds,
                billed_seconds,
                final_computed_amount,
                metered_rate_price_snapshot,
                metered_rate_duration_seconds_snapshot,
                settlement_pending,
                created_by_user_id,
                ended_by_user_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        connection.execute(
            """
            INSERT INTO payments (
                payment_id,
                session_id,
                amount,
                payment_method,
                status,
                created_at
            )
            VALUES ('payment-1', 'session-active', '20.00', 'cash', 'completed', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO session_events (
                event_id,
                session_id,
                event_type,
                seconds,
                message,
                created_at
            )
            VALUES (
                'event-1',
                'session-stranded',
                'session_ended_manually',
                301,
                'stranded settlement',
                ?
            )
            """,
            (now,),
        )
        connection.commit()
    finally:
        connection.close()


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        database_path = Path(temp_directory) / "migration.sqlite3"
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
        create_old_database(database_path)

        from server.database import initialize_database

        initialize_database()

        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        try:
            session_count = connection.execute(
                "SELECT COUNT(*) FROM sessions"
            ).fetchone()[0]
            payment_count = connection.execute(
                "SELECT COUNT(*) FROM payments"
            ).fetchone()[0]
            event_count = connection.execute(
                "SELECT COUNT(*) FROM session_events"
            ).fetchone()[0]
            assert session_count == 3
            assert payment_count == 1
            assert event_count == 1

            stranded_status = connection.execute(
                "SELECT status FROM sessions WHERE session_id = 'session-stranded'"
            ).fetchone()["status"]
            assert stranded_status == "settlement_pending"

            connection.execute(
                "UPDATE sessions SET status = 'ending' WHERE session_id = 'session-active'"
            )
            connection.execute(
                """
                UPDATE sessions
                SET status = 'settlement_pending'
                WHERE session_id = 'session-active'
                """
            )
            connection.commit()

            preserved_index = connection.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type = 'index'
                  AND name = 'idx_sessions_old_pc_status'
                """
            ).fetchone()
            assert preserved_index is not None
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            connection.close()

        initialize_database()

        connection = sqlite3.connect(database_path)
        try:
            assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 3
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            connection.close()

        print("Migration smoke test passed.")


if __name__ == "__main__":
    main()
