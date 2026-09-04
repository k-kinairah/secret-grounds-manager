"""A Members-page top-up auto-applies the credited time to the member's
currently-running session (Option A): the route calls the existing
session_service.add_member_time, so the client gets an add_time command
and the countdown grows -- no separate Add Time on the PC needed.

Covers: standard top-up on a standard station (1:1), no active session
(nothing applied, balance still credited), and a VIP-balance top-up while
the member is on a standard station (not usable there -> not applied).
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
        UserCreateRequest(username="ax-owner", display_name="ax-owner", password="pw-owner-123", role="owner")
    )
    cashier_public = main_module.user_repository.create_user(
        UserCreateRequest(username="ax-cashier", display_name="ax-cashier", password="pw-cashier-123", role="cashier")
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


def make_member(main_module, _cashier, code: str):
    from server.schemas import MemberCreateRequest

    # actor=None -> internal zero-balance create (skips the membership fee);
    # balance is added separately via topup() below.
    return main_module.member_repository.create_member(
        MemberCreateRequest(member_code=code, username=f"{code.lower()}.user", pin="1234", confirm_pin="1234"),
        None,
    )


def topup(main_module, member_id: str, cashier, product_id: str, ref: str):
    from server.schemas import MemberPurchaseRequest

    return main_module.member_repository.purchase(
        member_id,
        MemberPurchaseRequest(product_id=product_id, payment_method="gcash", reference_number=ref),
        cashier,
    )


def route_topup(main_module, member_id: str, cashier, product_id: str, ref: str):
    from server.schemas import MemberPurchaseRequest

    return asyncio.run(
        main_module.purchase_member_time(
            member_id,
            MemberPurchaseRequest(product_id=product_id, payment_method="gcash", reference_number=ref),
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


STD_TOPUP_SECONDS = 4 * 60 * 60  # member-promo


def test_standard_topup_extends_running_session_on_standard_station(temp_directory: str) -> None:
    main_module, cashier, sent = create_env(Path(temp_directory) / "ax1.sqlite3")
    member = make_member(main_module, cashier, "AX1")
    topup(main_module, member.member_id, cashier, "member-promo", "SEED-1")  # 4h balance

    login = start_session(main_module, "PC-STD", "ax1.user")
    session_id = login.session.session_id
    before = main_module.session_repository.get_session(session_id)
    assert before is not None
    commands_before = len(sent)

    result = route_topup(main_module, member.member_id, cashier, "member-promo", "TOPUP-1")

    assert result.applied_to_active_session_seconds == STD_TOPUP_SECONDS
    after = main_module.session_repository.get_session(session_id)
    assert after is not None
    assert after.total_seconds == before.total_seconds + STD_TOPUP_SECONDS
    assert after.scheduled_end_at > before.scheduled_end_at
    assert len(sent) == commands_before + 1, "an add_time command must have been dispatched to the client"


def test_topup_with_no_active_session_applies_nothing(temp_directory: str) -> None:
    main_module, cashier, sent = create_env(Path(temp_directory) / "ax2.sqlite3")
    member = make_member(main_module, cashier, "AX2")
    commands_before = len(sent)

    result = route_topup(main_module, member.member_id, cashier, "member-promo", "TOPUP-2")

    assert result.applied_to_active_session_seconds == 0
    assert result.member.time_balance_seconds == STD_TOPUP_SECONDS  # balance still credited
    assert len(sent) == commands_before, "no session -> no client command"


def test_vip_topup_not_applied_to_session_on_standard_station(temp_directory: str) -> None:
    main_module, cashier, sent = create_env(Path(temp_directory) / "ax3.sqlite3")
    member = make_member(main_module, cashier, "AX3")
    topup(main_module, member.member_id, cashier, "member-promo", "SEED-3")  # standard balance to start with

    login = start_session(main_module, "PC-STD", "ax3.user")
    session_id = login.session.session_id
    before = main_module.session_repository.get_session(session_id)
    assert before is not None
    commands_before = len(sent)

    result = route_topup(main_module, member.member_id, cashier, "vip-promo-250", "TOPUP-3")

    assert result.applied_to_active_session_seconds == 0, "VIP balance is not usable on a Standard station"
    assert result.member.vip_balance_seconds == 10 * 60 * 60  # VIP balance still credited
    after = main_module.session_repository.get_session(session_id)
    assert after is not None
    assert after.total_seconds == before.total_seconds
    assert len(sent) == commands_before


def main() -> None:
    tests = [
        test_standard_topup_extends_running_session_on_standard_station,
        test_topup_with_no_active_session_applies_nothing,
        test_vip_topup_not_applied_to_session_on_standard_station,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Member top-up auto-extend smoke tests passed.")


if __name__ == "__main__":
    main()
