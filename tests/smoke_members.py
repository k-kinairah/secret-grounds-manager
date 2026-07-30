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

        from server.database import get_connection, initialize_database
        from server.main import (
            client_member_login,
            create_member,
            end_session,
            get_member_ledger,
            list_membership_products,
            member_repository,
            pc_registry,
            purchase_member_time,
            refund_member_purchase,
            register_pc,
            reset_member_pin,
            session_service,
            start_member_session,
            update_member_status,
        )
        from server.schemas import (
            ClientMemberLoginRequest,
            CommandAcknowledgement,
            MemberCreateRequest,
            MemberPinResetRequest,
            MemberPurchaseRefundRequest,
            MemberPurchaseRequest,
            MemberSessionStartRequest,
            MemberStatusUpdate,
            PCRegistration,
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
        admin = user_repository.create_user(
            UserCreateRequest(
                username="refund-admin",
                display_name="Admin",
                password="admin-pass",
                role="admin",
            )
        )
        admin_user = user_repository.get_user(admin.user_id)
        assert admin_user is not None

        member = create_member(
            MemberCreateRequest(
                username="Ada.Player",
                display_name="Ada Player",
                pin="1234",
                confirm_pin="1234",
                phone_number="09170000000",
            ),
            cashier_user,
        )
        assert member.username == "Ada.Player"
        assert member.display_name == "Ada Player"
        assert member.member_code.startswith("SG-")
        assert member.has_pin is True
        assert member.requires_account_setup is False
        try:
            create_member(
                MemberCreateRequest(
                    username="ada.player",
                    display_name="Duplicate",
                    pin="1234",
                    confirm_pin="1234",
                ),
                cashier_user,
            )
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Duplicate member username should be rejected")
        try:
            create_member(
                MemberCreateRequest(
                    username="bad user",
                    display_name="Bad Username",
                    pin="1234",
                    confirm_pin="1234",
                ),
                cashier_user,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid member username should be rejected")
        try:
            create_member(
                MemberCreateRequest(
                    username="pinfail",
                    display_name="Bad PIN",
                    pin="123",
                    confirm_pin="123",
                ),
                cashier_user,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid member PIN should be rejected")

        products = list_membership_products()
        membership_fee = next(product for product in products if product.product_id == "membership-fee")
        promo = next(product for product in products if product.product_id == "member-promo")

        try:
            purchase_member_time(
                member.member_id,
                MemberPurchaseRequest(
                    product_id=membership_fee.product_id,
                    payment_method="cash",
                    cash_received=Decimal("1.00"),
                ),
                cashier_user,
            )
        except HTTPException as error:
            assert error.status_code == 400
            assert error.detail == "Cash received cannot be lower than payment amount"
        else:
            raise AssertionError("Invalid member purchase payment should be rejected")
        assert member_repository.get_member(member.member_id).time_balance_seconds == 0

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
        assert purchase.payment.shift_id is None
        assert purchase.payment.recorded_by_user_id == cashier_user.user_id
        with get_connection() as connection:
            payment_row = connection.execute(
                """
                SELECT p.recorded_by_user_id, p.shift_id, mt.related_payment_id
                FROM payments p
                JOIN member_time_transactions mt ON mt.related_payment_id = p.payment_id
                WHERE p.payment_id = ?
                """,
                (purchase.payment.payment_id,),
            ).fetchone()
        assert payment_row is not None
        assert payment_row["recorded_by_user_id"] == cashier_user.user_id
        assert payment_row["shift_id"] is None
        assert payment_row["related_payment_id"] == purchase.payment.payment_id

        refund_member = create_member(
            MemberCreateRequest(
                username="Refund.Player",
                display_name="Refund Player",
                pin="1234",
                confirm_pin="1234",
            ),
            cashier_user,
        )
        refund_purchase_key = "member-refund-smoke-key"
        refundable_purchase = purchase_member_time(
            refund_member.member_id,
            MemberPurchaseRequest(
                product_id=promo.product_id,
                payment_method="gcash",
                reference_number="GC-REFUND-1",
                idempotency_key=refund_purchase_key,
            ),
            cashier_user,
        )
        duplicate_purchase = purchase_member_time(
            refund_member.member_id,
            MemberPurchaseRequest(
                product_id=promo.product_id,
                payment_method="gcash",
                reference_number="GC-REFUND-1",
                idempotency_key=refund_purchase_key,
            ),
            cashier_user,
        )
        assert duplicate_purchase.payment.payment_id == refundable_purchase.payment.payment_id
        assert duplicate_purchase.transaction.transaction_id == refundable_purchase.transaction.transaction_id
        assert duplicate_purchase.member.time_balance_seconds == refundable_purchase.member.time_balance_seconds
        refund = refund_member_purchase(
            refundable_purchase.payment.payment_id,
            MemberPurchaseRefundRequest(reason="Duplicate transaction"),
            admin_user,
        )
        assert refund.payment.status == "voided"
        assert refund.reversal_transaction.transaction_type == "purchase_refund"
        assert refund.reversal_transaction.seconds_delta == -refundable_purchase.transaction.seconds_delta
        assert refund.member.time_balance_seconds == 0
        refund_ledger = get_member_ledger(refund_member.member_id, cashier_user)
        assert [transaction.transaction_type for transaction in refund_ledger] == [
            "promo_topup",
            "purchase_refund",
        ]
        secured_member = reset_member_pin(
            member.member_id,
            MemberPinResetRequest(pin="1234", confirm_pin="1234"),
            cashier_user,
        )
        assert secured_member.has_pin is True
        serialized_member = secured_member.model_dump_json().lower()
        assert "pin_hash" not in serialized_member
        assert "pbkdf2" not in serialized_member
        assert "1234" not in serialized_member

        with get_connection() as connection:
            legacy_now = member.created_at.isoformat()
            connection.execute(
                """
                INSERT INTO members (
                    member_id,
                    member_code,
                    full_name,
                    phone_number,
                    status,
                    time_balance_seconds,
                    joined_at,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-member",
                    "LEGACY-1",
                    "Legacy Player",
                    None,
                    "active",
                    3600,
                    legacy_now,
                    legacy_now,
                    legacy_now,
                ),
            )
        legacy_member = member_repository.get_member("legacy-member")
        assert legacy_member is not None
        assert legacy_member.requires_account_setup is True
        try:
            member_repository.require_usable_member("legacy-member")
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Legacy member without username and PIN should require setup")

        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-MEMBER", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda _: pc_registry["PC-MEMBER"]

        async def acknowledge_command(*_: object) -> list[object]:
            return [
                CommandAcknowledgement(command_id="test", status="received"),
                CommandAcknowledgement(command_id="test", status="completed"),
            ]

        session_service.send_required_command = acknowledge_command
        self_login_member = create_member(
            MemberCreateRequest(
                username="Self.Login",
                display_name="Self Login",
                pin="2468",
                confirm_pin="2468",
            ),
            cashier_user,
        )
        purchase_member_time(
            self_login_member.member_id,
            MemberPurchaseRequest(
                product_id=promo.product_id,
                payment_method="gcash",
                reference_number="SELF-LOGIN-1",
            ),
            cashier_user,
        )
        with get_connection() as connection:
            before_payment_count = connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        self_login = asyncio.run(
            client_member_login(
                ClientMemberLoginRequest(
                    pc_name="PC-MEMBER",
                    ip_address="127.0.0.1",
                    member_username="Self.Login",
                    member_pin="2468",
                )
            )
        )
        assert self_login.member_display_name == "Self Login"
        assert self_login.session.session_mode == "member_balance"
        assert self_login.remaining_seconds == 4 * 60 * 60
        with get_connection() as connection:
            after_payment_count = connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0]
        assert after_payment_count == before_payment_count
        try:
            asyncio.run(
                client_member_login(
                    ClientMemberLoginRequest(
                        pc_name="PC-MEMBER",
                        ip_address="127.0.0.1",
                        member_username="Self.Login",
                        member_pin="2468",
                    )
                )
            )
        except HTTPException as error:
            assert error.status_code == 409
            assert error.detail == "This computer is not currently available."
        else:
            raise AssertionError("Client self-login should reject unavailable PCs")
        ended_self_login = asyncio.run(end_session(self_login.session.session_id, cashier_user))
        assert ended_self_login.session.ended_reason == "manual_end"

        no_balance_member = create_member(
            MemberCreateRequest(
                username="Empty.Player",
                display_name="Empty Player",
                pin="1357",
                confirm_pin="1357",
            ),
            cashier_user,
        )
        try:
            asyncio.run(
                client_member_login(
                    ClientMemberLoginRequest(
                        pc_name="PC-MEMBER",
                        ip_address="127.0.0.1",
                        member_username=no_balance_member.username,
                        member_pin="1357",
                    )
                )
            )
        except HTTPException as error:
            assert error.status_code == 409
            assert error.detail == "This account has no remaining play time. Please ask the cashier to add time."
        else:
            raise AssertionError("Client self-login should reject members with no balance")
        try:
            asyncio.run(
                client_member_login(
                    ClientMemberLoginRequest(
                        pc_name="PC-MEMBER",
                        ip_address="127.0.0.1",
                        member_username="Self.Login",
                        member_pin="0000",
                    )
                )
            )
        except HTTPException as error:
            assert error.status_code == 403
            assert error.detail == "Username or PIN is incorrect."
        else:
            raise AssertionError("Client self-login should hide wrong-PIN details")
        try:
            asyncio.run(
                start_member_session(
                    MemberSessionStartRequest(
                        pc_name="PC-MEMBER",
                        member_username=member.username,
                        duration_seconds=60 * 60,
                    ),
                    cashier_user,
                )
            )
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Member PIN should be required when configured")
        try:
            asyncio.run(
                start_member_session(
                    MemberSessionStartRequest(
                        pc_name="PC-MEMBER",
                        member_username=member.username,
                        duration_seconds=60 * 60,
                        member_pin="9999",
                    ),
                    cashier_user,
                )
            )
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Incorrect member PIN should be rejected")
        for _ in range(3):
            try:
                member_repository.verify_member_pin(member.member_id, "9999")
            except HTTPException as error:
                assert error.status_code == 403
            else:
                raise AssertionError("Incorrect member PIN should be rejected")
        try:
            member_repository.verify_member_pin(member.member_id, "9999")
        except HTTPException as error:
            assert error.status_code == 429
        else:
            raise AssertionError("Repeated incorrect member PIN attempts should be throttled")
        reset_member_pin(
            member.member_id,
            MemberPinResetRequest(pin="1234", confirm_pin="1234"),
            cashier_user,
        )
        capped = asyncio.run(
            start_member_session(
                MemberSessionStartRequest(
                    pc_name="PC-MEMBER",
                    member_username=member.username,
                    duration_seconds=60 * 60,
                    member_pin="1234",
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
                        member_username=member.username,
                        duration_seconds=30 * 60,
                        member_pin="1234",
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
                    member_username=member.username,
                    duration_seconds=None,
                    member_pin="1234",
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
                        member_username=member.username,
                        duration_seconds=60 * 60,
                        member_pin="1234",
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
                        member_username=member.username,
                        duration_seconds=60,
                        member_pin="1234",
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
