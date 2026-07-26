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


def quote_identifier(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def ensure_session_events_accept_payment_events(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'session_events'"
    ).fetchone()
    if row is None or "recovery_force_end" in (row["sql"] or ""):
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
                    'payment_action_review_required',
                    'recovery_force_end'
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


SESSION_STATUS_VALUES = (
    "active",
    "ending",
    "settlement_pending",
    "completed",
    "ended_manually",
    "cancelled",
    "failed",
)


SESSION_COLUMN_DEFINITIONS = {
    "session_id": "TEXT PRIMARY KEY",
    "pc_name": "TEXT NOT NULL",
    "status": (
        "TEXT NOT NULL CHECK (status IN ("
        + ", ".join(f"'{status}'" for status in SESSION_STATUS_VALUES)
        + "))"
    ),
    "started_at": "TEXT NOT NULL",
    "scheduled_end_at": "TEXT NOT NULL",
    "ended_at": "TEXT",
    "original_seconds": "INTEGER NOT NULL",
    "added_seconds": "INTEGER NOT NULL DEFAULT 0",
    "total_seconds": "INTEGER NOT NULL",
    "amount_paid": "REAL",
    "ended_reason": "TEXT",
    "created_at": "TEXT NOT NULL",
    "rate_id": "TEXT",
    "rate_name_snapshot": "TEXT",
    "base_amount": "TEXT NOT NULL DEFAULT '0.00'",
    "additional_amount": "TEXT NOT NULL DEFAULT '0.00'",
    "total_amount": "TEXT NOT NULL DEFAULT '0.00'",
    "session_mode": "TEXT NOT NULL DEFAULT 'prepaid'",
    "actual_elapsed_seconds": "INTEGER",
    "billed_seconds": "INTEGER",
    "final_computed_amount": "TEXT",
    "metered_rate_price_snapshot": "TEXT",
    "metered_rate_duration_seconds_snapshot": "INTEGER",
    "settlement_pending": "INTEGER NOT NULL DEFAULT 0",
    "created_by_user_id": "TEXT",
    "ended_by_user_id": "TEXT",
}


SESSION_COLUMN_DEFAULT_SELECTS = {
    "added_seconds": "0",
    "base_amount": "'0.00'",
    "additional_amount": "'0.00'",
    "total_amount": "'0.00'",
    "session_mode": "'prepaid'",
    "settlement_pending": "0",
}


def sessions_status_check_is_current(table_sql: str) -> bool:
    return all(f"'{status}'" in table_sql for status in SESSION_STATUS_VALUES)


def get_columns(connection: sqlite3.Connection, table_name: str) -> list[sqlite3.Row]:
    return connection.execute(
        f"PRAGMA table_info({quote_identifier(table_name)})"
    ).fetchall()


def ensure_payments_accept_membership_payments(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'payments'"
    ).fetchone()
    table_sql = row["sql"] if row is not None else ""
    if "session_id TEXT NOT NULL" not in table_sql:
        return

    columns = get_columns(connection, "payments")
    column_names = [column["name"] for column in columns]
    index_rows = connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'index'
          AND tbl_name = 'payments'
          AND sql IS NOT NULL
        """
    ).fetchall()
    index_sql_statements = [index_row["sql"] for index_row in index_rows]

    original_foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN")
    try:
        connection.execute("DROP TABLE IF EXISTS payments_migration_new")
        connection.execute(
            """
            CREATE TABLE payments_migration_new (
                payment_id TEXT PRIMARY KEY,
                session_id TEXT,
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
                shift_id TEXT,
                recorded_by_user_id TEXT,
                voided_by_user_id TEXT,
                voided_at TEXT,
                void_reason TEXT,
                status TEXT NOT NULL CHECK (status IN ('completed', 'voided')),
                created_at TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
            """
        )
        insert_columns = [
            "payment_id",
            "session_id",
            "amount",
            "payment_method",
            "cash_received",
            "change_given",
            "reference_number",
            "notes",
            "action_type",
            "related_command_id",
            "review_required",
            "shift_id",
            "recorded_by_user_id",
            "voided_by_user_id",
            "voided_at",
            "void_reason",
            "status",
            "created_at",
        ]
        select_expressions = [
            quote_identifier(column) if column in column_names else "NULL"
            for column in insert_columns
        ]
        connection.execute(
            f"""
            INSERT INTO payments_migration_new (
                {", ".join(quote_identifier(column) for column in insert_columns)}
            )
            SELECT {", ".join(select_expressions)}
            FROM payments
            """
        )
        connection.execute("DROP TABLE payments")
        connection.execute("ALTER TABLE payments_migration_new RENAME TO payments")
        for index_sql in index_sql_statements:
            connection.execute(index_sql)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA foreign_keys = {int(original_foreign_keys)}")


def ensure_member_transactions_accept_reservation_events(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'member_time_transactions'"
    ).fetchone()
    table_sql = row["sql"] if row is not None else ""
    if not table_sql or "session_usage_finalized" in table_sql:
        return

    original_foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN")
    try:
        connection.execute("ALTER TABLE member_time_transactions RENAME TO member_time_transactions_old")
        connection.execute(
            """
            CREATE TABLE member_time_transactions (
                transaction_id TEXT PRIMARY KEY,
                member_id TEXT NOT NULL,
                transaction_type TEXT NOT NULL CHECK (
                    transaction_type IN (
                        'membership_purchase',
                        'promo_topup',
                        'session_reservation_created',
                        'session_usage_finalized',
                        'session_reservation_released',
                        'session_reservation',
                        'session_refund',
                        'manual_adjustment',
                        'expiration_adjustment'
                    )
                ),
                seconds_delta INTEGER NOT NULL,
                balance_before_seconds INTEGER NOT NULL,
                balance_after_seconds INTEGER NOT NULL CHECK (balance_after_seconds >= 0),
                amount_paid TEXT,
                related_session_id TEXT,
                related_payment_id TEXT,
                notes TEXT,
                performed_by_user_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (member_id) REFERENCES members(member_id),
                FOREIGN KEY (related_session_id) REFERENCES sessions(session_id),
                FOREIGN KEY (related_payment_id) REFERENCES payments(payment_id),
                FOREIGN KEY (performed_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        connection.execute(
            """
            INSERT INTO member_time_transactions (
                transaction_id,
                member_id,
                transaction_type,
                seconds_delta,
                balance_before_seconds,
                balance_after_seconds,
                amount_paid,
                related_session_id,
                related_payment_id,
                notes,
                performed_by_user_id,
                created_at
            )
            SELECT
                transaction_id,
                member_id,
                transaction_type,
                seconds_delta,
                balance_before_seconds,
                balance_after_seconds,
                amount_paid,
                related_session_id,
                related_payment_id,
                notes,
                performed_by_user_id,
                created_at
            FROM member_time_transactions_old
            """
        )
        connection.execute("DROP TABLE member_time_transactions_old")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA foreign_keys = {int(original_foreign_keys)}")


def ensure_sessions_accept_state_machine_statuses(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'sessions'"
    ).fetchone()
    table_sql = row["sql"] if row is not None else None
    if table_sql is None or sessions_status_check_is_current(table_sql):
        return

    original_columns = get_columns(connection, "sessions")
    original_column_names = {column["name"] for column in original_columns}
    index_rows = connection.execute(
        """
        SELECT name, sql
        FROM sqlite_master
        WHERE type = 'index'
          AND tbl_name = 'sessions'
          AND sql IS NOT NULL
        """
    ).fetchall()
    index_sql_statements = [index_row["sql"] for index_row in index_rows]

    column_definitions = dict(SESSION_COLUMN_DEFINITIONS)
    for column in original_columns:
        column_name = column["name"]
        if column_name not in column_definitions:
            column_type = column["type"] or "TEXT"
            column_definitions[column_name] = column_type

    insert_columns = list(column_definitions)
    select_expressions = []
    for column_name in insert_columns:
        if column_name in original_column_names:
            expression = quote_identifier(column_name)
            if column_name in SESSION_COLUMN_DEFAULT_SELECTS:
                expression = (
                    f"COALESCE({expression}, "
                    f"{SESSION_COLUMN_DEFAULT_SELECTS[column_name]})"
                )
            select_expressions.append(expression)
        else:
            select_expressions.append(SESSION_COLUMN_DEFAULT_SELECTS.get(column_name, "NULL"))

    original_foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN")

    try:
        connection.execute("DROP TABLE IF EXISTS sessions_migration_new")
        create_columns_sql = ",\n            ".join(
            f"{quote_identifier(name)} {definition}"
            for name, definition in column_definitions.items()
        )
        connection.execute(
            f"""
            CREATE TABLE sessions_migration_new (
                {create_columns_sql}
            )
            """
        )
        connection.execute(
            f"""
            INSERT INTO sessions_migration_new (
                {", ".join(quote_identifier(name) for name in insert_columns)}
            )
            SELECT
                {", ".join(select_expressions)}
            FROM sessions
            """
        )
        connection.execute("DROP TABLE sessions")
        connection.execute("ALTER TABLE sessions_migration_new RENAME TO sessions")
        for index_sql in index_sql_statements:
            connection.execute(index_sql)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.execute(f"PRAGMA foreign_keys = {int(original_foreign_keys)}")

    violations = connection.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError("Session status migration left invalid foreign key references")


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


def seed_default_membership_products(connection: sqlite3.Connection) -> None:
    existing_count = connection.execute(
        "SELECT COUNT(*) FROM membership_products"
    ).fetchone()[0]
    if existing_count:
        return

    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    connection.executemany(
        """
        INSERT INTO membership_products (
            product_id,
            name,
            price,
            time_seconds,
            product_type,
            is_active,
            sort_order,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                "membership-fee",
                "Membership Fee",
                "100.00",
                6 * 60 * 60,
                "initial_membership",
                1,
                10,
                now,
                now,
            ),
            (
                "member-promo",
                "Member Promo",
                "50.00",
                4 * 60 * 60,
                "member_topup",
                1,
                20,
                now,
                now,
            ),
        ],
    )


def initialize_database() -> None:
    with get_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                username_normalized TEXT NOT NULL UNIQUE,
                display_name TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('owner', 'admin', 'cashier')),
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_login_at TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS auth_sessions (
                session_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            )
            """
        )
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
            CREATE TABLE IF NOT EXISTS pc_metadata (
                pc_name TEXT PRIMARY KEY,
                mac_address TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS remote_commands (
                command_id TEXT PRIMARY KEY,
                pc_name TEXT NOT NULL,
                command_type TEXT NOT NULL CHECK (
                    command_type IN (
                        'shutdown',
                        'restart',
                        'sleep',
                        'show_message',
                        'show_announcement',
                        'wake_on_lan'
                    )
                ),
                payload_json TEXT,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'pending',
                        'received',
                        'completed',
                        'failed',
                        'rejected',
                        'timed_out'
                    )
                ),
                requested_by_user_id TEXT,
                requested_at TEXT NOT NULL,
                received_at TEXT,
                completed_at TEXT,
                failed_at TEXT,
                failure_reason TEXT,
                client_response TEXT,
                forced INTEGER NOT NULL DEFAULT 0,
                force_reason TEXT,
                FOREIGN KEY (requested_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                pc_name TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'active',
                        'ending',
                        'settlement_pending',
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
                settlement_pending INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        ensure_sessions_accept_state_machine_statuses(connection)
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
            "metered_rate_price_snapshot",
            "TEXT",
        )
        add_column_if_missing(
            connection,
            "sessions",
            "metered_rate_duration_seconds_snapshot",
            "INTEGER",
        )
        add_column_if_missing(
            connection,
            "sessions",
            "settlement_pending",
            "INTEGER NOT NULL DEFAULT 0",
        )
        add_column_if_missing(connection, "sessions", "created_by_user_id", "TEXT")
        add_column_if_missing(connection, "sessions", "ended_by_user_id", "TEXT")
        add_column_if_missing(connection, "sessions", "member_id", "TEXT")
        add_column_if_missing(connection, "sessions", "member_code_snapshot", "TEXT")
        add_column_if_missing(connection, "sessions", "member_name_snapshot", "TEXT")
        add_column_if_missing(connection, "sessions", "member_balance_before_seconds", "INTEGER")
        add_column_if_missing(connection, "sessions", "member_balance_after_seconds", "INTEGER")
        connection.execute(
            """
            UPDATE sessions
            SET status = 'settlement_pending'
            WHERE status = 'ended_manually'
              AND session_mode = 'open_time'
              AND settlement_pending = 1
            """
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
                    'payment_action_review_required',
                    'recovery_force_end'
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
        add_column_if_missing(connection, "session_events", "performed_by_user_id", "TEXT")
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
                shift_id TEXT,
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
        ensure_payments_accept_membership_payments(connection)
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
        add_column_if_missing(connection, "payments", "shift_id", "TEXT")
        add_column_if_missing(connection, "payments", "recorded_by_user_id", "TEXT")
        add_column_if_missing(connection, "payments", "voided_by_user_id", "TEXT")
        add_column_if_missing(connection, "payments", "voided_at", "TEXT")
        add_column_if_missing(connection, "payments", "void_reason", "TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS members (
                member_id TEXT PRIMARY KEY,
                member_code TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                phone_number TEXT,
                pin_hash TEXT,
                status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
                time_balance_seconds INTEGER NOT NULL CHECK (time_balance_seconds >= 0),
                joined_at TEXT NOT NULL,
                expires_at TEXT,
                created_by_user_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (created_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        add_column_if_missing(connection, "members", "pin_hash", "TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS member_time_transactions (
                transaction_id TEXT PRIMARY KEY,
                member_id TEXT NOT NULL,
                transaction_type TEXT NOT NULL CHECK (
                    transaction_type IN (
                        'membership_purchase',
                        'promo_topup',
                        'session_reservation_created',
                        'session_usage_finalized',
                        'session_reservation_released',
                        'session_reservation',
                        'session_refund',
                        'manual_adjustment',
                        'expiration_adjustment'
                    )
                ),
                seconds_delta INTEGER NOT NULL,
                balance_before_seconds INTEGER NOT NULL,
                balance_after_seconds INTEGER NOT NULL CHECK (balance_after_seconds >= 0),
                amount_paid TEXT,
                related_session_id TEXT,
                related_payment_id TEXT,
                notes TEXT,
                performed_by_user_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (member_id) REFERENCES members(member_id),
                FOREIGN KEY (related_session_id) REFERENCES sessions(session_id),
                FOREIGN KEY (related_payment_id) REFERENCES payments(payment_id),
                FOREIGN KEY (performed_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        ensure_member_transactions_accept_reservation_events(connection)
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS member_time_reservations (
                reservation_id TEXT PRIMARY KEY,
                member_id TEXT NOT NULL,
                session_id TEXT NOT NULL UNIQUE,
                reserved_seconds INTEGER NOT NULL CHECK (reserved_seconds > 0),
                consumed_seconds INTEGER NOT NULL DEFAULT 0 CHECK (consumed_seconds >= 0),
                status TEXT NOT NULL CHECK (status IN ('active', 'finalized', 'released')),
                created_at TEXT NOT NULL,
                finalized_at TEXT,
                FOREIGN KEY (member_id) REFERENCES members(member_id),
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS membership_products (
                product_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                price TEXT NOT NULL,
                time_seconds INTEGER NOT NULL CHECK (time_seconds > 0),
                product_type TEXT NOT NULL CHECK (
                    product_type IN ('initial_membership', 'member_topup')
                ),
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS cashier_shifts (
                shift_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
                opened_at TEXT NOT NULL,
                closed_at TEXT,
                opening_cash TEXT NOT NULL,
                expected_cash TEXT,
                actual_cash TEXT,
                difference TEXT,
                closing_notes TEXT,
                force_close_reason TEXT,
                opened_by_user_id TEXT NOT NULL,
                closed_by_user_id TEXT,
                gross_session_sales TEXT NOT NULL DEFAULT '0.00',
                cash_payments TEXT NOT NULL DEFAULT '0.00',
                gcash_payments TEXT NOT NULL DEFAULT '0.00',
                maya_payments TEXT NOT NULL DEFAULT '0.00',
                other_payments TEXT NOT NULL DEFAULT '0.00',
                voided_payments TEXT NOT NULL DEFAULT '0.00',
                payment_count INTEGER NOT NULL DEFAULT 0,
                sessions_handled INTEGER NOT NULL DEFAULT 0,
                outstanding_unsettled_balance TEXT NOT NULL DEFAULT '0.00',
                review_required_payments INTEGER NOT NULL DEFAULT 0,
                settlement_pending_sessions INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (user_id) REFERENCES users(user_id),
                FOREIGN KEY (opened_by_user_id) REFERENCES users(user_id),
                FOREIGN KEY (closed_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_members_search
            ON members (member_code, full_name, phone_number)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_member_time_transactions_member
            ON member_time_transactions (member_id, created_at)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_member_reservation
            ON member_time_reservations (member_id)
            WHERE status = 'active'
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_membership_products_active_sort
            ON membership_products (is_active, sort_order)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_remote_commands_requested_at
            ON remote_commands (requested_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_remote_commands_pc
            ON remote_commands (pc_name, requested_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_audit_events (
                event_id TEXT PRIMARY KEY,
                rate_id TEXT NOT NULL,
                action TEXT NOT NULL,
                old_values TEXT,
                new_values TEXT,
                performed_by_user_id TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_auth_sessions_token
            ON auth_sessions (token_hash)
            """
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
            CREATE INDEX IF NOT EXISTS idx_payments_shift_id
            ON payments (shift_id)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_open_shift_per_user
            ON cashier_shifts (user_id)
            WHERE status = 'open'
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_cashier_shifts_user_status
            ON cashier_shifts (user_id, status, opened_at)
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
        seed_default_membership_products(connection)
