"""Stage 4: VIP member-session consumption + Standard->VIP 2:1 conversion.

Pure calculation coverage (VIP-only, Standard-only-on-VIP, mixed, the 2h+6h=5h
example, odd-second retention, capped requests, zero balance) already lives
in tests/smoke_balance_conversion.py against server/balance_conversion.py
directly -- this file covers the reservation/session-service integration:
start, boundary/finalization, Add Time, recovery, and Member Lock, plus a
Standard-PC regression proving VIP balance is never touched there.
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

PC_NAMES = [
    ("PC-STD", False),
    ("PC-VIP", True),
    ("PC-VIP-2", True),
    ("PC-VIP-3", True),
    ("PC-VIP-4", True),
    ("PC-VIP-5", True),
    ("PC-VIP-6", True),
    ("PC-VIP-7", True),
    ("PC-VIP-8", True),
    ("PC-VIP-LOCK", True),
    ("PC-VIP-LOCK-2", True),
    ("PC-VIP-RECOVERY", True),
    ("PC-VIP-RECOVERY-2", True),
    ("PC-VIP-RELAYOUT", True),
    ("PC-STD-REGRESSION", False),
]


def create_service(database_path: Path):
    """Fresh SessionService/repositories bound to database_path. Calling
    this again with the SAME path simulates a backend restart: no
    in-process state survives except what's persisted."""
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
    for pc_name, is_vip in PC_NAMES:
        pc_layout_repository.upsert_layout(
            pc_name,
            PCLayoutUpdate(display_name=pc_name, area_label="TEST", layout_order=1, is_vip=is_vip, is_enabled=True),
        )

    session_repository = SessionRepository()
    payment_repository = PaymentRepository()
    shift_repository = ShiftRepository()
    member_repository = MemberRepository(payment_repository, shift_repository)
    billing_service = BillingService(session_repository, payment_repository, shift_repository)

    class DummyConnectionManager:
        def is_connected(self, _: str) -> bool:
            return True

    pc_registry = {
        pc_name: PCRecord(pc_name=pc_name, ip_address="127.0.0.1", status="online", last_seen=datetime.now(timezone.utc))
        for pc_name, _ in PC_NAMES
    }

    service = SessionService(
        repository=session_repository,
        rate_repository=RateRepository(),
        connection_manager=DummyConnectionManager(),  # type: ignore[arg-type]
        pc_registry=pc_registry,
        command_timeout_seconds=1,
        billing_service=billing_service,
        member_repository=member_repository,
        prepared_session_repository=PreparedSessionRepository(),
        pc_layout_repository=pc_layout_repository,
    )

    async def acknowledge_command(*_: object) -> list[object]:
        return [
            CommandAcknowledgement(command_id="test", status="received"),
            CommandAcknowledgement(command_id="test", status="completed"),
        ]

    service.send_required_command = acknowledge_command  # type: ignore[method-assign]
    return service, session_repository, member_repository


def create_test_member(member_repository, username: str, vip_seconds: int, standard_seconds: int):
    from server.schemas import MemberCreateRequest

    member = member_repository.create_member(
        MemberCreateRequest(username=username, display_name=username, pin="1234", confirm_pin="1234"),
        None,
    )
    if standard_seconds:
        member_repository.apply_balance_delta(
            member.member_id, "manual_adjustment", standard_seconds, None, balance_type="standard"
        )
    if vip_seconds:
        member_repository.apply_balance_delta(
            member.member_id, "manual_adjustment", vip_seconds, None, balance_type="vip"
        )
    return member_repository.get_member(member.member_id)


def rewind_session_start(session_repository, session_id: str, elapsed_seconds: int):
    """Backdates started_at so ending the session 'now' yields exactly
    elapsed_seconds of actual elapsed time, without a real sleep."""
    session = session_repository.get_session(session_id)
    assert session is not None
    rewound = session.model_copy(update={"started_at": datetime.now(timezone.utc) - timedelta(seconds=elapsed_seconds)})
    session_repository.update_session(rewound)


# --- Start ------------------------------------------------------------


def test_standard_pc_reserves_standard_only(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "std-start.sqlite3")
    member = create_test_member(member_repository, "std.start", vip_seconds=36000, standard_seconds=14400)

    started = asyncio.run(service.start_member_session("PC-STD", "std.start", None, "1234"))
    assert started.session.station_type == "standard"
    assert started.session.total_seconds == 14400
    reservation = member_repository.get_active_reservation_totals(member.member_id, started.session.session_id)
    assert reservation == (14400, 0), "Standard PC must reserve Standard only, never VIP"
    asyncio.run(service.end_session(started.session.session_id))
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded.vip_balance_seconds == 36000, "VIP balance must be untouched by a Standard-PC session"


def test_vip_pc_vip_only(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "vip-only.sqlite3")
    create_test_member(member_repository, "vip.only", vip_seconds=7200, standard_seconds=0)

    started = asyncio.run(service.start_member_session("PC-VIP", "vip.only", None, "1234"))
    assert started.session.station_type == "vip"
    assert started.session.total_seconds == 7200
    reservation = member_repository.get_active_reservation_totals(
        started.session.member_id, started.session.session_id
    )
    assert reservation == (0, 7200)


def test_vip_pc_standard_only(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "vip-standard-only.sqlite3")
    create_test_member(member_repository, "vip.stdonly", vip_seconds=0, standard_seconds=14400)

    started = asyncio.run(service.start_member_session("PC-VIP-2", "vip.stdonly", None, "1234"))
    assert started.session.station_type == "vip"
    assert started.session.total_seconds == 7200, "4 Standard hours must convert to 2 VIP playable hours"
    reservation = member_repository.get_active_reservation_totals(
        started.session.member_id, started.session.session_id
    )
    assert reservation == (14400, 0)


def test_vip_pc_mixed_matches_owner_example(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "vip-mixed.sqlite3")
    create_test_member(member_repository, "vip.mixed", vip_seconds=2 * 3600, standard_seconds=6 * 3600)

    started = asyncio.run(service.start_member_session("PC-VIP-3", "vip.mixed", None, "1234"))
    assert started.session.total_seconds == 5 * 3600, "2h VIP + 6h Standard must yield 5h real playable"
    reservation = member_repository.get_active_reservation_totals(
        started.session.member_id, started.session.session_id
    )
    assert reservation == (6 * 3600, 2 * 3600)
    expected_end = started.session.started_at + timedelta(seconds=5 * 3600)
    assert started.session.scheduled_end_at == expected_end


def test_vip_pc_zero_balance_rejected(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "vip-zero.sqlite3")
    create_test_member(member_repository, "vip.zero", vip_seconds=0, standard_seconds=0)

    try:
        asyncio.run(service.start_member_session("PC-VIP-4", "vip.zero", None, "1234"))
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("Zero playable balance must be rejected, not a zero-duration session")
    assert session_repository.get_active_session_for_pc("PC-VIP-4") is None


def test_member_cannot_start_second_session_while_reservation_active(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "vip-double-start.sqlite3")
    create_test_member(member_repository, "vip.double", vip_seconds=3600, standard_seconds=3600)

    asyncio.run(service.start_member_session("PC-VIP-5", "vip.double", None, "1234"))
    try:
        asyncio.run(service.start_member_session("PC-VIP-6", "vip.double", None, "1234"))
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("A member with an active reservation must not be able to start a second session")


# --- Boundary / finalization -------------------------------------------


def test_end_before_vip_exhausted(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "before-boundary.sqlite3")
    member = create_test_member(member_repository, "vip.before", vip_seconds=1800, standard_seconds=7200)

    started = asyncio.run(service.start_member_session("PC-VIP", "vip.before", None, "1234"))
    assert started.session.total_seconds == 5400  # 30m VIP + 2h Standard = 1h30 real
    rewind_session_start(session_repository, started.session.session_id, elapsed_seconds=900)  # 15 min
    ended = asyncio.run(service.end_session(started.session.session_id))
    assert ended.session.vip_seconds_consumed == 900
    assert ended.session.standard_seconds_consumed_at_vip_rate == 0
    assert ended.session.conversion_applied is False
    assert ended.session.billed_seconds == 900
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded.vip_balance_seconds == 1800 - 900
    assert reloaded.time_balance_seconds == 7200, "Unused Standard reservation must not be touched"


def test_end_exactly_at_vip_boundary(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "at-boundary.sqlite3")
    member = create_test_member(member_repository, "vip.at", vip_seconds=1800, standard_seconds=7200)

    started = asyncio.run(service.start_member_session("PC-VIP-2", "vip.at", None, "1234"))
    rewind_session_start(session_repository, started.session.session_id, elapsed_seconds=1800)
    ended = asyncio.run(service.end_session(started.session.session_id))
    assert ended.session.vip_seconds_consumed == 1800
    assert ended.session.standard_seconds_consumed_at_vip_rate == 0
    assert ended.session.conversion_applied is False
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded.vip_balance_seconds == 0
    assert reloaded.time_balance_seconds == 7200


def test_end_after_vip_boundary_matches_owner_example(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "after-boundary.sqlite3")
    member = create_test_member(member_repository, "vip.after", vip_seconds=1800, standard_seconds=7200)

    started = asyncio.run(service.start_member_session("PC-VIP-3", "vip.after", None, "1234"))
    rewind_session_start(session_repository, started.session.session_id, elapsed_seconds=2700)  # 45 min
    ended = asyncio.run(service.end_session(started.session.session_id))
    assert ended.session.vip_seconds_consumed == 1800, "first 30m from VIP"
    assert ended.session.standard_seconds_consumed_at_vip_rate == 1800, "remaining 15m real * 2 = 1800 Standard"
    assert ended.session.conversion_applied is True
    assert ended.session.billed_seconds == 2700
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded.vip_balance_seconds == 0
    assert reloaded.time_balance_seconds == 7200 - 1800


def test_full_exhaustion(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "full-exhaustion.sqlite3")
    member = create_test_member(member_repository, "vip.full", vip_seconds=1800, standard_seconds=7200)

    started = asyncio.run(service.start_member_session("PC-VIP-4", "vip.full", None, "1234"))
    # Play well beyond the reservation -- consumption must cap at the
    # reservation, never over-draw.
    rewind_session_start(session_repository, started.session.session_id, elapsed_seconds=999999)
    ended = asyncio.run(service.end_session(started.session.session_id))
    assert ended.session.vip_seconds_consumed == 1800
    assert ended.session.standard_seconds_consumed_at_vip_rate == 7200
    assert ended.session.billed_seconds == 5400
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded.vip_balance_seconds == 0
    assert reloaded.time_balance_seconds == 0


def test_early_end_releases_unused_reservation(temp_directory: str) -> None:
    """20 real minutes played of a 30m-VIP+2h-Standard reservation: only
    VIP is touched, and the untouched remainder (VIP and all of the
    Standard conversion headroom) is released back, not consumed."""
    service, session_repository, member_repository = create_service(Path(temp_directory) / "early-end.sqlite3")
    member = create_test_member(member_repository, "vip.early", vip_seconds=1800, standard_seconds=7200)

    started = asyncio.run(service.start_member_session("PC-VIP-5", "vip.early", None, "1234"))
    rewind_session_start(session_repository, started.session.session_id, elapsed_seconds=1200)  # 20 min
    ended = asyncio.run(service.end_session(started.session.session_id))
    assert ended.session.vip_seconds_consumed == 1200
    assert ended.session.standard_seconds_consumed_at_vip_rate == 0
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded.vip_balance_seconds == 600, "only the used 20 minutes of VIP is deducted"
    assert reloaded.time_balance_seconds == 7200, "the entire Standard reservation is released, unconsumed"

    # The reservation itself is fully finalized/closed, not left dangling.
    assert member_repository.has_active_reservation(member.member_id) is False


def test_double_finalize_cannot_double_deduct(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "double-finalize.sqlite3")
    member = create_test_member(member_repository, "vip.dup", vip_seconds=1800, standard_seconds=7200)

    started = asyncio.run(service.start_member_session("PC-VIP-6", "vip.dup", None, "1234"))
    rewind_session_start(session_repository, started.session.session_id, elapsed_seconds=2700)
    asyncio.run(service.end_session(started.session.session_id))
    balance_after_first = member_repository.get_member(member.member_id)

    # Directly re-invoke the idempotent finalize path a second time (as a
    # reconnect race or duplicate force-end would) -- must not deduct again.
    standard_consumed, vip_consumed = member_repository.finalize_reservation_usage(
        member.member_id, started.session.session_id, 2700, None, is_vip_station=True
    )
    assert (standard_consumed, vip_consumed) == (1800, 1800)
    balance_after_second = member_repository.get_member(member.member_id)
    assert balance_after_second.vip_balance_seconds == balance_after_first.vip_balance_seconds
    assert balance_after_second.time_balance_seconds == balance_after_first.time_balance_seconds


# --- Add Time -----------------------------------------------------------


def test_add_time_vip_addition(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "add-vip.sqlite3")
    member = create_test_member(member_repository, "vip.addvip", vip_seconds=3600, standard_seconds=0)

    started = asyncio.run(
        service.start_member_session("PC-VIP-7", "vip.addvip", 1800, "1234")
    )  # cap to 30 min so VIP remains
    assert started.session.total_seconds == 1800
    added = asyncio.run(service.add_member_time(started.session.session_id, member.member_id, 1200))
    assert added.session.total_seconds == 3000
    reservation = member_repository.get_active_reservation_totals(member.member_id, started.session.session_id)
    assert reservation == (0, 3000), "the extension must draw from VIP, not Standard"


def test_add_time_standard_extension_while_playing_vip(temp_directory: str) -> None:
    """VIP balance is fully reserved already; a further Add Time must
    convert at 2:1 from Standard."""
    service, session_repository, member_repository = create_service(Path(temp_directory) / "add-std-on-vip.sqlite3")
    member = create_test_member(member_repository, "vip.addstd", vip_seconds=600, standard_seconds=7200)

    started = asyncio.run(service.start_member_session("PC-VIP-8", "vip.addstd", 600, "1234"))
    assert started.session.total_seconds == 600
    reservation = member_repository.get_active_reservation_totals(member.member_id, started.session.session_id)
    assert reservation == (0, 600), "the entire VIP balance is already claimed by the initial reservation"

    added = asyncio.run(service.add_member_time(started.session.session_id, member.member_id, 300))
    assert added.session.total_seconds == 900
    reservation = member_repository.get_active_reservation_totals(member.member_id, started.session.session_id)
    assert reservation == (600, 600), "5 additional real minutes must convert to 600 Standard-balance seconds"


def test_add_time_mixed_extension(temp_directory: str) -> None:
    """Remaining VIP balance covers part of the extension; the rest
    converts from Standard -- a mixed extension mirrors a mixed start."""
    service, session_repository, member_repository = create_service(Path(temp_directory) / "add-mixed.sqlite3")
    member = create_test_member(member_repository, "vip.addmixed", vip_seconds=1800, standard_seconds=7200)

    started = asyncio.run(service.start_member_session("PC-VIP", "vip.addmixed", 600, "1234"))
    reservation = member_repository.get_active_reservation_totals(member.member_id, started.session.session_id)
    assert reservation == (0, 600), "only 10 of the 30 available VIP minutes were claimed initially"

    # Ask for 20 more real minutes: 20 remaining VIP minutes cover it
    # entirely -- still VIP-first, no Standard touched yet.
    added = asyncio.run(service.add_member_time(started.session.session_id, member.member_id, 1200))
    reservation = member_repository.get_active_reservation_totals(member.member_id, started.session.session_id)
    assert reservation == (0, 1800), "all 30 VIP minutes now claimed, still no Standard"

    # One more real minute must now come from Standard at 2:1.
    added = asyncio.run(service.add_member_time(added.session.session_id, member.member_id, 60))
    reservation = member_repository.get_active_reservation_totals(member.member_id, started.session.session_id)
    assert reservation == (120, 1800)
    assert added.session.total_seconds == 600 + 1200 + 60


def test_add_time_rejects_when_insufficient(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "add-insufficient.sqlite3")
    member = create_test_member(member_repository, "vip.addfail", vip_seconds=60, standard_seconds=0)

    started = asyncio.run(service.start_member_session("PC-VIP-2", "vip.addfail", 60, "1234"))
    try:
        asyncio.run(service.add_member_time(started.session.session_id, member.member_id, 3600))
    except HTTPException as error:
        assert error.status_code == 409
    else:
        raise AssertionError("An Add Time request exceeding available balance must be rejected outright")
    # Rejected request must not have partially extended the reservation.
    reservation = member_repository.get_active_reservation_totals(member.member_id, started.session.session_id)
    assert reservation == (0, 60)


def test_purchase_does_not_auto_extend_active_session(temp_directory: str) -> None:
    """Confirms today's established behavior (purchasing more balance never
    auto-extends a running reservation/session) is preserved for VIP too --
    the cashier must still use Add Time explicitly."""
    from server.schemas import MemberPurchaseRequest

    service, session_repository, member_repository = create_service(Path(temp_directory) / "no-auto-extend.sqlite3")
    from server.user_repository import UserRepository
    from server.schemas import UserCreateRequest

    user_repository = UserRepository()
    actor = user_repository.get_user(
        user_repository.create_user(
            UserCreateRequest(username="autoext-cashier", display_name="Cashier", password="cashier-pass", role="cashier")
        ).user_id
    )
    member = create_test_member(member_repository, "vip.noauto", vip_seconds=1800, standard_seconds=0)

    started = asyncio.run(service.start_member_session("PC-VIP-3", "vip.noauto", None, "1234"))
    original_total_seconds = started.session.total_seconds
    original_scheduled_end_at = started.session.scheduled_end_at

    member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="vip-promo-250", payment_method="cash", cash_received="1000.00"),
        actor,
    )

    untouched_session = session_repository.get_session(started.session.session_id)
    assert untouched_session.total_seconds == original_total_seconds
    assert untouched_session.scheduled_end_at == original_scheduled_end_at
    reservation = member_repository.get_active_reservation_totals(member.member_id, started.session.session_id)
    assert reservation == (0, 1800), "the reservation must be unaffected by the purchase alone"


# --- Recovery -------------------------------------------------------------


def test_recovery_restart_before_boundary(temp_directory: str) -> None:
    database_path = Path(temp_directory) / "recovery-before.sqlite3"
    service, session_repository, member_repository = create_service(database_path)
    member = create_test_member(member_repository, "vip.recover1", vip_seconds=1800, standard_seconds=7200)
    started = asyncio.run(service.start_member_session("PC-VIP-RECOVERY", "vip.recover1", None, "1234"))
    session_id = started.session.session_id
    scheduled_end_at = started.session.scheduled_end_at

    # Simulate a backend restart: brand-new service/repository instances
    # against the same database file, nothing carried over in memory.
    service2, session_repository2, member_repository2 = create_service(database_path)
    recovered_session = session_repository2.get_session(session_id)
    assert recovered_session is not None
    assert recovered_session.session_id == session_id
    assert recovered_session.scheduled_end_at == scheduled_end_at
    assert member_repository2.get_active_reservation_totals(member.member_id, session_id) == (7200, 1800)
    assert member_repository2.has_active_reservation(member.member_id) is True

    rewind_session_start(session_repository2, session_id, elapsed_seconds=900)
    ended = asyncio.run(service2.end_session(session_id))
    assert ended.session.vip_seconds_consumed == 900
    assert ended.session.standard_seconds_consumed_at_vip_rate == 0


def test_recovery_restart_after_boundary(temp_directory: str) -> None:
    database_path = Path(temp_directory) / "recovery-after.sqlite3"
    service, session_repository, member_repository = create_service(database_path)
    create_test_member(member_repository, "vip.recover2", vip_seconds=1800, standard_seconds=7200)
    started = asyncio.run(service.start_member_session("PC-VIP-RECOVERY-2", "vip.recover2", None, "1234"))
    session_id = started.session.session_id

    service2, session_repository2, member_repository2 = create_service(database_path)
    rewind_session_start(session_repository2, session_id, elapsed_seconds=2700)
    ended = asyncio.run(service2.end_session(session_id))
    assert ended.session.vip_seconds_consumed == 1800
    assert ended.session.standard_seconds_consumed_at_vip_rate == 1800
    assert ended.session.conversion_applied is True

    # A second "restart" attempting to finalize again must not double-deduct.
    service3, session_repository3, member_repository3 = create_service(database_path)
    standard_consumed, vip_consumed = member_repository3.finalize_reservation_usage(
        ended.session.member_id, session_id, 2700, None, is_vip_station=True
    )
    assert (standard_consumed, vip_consumed) == (1800, 1800)


# --- Member Lock ------------------------------------------------------


def test_member_lock_during_vip_only_portion(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "lock-vip.sqlite3")
    create_test_member(member_repository, "vip.lockvip", vip_seconds=1800, standard_seconds=7200)
    started = asyncio.run(service.start_member_session("PC-VIP-LOCK", "vip.lockvip", None, "1234"))
    session_id = started.session.session_id
    original_scheduled_end_at = started.session.scheduled_end_at

    locked = service.engage_customer_lock("PC-VIP-LOCK", session_id)
    assert locked.customer_locked is True
    assert locked.scheduled_end_at == original_scheduled_end_at
    assert locked.status == "active"

    _updated_session, member_display_name = service.clear_customer_lock_by_pin("PC-VIP-LOCK", "1234")
    assert member_display_name
    persisted = session_repository.get_session(session_id)
    assert persisted.customer_locked is False
    assert persisted.scheduled_end_at == original_scheduled_end_at
    assert persisted.session_id == session_id, "unlock must restore the same session"


def test_member_lock_during_standard_converted_portion_and_wall_clock_consumption(temp_directory: str) -> None:
    """Locking must not pause consumption: elapsed real time keeps counting
    against the reservation regardless of lock state, including through
    the VIP->Standard boundary."""
    service, session_repository, member_repository = create_service(Path(temp_directory) / "lock-std.sqlite3")
    member = create_test_member(member_repository, "vip.lockstd", vip_seconds=1800, standard_seconds=7200)
    started = asyncio.run(service.start_member_session("PC-VIP-LOCK-2", "vip.lockstd", None, "1234"))
    session_id = started.session.session_id
    original_scheduled_end_at = started.session.scheduled_end_at

    # Lock while still conceptually in the VIP-funded portion.
    locked = service.engage_customer_lock("PC-VIP-LOCK-2", session_id)
    assert locked.customer_locked is True

    # Cashier override clears the lock without the member's PIN, session
    # continues under the same id.
    override_response = asyncio.run(service.clear_customer_lock_override(session_id, None))
    assert override_response.session.customer_locked is False
    assert override_response.session.session_id == session_id
    assert override_response.session.scheduled_end_at == original_scheduled_end_at

    # 45 real minutes elapsed in total (crossing the 30-minute VIP
    # boundary) while the PC was locked for part of that time -- the lock
    # must not have paused or altered the wall-clock deduction.
    rewind_session_start(session_repository, session_id, elapsed_seconds=2700)
    ended = asyncio.run(service.end_session(session_id))
    assert ended.session.vip_seconds_consumed == 1800
    assert ended.session.standard_seconds_consumed_at_vip_rate == 1800
    assert ended.session.customer_locked is False, "ending must always clear any lock"
    reloaded = member_repository.get_member(member.member_id)
    assert reloaded.vip_balance_seconds == 0
    assert reloaded.time_balance_seconds == 7200 - 1800


# --- Layout change during active session (station_type snapshot) -------


def test_layout_change_during_active_session_does_not_reclassify_running_session(
    temp_directory: str,
) -> None:
    """station_type is resolved from pc_layouts once, at session creation,
    and persisted immediately. Later reservation/finalization/history reads
    must use that snapshot, never a fresh pc_layouts lookup -- so flipping a
    PC's layout mid-session must not reclassify the session already running
    on it. Only sessions started *after* the layout change should pick up
    the new classification."""
    from server.schemas import PCLayoutUpdate

    service, session_repository, member_repository = create_service(
        Path(temp_directory) / "layout-change.sqlite3"
    )
    member = create_test_member(member_repository, "vip.relayout", vip_seconds=1800, standard_seconds=7200)

    # 1-3: PC-VIP-RELAYOUT is configured VIP; start a mixed VIP session on it.
    started = asyncio.run(service.start_member_session("PC-VIP-RELAYOUT", "vip.relayout", None, "1234"))
    session_id = started.session.session_id

    # 4: session row is immediately snapshotted as VIP.
    assert started.session.station_type == "vip"

    # 5: capture session id, scheduled_end_at, reservation for later comparison.
    original_scheduled_end_at = started.session.scheduled_end_at
    original_reservation = member_repository.get_active_reservation_totals(member.member_id, session_id)
    assert original_reservation == (7200, 1800)

    # 6: flip the PC's layout to Standard via the normal upsert path while
    # the VIP session is still active.
    service.pc_layout_repository.upsert_layout(
        "PC-VIP-RELAYOUT",
        PCLayoutUpdate(display_name="PC-VIP-RELAYOUT", area_label="TEST", layout_order=1, is_vip=False, is_enabled=True),
    )
    assert service.is_vip_pc("PC-VIP-RELAYOUT") is False, "layout change itself must have taken effect"

    # 7-8: re-read the still-active session; nothing about it may have moved.
    still_active = session_repository.get_session(session_id)
    assert still_active.session_id == session_id
    assert still_active.station_type == "vip", "running session must keep its snapshotted station type"
    assert still_active.scheduled_end_at == original_scheduled_end_at
    reservation_after_layout_change = member_repository.get_active_reservation_totals(member.member_id, session_id)
    assert reservation_after_layout_change == original_reservation

    # 9-10: finalize after enough elapsed time to cross the VIP->Standard
    # conversion boundary; accounting must still follow VIP/mixed rules
    # from the snapshot, not the newly-Standard layout.
    rewind_session_start(session_repository, session_id, elapsed_seconds=2700)  # 45 min
    ended = asyncio.run(service.end_session(session_id))
    assert ended.session.vip_seconds_consumed == 1800
    assert ended.session.standard_seconds_consumed_at_vip_rate == 1800
    assert ended.session.conversion_applied is True
    reloaded_member = member_repository.get_member(member.member_id)
    assert reloaded_member.vip_balance_seconds == 0
    assert reloaded_member.time_balance_seconds == 7200 - 1800

    # 11: history keeps the VIP snapshot permanently.
    history = session_repository.get_session(session_id)
    assert history.station_type == "vip"

    # 12: a NEW session started on the same PC after the layout change must
    # be classified under the current (Standard) layout.
    create_test_member(member_repository, "std.relayout", vip_seconds=0, standard_seconds=3600)
    new_started = asyncio.run(service.start_member_session("PC-VIP-RELAYOUT", "std.relayout", None, "1234"))
    assert new_started.session.station_type == "standard"
    assert new_started.session.session_id != session_id


# --- Standard regression --------------------------------------------------


def test_standard_member_lifecycle_unaffected_and_vip_untouched(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "std-regression.sqlite3")
    member = create_test_member(member_repository, "std.lifecycle", vip_seconds=36000, standard_seconds=14400)

    started = asyncio.run(service.start_member_session("PC-STD-REGRESSION", "std.lifecycle", 1800, "1234"))
    assert started.session.station_type == "standard"
    assert started.session.total_seconds == 1800
    added = asyncio.run(service.add_member_time(started.session.session_id, member.member_id, 600))
    assert added.session.total_seconds == 2400
    reservation = member_repository.get_active_reservation_totals(member.member_id, started.session.session_id)
    assert reservation == (2400, 0)

    rewind_session_start(session_repository, started.session.session_id, elapsed_seconds=1000)
    ended = asyncio.run(service.end_session(started.session.session_id))
    assert ended.session.billed_seconds == 1000
    assert ended.session.vip_seconds_consumed is None
    assert ended.session.standard_seconds_consumed_at_vip_rate is None
    assert ended.session.conversion_applied is False

    reloaded = member_repository.get_member(member.member_id)
    assert reloaded.time_balance_seconds == 14400 - 1000
    assert reloaded.vip_balance_seconds == 36000, "VIP balance must never move on a Standard-PC session"


def main() -> None:
    tests = [
        test_standard_pc_reserves_standard_only,
        test_vip_pc_vip_only,
        test_vip_pc_standard_only,
        test_vip_pc_mixed_matches_owner_example,
        test_vip_pc_zero_balance_rejected,
        test_member_cannot_start_second_session_while_reservation_active,
        test_end_before_vip_exhausted,
        test_end_exactly_at_vip_boundary,
        test_end_after_vip_boundary_matches_owner_example,
        test_full_exhaustion,
        test_early_end_releases_unused_reservation,
        test_double_finalize_cannot_double_deduct,
        test_add_time_vip_addition,
        test_add_time_standard_extension_while_playing_vip,
        test_add_time_mixed_extension,
        test_add_time_rejects_when_insufficient,
        test_purchase_does_not_auto_extend_active_session,
        test_recovery_restart_before_boundary,
        test_recovery_restart_after_boundary,
        test_member_lock_during_vip_only_portion,
        test_member_lock_during_standard_converted_portion_and_wall_clock_consumption,
        test_layout_change_during_active_session_does_not_reclassify_running_session,
        test_standard_member_lifecycle_unaffected_and_vip_untouched,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
    print("VIP consumption smoke test passed.")


if __name__ == "__main__":
    main()
