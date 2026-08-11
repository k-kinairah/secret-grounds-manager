"""Stage 1 (schema-only) migration tests for income-group attribution.

Covers: server/database.py income_groups table + seed, pc_layouts
income_group_id backfill, payments attribution snapshot columns, and the
pending-attribution partial index. No stamping/sweep logic exists yet --
this only proves the additive schema foundation is correct, idempotent,
and non-destructive of existing data.
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_PC_INCOME_GROUP = {
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


def create_old_database(database_path: Path) -> None:
    """Pre-Stage-1 production shape (baseline e092088): payments/pc_layouts
    with no income-group concept at all."""
    connection = sqlite3.connect(database_path)
    try:
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
                created_by_user_id TEXT,
                ended_by_user_id TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE payments (
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
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE pc_layouts (
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

        now = "2026-07-25T00:00:00+00:00"

        # Existing PC layout rows, matching the shop's already-deployed
        # 15-PC default layout, plus one custom/renamed PC an owner added
        # later (must survive untouched and never get an income group).
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
            ("PC-15", "PC 15", "VIP", 150, 1, 1),
            ("PC-99", "Back Room Custom PC", "CUSTOM", 990, 0, 1),
        ]
        connection.executemany(
            """
            INSERT INTO pc_layouts (
                pc_name, display_name, area_label, layout_order,
                is_vip, is_enabled, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [(*layout, now) for layout in layouts],
        )

        connection.execute(
            """
            INSERT INTO sessions (
                session_id, pc_name, status, started_at, scheduled_end_at,
                original_seconds, total_seconds, created_at
            )
            VALUES ('session-old-1', 'PC-11', 'completed', ?, ?, 3600, 3600, ?)
            """,
            (now, now, now),
        )
        # Historical session-linked payment (pre-migration: PC was
        # reachable only via the sessions join, never snapshotted).
        connection.execute(
            """
            INSERT INTO payments (
                payment_id, session_id, amount, payment_method, status, created_at
            )
            VALUES ('payment-old-session', 'session-old-1', '20.00', 'cash', 'completed', ?)
            """,
            (now,),
        )
        # Historical member-purchase-style payment (pre-migration: no
        # session at all, so there was never any PC association).
        connection.execute(
            """
            INSERT INTO payments (
                payment_id, session_id, amount, payment_method, status, created_at
            )
            VALUES ('payment-old-member', NULL, '50.00', 'gcash', 'completed', ?)
            """,
            (now,),
        )
        connection.commit()
    finally:
        connection.close()


def test_fresh_database() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        database_path = Path(temp_directory) / "fresh.sqlite3"
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)

        from server.database import initialize_database

        initialize_database()

        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        try:
            # Exactly 4 income groups, stable ids, correct names.
            group_rows = connection.execute(
                "SELECT income_group_id, name, is_active FROM income_groups ORDER BY income_group_id"
            ).fetchall()
            groups = {row["income_group_id"]: row["name"] for row in group_rows}
            assert groups == {
                "gene": "Gene",
                "mama-lala": "Mama/Lala",
                "tyrone": "Tyrone",
                "united": "United",
            }, groups
            assert all(row["is_active"] == 1 for row in group_rows)

            # 15 default PCs, correctly mapped, is_vip untouched.
            pc_rows = connection.execute(
                "SELECT pc_name, income_group_id, is_vip FROM pc_layouts ORDER BY pc_name"
            ).fetchall()
            assert len(pc_rows) == 15
            for row in pc_rows:
                assert row["income_group_id"] == DEFAULT_PC_INCOME_GROUP[row["pc_name"]]
            vip_row = connection.execute(
                "SELECT is_vip FROM pc_layouts WHERE pc_name = 'PC-15'"
            ).fetchone()
            assert vip_row["is_vip"] == 1
            non_vip_row = connection.execute(
                "SELECT is_vip FROM pc_layouts WHERE pc_name = 'PC-01'"
            ).fetchone()
            assert non_vip_row["is_vip"] == 0

            # Payment attribution columns exist.
            payment_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(payments)").fetchall()
            }
            for column in (
                "pc_name_snapshot",
                "income_group_id_snapshot",
                "income_group_name_snapshot",
                "attribution_status",
                "attribution_assigned_at",
                "attribution_session_id",
            ):
                assert column in payment_columns, column

            # Pending-attribution partial index exists with the right shape.
            index_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_payments_pending_attribution'"
            ).fetchone()
            assert index_row is not None
            assert "attribution_status" in index_row["sql"]
            assert "pending" in index_row["sql"]

            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            connection.close()

        # Second init: no duplicate income groups, no duplicate PCs, same mapping.
        initialize_database()
        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        try:
            assert connection.execute("SELECT COUNT(*) FROM income_groups").fetchone()[0] == 4
            assert connection.execute("SELECT COUNT(*) FROM pc_layouts").fetchone()[0] == 15
            pc_rows = connection.execute(
                "SELECT pc_name, income_group_id FROM pc_layouts ORDER BY pc_name"
            ).fetchall()
            for row in pc_rows:
                assert row["income_group_id"] == DEFAULT_PC_INCOME_GROUP[row["pc_name"]]
        finally:
            connection.close()

    print("Fresh-database income-group migration test passed.")


def test_upgrade_from_existing_shop_database() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        database_path = Path(temp_directory) / "upgrade.sqlite3"
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
        create_old_database(database_path)

        from server.database import initialize_database

        # First init after upgrade: backfill assigns the 15 known default
        # PCs (all NULL beforehand), leaves the custom PC-99 alone, and
        # both historical payments become 'assigned' with no group data.
        initialize_database()

        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        try:
            assert connection.execute("SELECT COUNT(*) FROM pc_layouts").fetchone()[0] == 16

            pc_rows = connection.execute(
                "SELECT pc_name, income_group_id, is_vip FROM pc_layouts WHERE pc_name != 'PC-99' ORDER BY pc_name"
            ).fetchall()
            for row in pc_rows:
                assert row["income_group_id"] == DEFAULT_PC_INCOME_GROUP[row["pc_name"]]

            custom_row = connection.execute(
                "SELECT income_group_id, is_vip, area_label FROM pc_layouts WHERE pc_name = 'PC-99'"
            ).fetchone()
            assert custom_row["income_group_id"] is None
            assert custom_row["is_vip"] == 0
            assert custom_row["area_label"] == "CUSTOM"

            vip_row = connection.execute(
                "SELECT is_vip FROM pc_layouts WHERE pc_name = 'PC-15'"
            ).fetchone()
            assert vip_row["is_vip"] == 1

            assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 2
            for payment_id in ("payment-old-session", "payment-old-member"):
                row = connection.execute(
                    """
                    SELECT status, attribution_status, pc_name_snapshot,
                           income_group_id_snapshot, income_group_name_snapshot,
                           attribution_assigned_at, attribution_session_id
                    FROM payments WHERE payment_id = ?
                    """,
                    (payment_id,),
                ).fetchone()
                assert row["status"] == "completed"
                assert row["attribution_status"] == "assigned"
                assert row["pc_name_snapshot"] is None
                assert row["income_group_id_snapshot"] is None
                assert row["income_group_name_snapshot"] is None
                assert row["attribution_assigned_at"] is None
                assert row["attribution_session_id"] is None

            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            connection.close()

        # Simulate an owner manually reassigning PC-01 away from the
        # initial default mapping (United -> Tyrone) after the first init.
        connection = sqlite3.connect(database_path)
        try:
            connection.execute(
                "UPDATE pc_layouts SET income_group_id = 'tyrone' WHERE pc_name = 'PC-01'"
            )
            connection.commit()
        finally:
            connection.close()

        # Second init must not stomp the admin edit, must not duplicate
        # income groups, and must leave everything else exactly as-is.
        initialize_database()

        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        try:
            assert connection.execute("SELECT COUNT(*) FROM income_groups").fetchone()[0] == 4
            reassigned_row = connection.execute(
                "SELECT income_group_id FROM pc_layouts WHERE pc_name = 'PC-01'"
            ).fetchone()
            assert reassigned_row["income_group_id"] == "tyrone"

            # every other default PC keeps its original mapping
            other_rows = connection.execute(
                "SELECT pc_name, income_group_id FROM pc_layouts WHERE pc_name NOT IN ('PC-01', 'PC-99') ORDER BY pc_name"
            ).fetchall()
            for row in other_rows:
                assert row["income_group_id"] == DEFAULT_PC_INCOME_GROUP[row["pc_name"]]

            assert connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0] == 2
            assert connection.execute("SELECT COUNT(*) FROM pc_layouts").fetchone()[0] == 16
        finally:
            connection.close()

    print("Upgrade-from-existing-shop-database income-group migration test passed.")


def test_schema_row_mapping_reads_new_columns_safely() -> None:
    """server/schemas.py + repository row-mapping functions must be able
    to read the new columns without error, both when present (post-
    migration) and absent (defensive fallback, matching the existing
    idempotency_key precedent in payment_repository.row_to_payment)."""
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        database_path = Path(temp_directory) / "mapping.sqlite3"
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)

        from server.database import get_connection, initialize_database
        from server.payment_repository import row_to_payment
        from server.pc_layout_repository import PCLayoutRepository, row_to_income_group

        initialize_database()

        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM income_groups WHERE income_group_id = 'united'"
            ).fetchone()
        income_group = row_to_income_group(row)
        assert income_group.income_group_id == "united"
        assert income_group.name == "United"
        assert income_group.is_active is True

        layout = PCLayoutRepository().get_layout("PC-11")
        assert layout is not None
        assert layout.income_group_id == "gene"
        assert layout.is_vip is False

        now = "2026-08-11T00:00:00+00:00"
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO sessions (
                    session_id, pc_name, status, started_at, scheduled_end_at,
                    original_seconds, total_seconds, created_at
                )
                VALUES ('session-map-1', 'PC-11', 'completed', ?, ?, 3600, 3600, ?)
                """,
                (now, now, now),
            )
            connection.execute(
                """
                INSERT INTO payments (
                    payment_id, session_id, amount, payment_method, status, created_at
                )
                VALUES ('payment-map-1', 'session-map-1', '20.00', 'cash', 'completed', ?)
                """,
                (now,),
            )
            payment_row = connection.execute(
                "SELECT * FROM payments WHERE payment_id = 'payment-map-1'"
            ).fetchone()
        payment = row_to_payment(payment_row)
        assert payment.attribution_status == "assigned"
        assert payment.pc_name_snapshot is None
        assert payment.income_group_id_snapshot is None
        assert payment.income_group_name_snapshot is None
        assert payment.attribution_assigned_at is None
        assert payment.attribution_session_id is None

    print("Schema/row-mapping read-safety test passed.")


def main() -> None:
    test_fresh_database()
    test_upgrade_from_existing_shop_database()
    test_schema_row_mapping_reads_new_columns_safely()
    print("Income-group migration smoke tests passed.")


if __name__ == "__main__":
    main()
