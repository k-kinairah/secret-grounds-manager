"""A member should be able to top up a small cash amount priced off the
same active metered (Standard) rate everyone else's Standard add-time
uses, not just the fixed Member packages -- for amounts too small to
match any catalog product (see purchase_member_custom_amount /
MemberRepository.purchase_custom_amount).

Covers: correct amount->seconds conversion against the default seeded
metered rate, auto-extend of a running session (1:1 on a Standard
station, 2:1 on a VIP station -- same conversion the package top-up
flow uses), no active session (balance still credited, nothing
applied), and idempotency-key dedup.
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module
    from server.schemas import CommandAcknowledgement, PCLayoutUpdate, UserCreateRequest

    main_module.pc_registry.clear()
    for pc_name, is_vip in (("PC-STD", False), ("PC-VIP", True)):
        main_module.pc_layout_repository.upsert_layout(
            pc_name,
            PCLayoutUpdate(
                display_name=pc_name, area_label="T", layout_order=1,
                is_vip=is_vip, is_enabled=True, income_group_id=None,
            ),
        )

    main_module.user_repository.create_user(
        UserCreateRequest(username="cx-owner", display_name="cx-owner", password="pw-owner-123", role="owner")
    )
    cashier_public = main_module.user_repository.create_user(
        UserCreateRequest(username="cx-cashier", display_name="cx-cashier", password="pw-cashier-123", role="cashier")
    )
    cashier = main_module.user_repository.get_user(cashier_public.user_id)
    assert cashier is not None

    sent: list[object] = []
    main_module.session_service.require_available_client = lambda pc: main_module.pc_registry[pc]

    async def ack(_pc_name: object, command: object) -> list[CommandAcknowledgement]:
        sent.append(command)
        return [
            CommandAcknowledgement(command_id="test", status="received"),
            CommandAcknowledgement(command_id="test", status="completed"),
        ]

    main_module.session_service.send_required_command = ack
    return main_module, cashier, sent


def make_member(main_module, code: str):
    from server.schemas import MemberCreateRequest

    return main_module.member_repository.create_member(
        MemberCreateRequest(member_code=code, username=f"{code.lower()}.user", pin="1234", confirm_pin="1234"),
        None,
    )


def route_custom_topup(main_module, member_id: str, cashier, amount: str, idempotency_key: str | None = None):
    from server.schemas import MemberCustomTopupRequest

    return asyncio.run(
        main_module.purchase_member_custom_amount(
            member_id,
            MemberCustomTopupRequest(
                amount=amount,
                payment_method="gcash",
                reference_number=f"REF-{amount}",
                idempotency_key=idempotency_key,
            ),
            cashier,
        )
    )


def start_session(main_module, pc_name: str, username: str):
    from server.schemas import ClientMemberLoginRequest, PCRegistration

    main_module.register_pc(PCRegistration(pc_name=pc_name, ip_address="127.0.0.1"))
    return asyncio.run(
        main_module.client_member_login(
            ClientMemberLoginRequest(
                pc_name=pc_name, ip_address="127.0.0.1", member_username=username, member_pin="1234"
            )
        )
    )


# Default seeded metered rate: P5 = 20 minutes (P1 = 4 minutes = 240s).
SECONDS_PER_PESO = 240


def test_custom_amount_computes_seconds_from_metered_rate(temp_directory: str) -> None:
    main_module, cashier, _sent = create_env(Path(temp_directory) / "cx1.sqlite3")
    member = make_member(main_module, "CX1")

    result = route_custom_topup(main_module, member.member_id, cashier, "20.00")

    assert result.base_seconds == 20 * SECONDS_PER_PESO
    assert result.total_seconds_credited == 20 * SECONDS_PER_PESO
    assert result.product.balance_type == "standard"
    assert result.member.time_balance_seconds == 20 * SECONDS_PER_PESO


def test_custom_amount_extends_running_session_on_standard_station(temp_directory: str) -> None:
    main_module, cashier, sent = create_env(Path(temp_directory) / "cx2.sqlite3")
    member = make_member(main_module, "CX2")
    route_custom_topup(main_module, member.member_id, cashier, "5.00")  # seed a startable balance

    login = start_session(main_module, "PC-STD", "cx2.user")
    session_id = login.session.session_id
    before = main_module.session_repository.get_session(session_id)
    assert before is not None
    commands_before = len(sent)

    result = route_custom_topup(main_module, member.member_id, cashier, "20.00")

    expected_seconds = 20 * SECONDS_PER_PESO
    assert result.applied_to_active_session_seconds == expected_seconds
    after = main_module.session_repository.get_session(session_id)
    assert after is not None
    assert after.total_seconds == before.total_seconds + expected_seconds
    assert after.scheduled_end_at > before.scheduled_end_at
    assert len(sent) == commands_before + 1, "an add_time command must have been dispatched to the client"


def test_custom_amount_extends_running_session_on_vip_station_at_half_rate(temp_directory: str) -> None:
    main_module, cashier, _sent = create_env(Path(temp_directory) / "cx3.sqlite3")
    member = make_member(main_module, "CX3")
    route_custom_topup(main_module, member.member_id, cashier, "5.00")  # seed a startable balance

    login = start_session(main_module, "PC-VIP", "cx3.user")
    session_id = login.session.session_id
    before = main_module.session_repository.get_session(session_id)
    assert before is not None

    result = route_custom_topup(main_module, member.member_id, cashier, "20.00")

    credited_seconds = 20 * SECONDS_PER_PESO
    assert result.total_seconds_credited == credited_seconds
    # Standard balance spends at 2:1 on a VIP station -- same conversion
    # topup_real_seconds_for_session uses for the package purchase flow.
    assert result.applied_to_active_session_seconds == credited_seconds // 2
    after = main_module.session_repository.get_session(session_id)
    assert after is not None
    assert after.total_seconds == before.total_seconds + credited_seconds // 2


def test_custom_amount_with_no_active_session_applies_nothing(temp_directory: str) -> None:
    main_module, cashier, sent = create_env(Path(temp_directory) / "cx4.sqlite3")
    member = make_member(main_module, "CX4")
    commands_before = len(sent)

    result = route_custom_topup(main_module, member.member_id, cashier, "10.00")

    assert result.applied_to_active_session_seconds == 0
    assert result.member.time_balance_seconds == 10 * SECONDS_PER_PESO  # balance still credited
    assert len(sent) == commands_before, "no session -> no client command"


def test_custom_amount_idempotency_key_dedupes(temp_directory: str) -> None:
    main_module, cashier, _sent = create_env(Path(temp_directory) / "cx5.sqlite3")
    member = make_member(main_module, "CX5")

    first = route_custom_topup(main_module, member.member_id, cashier, "15.00", idempotency_key="cx5-key-12345")
    second = route_custom_topup(main_module, member.member_id, cashier, "15.00", idempotency_key="cx5-key-12345")

    assert first.payment.payment_id == second.payment.payment_id
    updated_member = main_module.member_repository.get_member(member.member_id)
    assert updated_member is not None
    assert updated_member.time_balance_seconds == 15 * SECONDS_PER_PESO, "must not be charged twice"


def main() -> None:
    tests = [
        test_custom_amount_computes_seconds_from_metered_rate,
        test_custom_amount_extends_running_session_on_standard_station,
        test_custom_amount_extends_running_session_on_vip_station_at_half_rate,
        test_custom_amount_with_no_active_session_applies_nothing,
        test_custom_amount_idempotency_key_dedupes,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Member custom top-up smoke tests passed.")


if __name__ == "__main__":
    main()
