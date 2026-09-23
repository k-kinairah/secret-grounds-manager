"""Arbitrary cash top-ups on a VIP PC are priced off the Standard metered
rate scaled by the configured VIP multiplier (Settings ->
sessions.vip_metered_multiplier, default 2).

Standard metered rate is the seeded P5 = 20 min (P1 = 240s). So on a VIP
PC with multiplier 2: P5 = 10 min, P30 = 1 hour (matching the VIP Guest
packages). A Standard PC is unaffected.
"""

import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

PC_NAMES = [("PC-STD", False), ("PC-VIP", True)]


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
    for pc_name, is_vip in PC_NAMES:
        pc_layout_repository.upsert_layout(
            pc_name,
            PCLayoutUpdate(
                display_name=pc_name, area_label="TEST", layout_order=1,
                is_vip=is_vip, is_enabled=True,
            ),
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
        pc_name: PCRecord(
            pc_name=pc_name, ip_address="127.0.0.1", status="online",
            last_seen=datetime.now(timezone.utc),
        )
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
    return service, session_repository


def cash(amount: str):
    from server.schemas import PaymentCreateRequest

    return PaymentCreateRequest(
        payment_method="cash", amount=Decimal(amount), cash_received=Decimal(amount)
    )


def set_vip_multiplier(value: str) -> None:
    from server.admin_settings_service import SETTING_KEY, AdminSettingsService
    from server.database import get_connection

    payload = AdminSettingsService().get_settings().model_dump(mode="json")
    payload["sessions"]["vip_metered_multiplier"] = value
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO admin_settings (setting_key, value_json, updated_by_user_id, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET value_json = excluded.value_json
            """,
            (SETTING_KEY, json.dumps(payload, sort_keys=True), None, "2026-01-01T00:00:00+00:00"),
        )


def start_amount(service, pc_name: str, amount: str) -> int:
    response = asyncio.run(
        service.start_prepaid_session(pc_name, cash(amount), amount=Decimal(amount))
    )
    total_seconds = response.session.total_seconds
    # End it so the same PC can be reused for the next assertion in a test.
    asyncio.run(service.end_session(response.session.session_id))
    return total_seconds


def test_standard_pc_arbitrary_amount_unchanged(temp: str) -> None:
    service, _ = create_service(Path(temp) / "std.sqlite3")
    assert start_amount(service, "PC-STD", "5") == 1200, "Standard PC: P5 stays 20 minutes"
    assert start_amount(service, "PC-STD", "1") == 240


def test_vip_pc_arbitrary_amount_uses_2x(temp: str) -> None:
    service, _ = create_service(Path(temp) / "vip.sqlite3")
    assert start_amount(service, "PC-VIP", "5") == 600, "VIP PC at 2x: P5 buys 10 minutes"
    assert start_amount(service, "PC-VIP", "30") == 3600, "VIP PC at 2x: P30 buys 1 hour"


def test_vip_pc_add_time_arbitrary_amount_uses_2x(temp: str) -> None:
    service, session_repository = create_service(Path(temp) / "vip-add.sqlite3")
    started = asyncio.run(
        service.start_prepaid_session("PC-VIP", cash("5"), amount=Decimal("5"))
    )
    asyncio.run(
        service.add_prepaid_time(started.session.session_id, cash("10"), amount=Decimal("10"))
    )
    reloaded = session_repository.get_session(started.session.session_id)
    assert reloaded is not None
    # 600 (P5) + 1200 (P10 at 2x) = 1800
    assert reloaded.total_seconds == 1800


def test_vip_multiplier_setting_is_respected(temp: str) -> None:
    service, _ = create_service(Path(temp) / "vip-mult3.sqlite3")
    set_vip_multiplier("3")
    # floor(30 / (5 * 3) * 1200) = 2400
    assert start_amount(service, "PC-VIP", "30") == 2400
    # Standard PC still ignores the VIP multiplier entirely.
    assert start_amount(service, "PC-STD", "30") == 7200


def test_vip_multiplier_one_is_noop(temp: str) -> None:
    service, _ = create_service(Path(temp) / "vip-mult1.sqlite3")
    set_vip_multiplier("1")
    assert start_amount(service, "PC-VIP", "5") == 1200, "multiplier 1 => VIP priced like Standard"


def test_add_prepaid_time_racing_client_expiration_does_not_lose_added_time(temp: str) -> None:
    # Pre-deployment audit finding: add_prepaid_time's non-promo branch built
    # its final database write from a snapshot taken before awaiting the
    # client's command ack, exactly like add_time (see
    # smoke_expiration_grace.py's matching regression test). If the client's
    # own expiration message independently pushed the session into grace_period
    # during that await -- the common "rescue" scenario, a top-up racing the
    # session's own timer -- add_prepaid_time's write would blindly revert it,
    # even though the payment was already recorded.
    service, session_repository = create_service(Path(temp) / "prepaid-race.sqlite3")
    started = asyncio.run(
        service.start_prepaid_session("PC-STD", cash("5"), amount=Decimal("5"))
    )
    session_id = started.session.session_id

    triggered = {"done": False}
    original_send = service.send_required_command

    async def racing_send(*args: object, **kwargs: object) -> list[object]:
        if not triggered["done"]:
            triggered["done"] = True
            from server.time_utils import current_utc_time

            service.begin_expiration_grace(session_id, current_utc_time())
        return await original_send(*args, **kwargs)

    service.send_required_command = racing_send  # type: ignore[method-assign]

    result = asyncio.run(
        service.add_prepaid_time(session_id, cash("10"), amount=Decimal("10"))
    )

    assert triggered["done"], "race setup did not actually interleave begin_expiration_grace"
    # P5 (1200s) + P10 (2400s) at the seeded Standard rate, unaffected by
    # this being a race -- the paid time must not be silently dropped.
    assert result.session.total_seconds == 1200 + 2400
    # And the grace-period transition that raced it must be correctly
    # recognized (fresh re-read) and cancelled, not left half-applied.
    assert result.session.status == "active"
    assert result.session.grace_started_at is None
    assert result.session.shutdown_at is None

    persisted = session_repository.get_session(session_id)
    assert persisted is not None
    assert persisted.total_seconds == 1200 + 2400
    assert persisted.status == "active"


def main() -> None:
    tests = [
        test_standard_pc_arbitrary_amount_unchanged,
        test_vip_pc_arbitrary_amount_uses_2x,
        test_vip_pc_add_time_arbitrary_amount_uses_2x,
        test_vip_multiplier_setting_is_respected,
        test_vip_multiplier_one_is_noop,
        test_add_prepaid_time_racing_client_expiration_does_not_lose_added_time,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
            test(temp)
        print(f"{test.__name__} passed.")
    print("VIP arbitrary top-up smoke test passed.")


if __name__ == "__main__":
    main()
