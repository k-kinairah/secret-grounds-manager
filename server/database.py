import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from server.operator_config import FAMILY_OPERATORS
from server.passwords import hash_password
from server.time_utils import current_utc_time


DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent / "secret_grounds.sqlite3"


def get_database_path() -> Path:
    configured_path = os.getenv("SECRET_GROUNDS_DB_PATH")
    if configured_path:
        path = Path(configured_path).expanduser()
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        return path.resolve()

    return DEFAULT_DATABASE_PATH.resolve()


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
    if row is None or "shutdown_command_invoked" in (row["sql"] or ""):
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
                    'recovery_force_end',
                    'session_prepared',
                    'wake_signal_sent',
                    'client_connected',
                    'session_activation_started',
                    'session_activated',
                    'startup_failed',
                    'prepared_session_cancelled',
                    'automatic_reminder_sent',
                    'time_expired',
                    'operator_changed',
                    'shutdown_grace_started',
                    'time_added_during_grace',
                    'automatic_shutdown_cancelled',
                    'shutdown_grace_completed',
                    'automatic_shutdown_initiated',
                    'shutdown_command_acknowledged',
                    'shutdown_command_invoked',
                    'automatic_shutdown_failed',
                    'session_moved_to_settlement',
                    'expiration_final_warning_reached'
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


def ensure_session_events_accept_customer_lock_events(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'session_events'"
    ).fetchone()
    if row is None or "customer_lock_engaged" in (row["sql"] or ""):
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
                    'recovery_force_end',
                    'session_prepared',
                    'wake_signal_sent',
                    'client_connected',
                    'session_activation_started',
                    'session_activated',
                    'startup_failed',
                    'prepared_session_cancelled',
                    'automatic_reminder_sent',
                    'time_expired',
                    'operator_changed',
                    'shutdown_grace_started',
                    'time_added_during_grace',
                    'automatic_shutdown_cancelled',
                    'shutdown_grace_completed',
                    'automatic_shutdown_initiated',
                    'shutdown_command_acknowledged',
                    'shutdown_command_invoked',
                    'automatic_shutdown_failed',
                    'session_moved_to_settlement',
                    'expiration_final_warning_reached',
                    'customer_lock_engaged',
                    'customer_lock_cleared'
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
            amount,
            payment_id,
            payment_method,
            performed_by_user_id
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
            amount,
            payment_id,
            payment_method,
            performed_by_user_id
        FROM session_events_old
        """
    )
    connection.execute("DROP TABLE session_events_old")


SESSION_STATUS_VALUES = (
    "active",
    "grace_period",
    "shutting_down",
    "ending",
    "settlement_pending",
    "completed",
    "ended_manually",
    "cancelled",
    "failed",
)


STANDARD_OR_VIP_VALUES = ("standard", "vip")


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
    "grace_started_at": "TEXT",
    "shutdown_at": "TEXT",
    "customer_locked": "INTEGER NOT NULL DEFAULT 0",
    "station_type": "TEXT NOT NULL DEFAULT 'standard'",
    "vip_seconds_consumed": "INTEGER",
    "standard_seconds_consumed_at_vip_rate": "INTEGER",
    "conversion_applied": "INTEGER NOT NULL DEFAULT 0",
}


SESSION_COLUMN_DEFAULT_SELECTS = {
    "added_seconds": "0",
    "base_amount": "'0.00'",
    "additional_amount": "'0.00'",
    "total_amount": "'0.00'",
    "session_mode": "'prepaid'",
    "settlement_pending": "0",
    "customer_locked": "0",
    "station_type": "'standard'",
    "conversion_applied": "0",
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
                idempotency_key TEXT UNIQUE,
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
            "idempotency_key",
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
                        'purchase_refund',
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


def ensure_member_transactions_accept_purchase_refund(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'member_time_transactions'"
    ).fetchone()
    table_sql = row["sql"] if row is not None else ""
    if not table_sql or "purchase_refund" in table_sql:
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
                        'purchase_refund',
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


def ensure_member_time_reservations_allow_zero_standard(connection: sqlite3.Connection) -> None:
    """A VIP-only reservation (the member's VIP balance alone covers the
    whole session) legitimately has reserved_seconds == 0 for its Standard
    side. The original CHECK (reserved_seconds > 0) -- written before VIP
    reservations existed -- rejects that. Relaxes it to >= 0, matching
    consumed_seconds' existing bound, while preserving every row and index."""
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'member_time_reservations'"
    ).fetchone()
    table_sql = row["sql"] if row is not None else None
    if table_sql is None or "CHECK (reserved_seconds > 0)" not in table_sql:
        return

    index_rows = connection.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'index' AND tbl_name = 'member_time_reservations' AND sql IS NOT NULL
        """
    ).fetchall()
    index_sql_statements = [index_row["sql"] for index_row in index_rows]

    original_foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    connection.commit()
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute("BEGIN")
    try:
        connection.execute("ALTER TABLE member_time_reservations RENAME TO member_time_reservations_old")
        connection.execute(
            """
            CREATE TABLE member_time_reservations (
                reservation_id TEXT PRIMARY KEY,
                member_id TEXT NOT NULL,
                session_id TEXT NOT NULL UNIQUE,
                reserved_seconds INTEGER NOT NULL CHECK (reserved_seconds >= 0),
                consumed_seconds INTEGER NOT NULL DEFAULT 0 CHECK (consumed_seconds >= 0),
                reserved_vip_seconds INTEGER NOT NULL DEFAULT 0,
                consumed_vip_seconds INTEGER NOT NULL DEFAULT 0,
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
            INSERT INTO member_time_reservations (
                reservation_id, member_id, session_id, reserved_seconds, consumed_seconds,
                reserved_vip_seconds, consumed_vip_seconds, status, created_at, finalized_at
            )
            SELECT
                reservation_id, member_id, session_id, reserved_seconds, consumed_seconds,
                reserved_vip_seconds, consumed_vip_seconds, status, created_at, finalized_at
            FROM member_time_reservations_old
            """
        )
        connection.execute("DROP TABLE member_time_reservations_old")
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
        raise RuntimeError("member_time_reservations migration left invalid foreign key references")


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


# seed_default_rates() was removed here. It pre-dated the owner-confirmed
# Standard/VIP pricing redesign and, on any database whose rate_profiles
# table was still completely empty at first startup, inserted four ₱0.00
# placeholder packages ("30 Minutes", "1 Hour", "2 Hours", "Overnight" --
# ids rate-30-minutes/rate-1-hour/rate-2-hours/rate-overnight). Those rows
# were never removed once seed_required_default_pricing() below became the
# real source of default pricing, so a fresh install ended up with both the
# obsolete ₱0 packages and the current owner-approved ones active at once.
# seed_required_default_pricing() is now the single authoritative seeder for
# default package pricing -- see deactivate_untouched_legacy_default_rates()
# for the conservative, non-destructive handling of any database that
# already has one of those four legacy rows from a prior run of the old
# seeder.


def seed_default_metered_rate(connection: sqlite3.Connection) -> None:
    """Fresh-install default for the Standard tab's amount-based custom
    session. Must match the authoritative Standard package pricing (₱5 =
    20 min, ₱10 = 40 min, ₱15 = 1 hour, i.e. ₱1 = 4 minutes) so a typed
    amount and a package buy the same time -- NOT the old ₱1 = 5 minutes
    figure (see correct_regular_metered_rate_pricing below for the
    non-destructive migration of an existing install's row)."""
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
            20 * 60,
            "5.00",
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
                "Standard Top-up",
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


# Owner-confirmed default pricing. Each row has a fixed, permanent id.
# seed_required_default_pricing() inserts a row only when that exact id is
# still absent -- unlike seed_default_membership_products above (which only
# ever runs once, against a completely empty table), this is designed to
# run on every startup against an already-configured shop without ever
# duplicating a row or overwriting a price/duration an owner has since
# edited under one of these ids.
REQUIRED_DEFAULT_RATES = [
    # (rate_id, name, duration_seconds, price, sort_order, station_scope,
    #  guest_only, member_eligible, eligible_start_local_time, eligible_end_local_time)
    (
        "rate-standard-guest-5", "Standard Guest - P5 (20 Minutes)", 20 * 60, "5.00", 1,
        "standard", 0, 1, None, None,
    ),
    (
        "rate-standard-guest-10", "Standard Guest - P10 (40 Minutes)", 40 * 60, "10.00", 2,
        "standard", 0, 1, None, None,
    ),
    (
        "rate-standard-guest-15", "Standard Guest - P15 (1 Hour)", 60 * 60, "15.00", 3,
        "standard", 0, 1, None, None,
    ),
    # rate-standard-midnight-promo intentionally removed here: the owner
    # ended the Midnight Promo. A fresh install must never seed it again.
    # Any existing install that already has this row from a prior startup
    # is handled non-destructively by deactivate_untouched_midnight_promo_rate
    # below -- this list only controls what gets newly INSERTed.
    (
        "rate-vip-guest-1hr", "VIP Guest - P30 (1 Hour)", 60 * 60, "30.00", 5,
        "vip", 0, 1, None, None,
    ),
    # Ordinary VIP amounts follow the configured P30/hour rate exactly
    # (P60=2hr, P90=3hr, P120=4hr are plain multiples) -- fixed package
    # rows rather than a live calculator, same architecture as the
    # Standard guest packages above. P250/10hr is a separate promotional
    # package, deliberately NOT priced off P30/hour (P25/hour effective)
    # -- see rate-vip-promo-10hr below.
    (
        "rate-vip-guest-2hr", "VIP Guest - P60 (2 Hours)", 2 * 60 * 60, "60.00", 6,
        "vip", 0, 1, None, None,
    ),
    (
        "rate-vip-guest-3hr", "VIP Guest - P90 (3 Hours)", 3 * 60 * 60, "90.00", 7,
        "vip", 0, 1, None, None,
    ),
    (
        "rate-vip-guest-4hr", "VIP Guest - P120 (4 Hours)", 4 * 60 * 60, "120.00", 8,
        "vip", 0, 1, None, None,
    ),
    # Special VIP promo package -- fixed P250/10 hours, never computed
    # from the P30/hour rate (see docstring above).
    (
        "rate-vip-promo-10hr", "VIP Guest - P250 (10 Hour Promo)", 10 * 60 * 60, "250.00", 9,
        "vip", 0, 1, None, None,
    ),
]

REQUIRED_DEFAULT_MEMBERSHIP_PRODUCTS = [
    # (product_id, name, price, time_seconds, product_type, balance_type, sort_order, is_active)
    ("membership-fee", "Membership Fee", "100.00", 6 * 60 * 60, "initial_membership", "standard", 10, 1),
    ("member-promo", "Standard Top-up", "50.00", 4 * 60 * 60, "member_topup", "standard", 20, 1),
    ("member-topup-100", "Standard Top-up", "100.00", 8 * 60 * 60, "member_topup", "standard", 30, 1),
    # Stage 3 implements and tests VIP balance purchase/credit, so this
    # seeds active for any shop installing fresh from this point on. A shop
    # upgrading from a Stage 2 install (where this was seeded inactive) is
    # handled separately by activate_vip_promo_product_if_untouched below,
    # since this insert is skipped once the row already exists.
    ("vip-promo-250", "VIP Member Promo", "250.00", 10 * 60 * 60, "member_topup", "vip", 40, 1),
]


def seed_required_default_pricing(connection: sqlite3.Connection) -> None:
    """Idempotently create the owner-confirmed default rates/products.

    Per-item idempotency by fixed id: a row is inserted only when that exact
    id does not already exist. This never overwrites an owner-edited price
    or duration (including the pre-existing 'membership-fee'/'member-promo'
    defaults, which already satisfy the confirmed Create Account and P50
    top-up rules and are intentionally left untouched), and re-running this
    on every startup can never create a duplicate or reactivate a row an
    owner has since disabled (an existing id is always skipped, never
    updated).
    """
    now = current_utc_time().isoformat()

    for (
        rate_id,
        name,
        duration_seconds,
        price,
        sort_order,
        station_scope,
        guest_only,
        member_eligible,
        eligible_start_local_time,
        eligible_end_local_time,
    ) in REQUIRED_DEFAULT_RATES:
        existing = connection.execute(
            "SELECT 1 FROM rate_profiles WHERE rate_id = ?",
            (rate_id,),
        ).fetchone()
        if existing is not None:
            continue
        connection.execute(
            """
            INSERT INTO rate_profiles (
                rate_id, name, duration_seconds, price, is_active, sort_order,
                rate_type, station_scope, guest_only, member_eligible,
                eligible_start_local_time, eligible_end_local_time,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, 1, ?, 'package', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rate_id,
                name,
                duration_seconds,
                price,
                sort_order,
                station_scope,
                guest_only,
                member_eligible,
                eligible_start_local_time,
                eligible_end_local_time,
                now,
                now,
            ),
        )

    for (
        product_id,
        name,
        price,
        time_seconds,
        product_type,
        balance_type,
        sort_order,
        is_active,
    ) in REQUIRED_DEFAULT_MEMBERSHIP_PRODUCTS:
        existing = connection.execute(
            "SELECT 1 FROM membership_products WHERE product_id = ?",
            (product_id,),
        ).fetchone()
        if existing is not None:
            continue
        connection.execute(
            """
            INSERT INTO membership_products (
                product_id, name, price, time_seconds, product_type,
                balance_type, is_active, sort_order, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                name,
                price,
                time_seconds,
                product_type,
                balance_type,
                is_active,
                sort_order,
                now,
                now,
            ),
        )


# The four rows below are what the now-removed seed_default_rates() used to
# insert on any pre-Stage-0 database whose rate_profiles table was still
# completely empty at first startup. (rate_id, name, duration_seconds) --
# the exact content that function wrote, used below to recognize a row as
# still being that pristine placeholder rather than something an owner has
# since repurposed.
LEGACY_DEFAULT_RATE_PLACEHOLDERS = [
    ("rate-30-minutes", "30 Minutes", 30 * 60),
    ("rate-1-hour", "1 Hour", 60 * 60),
    ("rate-2-hours", "2 Hours", 2 * 60 * 60),
    ("rate-overnight", "Overnight", 8 * 60 * 60),
]


def deactivate_untouched_legacy_default_rates(connection: sqlite3.Connection) -> None:
    """Conservative cleanup for a database that already has one of the four
    legacy ₱0.00 placeholder packages from the old seed_default_rates()
    (removed above; see the comment where it used to live).

    A row is deactivated only when it is still exactly what that function
    left behind: same id, same name, same duration, price still '0.00',
    still a 'package', still active, and never edited since creation
    (updated_at == created_at, the same one-time-transition guard used by
    activate_vip_promo_product_if_untouched). Any row that fails even one of
    those checks -- an owner renamed it, repriced it, changed its duration,
    or already disabled it -- is left completely untouched; this can never
    destroy owner-edited data. Deactivates rather than deletes, so any
    historical session that still references one of these ids by rate_id
    keeps resolving correctly. Once deactivated, is_active=0 means this
    WHERE clause never matches that row again.
    """
    now = current_utc_time().isoformat()
    for rate_id, name, duration_seconds in LEGACY_DEFAULT_RATE_PLACEHOLDERS:
        connection.execute(
            """
            UPDATE rate_profiles
            SET is_active = 0, updated_at = ?
            WHERE rate_id = ?
              AND name = ?
              AND duration_seconds = ?
              AND price = '0.00'
              AND rate_type = 'package'
              AND is_active = 1
              AND updated_at = created_at
            """,
            (now, rate_id, name, duration_seconds),
        )


def deactivate_untouched_midnight_promo_rate(connection: sqlite3.Connection) -> None:
    """One-time retirement of the Midnight Promo package.

    The owner ended Midnight Promo. rate-standard-midnight-promo is no
    longer in REQUIRED_DEFAULT_RATES, so a fresh install never seeds it --
    but any shop that already has the row (from a prior startup, before
    this retirement) needs it turned off without touching history.

    Same conservative one-time-transition guard as
    deactivate_untouched_legacy_default_rates: only deactivates a row that
    is still exactly what the seeder created (same name/duration/price/
    scope/eligibility fields, never edited since -- updated_at ==
    created_at). An owner who repriced, renamed, or already
    activated/deactivated it themselves is left untouched. Deactivates
    rather than deletes, so every historical session that used this
    rate_id keeps resolving its rate name/amount for billing and reports.
    Once is_active=0, this WHERE clause never matches the row again, so a
    later owner reactivation is never silently undone by a future
    restart re-running this.
    """
    connection.execute(
        """
        UPDATE rate_profiles
        SET is_active = 0, updated_at = ?
        WHERE rate_id = 'rate-standard-midnight-promo'
          AND name = 'Standard Midnight Promo'
          AND duration_seconds = 3600
          AND price = '10.00'
          AND rate_type = 'package'
          AND station_scope = 'standard'
          AND guest_only = 1
          AND member_eligible = 0
          AND eligible_start_local_time = '00:00'
          AND eligible_end_local_time = '05:00'
          AND is_active = 1
          AND updated_at = created_at
        """,
        (current_utc_time().isoformat(),),
    )


def correct_regular_metered_rate_pricing(connection: sqlite3.Connection) -> None:
    """One-time correction of the Standard metered rate's pricing.

    The Regular Rate metered row was originally seeded at an incorrect
    ₱1.00 = 5 minutes (300 seconds). It must match the authoritative
    Standard package pricing instead -- ₱5 = 20 minutes (1200 seconds),
    i.e. ₱1 = 4 minutes -- so a typed amount and a fixed package buy the
    same time. seed_default_metered_rate above already seeds the
    corrected value on a brand-new install; this corrects an existing
    install's already-seeded row instead.

    Same conservative one-time-transition guard used throughout this
    file: only corrects a row that is still exactly what the old seeder
    created (same id/name/rate_type/price/duration, never touched since
    -- updated_at == created_at). An owner who has repriced this rate,
    renamed it, or even just toggled its active status is left
    completely untouched -- this can never overwrite a genuine owner
    customization. Price/duration is corrected regardless of the row's
    current is_active value (a currently-inactive-but-untouched row
    should not keep the wrong numbers if it's ever reactivated); the
    row's own active/inactive state is never changed here. Once
    corrected, price is no longer '1.00', so this WHERE clause can never
    match the row again on a later restart.
    """
    connection.execute(
        """
        UPDATE rate_profiles
        SET price = '5.00', duration_seconds = 1200, updated_at = ?
        WHERE rate_id = 'rate-regular-metered'
          AND name = 'Regular Rate'
          AND rate_type = 'metered'
          AND price = '1.00'
          AND duration_seconds = 300
          AND updated_at = created_at
        """,
        (current_utc_time().isoformat(),),
    )


def activate_vip_promo_product_if_untouched(connection: sqlite3.Connection) -> None:
    """One-time Stage 2 -> Stage 3 transition.

    vip-promo-250 was seeded inactive in Stage 2 because the purchase/credit
    logic did not exist yet (see REQUIRED_DEFAULT_MEMBERSHIP_PRODUCTS).
    Stage 3 implements and tests that logic, so this activates it -- but
    only if nobody has touched the row since it was seeded (updated_at
    still equals created_at). Once active, is_active=1 means this WHERE
    clause never matches again, so an owner who later disables the product
    is never silently overridden by a future restart re-running this.
    """
    connection.execute(
        """
        UPDATE membership_products
        SET is_active = 1, updated_at = ?
        WHERE product_id = 'vip-promo-250'
          AND is_active = 0
          AND updated_at = created_at
        """,
        (current_utc_time().isoformat(),),
    )


def rename_untouched_member_promo_product(connection: sqlite3.Connection) -> None:
    """Owner rename: the P50 'member-promo' product was originally seeded
    'Member Promo', but it is an ordinary standard top-up, not a promo.
    Guarded to the exact original seeded name so an owner-edited name is
    never clobbered on a later restart."""
    connection.execute(
        """
        UPDATE membership_products
        SET name = 'Standard Top-up', updated_at = ?
        WHERE product_id = 'member-promo' AND name = 'Member Promo'
        """,
        (current_utc_time().isoformat(),),
    )


DEFAULT_INCOME_GROUPS = [
    ("united", "United"),
    ("gene", "Gene"),
    ("mama-lala", "Mama/Lala"),
    ("tyrone", "Tyrone"),
]


def seed_default_income_groups(connection: sqlite3.Connection) -> None:
    """Idempotent by fixed income_group_id (INSERT OR IGNORE) -- safe to
    call on every startup, never duplicates rows, never overwrites a name
    an owner may have edited later."""
    now = current_utc_time().isoformat()
    connection.executemany(
        """
        INSERT OR IGNORE INTO income_groups (
            income_group_id, name, is_active, created_at, updated_at
        )
        VALUES (?, ?, 1, ?, ?)
        """,
        [(income_group_id, name, now, now) for income_group_id, name in DEFAULT_INCOME_GROUPS],
    )


# Initial shop mapping only (owner-confirmed PC-01..10 United, PC-11..12
# Gene, PC-13..14 Mama/Lala, PC-15 Tyrone). This dict exists solely to seed
# pc_layouts.income_group_id on first boot -- billing/payment/reporting
# code must always read the live column, never this map, so reassigning a
# PC's income group later never requires a code change.
DEFAULT_PC_INCOME_GROUP_MAP = {
    "PC-01": "united",
    "PC-02": "united",
    "PC-03": "united",
    "PC-04": "united",
    "PC-05": "united",
    "PC-06": "united",
    "PC-07": "united",
    "PC-08": "united",
    "PC-09": "united",
    "PC-10": "united",
    "PC-11": "gene",
    "PC-12": "gene",
    "PC-13": "mama-lala",
    "PC-14": "mama-lala",
    "PC-15": "tyrone",
}


def backfill_default_pc_income_groups(connection: sqlite3.Connection) -> None:
    """One-time convenience seed for the shop's initial PC ownership
    mapping. Only ever updates rows where income_group_id is still NULL,
    so it never overwrites an admin-edited assignment, and only ever
    targets the known default pc_name values, so custom/renamed PC rows
    are left completely alone. Safe to call on every startup."""
    connection.executemany(
        """
        UPDATE pc_layouts
        SET income_group_id = ?
        WHERE pc_name = ? AND income_group_id IS NULL
        """,
        [
            (income_group_id, pc_name)
            for pc_name, income_group_id in DEFAULT_PC_INCOME_GROUP_MAP.items()
        ],
    )


def rename_untouched_default_pc15_display_name(connection: sqlite3.Connection) -> None:
    """One-time rename of PC-15's default display name from 'PC 15' to
    'VIP 1'. seed_default_pc_layouts only ever runs against a completely
    empty pc_layouts table, so any shop whose database already had rows
    (i.e. everyone who installed before this rename) needs this instead to
    pick up the new default without re-seeding.

    Only updates the row when display_name is still the exact literal
    default the seeder used to write ('PC 15') -- pc_layouts has no
    created_at/updated_at-equality guard to lean on the way rate_profiles
    does, but an owner-renamed row can never coincidentally read back as
    that exact string once changed to anything else, so matching on the
    literal value alone is a safe, conservative "still untouched" signal.
    pc_name/is_vip/MAC assignment/session and billing history are never
    touched -- this only ever writes display_name. Once the row no longer
    reads 'PC 15', this WHERE clause never matches it again, so it can
    never re-fight a later owner rename.
    """
    now = current_utc_time().isoformat()
    connection.execute(
        """
        UPDATE pc_layouts
        SET display_name = 'VIP 1', updated_at = ?
        WHERE pc_name = 'PC-15' AND display_name = 'PC 15'
        """,
        (now,),
    )


def seed_default_pc_layouts(connection: sqlite3.Connection) -> None:
    existing_count = connection.execute(
        "SELECT COUNT(*) FROM pc_layouts"
    ).fetchone()[0]
    if existing_count:
        return

    now = __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).isoformat()
    layouts = [
        ("PC-01", "PC 1", "SECRET GROUND SHOP", 10, 0, 1),
        ("PC-02", "PC 2", "SECRET GROUND SHOP", 20, 0, 1),
        ("PC-03", "PC 3", "SECRET GROUND SHOP", 30, 0, 1),
        ("PC-04", "PC 4", "SECRET GROUND SHOP", 40, 0, 1),
        ("PC-05", "PC 5", "SECRET GROUND SHOP", 50, 0, 1),
        ("PC-06", "PC 6", "SECRET GROUND SHOP", 60, 0, 1),
        ("PC-07", "PC 7", "SECRET GROUND SHOP", 70, 0, 1),
        ("PC-08", "PC 8", "SECRET GROUND SHOP", 80, 0, 1),
        ("PC-09", "PC 9", "SECRET GROUND SHOP", 90, 0, 1),
        ("PC-10", "PC 10", "SECRET GROUND SHOP", 100, 0, 1),
        ("PC-11", "PC 11", "ROGENE", 110, 0, 1),
        ("PC-12", "PC 12", "ROGENE", 120, 0, 1),
        ("PC-13", "PC 13", "MAMA", 130, 0, 1),
        ("PC-14", "PC 14", "LALA", 140, 0, 1),
        ("PC-15", "VIP 1", "VIP", 150, 1, 1),
    ]
    connection.executemany(
        """
        INSERT INTO pc_layouts (
            pc_name,
            display_name,
            area_label,
            layout_order,
            is_vip,
            is_enabled,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [(*layout, now) for layout in layouts],
    )


def seed_family_operators(connection: sqlite3.Connection) -> None:
    now = current_utc_time().isoformat()
    admin_password = os.getenv("SECRET_GROUNDS_ADMIN_PASSWORD", "admin12345")
    disabled_password_hash = hash_password(str(uuid4()))
    admin_password_hash = hash_password(admin_password)

    for operator in FAMILY_OPERATORS:
        existing = connection.execute(
            "SELECT user_id FROM users WHERE username_normalized = ?",
            (operator.username.casefold(),),
        ).fetchone()
        if existing is None:
            # The operator may have been renamed through the admin UI, so
            # the username lookup above misses. Fall back to the stable
            # operator_id (which is the seeded user_id) -- take the UPDATE
            # path and keep the custom username rather than trying to
            # re-INSERT and hitting a PRIMARY KEY collision.
            existing = connection.execute(
                "SELECT user_id FROM users WHERE user_id = ?",
                (operator.operator_id,),
            ).fetchone()
        if existing is not None:
            access_method = "password" if operator.password_required else "quick"
            is_protected = 1 if operator.username == "admin" else 0
            if operator.password_required:
                connection.execute(
                    """
                    UPDATE users
                    SET display_name = ?, role = 'owner', is_active = 1,
                        is_operator = 1, access_method = ?, is_protected = ?,
                        archived_at = NULL, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (operator.display_name, access_method, is_protected, now, existing["user_id"]),
                )
            else:
                connection.execute(
                    """
                    UPDATE users
                    SET is_operator = 1, access_method = ?, is_protected = ?,
                        archived_at = NULL, updated_at = ?
                    WHERE user_id = ?
                    """,
                    (access_method, is_protected, now, existing["user_id"]),
                )
            continue

        connection.execute(
            """
            INSERT INTO users (
                user_id,
                username,
                username_normalized,
                display_name,
                password_hash,
                role,
                is_active,
                created_at,
                updated_at,
                last_login_at,
                is_operator,
                access_method,
                is_protected,
                archived_at
            )
            VALUES (?, ?, ?, ?, ?, 'owner', 1, ?, ?, NULL, 1, ?, ?, NULL)
            """,
            (
                operator.operator_id,
                operator.username,
                operator.username.casefold(),
                operator.display_name,
                admin_password_hash if operator.password_required else disabled_password_hash,
                now,
                now,
                "password" if operator.password_required else "quick",
                1 if operator.username == "admin" else 0,
            ),
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
        existing_user_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        if "is_operator" not in existing_user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN is_operator INTEGER NOT NULL DEFAULT 0")
        if "access_method" not in existing_user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN access_method TEXT NOT NULL DEFAULT 'password'")
        if "is_protected" not in existing_user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN is_protected INTEGER NOT NULL DEFAULT 0")
        if "archived_at" not in existing_user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN archived_at TEXT")
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
                station_scope TEXT NOT NULL DEFAULT 'standard',
                guest_only INTEGER NOT NULL DEFAULT 0,
                member_eligible INTEGER NOT NULL DEFAULT 1,
                eligible_start_local_time TEXT,
                eligible_end_local_time TEXT,
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
        # Standard/VIP package scoping (see server/balance_conversion.py).
        # Metered/Open Time rates are retired for active use -- these
        # columns are only ever meaningful for rate_type='package' rows.
        add_column_if_missing(
            connection,
            "rate_profiles",
            "station_scope",
            "TEXT NOT NULL DEFAULT 'standard'",
        )
        add_column_if_missing(
            connection,
            "rate_profiles",
            "guest_only",
            "INTEGER NOT NULL DEFAULT 0",
        )
        add_column_if_missing(
            connection,
            "rate_profiles",
            "member_eligible",
            "INTEGER NOT NULL DEFAULT 1",
        )
        add_column_if_missing(
            connection,
            "rate_profiles",
            "eligible_start_local_time",
            "TEXT",
        )
        add_column_if_missing(
            connection,
            "rate_profiles",
            "eligible_end_local_time",
            "TEXT",
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
        # Server-owned physical-station identity: mac_address -> pc_name.
        # Deliberately separate from pc_metadata above, which is keyed by
        # pc_name and only ever records "what MAC does this already-known
        # station currently report" (populated by register/heartbeat).
        # station_assignments answers the opposite, prior question -- "what
        # station does this physical machine belong to" -- and is the sole
        # write path for that binding. Only an 'assigned' row may hold a
        # non-NULL pc_name; UNIQUE(pc_name) then guarantees one active
        # assignment per station, since SQLite UNIQUE permits any number of
        # NULLs. previous_pc_name preserves the last binding a MAC held once
        # revoked/reassigned, for audit and for the reassignment UI.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS station_assignments (
                mac_address TEXT PRIMARY KEY,
                pc_name TEXT UNIQUE,
                status TEXT NOT NULL DEFAULT 'unclaimed' CHECK (
                    status IN ('unclaimed', 'assigned', 'revoked')
                ),
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_seen_ip TEXT,
                assigned_at TEXT,
                assigned_by_user_id TEXT,
                previous_pc_name TEXT,
                CHECK (
                    (status = 'assigned' AND pc_name IS NOT NULL AND assigned_at IS NOT NULL)
                    OR (status IN ('unclaimed', 'revoked') AND pc_name IS NULL)
                ),
                FOREIGN KEY (assigned_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_station_assignments_status
            ON station_assignments (status, last_seen_at)
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
                        'grace_period',
                        'shutting_down',
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
                settlement_pending INTEGER NOT NULL DEFAULT 0,
                grace_started_at TEXT,
                shutdown_at TEXT,
                customer_locked INTEGER NOT NULL DEFAULT 0,
                station_type TEXT NOT NULL DEFAULT 'standard',
                vip_seconds_consumed INTEGER,
                standard_seconds_consumed_at_vip_rate INTEGER,
                conversion_applied INTEGER NOT NULL DEFAULT 0
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
        add_column_if_missing(connection, "sessions", "grace_started_at", "TEXT")
        add_column_if_missing(connection, "sessions", "shutdown_at", "TEXT")
        add_column_if_missing(connection, "sessions", "member_id", "TEXT")
        add_column_if_missing(connection, "sessions", "member_code_snapshot", "TEXT")
        add_column_if_missing(connection, "sessions", "member_name_snapshot", "TEXT")
        add_column_if_missing(connection, "sessions", "member_balance_before_seconds", "INTEGER")
        add_column_if_missing(connection, "sessions", "member_balance_after_seconds", "INTEGER")
        add_column_if_missing(connection, "sessions", "customer_locked", "INTEGER NOT NULL DEFAULT 0")
        # Standard/VIP balance-source snapshot -- written once at session
        # finalization from server/balance_conversion.py, never recomputed
        # from current (drifted) member balances. Historical sessions from
        # before this column existed are correctly backfilled as 'standard'
        # with no conversion, since VIP consumption did not exist yet.
        add_column_if_missing(connection, "sessions", "station_type", "TEXT NOT NULL DEFAULT 'standard'")
        add_column_if_missing(connection, "sessions", "vip_seconds_consumed", "INTEGER")
        add_column_if_missing(
            connection,
            "sessions",
            "standard_seconds_consumed_at_vip_rate",
            "INTEGER",
        )
        add_column_if_missing(connection, "sessions", "conversion_applied", "INTEGER NOT NULL DEFAULT 0")
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
                    'recovery_force_end',
                    'session_prepared',
                    'wake_signal_sent',
                    'client_connected',
                    'session_activation_started',
                    'session_activated',
                    'startup_failed',
                    'prepared_session_cancelled',
                    'automatic_reminder_sent',
                    'time_expired',
                    'operator_changed',
                    'shutdown_grace_started',
                    'time_added_during_grace',
                    'automatic_shutdown_cancelled',
                    'shutdown_grace_completed',
                    'automatic_shutdown_initiated',
                    'shutdown_command_acknowledged',
                    'shutdown_command_invoked',
                    'automatic_shutdown_failed',
                    'session_moved_to_settlement',
                    'expiration_final_warning_reached',
                    'customer_lock_engaged',
                    'customer_lock_cleared'
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
        ensure_session_events_accept_customer_lock_events(connection)
        add_column_if_missing(connection, "session_events", "rate_id", "TEXT")
        add_column_if_missing(connection, "session_events", "rate_name_snapshot", "TEXT")
        add_column_if_missing(connection, "session_events", "amount", "TEXT")
        add_column_if_missing(connection, "session_events", "payment_id", "TEXT")
        add_column_if_missing(connection, "session_events", "payment_method", "TEXT")
        add_column_if_missing(connection, "session_events", "performed_by_user_id", "TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS pc_layouts (
                pc_name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                area_label TEXT NOT NULL,
                layout_order INTEGER NOT NULL DEFAULT 0,
                is_vip INTEGER NOT NULL DEFAULT 0,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS income_groups (
                income_group_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Revenue-owner assignment for a station. Deliberately separate from
        # is_vip (VIP/Standard billing classification) -- a PC's income
        # group and its VIP status are independent concepts. Nullable so an
        # unassigned PC is explicit, not defaulted into any group.
        add_column_if_missing(connection, "pc_layouts", "income_group_id", "TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS prepared_sessions (
                prepared_session_id TEXT PRIMARY KEY,
                pc_name TEXT NOT NULL,
                status TEXT NOT NULL CHECK (
                    status IN (
                        'prepared',
                        'waking',
                        'waiting_for_client',
                        'starting',
                        'active',
                        'failed',
                        'cancelled'
                    )
                ),
                session_mode TEXT NOT NULL CHECK (
                    session_mode IN ('prepaid', 'open_time', 'member_balance')
                ),
                rate_id TEXT,
                rate_name_snapshot TEXT,
                duration_seconds INTEGER,
                amount TEXT,
                payment_json TEXT,
                member_id TEXT,
                member_pin TEXT,
                member_name_snapshot TEXT,
                member_open_play INTEGER NOT NULL DEFAULT 0,
                prepared_by_user_id TEXT,
                prepared_operator_id TEXT,
                prepared_at TEXT NOT NULL,
                wake_requested_at TEXT,
                wake_status TEXT,
                startup_status TEXT NOT NULL,
                failure_reason TEXT,
                expires_at TEXT NOT NULL,
                activated_session_id TEXT,
                activated_at TEXT,
                cancelled_by_user_id TEXT,
                cancelled_at TEXT,
                FOREIGN KEY (activated_session_id) REFERENCES sessions(session_id),
                FOREIGN KEY (prepared_by_user_id) REFERENCES users(user_id),
                FOREIGN KEY (prepared_operator_id) REFERENCES users(user_id),
                FOREIGN KEY (cancelled_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        connection.execute("UPDATE prepared_sessions SET member_pin = NULL WHERE member_pin IS NOT NULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_state (
                state_id TEXT PRIMARY KEY CHECK (state_id = 'current'),
                operator_user_id TEXT,
                selected_by_user_id TEXT,
                selected_at TEXT,
                FOREIGN KEY (operator_user_id) REFERENCES users(user_id),
                FOREIGN KEY (selected_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS operator_audit_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                operator_user_id TEXT,
                operator_display_name TEXT NOT NULL,
                selected_by_user_id TEXT,
                created_at TEXT NOT NULL,
                metadata_json TEXT,
                FOREIGN KEY (operator_user_id) REFERENCES users(user_id),
                FOREIGN KEY (selected_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        existing_audit_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(operator_audit_events)").fetchall()
        }
        if "metadata_json" not in existing_audit_columns:
            connection.execute("ALTER TABLE operator_audit_events ADD COLUMN metadata_json TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_settings (
                setting_key TEXT PRIMARY KEY,
                value_json TEXT NOT NULL,
                updated_by_user_id TEXT,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (updated_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS admin_settings_audit_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL CHECK (
                    event_type IN ('settings_updated', 'settings_reset')
                ),
                category TEXT NOT NULL,
                changed_fields_json TEXT NOT NULL,
                old_values_json TEXT NOT NULL,
                new_values_json TEXT NOT NULL,
                applied_immediately INTEGER NOT NULL DEFAULT 1,
                performed_by_user_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (performed_by_user_id) REFERENCES users(user_id)
            )
            """
        )
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
                idempotency_key TEXT UNIQUE,
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
        add_column_if_missing(connection, "payments", "idempotency_key", "TEXT")
        add_column_if_missing(connection, "payments", "voided_by_user_id", "TEXT")
        add_column_if_missing(connection, "payments", "voided_at", "TEXT")
        add_column_if_missing(connection, "payments", "void_reason", "TEXT")
        # Income-group revenue attribution snapshot -- captured once, at
        # write time, and never rewritten afterward (same append-only
        # philosophy as member_name_snapshot/rate_name_snapshot elsewhere).
        # Reassigning a PC's income_group_id later must never change what
        # these columns say about a payment that already happened.
        add_column_if_missing(connection, "payments", "pc_name_snapshot", "TEXT")
        add_column_if_missing(connection, "payments", "income_group_id_snapshot", "TEXT")
        add_column_if_missing(connection, "payments", "income_group_name_snapshot", "TEXT")
        # 'assigned' | 'pending' (enforced in schemas.py, not a DB CHECK --
        # matches the existing convention for columns added via
        # add_column_if_missing, e.g. action_type above). Historical rows
        # predating this migration default to 'assigned' with every
        # snapshot column left NULL: that marks them as attribution-
        # resolved (never eligible for a future pending-attribution sweep)
        # without ever claiming they belong to one of the four income
        # groups. Only 'pending' rows are ever swept, so 'assigned'+NULL is
        # the only default that can't be mistaken for real group data.
        add_column_if_missing(connection, "payments", "attribution_status", "TEXT NOT NULL DEFAULT 'assigned'")
        add_column_if_missing(connection, "payments", "attribution_assigned_at", "TEXT")
        add_column_if_missing(connection, "payments", "attribution_session_id", "TEXT")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS members (
                member_id TEXT PRIMARY KEY,
                member_code TEXT NOT NULL UNIQUE,
                username TEXT,
                username_normalized TEXT,
                full_name TEXT NOT NULL,
                phone_number TEXT,
                pin_hash TEXT,
                requires_account_setup INTEGER NOT NULL DEFAULT 1,
                pin_failed_attempts INTEGER NOT NULL DEFAULT 0,
                pin_locked_until TEXT,
                status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
                time_balance_seconds INTEGER NOT NULL CHECK (time_balance_seconds >= 0),
                vip_balance_seconds INTEGER NOT NULL DEFAULT 0,
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
        add_column_if_missing(connection, "members", "username", "TEXT")
        add_column_if_missing(connection, "members", "username_normalized", "TEXT")
        add_column_if_missing(connection, "members", "requires_account_setup", "INTEGER NOT NULL DEFAULT 1")
        add_column_if_missing(connection, "members", "pin_failed_attempts", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing(connection, "members", "pin_locked_until", "TEXT")
        # Separate, persistent VIP promo balance (server/balance_conversion.py).
        # Existing members keep their time_balance_seconds (Standard balance)
        # exactly as-is; VIP balance defaults to 0 for every existing member.
        add_column_if_missing(connection, "members", "vip_balance_seconds", "INTEGER NOT NULL DEFAULT 0")
        connection.execute(
            """
            UPDATE members
            SET username_normalized = LOWER(TRIM(username))
            WHERE username IS NOT NULL
              AND TRIM(username) <> ''
              AND (username_normalized IS NULL OR username_normalized = '')
            """
        )
        connection.execute(
            """
            UPDATE members
            SET requires_account_setup = CASE
                WHEN username_normalized IS NULL OR username_normalized = '' OR pin_hash IS NULL OR pin_hash = ''
                THEN 1
                ELSE 0
            END
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS member_time_transactions (
                transaction_id TEXT PRIMARY KEY,
                member_id TEXT NOT NULL,
                transaction_type TEXT NOT NULL CHECK (
                    transaction_type IN (
                        'membership_purchase',
                        'promo_topup',
                        'purchase_refund',
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
                balance_type TEXT NOT NULL DEFAULT 'standard',
                FOREIGN KEY (member_id) REFERENCES members(member_id),
                FOREIGN KEY (related_session_id) REFERENCES sessions(session_id),
                FOREIGN KEY (related_payment_id) REFERENCES payments(payment_id),
                FOREIGN KEY (performed_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        ensure_member_transactions_accept_reservation_events(connection)
        ensure_member_transactions_accept_purchase_refund(connection)
        # Every historical ledger row predates VIP balance and is correctly
        # backfilled as 'standard' by the column default.
        add_column_if_missing(connection, "member_time_transactions", "balance_type", "TEXT NOT NULL DEFAULT 'standard'")
        # Stage 4: nullable linkage from a ledger row to the promo
        # redemption that produced it (base and bonus rows from the same
        # promotional top-up share one promo_redemption_id). Every
        # historical row predates promos and is correctly backfilled NULL
        # by the column default -- no CHECK/transaction_type rebuild.
        add_column_if_missing(connection, "member_time_transactions", "promo_redemption_id", "TEXT")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_member_time_transactions_promo_redemption
            ON member_time_transactions (promo_redemption_id)
            WHERE promo_redemption_id IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS member_time_reservations (
                reservation_id TEXT PRIMARY KEY,
                member_id TEXT NOT NULL,
                session_id TEXT NOT NULL UNIQUE,
                reserved_seconds INTEGER NOT NULL CHECK (reserved_seconds >= 0),
                consumed_seconds INTEGER NOT NULL DEFAULT 0 CHECK (consumed_seconds >= 0),
                reserved_vip_seconds INTEGER NOT NULL DEFAULT 0,
                consumed_vip_seconds INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL CHECK (status IN ('active', 'finalized', 'released')),
                created_at TEXT NOT NULL,
                finalized_at TEXT,
                FOREIGN KEY (member_id) REFERENCES members(member_id),
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
            """
        )
        # reserved_seconds/consumed_seconds remain the Standard-balance pair
        # (unchanged meaning for Standard-PC sessions); the new vip_* pair is
        # only ever populated for a VIP-PC session's reservation.
        add_column_if_missing(connection, "member_time_reservations", "reserved_vip_seconds", "INTEGER NOT NULL DEFAULT 0")
        add_column_if_missing(connection, "member_time_reservations", "consumed_vip_seconds", "INTEGER NOT NULL DEFAULT 0")
        ensure_member_time_reservations_allow_zero_standard(connection)
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
                balance_type TEXT NOT NULL DEFAULT 'standard',
                is_active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        # Existing membership-fee/member-promo defaults predate VIP and are
        # correctly backfilled as 'standard' by the column default.
        add_column_if_missing(connection, "membership_products", "balance_type", "TEXT NOT NULL DEFAULT 'standard'")
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
            CREATE INDEX IF NOT EXISTS idx_members_identity_search
            ON members (member_code, username, username_normalized, full_name, phone_number)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_members_username_normalized
            ON members (username_normalized)
            WHERE username_normalized IS NOT NULL AND username_normalized <> ''
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
            CREATE INDEX IF NOT EXISTS idx_admin_settings_audit_created
            ON admin_settings_audit_events (created_at)
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
            CREATE UNIQUE INDEX IF NOT EXISTS idx_payments_idempotency_key
            ON payments (idempotency_key)
            WHERE idempotency_key IS NOT NULL
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_payments_pending_attribution
            ON payments (attribution_status)
            WHERE attribution_status = 'pending'
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
            CREATE INDEX IF NOT EXISTS idx_pc_layouts_enabled_order
            ON pc_layouts (is_enabled, layout_order)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_prepared_sessions_pc_status
            ON prepared_sessions (pc_name, status, prepared_at)
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_metered_rate
            ON rate_profiles (rate_type)
            WHERE rate_type = 'metered' AND is_active = 1
            """
        )
        # Promo Maker Stage 1: schema only -- no eligibility resolution,
        # session/member integration, or API routes exist yet. Owner state
        # (draft/enabled/disabled/ended) is stored; Draft/Scheduled/Active/
        # Ended/Disabled is a derived, never-persisted display value (see
        # server/promo_status.py). No seeding: promos start empty until an
        # owner creates one through a future Stage-5 API.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS promos (
                promo_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                audience TEXT NOT NULL CHECK (
                    audience IN ('standard', 'vip', 'member_topup', 'new_member')
                ),
                promo_type TEXT NOT NULL DEFAULT 'purchase' CHECK (
                    promo_type IN ('purchase', 'time_based_rate')
                ),
                owner_state TEXT NOT NULL DEFAULT 'draft' CHECK (
                    owner_state IN ('draft', 'enabled', 'disabled', 'ended')
                ),
                amount_mode TEXT NOT NULL DEFAULT 'exact' CHECK (
                    amount_mode IN ('exact', 'minimum')
                ),
                price TEXT NOT NULL,
                granted_seconds INTEGER NOT NULL CHECK (granted_seconds > 0),
                station_scope TEXT CHECK (station_scope IN ('standard', 'vip')),
                applies_to TEXT CHECK (
                    applies_to IN ('start_session', 'add_time', 'both')
                ),
                starts_at TEXT NOT NULL,
                ends_at TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0,
                per_member_redemption_limit INTEGER CHECK (
                    per_member_redemption_limit IS NULL OR per_member_redemption_limit > 0
                ),
                total_redemption_limit INTEGER CHECK (
                    total_redemption_limit IS NULL OR total_redemption_limit > 0
                ),
                -- Time-Based Rate only (NULL for every 'purchase' promo).
                -- days_of_week is a CSV of 0=Monday..6=Sunday; window_*
                -- is 'HH:MM' local shop time. See server/promo_time_based.py.
                days_of_week TEXT,
                window_start_local_time TEXT,
                window_end_local_time TEXT,
                created_by_user_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (created_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        # Stage 7 additive columns for existing installs -- every pre-Stage-7
        # row is a 'purchase' promo with NULL schedule fields (column
        # defaults). No CHECK rebuild: promo_type / schedule validity is
        # enforced in server/schemas.py's PromoFields as defense in depth.
        add_column_if_missing(connection, "promos", "promo_type", "TEXT NOT NULL DEFAULT 'purchase'")
        add_column_if_missing(connection, "promos", "days_of_week", "TEXT")
        add_column_if_missing(connection, "promos", "window_start_local_time", "TEXT")
        add_column_if_missing(connection, "promos", "window_end_local_time", "TEXT")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_promos_audience_state
            ON promos (audience, owner_state)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_promos_state_dates
            ON promos (owner_state, starts_at, ends_at)
            """
        )

        # Every promos row mutation (create/update/state transition) writes
        # exactly one of these in the same transaction -- mirrors
        # rate_audit_events (server/rate_repository.py) exactly.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_audit_events (
                event_id TEXT PRIMARY KEY,
                promo_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK (
                    action IN ('created', 'updated', 'enabled', 'disabled', 'ended', 'reactivated')
                ),
                old_values TEXT,
                new_values TEXT,
                performed_by_user_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (promo_id) REFERENCES promos(promo_id),
                FOREIGN KEY (performed_by_user_id) REFERENCES users(user_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_promo_audit_events_promo
            ON promo_audit_events (promo_id, created_at)
            """
        )

        # Not written or read by anything in Stage 1 -- created now so the
        # foreign keys below are valid, and so Stage 4's redemption-write
        # path (see server/promo_repository.py's module docstring for the
        # exact BEGIN IMMEDIATE requirement) has its table ready. Every
        # nullable *_snapshot column here is intentionally nullable: which
        # ones apply depends on audience (e.g. session_id is standard/vip
        # only, member_id/base_transaction_id/bonus_transaction_id are
        # member_topup/new_member only).
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_redemptions (
                redemption_id TEXT PRIMARY KEY,
                promo_id TEXT NOT NULL,
                promo_name_snapshot TEXT NOT NULL,
                audience_snapshot TEXT NOT NULL,
                amount_mode_snapshot TEXT NOT NULL,
                price_snapshot TEXT NOT NULL,
                granted_seconds_snapshot INTEGER NOT NULL,
                station_scope_snapshot TEXT,
                applies_to_snapshot TEXT,
                starts_at_snapshot TEXT NOT NULL,
                ends_at_snapshot TEXT NOT NULL,
                member_id TEXT,
                session_id TEXT,
                payment_id TEXT NOT NULL,
                base_transaction_id TEXT,
                bonus_transaction_id TEXT,
                pc_name_snapshot TEXT,
                income_group_id_snapshot TEXT,
                income_group_name_snapshot TEXT,
                operator_user_id TEXT,
                voided_at TEXT,
                void_reason TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (promo_id) REFERENCES promos(promo_id),
                FOREIGN KEY (payment_id) REFERENCES payments(payment_id),
                FOREIGN KEY (member_id) REFERENCES members(member_id),
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_promo_redemptions_promo_active
            ON promo_redemptions (promo_id)
            WHERE voided_at IS NULL
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_promo_redemptions_promo_member_active
            ON promo_redemptions (promo_id, member_id)
            WHERE voided_at IS NULL
            """
        )
        # Stage 3: exactly one redemption per payment -- a retried
        # idempotency-key request that reuses the same payment_id can
        # never insert a second redemption row.
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_promo_redemptions_one_per_payment
            ON promo_redemptions (payment_id)
            """
        )

        # Stage 7: Time-Based Rate promo usage log. Deliberately NOT
        # promo_redemptions -- a time-based promo temporarily REPLACES the
        # Standard linear amount->time conversion for the length of its
        # recurring daily window and is never limit-accounted, so there is
        # no slot to reserve, no per-member/total limit, and no voided_at.
        # This table exists purely so the owner can see which time-based
        # rate was active for a given session/payment and what it produced.
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS time_based_rate_applications (
                application_id TEXT PRIMARY KEY,
                promo_id TEXT NOT NULL,
                promo_name_snapshot TEXT NOT NULL,
                price_snapshot TEXT NOT NULL,
                granted_seconds_snapshot INTEGER NOT NULL,
                days_of_week_snapshot TEXT NOT NULL,
                window_start_snapshot TEXT NOT NULL,
                window_end_snapshot TEXT NOT NULL,
                session_id TEXT,
                payment_id TEXT NOT NULL,
                action TEXT NOT NULL CHECK (action IN ('start_session', 'add_time')),
                normal_seconds INTEGER NOT NULL,
                promo_seconds INTEGER NOT NULL,
                amount TEXT NOT NULL,
                pc_name_snapshot TEXT,
                income_group_id_snapshot TEXT,
                income_group_name_snapshot TEXT,
                operator_user_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (promo_id) REFERENCES promos(promo_id),
                FOREIGN KEY (payment_id) REFERENCES payments(payment_id),
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_time_based_rate_applications_promo
            ON time_based_rate_applications (promo_id, created_at)
            """
        )

        seed_default_metered_rate(connection)
        # Corrects an existing install's already-seeded Regular Rate row
        # from the old, incorrect ₱1 = 5 minutes to the authoritative ₱5 =
        # 20 minutes (₱1 = 4 minutes) -- see the function's docstring for
        # exactly what "untouched" means here.
        correct_regular_metered_rate_pricing(connection)
        seed_default_membership_products(connection)
        seed_default_income_groups(connection)
        seed_default_pc_layouts(connection)
        # Owner-facing rename of the default PC-15 label -- see the
        # function's docstring for exactly what "untouched" means here.
        rename_untouched_default_pc15_display_name(connection)
        backfill_default_pc_income_groups(connection)
        seed_family_operators(connection)
        # Station/eligibility enforcement (server/rate_eligibility.py) is
        # live as of Stage 2, so these owner-confirmed defaults are now safe
        # to activate. Idempotent by fixed id -- see seed_required_default_pricing.
        # This is the single authoritative seeder for default package
        # pricing; the old seed_default_rates() has been removed.
        seed_required_default_pricing(connection)
        # Conservative, non-destructive cleanup for any database that still
        # has one of the four obsolete ₱0 placeholders the old
        # seed_default_rates() used to create -- see the function's
        # docstring for exactly what "untouched" means here.
        deactivate_untouched_legacy_default_rates(connection)
        # Owner ended Midnight Promo -- see the function's docstring for
        # exactly what "untouched" means here.
        deactivate_untouched_midnight_promo_rate(connection)
        # Stage 3: atomic purchase/credit + non-refundable enforcement are
        # now implemented and tested, so the VIP promo product is safe to
        # sell. See activate_vip_promo_product_if_untouched's docstring for
        # why this never fights a later owner disable.
        activate_vip_promo_product_if_untouched(connection)
        # Owner rename: 'member-promo' is a standard top-up, not a promo --
        # see the function's docstring for what "untouched" means here.
        rename_untouched_member_promo_product(connection)
