"""Legacy default-rate seeder cleanup.

server/database.py's old seed_default_rates() (removed) inserted four ₱0.00
placeholder packages ("30 Minutes"/"1 Hour"/"2 Hours"/"Overnight", ids
rate-30-minutes/rate-1-hour/rate-2-hours/rate-overnight) on any database
whose rate_profiles table was still completely empty at first startup.
seed_required_default_pricing() is now the single authoritative seeder for
default package pricing. This file covers:
  - a genuinely fresh database gets exactly the eight owner-approved
    packages (Standard Midnight Promo included until the owner ended it --
    see tests/smoke_midnight_promo_retirement.py) and none of the four
    obsolete placeholders
  - re-running initialize_database() never duplicates anything
  - deactivate_untouched_legacy_default_rates() conservatively deactivates
    (never deletes) a legacy-id row only when it is still exactly what the
    old seeder left behind, and never touches one an owner has since edited
  - unrelated owner-edited/disabled/custom rates are never touched
  - Open Time and eligibility enforcement are unaffected by this change
"""

import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

APPROVED_PACKAGE_NAMES = [
    "Standard Guest - P5 (20 Minutes)",
    "Standard Guest - P10 (40 Minutes)",
    "Standard Guest - P15 (1 Hour)",
    "VIP Guest - P30 (1 Hour)",
    "VIP Guest - P60 (2 Hours)",
    "VIP Guest - P90 (3 Hours)",
    "VIP Guest - P120 (4 Hours)",
    "VIP Guest - P250 (10 Hour Promo)",
]

LEGACY_IDS = ["rate-30-minutes", "rate-1-hour", "rate-2-hours", "rate-overnight"]


def fresh_db(temp_directory: str, name: str) -> Path:
    path = Path(temp_directory) / name
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(path)
    return path


def test_empty_db_initializes_cleanly(temp_directory: str) -> None:
    fresh_db(temp_directory, "empty.sqlite3")
    from server.database import initialize_database, get_connection

    initialize_database()
    with get_connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM rate_profiles").fetchone()[0]
    assert count > 0, "initialization must still seed something on a completely empty database"


def test_fresh_db_has_exactly_the_eight_approved_packages(temp_directory: str) -> None:
    fresh_db(temp_directory, "approved.sqlite3")
    from server.database import initialize_database, get_connection

    initialize_database()
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT name FROM rate_profiles WHERE rate_type = 'package' ORDER BY sort_order"
        ).fetchall()
    assert [row["name"] for row in rows] == APPROVED_PACKAGE_NAMES


def test_fresh_db_has_none_of_the_four_legacy_placeholders(temp_directory: str) -> None:
    fresh_db(temp_directory, "no-legacy.sqlite3")
    from server.database import initialize_database, get_connection

    initialize_database()
    with get_connection() as connection:
        for rate_id in LEGACY_IDS:
            row = connection.execute(
                "SELECT 1 FROM rate_profiles WHERE rate_id = ?", (rate_id,)
            ).fetchone()
            assert row is None, f"{rate_id} must not be created on a fresh database"


def test_second_initialization_creates_no_duplicates(temp_directory: str) -> None:
    fresh_db(temp_directory, "idempotent.sqlite3")
    from server.database import initialize_database, get_connection

    initialize_database()
    with get_connection() as connection:
        first_count = connection.execute("SELECT COUNT(*) FROM rate_profiles").fetchone()[0]
    initialize_database()
    initialize_database()
    with get_connection() as connection:
        second_count = connection.execute("SELECT COUNT(*) FROM rate_profiles").fetchone()[0]
        names = [
            row["name"]
            for row in connection.execute(
                "SELECT name FROM rate_profiles WHERE rate_type = 'package' ORDER BY sort_order"
            ).fetchall()
        ]
    assert second_count == first_count
    assert names == APPROVED_PACKAGE_NAMES


def _insert_row(connection, **kwargs) -> None:
    connection.execute(
        """
        INSERT INTO rate_profiles (
            rate_id, name, duration_seconds, price, is_active, sort_order,
            rate_type, station_scope, guest_only, member_eligible,
            eligible_start_local_time, eligible_end_local_time,
            created_at, updated_at
        ) VALUES (
            :rate_id, :name, :duration_seconds, :price, :is_active, :sort_order,
            :rate_type, :station_scope, :guest_only, :member_eligible,
            :eligible_start_local_time, :eligible_end_local_time,
            :created_at, :updated_at
        )
        """,
        {
            "station_scope": "standard",
            "guest_only": 0,
            "member_eligible": 1,
            "eligible_start_local_time": None,
            "eligible_end_local_time": None,
            **kwargs,
        },
    )


def test_existing_owner_edited_current_rate_remains_unchanged(temp_directory: str) -> None:
    """A REQUIRED_DEFAULT_RATES row (e.g. rate-standard-guest-5) that an
    owner has already repriced must never be reset back to the confirmed
    default just because it shares that id."""
    db_path = fresh_db(temp_directory, "owner-edited-current.sqlite3")
    from server.database import initialize_database, get_connection

    initialize_database()
    with get_connection() as connection:
        connection.execute(
            "UPDATE rate_profiles SET price = '7.00', updated_at = ? WHERE rate_id = 'rate-standard-guest-5'",
            ("2026-01-01T00:00:00+00:00",),
        )
    initialize_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT price FROM rate_profiles WHERE rate_id = 'rate-standard-guest-5'"
        ).fetchone()
    assert row["price"] == "7.00"


def test_existing_intentionally_disabled_current_rate_remains_disabled(temp_directory: str) -> None:
    fresh_db(temp_directory, "owner-disabled-current.sqlite3")
    from server.database import initialize_database, get_connection

    initialize_database()
    with get_connection() as connection:
        connection.execute(
            "UPDATE rate_profiles SET is_active = 0, updated_at = ? WHERE rate_id = 'rate-vip-guest-1hr'",
            ("2026-01-01T00:00:00+00:00",),
        )
    initialize_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT is_active FROM rate_profiles WHERE rate_id = 'rate-vip-guest-1hr'"
        ).fetchone()
    assert row["is_active"] == 0


def test_existing_unrelated_custom_rate_remains_untouched(temp_directory: str) -> None:
    fresh_db(temp_directory, "custom-rate.sqlite3")
    from server.database import initialize_database, get_connection

    initialize_database()
    with get_connection() as connection:
        _insert_row(
            connection,
            rate_id="rate-owner-custom-lan-party",
            name="LAN Party Special",
            duration_seconds=4 * 3600,
            price="123.45",
            is_active=1,
            sort_order=99,
            rate_type="package",
            created_at="2025-05-01T00:00:00+00:00",
            updated_at="2025-05-01T00:00:00+00:00",
        )
    initialize_database()
    initialize_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT name, price, is_active FROM rate_profiles WHERE rate_id = 'rate-owner-custom-lan-party'"
        ).fetchone()
    assert row is not None
    assert row["name"] == "LAN Party Special"
    assert row["price"] == "123.45"
    assert row["is_active"] == 1


def test_untouched_legacy_placeholder_is_deactivated_not_deleted(temp_directory: str) -> None:
    """A database carrying one of the four legacy rows exactly as the old
    seed_default_rates() left it (never edited since creation) gets that
    row conservatively deactivated -- never deleted, so any historical
    session referencing it by rate_id still resolves -- and never
    reactivated by a later restart."""
    fresh_db(temp_directory, "legacy-untouched.sqlite3")
    from server.database import initialize_database, get_connection

    initialize_database()
    now = "2019-01-01T00:00:00+00:00"
    with get_connection() as connection:
        _insert_row(
            connection,
            rate_id="rate-1-hour",
            name="1 Hour",
            duration_seconds=3600,
            price="0.00",
            is_active=1,
            sort_order=20,
            rate_type="package",
            created_at=now,
            updated_at=now,
        )
    initialize_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT name, price, is_active FROM rate_profiles WHERE rate_id = 'rate-1-hour'"
        ).fetchone()
    assert row is not None, "the row itself must be preserved, not deleted"
    assert row["name"] == "1 Hour"
    assert row["price"] == "0.00"
    assert row["is_active"] == 0

    # Re-running must not flip it back on, and an owner who deliberately
    # re-enables it afterward must never be silently overridden again.
    initialize_database()
    with get_connection() as connection:
        still_off = connection.execute(
            "SELECT is_active FROM rate_profiles WHERE rate_id = 'rate-1-hour'"
        ).fetchone()["is_active"]
        connection.execute(
            "UPDATE rate_profiles SET is_active = 1, updated_at = ? WHERE rate_id = 'rate-1-hour'",
            ("2026-01-01T00:00:00+00:00",),
        )
    assert still_off == 0
    initialize_database()
    with get_connection() as connection:
        reenabled = connection.execute(
            "SELECT is_active FROM rate_profiles WHERE rate_id = 'rate-1-hour'"
        ).fetchone()["is_active"]
    assert reenabled == 1, "an owner's later re-enable must never be fought by a future restart"


def test_owner_edited_legacy_id_row_is_never_destroyed(temp_directory: str) -> None:
    """If an owner repurposed one of the four legacy ids into a real house
    rate (renamed, repriced, or just edited at all since creation), the
    cleanup must leave it completely alone -- content and active state."""
    fresh_db(temp_directory, "legacy-owner-edited.sqlite3")
    from server.database import initialize_database, get_connection

    initialize_database()
    with get_connection() as connection:
        _insert_row(
            connection,
            rate_id="rate-overnight",
            name="Overnight House Rate",
            duration_seconds=8 * 3600,
            price="25.00",
            is_active=1,
            sort_order=40,
            rate_type="package",
            created_at="2019-01-01T00:00:00+00:00",
            updated_at="2021-06-01T00:00:00+00:00",
        )
    initialize_database()
    initialize_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT name, price, is_active FROM rate_profiles WHERE rate_id = 'rate-overnight'"
        ).fetchone()
    assert row["name"] == "Overnight House Rate"
    assert row["price"] == "25.00"
    assert row["is_active"] == 1


def test_legacy_row_already_disabled_by_owner_is_left_alone(temp_directory: str) -> None:
    """A legacy-id row that still has the exact placeholder content but was
    already disabled by an owner (is_active=0, never otherwise edited) is
    not something the cleanup needs to act on -- and must not error."""
    fresh_db(temp_directory, "legacy-already-off.sqlite3")
    from server.database import initialize_database, get_connection

    initialize_database()
    now = "2019-01-01T00:00:00+00:00"
    with get_connection() as connection:
        _insert_row(
            connection,
            rate_id="rate-2-hours",
            name="2 Hours",
            duration_seconds=2 * 3600,
            price="0.00",
            is_active=0,
            sort_order=30,
            rate_type="package",
            created_at=now,
            updated_at=now,
        )
    initialize_database()
    with get_connection() as connection:
        row = connection.execute(
            "SELECT is_active FROM rate_profiles WHERE rate_id = 'rate-2-hours'"
        ).fetchone()
    assert row["is_active"] == 0


def test_midnight_promo_absent_and_vip_eligibility_unaffected(temp_directory: str) -> None:
    fresh_db(temp_directory, "eligibility.sqlite3")
    from server.database import initialize_database, get_connection

    initialize_database()
    with get_connection() as connection:
        # Owner ended Midnight Promo -- a fresh install never seeds it.
        midnight = connection.execute(
            "SELECT 1 FROM rate_profiles WHERE rate_id = 'rate-standard-midnight-promo'"
        ).fetchone()
        vip_guest = connection.execute(
            "SELECT * FROM rate_profiles WHERE rate_id = 'rate-vip-guest-1hr'"
        ).fetchone()
    assert midnight is None
    assert vip_guest["station_scope"] == "vip"


def test_metered_calculator_reactivated_for_standard_amount(temp_directory: str) -> None:
    """GET /rates/metered and POST /rates/calculate-time back the Standard
    tab's amount-based custom session -- reactivated for that purpose.
    Creating/editing/reactivating a *new* metered rate through the rates
    admin API remains permanently rejected (see tests/smoke_rates.py)."""
    fresh_db(temp_directory, "metered-calculator.sqlite3")
    from server.database import initialize_database
    from server.main import get_metered_rate, calculate_time
    from server.schemas import CalculateTimeRequest

    initialize_database()
    rate = get_metered_rate()
    assert rate.name == "Regular Rate"
    assert rate.is_active is True
    # ₱5 = 20 min, i.e. ₱1 = 4 minutes -- matches the authoritative
    # Standard package pricing, not the old (incorrect) ₱1 = 5 minutes.
    assert rate.price == Decimal("5.00")
    assert rate.duration_seconds == 1200

    calculated = calculate_time(CalculateTimeRequest(amount=Decimal("20.00")))
    assert calculated.duration_seconds == 4800, "₱20 must buy 80 minutes (1 hr 20 min)"


def main() -> None:
    tests = [
        test_empty_db_initializes_cleanly,
        test_fresh_db_has_exactly_the_eight_approved_packages,
        test_fresh_db_has_none_of_the_four_legacy_placeholders,
        test_second_initialization_creates_no_duplicates,
        test_existing_owner_edited_current_rate_remains_unchanged,
        test_existing_intentionally_disabled_current_rate_remains_disabled,
        test_existing_unrelated_custom_rate_remains_untouched,
        test_untouched_legacy_placeholder_is_deactivated_not_deleted,
        test_owner_edited_legacy_id_row_is_never_destroyed,
        test_legacy_row_already_disabled_by_owner_is_left_alone,
        test_midnight_promo_absent_and_vip_eligibility_unaffected,
        test_metered_calculator_reactivated_for_standard_amount,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
    print("Legacy rate seeder cleanup smoke test passed.")


if __name__ == "__main__":
    main()
