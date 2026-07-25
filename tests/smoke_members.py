import asyncio
import os
import sys
import tempfile
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def assert_ledger_chain(transactions: list[object]) -> None:
    previous_after: int | None = None
    for transaction in transactions:
        before = transaction.balance_before_seconds
        after = transaction.balance_after_seconds
        if previous_after is not None:
            assert before == previous_after
        assert after == before + transaction.seconds_delta
        assert after >= 0
        previous_after = after


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "members.sqlite3")

        from server.database import initialize_database
        from server.main import (
            create_member,
            end_session,
            get_member_ledger,
            list_membership_products,
            member_repository,
            open_shift,
            pc_registry,
            purchase_member_time,
            register_pc,
            session_service,
            start_member_session,
            update_member_status,
        )
        from server.schemas import (
            CommandAcknowledgement,
            MemberCreateRequest,
            MemberPurchaseRequest,
            MemberSessionStartRequest,
            MemberStatusUpdate,
            PCRegistration,
            ShiftOpenRequest,
            UserCreateRequest,
        )
        from server.user_repository import UserRepository

        initialize_database()
        user_repository = UserRepository()
        cashier = user_repository.create_user(
            UserCreateRequest(
                username="cashier",
                display_name="Cashier",
                password="cashier-pass",
                role="cashier",
            )
        )
        cashier_user = user_repository.get_user(cashier.user_id)
        assert cashier_user is not None
        open_shift(ShiftOpenRequest(opening_cash=Decimal("1000.00")), cashier_user)

        member = create_member(
            MemberCreateRequest(
                member_code="sg-001",
                full_name="Ada Player",
                phone_number="09170000000",
            ),
            cashier_user,
        )
        try:
            create_member(
                MemberCreateRequest(member_code="SG-001", full_name="Duplicate"),
                cashier_user,
            )
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Duplicate member code should be rejected")

        products = list_membership_products()
        membership_fee = next(product for product in products if product.product_id == "membership-fee")
        promo = next(product for product in products if product.product_id == "member-promo")

        purchase = purchase_member_time(
            member.member_id,
            MemberPurchaseRequest(
                product_id=membership_fee.product_id,
                payment_method="cash",
                cash_received=Decimal("500.00"),
            ),
            cashier_user,
        )
        assert purchase.member.time_balance_seconds == 6 * 60 * 60
        assert purchase.payment.change_given == Decimal("400.00")

        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-MEMBER", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda _: pc_registry["PC-MEMBER"]

        async def acknowledge_command(*_: object) -> list[object]:
            return [
                CommandAcknowledgement(command_id="test", status="received"),
                CommandAcknowledgement(command_id="test", status="completed"),
            ]

        session_service.send_required_command = acknowledge_command
        capped = asyncio.run(
            start_member_session(
                MemberSessionStartRequest(
                    pc_name="PC-MEMBER",
                    member_id=member.member_id,
                    duration_seconds=60 * 60,
                ),
                cashier_user,
            )
        )
        assert capped.session.member_balance_before_seconds == 6 * 60 * 60
        assert capped.session.member_balance_after_seconds == 6 * 60 * 60
        try:
            asyncio.run(
                start_member_session(
                    MemberSessionStartRequest(
                        pc_name="PC-MEMBER-2",
                        member_id=member.member_id,
                        duration_seconds=30 * 60,
                    ),
                    cashier_user,
                )
            )
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Second simultaneous member session should be rejected")

        adjusted_capped = capped.session.model_copy(
            update={"started_at": capped.session.started_at - timedelta(minutes=37)}
        )
        session_service.repository.update_session(adjusted_capped)
        ended_capped = asyncio.run(end_session(capped.session.session_id, cashier_user))
        assert ended_capped.session.billed_seconds == 37 * 60
        assert ended_capped.session.member_balance_after_seconds == (6 * 60 * 60) - (37 * 60)
        assert member_repository.get_member(member.member_id).time_balance_seconds == (6 * 60 * 60) - (37 * 60)

        open_member = asyncio.run(
            start_member_session(
                MemberSessionStartRequest(
                    pc_name="PC-MEMBER",
                    member_id=member.member_id,
                    duration_seconds=None,
                ),
                cashier_user,
            )
        )
        assert open_member.session.original_seconds == 0
        adjusted_open = open_member.session.model_copy(
            update={"started_at": open_member.session.started_at - timedelta(minutes=2)}
        )
        session_service.repository.update_session(adjusted_open)
        ended_open = asyncio.run(end_session(open_member.session.session_id, cashier_user))
        assert ended_open.session.billed_seconds == 2 * 60
        expected_after_open = (6 * 60 * 60) - (39 * 60)
        assert ended_open.session.member_balance_after_seconds == expected_after_open

        promo_purchase = purchase_member_time(
            member.member_id,
            MemberPurchaseRequest(
                product_id=promo.product_id,
                payment_method="gcash",
                reference_number="GC-1",
            ),
            cashier_user,
        )
        assert promo_purchase.member.time_balance_seconds == expected_after_open + (4 * 60 * 60)

        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-FAIL-MEMBER", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda _: pc_registry["PC-FAIL-MEMBER"]

        async def fail_command(*_: object) -> list[object]:
            raise RuntimeError("simulated member command failure")

        before_failure = member_repository.get_member(member.member_id).time_balance_seconds
        session_service.send_required_command = fail_command
        try:
            asyncio.run(
                start_member_session(
                    MemberSessionStartRequest(
                        pc_name="PC-FAIL-MEMBER",
                        member_id=member.member_id,
                        duration_seconds=60 * 60,
                    ),
                    cashier_user,
                )
            )
        except HTTPException as error:
            assert error.status_code == 502
        else:
            raise AssertionError("Failed command should release reservation and leave balance unchanged")
        assert member_repository.get_member(member.member_id).time_balance_seconds == before_failure

        update_member_status(
            member.member_id,
            MemberStatusUpdate(status="inactive"),
            cashier_user,
        )
        try:
            asyncio.run(
                start_member_session(
                    MemberSessionStartRequest(
                        pc_name="PC-FAIL-MEMBER",
                        member_id=member.member_id,
                        duration_seconds=60,
                    ),
                    cashier_user,
                )
            )
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Inactive member should be rejected")

        ledger = get_member_ledger(member.member_id, cashier_user)
        transaction_types = [transaction.transaction_type for transaction in ledger]
        assert transaction_types.count("session_usage_finalized") == 2
        assert transaction_types.count("session_reservation_released") >= 1
        assert_ledger_chain(ledger)

        print("Membership smoke test passed.")


if __name__ == "__main__":
    main()
