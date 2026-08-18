"""Stage 2: station scope + eligibility-window enforcement.

Covers server/rate_eligibility.py directly (pure boundary checks) and the
full SessionService integration (start/add-time/prepared-startup), plus the
Stage 2 activation of seed_required_default_pricing() via
initialize_database().
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

MANILA = ZoneInfo("Asia/Manila")


def local_manila(year, month, day, hour, minute) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=MANILA)


def test_timezone_resolution_degrades_to_utc_never_crashes() -> None:
    """Windows ships no IANA tzdata by default; ZoneInfo needs the `tzdata`
    package (see server/requirements.txt) to resolve any name at all. If
    that package were somehow missing from the environment, resolving a
    timezone must degrade to UTC rather than raising and taking down every
    session start."""
    import zoneinfo

    from server import rate_eligibility

    original_zoneinfo = rate_eligibility.ZoneInfo

    def always_missing(_name: str):
        raise zoneinfo.ZoneInfoNotFoundError("simulated missing tzdata")

    rate_eligibility.ZoneInfo = always_missing  # type: ignore[assignment]
    try:
        from datetime import timezone as datetime_timezone

        assert rate_eligibility.resolve_shop_timezone("Asia/Manila") is datetime_timezone.utc
        assert rate_eligibility.resolve_shop_timezone("not a real zone") is datetime_timezone.utc
    finally:
        rate_eligibility.ZoneInfo = original_zoneinfo

    # Sanity check the real (installed) tzdata resolves normally again.
    assert str(rate_eligibility.resolve_shop_timezone("Asia/Manila")) == "Asia/Manila"


def test_pure_eligibility_window() -> None:
    from server.rate_eligibility import is_within_eligibility_window

    start, end = "00:00", "05:00"
    assert is_within_eligibility_window(local_manila(2026, 1, 2, 0, 0), "Asia/Manila", start, end) is True
    assert is_within_eligibility_window(local_manila(2026, 1, 2, 4, 59), "Asia/Manila", start, end) is True
    assert is_within_eligibility_window(local_manila(2026, 1, 2, 5, 0), "Asia/Manila", start, end) is False
    assert is_within_eligibility_window(local_manila(2026, 1, 1, 23, 59), "Asia/Manila", start, end) is False
    # No window at all -> always eligible.
    assert is_within_eligibility_window(local_manila(2026, 1, 1, 13, 0), "Asia/Manila", None, None) is True


def test_validate_rate_for_station_and_context_pure() -> None:
    from server.rate_eligibility import validate_rate_for_station_and_context
    from server.schemas import RateProfile

    now = datetime.now(MANILA)
    standard_rate = RateProfile(
        rate_id="r-std", name="Standard Rate", duration_seconds=1200, price=Decimal("5.00"),
        rate_type="package", is_active=True, sort_order=1, station_scope="standard",
        created_at=now, updated_at=now,
    )
    vip_rate = standard_rate.model_copy(update={"rate_id": "r-vip", "station_scope": "vip"})
    midnight_rate = standard_rate.model_copy(
        update={
            "rate_id": "r-mid", "guest_only": True, "member_eligible": False,
            "eligible_start_local_time": "00:00", "eligible_end_local_time": "05:00",
        }
    )

    # Station scope mismatches.
    try:
        validate_rate_for_station_and_context(standard_rate, True, False, now, "Asia/Manila")
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("Standard rate on a VIP station must be rejected")

    try:
        validate_rate_for_station_and_context(vip_rate, False, False, now, "Asia/Manila")
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("VIP rate on a Standard station must be rejected")

    # Matching scope, within window: allowed.
    validate_rate_for_station_and_context(
        midnight_rate, False, False, local_manila(2026, 1, 2, 0, 0), "Asia/Manila"
    )

    # Outside window: rejected.
    try:
        validate_rate_for_station_and_context(
            midnight_rate, False, False, local_manila(2026, 1, 2, 5, 0), "Asia/Manila"
        )
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("Midnight promo at 05:00 local must be rejected")

    # Midnight rate is member-ineligible: rejected through a member context
    # even though no current endpoint can reach this combination -- this is
    # the single authoritative choke point enforcing it regardless.
    try:
        validate_rate_for_station_and_context(
            midnight_rate, False, True, local_manila(2026, 1, 2, 0, 0), "Asia/Manila"
        )
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("guest_only/member_eligible=False rate must be rejected in a member context")


def create_service(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    from server.billing_service import BillingService
    from server.member_repository import MemberRepository
    from server.payment_repository import PaymentRepository
    from server.pc_layout_repository import PCLayoutRepository
    from server.prepared_session_repository import PreparedSessionRepository
    from server.rate_repository import RateRepository
    from server.schemas import CommandAcknowledgement, PCLayoutUpdate, PCRecord
    from server.session_repository import SessionRepository
    from server.session_service import SessionService
    from server.shift_repository import ShiftRepository

    pc_layout_repository = PCLayoutRepository()
    for pc_name, is_vip in [("PC-STD", False), ("PC-STD-2", False), ("PC-STD-3", False), ("PC-VIP", True), ("PC-VIP-2", True)]:
        pc_layout_repository.upsert_layout(
            pc_name,
            PCLayoutUpdate(display_name=pc_name, area_label="TEST", layout_order=1, is_vip=is_vip, is_enabled=True),
        )

    session_repository = SessionRepository()
    payment_repository = PaymentRepository()
    shift_repository = ShiftRepository()

    class DummyConnectionManager:
        def is_connected(self, _: str) -> bool:
            return True

    pc_registry = {
        pc_name: PCRecord(
            pc_name=pc_name, ip_address="127.0.0.1", status="online", last_seen=datetime.now(timezone.utc)
        )
        for pc_name in ["PC-STD", "PC-STD-2", "PC-STD-3", "PC-VIP", "PC-VIP-2", "PC-PREP-VIP", "PC-PREP-STD"]
    }
    for pc_name in ["PC-PREP-VIP", "PC-PREP-STD"]:
        pc_layout_repository.upsert_layout(
            pc_name,
            PCLayoutUpdate(
                display_name=pc_name, area_label="TEST", layout_order=1,
                is_vip=(pc_name == "PC-PREP-VIP"), is_enabled=True,
            ),
        )

    service = SessionService(
        repository=session_repository,
        rate_repository=RateRepository(),
        connection_manager=DummyConnectionManager(),  # type: ignore[arg-type]
        pc_registry=pc_registry,
        command_timeout_seconds=1,
        billing_service=BillingService(session_repository, payment_repository, shift_repository),
        member_repository=MemberRepository(payment_repository, shift_repository),
        prepared_session_repository=PreparedSessionRepository(),
        pc_layout_repository=pc_layout_repository,
    )

    async def acknowledge_command(*_: object) -> list[object]:
        return [
            CommandAcknowledgement(command_id="test", status="received"),
            CommandAcknowledgement(command_id="test", status="completed"),
        ]

    service.send_required_command = acknowledge_command  # type: ignore[method-assign]
    return service, session_repository


def get_rate_id(rate_repository, name: str) -> str:
    rates = rate_repository.list_rates(include_inactive=True)
    return next(rate.rate_id for rate in rates if rate.name == name)


def insert_time_windowed_test_rate(rate_id: str, name: str) -> None:
    """Fabricates a Standard/guest-only/00:00-05:00 package rate directly in
    the DB, in the exact shape the (now-retired) Standard Midnight Promo
    used to have. RateProfileCreate/RateProfileUpdate don't expose
    guest_only/eligible_start_local_time/eligible_end_local_time, so this
    is inserted the same way seed_required_default_pricing does -- this
    test is about rate_eligibility.py's window/guest-only/station-scope
    logic in general, not the retired promo specifically, and that logic
    needs *some* time-windowed rate to exercise now that Midnight Promo is
    gone from fresh installs."""
    from server.database import get_connection
    from server.time_utils import current_utc_time

    now = current_utc_time().isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO rate_profiles (
                rate_id, name, duration_seconds, price, is_active, sort_order,
                rate_type, station_scope, guest_only, member_eligible,
                eligible_start_local_time, eligible_end_local_time,
                created_at, updated_at
            )
            VALUES (?, ?, 3600, '10.00', 1, 50, 'package', 'standard', 1, 0, '00:00', '05:00', ?, ?)
            """,
            (rate_id, name, now, now),
        )


def test_station_and_midnight_enforcement(temp_directory: str) -> None:
    import server.session_service as session_service_module
    from server.rate_repository import RateRepository

    service, session_repository = create_service(Path(temp_directory) / "eligibility.sqlite3")
    rate_repository = RateRepository()
    standard_5 = get_rate_id(rate_repository, "Standard Guest - P5 (20 Minutes)")
    vip_guest = get_rate_id(rate_repository, "VIP Guest - P30 (1 Hour)")
    insert_time_windowed_test_rate("test-standard-overnight-window", "Test Standard Overnight Window")
    midnight = get_rate_id(rate_repository, "Test Standard Overnight Window")

    # 1. Standard package on Standard PC -> allowed.
    started = asyncio.run(service.start_session("PC-STD", rate_id=standard_5))
    assert started.session.rate_id == standard_5

    # 2. Standard package on VIP PC -> rejected.
    try:
        asyncio.run(service.start_session("PC-VIP", rate_id=standard_5))
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("Standard package must be rejected on a VIP PC")

    # 3. VIP package on VIP PC -> allowed.
    vip_started = asyncio.run(service.start_session("PC-VIP", rate_id=vip_guest))
    assert vip_started.session.rate_id == vip_guest

    # 4. VIP package on Standard PC -> rejected.
    try:
        asyncio.run(service.start_session("PC-STD-2", rate_id=vip_guest))
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("VIP package must be rejected on a Standard PC")

    original_now = session_service_module.current_utc_time
    try:
        # 5. Midnight at 23:59 local -> rejected.
        session_service_module.current_utc_time = lambda: local_manila(2026, 1, 1, 23, 59)
        try:
            asyncio.run(service.start_session("PC-STD-3", rate_id=midnight))
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Midnight promo at 23:59 must be rejected")

        # 6. Midnight at 00:00 local -> allowed.
        session_service_module.current_utc_time = lambda: local_manila(2026, 1, 2, 0, 0)
        midnight_session = asyncio.run(service.start_session("PC-STD-3", rate_id=midnight))
        assert midnight_session.session.rate_id == midnight
        asyncio.run(service.end_session(midnight_session.session.session_id))

        # 7. Midnight at 04:59 local -> allowed, and remains valid past 05:00
        # (11. midnight started before 05:00 keeps its full purchased hour).
        session_service_module.current_utc_time = lambda: local_manila(2026, 1, 2, 4, 59)
        late_midnight = asyncio.run(service.start_session("PC-STD-3", rate_id=midnight))
        expected_end = late_midnight.session.scheduled_end_at
        session_service_module.current_utc_time = lambda: local_manila(2026, 1, 2, 6, 0)
        persisted = session_repository.get_session(late_midnight.session.session_id)
        assert persisted is not None
        assert persisted.status == "active"
        assert persisted.scheduled_end_at == expected_end, "purchased duration must not shrink after the window closes"
        asyncio.run(service.end_session(late_midnight.session.session_id))

        # 8. Midnight at 05:00 local -> rejected.
        session_service_module.current_utc_time = lambda: local_manila(2026, 1, 2, 5, 0)
        try:
            asyncio.run(service.start_session("PC-STD-3", rate_id=midnight))
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Midnight promo at 05:00 must be rejected")

        # 9. Midnight on a VIP PC -> rejected (station scope, independent of time).
        try:
            asyncio.run(service.start_session("PC-VIP-2", rate_id=midnight))
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Midnight promo must be rejected on a VIP PC")

        # 13. Add Time: midnight after 05:00 rejected on an existing session.
        session_service_module.current_utc_time = lambda: local_manila(2026, 1, 2, 0, 0)
        base_session = asyncio.run(service.start_session("PC-STD-3", rate_id=standard_5))
        session_service_module.current_utc_time = lambda: local_manila(2026, 1, 2, 5, 0)
        try:
            asyncio.run(service.add_time(base_session.session.session_id, rate_id=midnight))
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Adding the midnight promo after 05:00 must be rejected")
    finally:
        session_service_module.current_utc_time = original_now

    # 12. Add Time cross-station misuse: a Standard session cannot have a
    # VIP package added, and vice versa.
    standard_session = asyncio.run(service.start_session("PC-STD-2", rate_id=standard_5))
    try:
        asyncio.run(service.add_time(standard_session.session.session_id, rate_id=vip_guest))
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("Adding a VIP package to a Standard-PC session must be rejected")

    vip_session = asyncio.run(service.start_session("PC-VIP-2", rate_id=vip_guest))
    try:
        asyncio.run(service.add_time(vip_session.session.session_id, rate_id=standard_5))
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("Adding a Standard package to a VIP-PC session must be rejected")


def test_prepared_startup_follows_identical_rules(temp_directory: str) -> None:
    from server.rate_repository import RateRepository
    from server.schemas import PaymentCreateRequest, PreparedSessionStartRequest

    service, _ = create_service(Path(temp_directory) / "prepared-eligibility.sqlite3")
    rate_repository = RateRepository()
    standard_5 = get_rate_id(rate_repository, "Standard Guest - P5 (20 Minutes)")
    vip_guest = get_rate_id(rate_repository, "VIP Guest - P30 (1 Hour)")

    # The normal Start Session endpoint would reject a Standard package on a
    # VIP PC; the prepared/offline (wake) path must reject it identically.
    try:
        service.prepare_offline_session(
            PreparedSessionStartRequest(pc_name="PC-PREP-VIP", session_mode="prepaid", rate_id=standard_5),
            "cashier-user",
        )
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("Prepared startup must reject a Standard package on a VIP PC")

    try:
        service.prepare_offline_session(
            PreparedSessionStartRequest(pc_name="PC-PREP-STD", session_mode="prepaid", rate_id=vip_guest),
            "cashier-user",
        )
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("Prepared startup must reject a VIP package on a Standard PC")

    # Correctly scoped package still succeeds through the prepared path.
    prepared = service.prepare_offline_session(
        PreparedSessionStartRequest(
            pc_name="PC-PREP-STD",
            session_mode="prepaid",
            rate_id=standard_5,
            payment=PaymentCreateRequest(payment_method="cash", amount=Decimal("5.00"), cash_received=Decimal("5.00")),
        ),
        "cashier-user",
    )
    assert prepared.prepared_session.status == "prepared"


def test_seeding_activation_and_disabled_rate_not_reactivated(temp_directory: str) -> None:
    database_path = Path(temp_directory) / "seed-activation.sqlite3"
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    from server.database import get_connection

    with get_connection() as connection:
        rate_count_first = connection.execute("SELECT COUNT(*) FROM rate_profiles").fetchone()[0]
        product_count_first = connection.execute("SELECT COUNT(*) FROM membership_products").fetchone()[0]
        required_rate_row = connection.execute(
            "SELECT is_active FROM rate_profiles WHERE rate_id = 'rate-standard-guest-5'"
        ).fetchone()
        assert required_rate_row is not None, "seeder must run automatically on initialize_database()"
        assert required_rate_row["is_active"] == 1

        vip_promo_row = connection.execute(
            "SELECT is_active FROM membership_products WHERE product_id = 'vip-promo-250'"
        ).fetchone()
        assert vip_promo_row is not None
        # As of Stage 3, VIP balance purchase/credit is implemented and
        # tested, so this seeds/activates directly (see
        # tests/smoke_vip_purchase.py for the full purchase/activation
        # coverage). This was 0 ("stay inactive until Stage 3") when this
        # assertion was first written in Stage 2.
        assert vip_promo_row["is_active"] == 1

        # Disable a seeded default (the owner's prerogative) then re-run
        # initialize_database(): it must not be silently reactivated.
        connection.execute(
            "UPDATE rate_profiles SET is_active = 0 WHERE rate_id = 'rate-standard-guest-5'"
        )

    initialize_database()

    with get_connection() as connection:
        rate_count_second = connection.execute("SELECT COUNT(*) FROM rate_profiles").fetchone()[0]
        product_count_second = connection.execute("SELECT COUNT(*) FROM membership_products").fetchone()[0]
        disabled_row = connection.execute(
            "SELECT is_active FROM rate_profiles WHERE rate_id = 'rate-standard-guest-5'"
        ).fetchone()
        assert disabled_row["is_active"] == 0, "an intentionally disabled seeded rate must not be reactivated"

    assert rate_count_second == rate_count_first, "re-running initialize_database() must never duplicate a seeded rate"
    assert product_count_second == product_count_first, "re-running initialize_database() must never duplicate a seeded product"


def main() -> None:
    test_timezone_resolution_degrades_to_utc_never_crashes()
    test_pure_eligibility_window()
    test_validate_rate_for_station_and_context_pure()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        test_station_and_midnight_enforcement(temp_directory)
        test_prepared_startup_follows_identical_rules(temp_directory)
        test_seeding_activation_and_disabled_rate_not_reactivated(temp_directory)
    print("Rate eligibility smoke test passed.")


if __name__ == "__main__":
    main()
