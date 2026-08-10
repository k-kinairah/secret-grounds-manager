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

        # Pre-Stage-0 shape of the member/rate tables: no vip_balance_seconds,
        # no balance_type, no station_scope/guest_only/member_eligible/
        # eligibility columns, no reserved_vip_seconds/consumed_vip_seconds.
        # This is what the already-deployed production database looks like
        # today, including a row already sitting under the 'member-promo'
        # id with a price an owner could plausibly have edited away from
        # the confirmed P50 default.
        connection.execute(
            """
            CREATE TABLE members (
                member_id TEXT PRIMARY KEY,
                member_code TEXT NOT NULL UNIQUE,
                username TEXT,
                full_name TEXT NOT NULL,
                phone_number TEXT,
                pin_hash TEXT,
                status TEXT NOT NULL CHECK (status IN ('active', 'inactive')),
                time_balance_seconds INTEGER NOT NULL CHECK (time_balance_seconds >= 0),
                joined_at TEXT NOT NULL,
                expires_at TEXT,
                created_by_user_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO members (
                member_id, member_code, username, full_name, phone_number,
                pin_hash, status, time_balance_seconds, joined_at, created_at, updated_at
            )
            VALUES (
                'member-old-1', 'SG-OLD0001', 'oldmember', 'Old Member', NULL,
                'hash', 'active', 4321, ?, ?, ?
            )
            """,
            (now, now, now),
        )
        connection.execute(
            """
            CREATE TABLE rate_profiles (
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
        connection.execute(
            """
            INSERT INTO rate_profiles (
                rate_id, name, duration_seconds, price, is_active, sort_order,
                rate_type, created_at, updated_at
            )
            VALUES (
                'rate-30-minutes', '30 Minutes', 1800, '0.00', 1, 10,
                'package', ?, ?
            )
            """,
            (now, now),
        )
        connection.execute(
            """
            CREATE TABLE membership_products (
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
            INSERT INTO membership_products (
                product_id, name, price, time_seconds, product_type,
                is_active, sort_order, created_at, updated_at
            )
            VALUES
                ('membership-fee', 'Membership Fee', '100.00', 21600, 'initial_membership', 1, 10, ?, ?),
                ('member-promo', 'Member Promo (owner-edited)', '75.00', 18000, 'member_topup', 1, 20, ?, ?)
            """,
            (now, now, now, now),
        )
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
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO member_time_transactions (
                transaction_id, member_id, transaction_type, seconds_delta,
                balance_before_seconds, balance_after_seconds, created_at
            )
            VALUES (
                'txn-old-1', 'member-old-1', 'membership_purchase', 21600,
                0, 21600, ?
            )
            """,
            (now,),
        )
        connection.execute(
            """
            CREATE TABLE member_time_reservations (
                reservation_id TEXT PRIMARY KEY,
                member_id TEXT NOT NULL,
                session_id TEXT NOT NULL UNIQUE,
                reserved_seconds INTEGER NOT NULL CHECK (reserved_seconds > 0),
                consumed_seconds INTEGER NOT NULL DEFAULT 0 CHECK (consumed_seconds >= 0),
                status TEXT NOT NULL CHECK (status IN ('active', 'finalized', 'released')),
                created_at TEXT NOT NULL,
                finalized_at TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO member_time_reservations (
                reservation_id, member_id, session_id, reserved_seconds,
                consumed_seconds, status, created_at, finalized_at
            )
            VALUES (
                'reservation-old-1', 'member-old-1', 'session-completed', 1800,
                1800, 'finalized', ?, ?
            )
            """,
            (now, now),
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

            # Standard/VIP snapshot columns: every pre-existing session
            # (including the one that just went through the state-machine
            # rebuild) is correctly backfilled as Standard with no
            # conversion, since VIP consumption did not exist before now.
            for session_id in ("session-active", "session-stranded", "session-completed"):
                row = connection.execute(
                    "SELECT station_type, conversion_applied, vip_seconds_consumed, "
                    "standard_seconds_consumed_at_vip_rate FROM sessions WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
                assert row["station_type"] == "standard"
                assert row["conversion_applied"] == 0
                assert row["vip_seconds_consumed"] is None
                assert row["standard_seconds_consumed_at_vip_rate"] is None

            # members: existing balance untouched, new VIP balance defaults to 0.
            member_row = connection.execute(
                "SELECT time_balance_seconds, vip_balance_seconds FROM members WHERE member_id = 'member-old-1'"
            ).fetchone()
            assert member_row["time_balance_seconds"] == 4321
            assert member_row["vip_balance_seconds"] == 0

            # rate_profiles: existing rate untouched, new columns default to
            # the Standard/guest/member-eligible/no-window baseline.
            rate_row = connection.execute(
                "SELECT price, station_scope, guest_only, member_eligible, "
                "eligible_start_local_time, eligible_end_local_time "
                "FROM rate_profiles WHERE rate_id = 'rate-30-minutes'"
            ).fetchone()
            assert rate_row["price"] == "0.00"
            assert rate_row["station_scope"] == "standard"
            assert rate_row["guest_only"] == 0
            assert rate_row["member_eligible"] == 1
            assert rate_row["eligible_start_local_time"] is None
            assert rate_row["eligible_end_local_time"] is None

            # membership_products: existing prices (including the
            # deliberately "owner-edited" member-promo row) are untouched;
            # both default to the Standard balance type.
            fee_row = connection.execute(
                "SELECT price, time_seconds, balance_type FROM membership_products WHERE product_id = 'membership-fee'"
            ).fetchone()
            assert fee_row["price"] == "100.00"
            assert fee_row["time_seconds"] == 21600
            assert fee_row["balance_type"] == "standard"
            promo_row = connection.execute(
                "SELECT price, time_seconds, balance_type FROM membership_products WHERE product_id = 'member-promo'"
            ).fetchone()
            assert promo_row["price"] == "75.00"
            assert promo_row["time_seconds"] == 18000
            assert promo_row["balance_type"] == "standard"

            # member_time_transactions: existing ledger row backfilled Standard.
            txn_row = connection.execute(
                "SELECT balance_type FROM member_time_transactions WHERE transaction_id = 'txn-old-1'"
            ).fetchone()
            assert txn_row["balance_type"] == "standard"

            # member_time_reservations: existing reservation untouched, new
            # VIP reservation columns default to 0.
            reservation_row = connection.execute(
                "SELECT reserved_seconds, consumed_seconds, reserved_vip_seconds, consumed_vip_seconds "
                "FROM member_time_reservations WHERE reservation_id = 'reservation-old-1'"
            ).fetchone()
            assert reservation_row["reserved_seconds"] == 1800
            assert reservation_row["consumed_seconds"] == 1800
            assert reservation_row["reserved_vip_seconds"] == 0
            assert reservation_row["consumed_vip_seconds"] == 0

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

        # seed_required_default_pricing() is not yet wired into
        # initialize_database() (see server/database.py), but it must be
        # safe to run repeatedly against an already-configured shop: create
        # only what's missing, by fixed id, and never touch an existing row
        # -- including the pre-existing 'membership-fee'/'member-promo'
        # rows and the "owner-edited" member-promo price seeded above.
        from server.database import get_connection, seed_required_default_pricing

        with get_connection() as connection:
            seed_required_default_pricing(connection)

        with get_connection() as connection:
            rate_count_after_first_seed = connection.execute(
                "SELECT COUNT(*) FROM rate_profiles"
            ).fetchone()[0]
            product_count_after_first_seed = connection.execute(
                "SELECT COUNT(*) FROM membership_products"
            ).fetchone()[0]

            for rate_id, expected_price, expected_duration in [
                ("rate-standard-guest-5", "5.00", 1200),
                ("rate-standard-guest-10", "10.00", 2400),
                ("rate-standard-guest-15", "15.00", 3600),
                ("rate-standard-midnight-promo", "10.00", 3600),
                ("rate-vip-guest-1hr", "30.00", 3600),
            ]:
                row = connection.execute(
                    "SELECT price, duration_seconds FROM rate_profiles WHERE rate_id = ?",
                    (rate_id,),
                ).fetchone()
                assert row is not None, f"{rate_id} was not created"
                assert row["price"] == expected_price
                assert row["duration_seconds"] == expected_duration

            midnight_row = connection.execute(
                "SELECT station_scope, guest_only, member_eligible, "
                "eligible_start_local_time, eligible_end_local_time "
                "FROM rate_profiles WHERE rate_id = 'rate-standard-midnight-promo'"
            ).fetchone()
            assert midnight_row["station_scope"] == "standard"
            assert midnight_row["guest_only"] == 1
            assert midnight_row["member_eligible"] == 0
            assert midnight_row["eligible_start_local_time"] == "00:00"
            assert midnight_row["eligible_end_local_time"] == "05:00"

            vip_guest_row = connection.execute(
                "SELECT station_scope FROM rate_profiles WHERE rate_id = 'rate-vip-guest-1hr'"
            ).fetchone()
            assert vip_guest_row["station_scope"] == "vip"

            for product_id, expected_price, expected_seconds, expected_balance_type in [
                ("member-topup-100", "100.00", 28800, "standard"),
                ("vip-promo-250", "250.00", 36000, "vip"),
            ]:
                row = connection.execute(
                    "SELECT price, time_seconds, balance_type FROM membership_products WHERE product_id = ?",
                    (product_id,),
                ).fetchone()
                assert row is not None, f"{product_id} was not created"
                assert row["price"] == expected_price
                assert row["time_seconds"] == expected_seconds
                assert row["balance_type"] == expected_balance_type

            # The pre-existing, "owner-edited" member-promo row must remain
            # exactly as it was -- never reset back to the confirmed P50/4h
            # default just because that default shares its id.
            promo_row = connection.execute(
                "SELECT price, time_seconds FROM membership_products WHERE product_id = 'member-promo'"
            ).fetchone()
            assert promo_row["price"] == "75.00"
            assert promo_row["time_seconds"] == 18000

        # Re-running must never duplicate a row.
        with get_connection() as connection:
            seed_required_default_pricing(connection)

        with get_connection() as connection:
            rate_count_after_second_seed = connection.execute(
                "SELECT COUNT(*) FROM rate_profiles"
            ).fetchone()[0]
            product_count_after_second_seed = connection.execute(
                "SELECT COUNT(*) FROM membership_products"
            ).fetchone()[0]
            assert rate_count_after_second_seed == rate_count_after_first_seed
            assert product_count_after_second_seed == product_count_after_first_seed

        print("Migration smoke test passed.")


if __name__ == "__main__":
    main()
