"""Standard metered rate pricing correction.

The Regular Rate metered row was originally seeded at an incorrect
price/duration (see git history). Covers server/database.py's
correct_regular_metered_rate_pricing: a fresh install now seeds the
authoritative ₱5 = 20 minutes (₱1 = 4 minutes) directly, an existing shop
that already has the old, untouched row gets it corrected on the next
startup, and an owner who has since repriced/renamed it is never silently
overridden. Also covers the resulting end-to-end calculation via
POST /rates/calculate-time (the Standard tab's live preview).
"""

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def insert_legacy_regular_rate(connection, *, price: str = "1.00", duration_seconds: int = 300, is_active: int = 1) -> None:
    from server.time_utils import current_utc_time

    now = current_utc_time().isoformat()
    connection.execute(
        """
        INSERT INTO rate_profiles (
            rate_id, name, duration_seconds, price, is_active, sort_order,
            rate_type, created_at, updated_at
        )
        VALUES ('rate-regular-metered', 'Regular Rate', ?, ?, ?, 0, 'metered', ?, ?)
        """,
        (duration_seconds, price, is_active, now, now),
    )


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "metered-fresh.sqlite3")

        from server.database import get_connection, initialize_database

        # 1. A fresh install seeds the authoritative pricing directly --
        # ₱5 = 20 minutes (1200 seconds), i.e. ₱1 = 4 minutes.
        initialize_database()
        with get_connection() as connection:
            row = connection.execute(
                "SELECT price, duration_seconds, is_active FROM rate_profiles "
                "WHERE rate_id = 'rate-regular-metered'"
            ).fetchone()
        assert row is not None
        assert row["price"] == "5.00"
        assert row["duration_seconds"] == 1200
        assert row["is_active"] == 1

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "metered-existing-active.sqlite3")

        from server.database import (
            correct_regular_metered_rate_pricing,
            get_connection,
            initialize_database,
        )

        # 2. A shop that already has the old, untouched, *active* row gets
        # it corrected -- not replaced -- on the next startup. The row's
        # own active state is left exactly as it was.
        initialize_database()
        with get_connection() as connection:
            connection.execute("DELETE FROM rate_profiles WHERE rate_id = 'rate-regular-metered'")
            insert_legacy_regular_rate(connection, price="1.00", duration_seconds=300, is_active=1)
            connection.commit()

        with get_connection() as connection:
            correct_regular_metered_rate_pricing(connection)
            connection.commit()

        with get_connection() as connection:
            row = connection.execute(
                "SELECT price, duration_seconds, is_active FROM rate_profiles "
                "WHERE rate_id = 'rate-regular-metered'"
            ).fetchone()
        assert row["price"] == "5.00"
        assert row["duration_seconds"] == 1200
        assert row["is_active"] == 1, "Active state must be preserved, not changed by this correction"

        # Running it again (e.g. next restart) is a no-op -- price is no
        # longer '1.00', so the WHERE clause never matches this row again.
        with get_connection() as connection:
            correct_regular_metered_rate_pricing(connection)
            connection.commit()
        with get_connection() as connection:
            still_correct = connection.execute(
                "SELECT price, duration_seconds FROM rate_profiles WHERE rate_id = 'rate-regular-metered'"
            ).fetchone()
        assert still_correct["price"] == "5.00"
        assert still_correct["duration_seconds"] == 1200

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "metered-existing-inactive.sqlite3")

        from server.database import (
            correct_regular_metered_rate_pricing,
            get_connection,
            initialize_database,
        )

        # 3. An owner who deactivated the old untouched row (allowed --
        # see tests/smoke_rates.py) still gets its pricing corrected if
        # they ever reactivate it -- the correction never touches
        # is_active itself.
        initialize_database()
        with get_connection() as connection:
            connection.execute("DELETE FROM rate_profiles WHERE rate_id = 'rate-regular-metered'")
            insert_legacy_regular_rate(connection, price="1.00", duration_seconds=300, is_active=0)
            connection.commit()

        with get_connection() as connection:
            correct_regular_metered_rate_pricing(connection)
            connection.commit()

        with get_connection() as connection:
            row = connection.execute(
                "SELECT price, duration_seconds, is_active FROM rate_profiles "
                "WHERE rate_id = 'rate-regular-metered'"
            ).fetchone()
        assert row["price"] == "5.00"
        assert row["duration_seconds"] == 1200
        assert row["is_active"] == 0, "Correcting pricing must never reactivate a deactivated row"

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "metered-owner-repriced.sqlite3")

        from server.database import (
            correct_regular_metered_rate_pricing,
            get_connection,
            initialize_database,
        )

        # 4. An owner who has already repriced this rate to something of
        # their own choosing must never be silently overridden.
        initialize_database()
        with get_connection() as connection:
            connection.execute("DELETE FROM rate_profiles WHERE rate_id = 'rate-regular-metered'")
            insert_legacy_regular_rate(connection, price="2.50", duration_seconds=600, is_active=1)
            connection.commit()

        with get_connection() as connection:
            correct_regular_metered_rate_pricing(connection)
            connection.commit()

        with get_connection() as connection:
            row = connection.execute(
                "SELECT price, duration_seconds FROM rate_profiles WHERE rate_id = 'rate-regular-metered'"
            ).fetchone()
        assert row["price"] == "2.50", "An owner-repriced row must never be auto-corrected"
        assert row["duration_seconds"] == 600

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "metered-uniqueness.sqlite3")

        from server.database import get_connection, initialize_database
        from server.rate_repository import RateRepository

        # 5. Exactly one eligible active Standard metered rate exists.
        initialize_database()
        repository = RateRepository()
        active_metered = [rate for rate in repository.list_rates(include_inactive=False) if rate.rate_type == "metered"]
        assert len(active_metered) == 1
        assert active_metered[0].station_scope == "standard"
        with get_connection() as connection:
            all_metered_count = connection.execute(
                "SELECT COUNT(*) FROM rate_profiles WHERE rate_type = 'metered' AND is_active = 1"
            ).fetchone()[0]
        assert all_metered_count == 1

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "metered-calculation.sqlite3")

        from server.database import initialize_database
        from server.main import calculate_time
        from server.schemas import CalculateTimeRequest

        # 6. End-to-end calculation matches the authoritative worked
        # examples exactly.
        initialize_database()
        for amount, expected_seconds in [
            ("5.00", 1200),   # 20 minutes
            ("7.00", 1680),   # 28 minutes
            ("10.00", 2400),  # 40 minutes
            ("15.00", 3600),  # 60 minutes (1 hour)
            ("20.00", 4800),  # 80 minutes (1 hour 20 minutes)
        ]:
            result = calculate_time(CalculateTimeRequest(amount=Decimal(amount)))
            assert result.duration_seconds == expected_seconds, (
                f"₱{amount} must buy {expected_seconds} seconds, got {result.duration_seconds}"
            )

    print("Regular metered rate pricing smoke test passed.")


if __name__ == "__main__":
    main()
