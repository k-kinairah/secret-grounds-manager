"""Owner ended Midnight Promo.

Covers server/database.py's deactivate_untouched_midnight_promo_rate: a
fresh install never seeds rate-standard-midnight-promo again, an existing
shop that already has the row (from before this retirement) gets it
deactivated non-destructively on the next startup, and an owner who has
since repriced/renamed/reactivated their own copy of it is never silently
overridden.
"""

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def insert_legacy_midnight_promo(connection, *, price: str = "10.00", is_active: int = 1) -> None:
    from server.time_utils import current_utc_time

    now = current_utc_time().isoformat()
    connection.execute(
        """
        INSERT INTO rate_profiles (
            rate_id, name, duration_seconds, price, is_active, sort_order,
            rate_type, station_scope, guest_only, member_eligible,
            eligible_start_local_time, eligible_end_local_time,
            created_at, updated_at
        )
        VALUES (
            'rate-standard-midnight-promo', 'Standard Midnight Promo', 3600, ?, ?, 4,
            'package', 'standard', 1, 0, '00:00', '05:00', ?, ?
        )
        """,
        (price, is_active, now, now),
    )


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "midnight-fresh.sqlite3")

        from server.database import get_connection, initialize_database

        # 1. A fresh install never seeds it at all.
        initialize_database()
        with get_connection() as connection:
            row = connection.execute(
                "SELECT 1 FROM rate_profiles WHERE rate_id = 'rate-standard-midnight-promo'"
            ).fetchone()
        assert row is None, "A fresh install must never seed Midnight Promo"

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "midnight-existing.sqlite3")

        from server.database import (
            deactivate_untouched_midnight_promo_rate,
            get_connection,
            initialize_database,
        )

        # 2. A shop that already has the row (seeded before this retirement,
        # never touched since) gets it deactivated -- not deleted -- on the
        # next startup.
        initialize_database()
        with get_connection() as connection:
            insert_legacy_midnight_promo(connection)
            connection.commit()

        with get_connection() as connection:
            deactivate_untouched_midnight_promo_rate(connection)
            connection.commit()

        with get_connection() as connection:
            row = connection.execute(
                "SELECT name, duration_seconds, price, is_active, station_scope, guest_only, "
                "member_eligible, eligible_start_local_time, eligible_end_local_time "
                "FROM rate_profiles WHERE rate_id = 'rate-standard-midnight-promo'"
            ).fetchone()
        assert row is not None, "Deactivation must never delete the historical row"
        assert row["is_active"] == 0
        # Every other field -- what any historical session's rate_id still
        # resolves to for billing/reports -- is completely unchanged.
        assert row["name"] == "Standard Midnight Promo"
        assert row["duration_seconds"] == 3600
        assert row["price"] == "10.00"
        assert row["station_scope"] == "standard"
        assert row["guest_only"] == 1
        assert row["member_eligible"] == 0
        assert row["eligible_start_local_time"] == "00:00"
        assert row["eligible_end_local_time"] == "05:00"

        # Running it again (e.g. next restart) is a no-op -- is_active=0
        # means the WHERE clause never matches this row again.
        with get_connection() as connection:
            deactivate_untouched_midnight_promo_rate(connection)
            connection.commit()
        with get_connection() as connection:
            still_off = connection.execute(
                "SELECT is_active FROM rate_profiles WHERE rate_id = 'rate-standard-midnight-promo'"
            ).fetchone()
        assert still_off["is_active"] == 0

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "midnight-owner-edited.sqlite3")

        from server.database import (
            deactivate_untouched_midnight_promo_rate,
            get_connection,
            initialize_database,
        )

        # 3. An owner who already repriced their own copy of this rate (or
        # otherwise edited it, or had already deactivated/reactivated it
        # themselves) must never be silently overridden -- only a row that
        # is still byte-for-byte what the seeder created is touched.
        initialize_database()
        with get_connection() as connection:
            insert_legacy_midnight_promo(connection, price="12.00")
            connection.commit()

        with get_connection() as connection:
            deactivate_untouched_midnight_promo_rate(connection)
            connection.commit()

        with get_connection() as connection:
            row = connection.execute(
                "SELECT price, is_active FROM rate_profiles WHERE rate_id = 'rate-standard-midnight-promo'"
            ).fetchone()
        assert row["price"] == "12.00"
        assert row["is_active"] == 1, "An owner-repriced row must never be auto-deactivated"

    print("Midnight Promo retirement smoke test passed.")


if __name__ == "__main__":
    main()
