import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent / "secret_grounds.sqlite3"


def get_database_path() -> Path:
    configured_path = os.getenv("SECRET_GROUNDS_DB_PATH")
    if configured_path:
        return Path(configured_path)

    return DEFAULT_DATABASE_PATH


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    database_path = get_database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row

    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def column_exists(connection: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    columns = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(column["name"] == column_name for column in columns)


def add_column_if_missing(
    connection: sqlite3.Connection,
    table_name: str,
    column_name: str,
    definition: str,
) -> None:
    if not column_exists(connection, table_name, column_name):
        connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}"
        )


def ensure_session_events_accept_payment_events(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'session_events'"
    ).fetchone()
    if row is None or "payment_action_review_required" in (row["sql"] or ""):
        return

    connection.execute("ALTER TABLE session_events RENAME TO session_events_old")
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
                    'command_failed',
                    'payment_recorded',
                    'payment_voided',
                    'prepaid_start_paid',
                    'prepaid_add_time_paid',
                    'open_time_started',
                    'open_time_ended',
                    'open_time_settled',
                    'payment_action_review_required'
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
            FOREIGN KEY (session_id) REFERENCES sessions(session_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO session_events (
            event_id,
            session_id,
            event_type,
            seconds,
            message,
            created_at,
            rate_id,
            rate_name_snapshot,
            amount
        )
        SELECT
            event_id,
            session_id,
            event_type,
            seconds,
            message,
            created_at,
            rate_id,
            rate_name_snapshot,
            amount
        FROM session_events_old
        """
    )
    connection.execute("DROP TABLE session_events_old")


def seed_default_rates(connection: sqlite3.Connection) -> None:
    existing_count = connection.execute(
        "SELECT COUNT(*) FROM rate_profiles"
    ).fetchone()[0]
    if existing_count:
        return

    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    defaults = [
        ("rate-30-minutes", "30 Minutes", 30 * 60, "0.00", 1, 10, "package"),
        ("rate-1-hour", "1 Hour", 60 * 60, "0.00", 1, 20, "package"),
        ("rate-2-hours", "2 Hours", 2 * 60 * 60, "0.00", 1, 30, "package"),
        ("rate-overnight", "Overnight", 8 * 60 * 60, "0.00", 1, 40, "package"),
    ]
    connection.executemany(
        """
        INSERT INTO rate_profiles (
            rate_id,
            name,
            duration_seconds,
            price,
            is_active,
            sort_order,
            rate_type,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (*rate, now, now)
            for rate in defaults
        ],
    )


def seed_default_metered_rate(connection: sqlite3.Connection) -> None:
    existing_count = connection.execute(
        "SELECT COUNT(*) FROM rate_profiles WHERE rate_type = 'metered'"
    ).fetchone()[0]
    if existing_count:
        return

    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    connection.execute(
        """
        INSERT INTO rate_profiles (
            rate_id,
            name,
            duration_seconds,
            price,
            is_active,
            sort_order,
            rate_type,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "rate-regular-metered",
            "Regular Rate",
            5 * 60,
            "1.00",
            1,
            0,
            "metered",
            now,
            now,
        ),
    )


def initialize_database() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_profiles (
                rate_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL CHECK (duration_seconds > 0),
                price TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                rate_type TEXT NOT NULL DEFAULT 'package' CHECK (
                    rate_type IN ('metered', 'package')
                ),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        add_column_if_missing(
            connection,
            "rate_profiles",
            "rate_type",
            "TEXT NOT NULL DEFAULT 'package'",
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
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
                settlement_pending INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        add_column_if_missing(connection, "sessions", "rate_id", "TEXT")
        add_column_if_missing(connection, "sessions", "rate_name_snapshot", "TEXT")
        add_column_if_missing(
            connection,
            "sessions",
            "base_amount",
            "TEXT NOT NULL DEFAULT '0.00'",
        )
        add_column_if_missing(
            connection,
            "sessions",
            "additional_amount",
            "TEXT NOT NULL DEFAULT '0.00'",
        )
        add_column_if_missing(
            connection,
            "sessions",
            "total_amount",
            "TEXT NOT NULL DEFAULT '0.00'",
        )
        add_column_if_missing(
            connection,
            "sessions",
            "session_mode",
            "TEXT NOT NULL DEFAULT 'prepaid'",
        )
        add_column_if_missing(connection, "sessions", "actual_elapsed_seconds", "INTEGER")
        add_column_if_missing(connection, "sessions", "billed_seconds", "INTEGER")
        add_column_if_missing(connection, "sessions", "final_computed_amount", "TEXT")
        add_column_if_missing(
            connection,
            "sessions",
            "settlement_pending",
            "INTEGER NOT NULL DEFAULT 0",
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS session_events (
                event_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                event_type TEXT NOT NULL CHECK (
                    event_type IN (
                        'session_started',
                        'time_added',
                        'session_completed',
                        'session_ended_manually',
                    'session_cancelled',
                    'command_failed',
                    'payment_recorded',
                    'payment_voided',
                    'prepaid_start_paid',
                    'prepaid_add_time_paid',
                    'open_time_started',
                    'open_time_ended',
                    'open_time_settled',
                    'payment_action_review_required'
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
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
            """
        )
        ensure_session_events_accept_payment_events(connection)
        add_column_if_missing(connection, "session_events", "rate_id", "TEXT")
        add_column_if_missing(connection, "session_events", "rate_name_snapshot", "TEXT")
        add_column_if_missing(connection, "session_events", "amount", "TEXT")
        add_column_if_missing(connection, "session_events", "payment_id", "TEXT")
        add_column_if_missing(connection, "session_events", "payment_method", "TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
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
                status TEXT NOT NULL CHECK (status IN ('completed', 'voided')),
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
            """
        )
        add_column_if_missing(
            connection,
            "payments",
            "action_type",
            "TEXT NOT NULL DEFAULT 'manual'",
        )
        add_column_if_missing(connection, "payments", "related_command_id", "TEXT")
        add_column_if_missing(
            connection,
            "payments",
            "review_required",
            "INTEGER NOT NULL DEFAULT 0",
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_sessions_pc_status
            ON sessions (pc_name, status)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_session_events_session_id
            ON session_events (session_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payments_session_status
            ON payments (session_id, status)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_rate_profiles_active_sort
            ON rate_profiles (is_active, sort_order)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_metered_rate
            ON rate_profiles (rate_type)
            WHERE rate_type = 'metered' AND is_active = 1
            """
        )
        seed_default_rates(connection)
        seed_default_metered_rate(connection)
