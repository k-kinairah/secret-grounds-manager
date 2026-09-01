import asyncio
import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(Path(temp_directory) / "customer_lock.sqlite3")

        from server.database import initialize_database
        from server.main import (
            client_member_login,
            client_session_lock,
            client_session_unlock,
            clear_customer_lock,
            create_member,
            end_session,
            list_membership_products,
            pc_registry,
            purchase_member_time,
            register_pc,
            session_service,
            start_session,
        )
        from server.schemas import (
            ClientMemberLoginRequest,
            ClientSessionLockRequest,
            ClientSessionUnlockRequest,
            CommandAcknowledgement,
            MemberCreateRequest,
            MemberPurchaseRequest,
            PCRegistration,
            SessionRecord,
            StartSessionRequest,
            UserCreateRequest,
        )
        from decimal import Decimal

        from server.time_utils import current_utc_time
        from server.user_repository import UserRepository

        initialize_database()
        user_repository = UserRepository()
        cashier = user_repository.create_user(
            UserCreateRequest(
                username="lock-cashier",
                display_name="Cashier",
                password="cashier-pass",
                role="cashier",
            )
        )
        cashier_user = user_repository.get_user(cashier.user_id)
        assert cashier_user is not None

        member = create_member(
            MemberCreateRequest(
                username="Locker.Member",
                display_name="Locker Member",
                pin="1357",
                confirm_pin="1357",
                payment_method="cash",
                cash_received=Decimal("100.00"),
            ),
            cashier_user,
        )
        promo = next(
            product
            for product in list_membership_products()
            if product.product_type == "member_topup"
        )
        purchase_member_time(
            member.member_id,
            MemberPurchaseRequest(
                product_id=promo.product_id,
                payment_method="gcash",
                reference_number="LOCK-TOPUP-1",
            ),
            cashier_user,
        )

        pc_registry.clear()
        register_pc(PCRegistration(pc_name="PC-LOCK", ip_address="127.0.0.1"))
        session_service.require_available_client = lambda pc_name: pc_registry[pc_name]

        sent_commands: list[dict[str, object]] = []

        async def acknowledge_command(pc_name, command):
            sent_commands.append({"pc_name": pc_name, "type": command.type, "payload": command.payload})
            return [
                CommandAcknowledgement(command_id=command.command_id, status="received"),
                CommandAcknowledgement(command_id=command.command_id, status="completed"),
            ]

        session_service.send_required_command = acknowledge_command

        # --- start a real member_balance session (the only eligible mode) ---
        login = asyncio.run(
            client_member_login(
                ClientMemberLoginRequest(
                    pc_name="PC-LOCK",
                    ip_address="127.0.0.1",
                    member_username="Locker.Member",
                    member_pin="1357",
                )
            )
        )
        session_id = login.session.session_id
        assert login.session.session_mode == "member_balance"
        assert login.session.customer_locked is False
        original_scheduled_end_at = login.session.scheduled_end_at

        # --- eligibility: a non-member (open_time) session must not be
        # lockable. Open Time can no longer be started via the API, so this
        # constructs a historical open_time session directly -- the same
        # shape a session already active before the upgrade would have --
        # to prove the invariant still holds for it. ---
        register_pc(PCRegistration(pc_name="PC-OPEN", ip_address="127.0.0.2"))
        now = current_utc_time()
        historical_open_time_session = SessionRecord(
            session_id="historical-open-time-lock-test",
            pc_name="PC-OPEN",
            status="active",
            started_at=now,
            scheduled_end_at=now,
            original_seconds=0,
            added_seconds=0,
            total_seconds=0,
            amount_paid=None,
            ended_reason=None,
            created_at=now,
            base_amount=Decimal("0.00"),
            additional_amount=Decimal("0.00"),
            total_amount=Decimal("0.00"),
            session_mode="open_time",
            settlement_pending=True,
        )
        session_service.repository.create_session(historical_open_time_session)
        try:
            session_service.engage_customer_lock("PC-OPEN", historical_open_time_session.session_id)
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Open Time sessions must not be lockable")
        assert session_service.repository.get_session(historical_open_time_session.session_id).customer_locked is False

        # --- lock the member session (member-initiated, no PIN needed) ---
        locked = asyncio.run(
            client_session_lock(
                ClientSessionLockRequest(
                    pc_name="PC-LOCK",
                    ip_address="127.0.0.1",
                    session_id=session_id,
                )
            )
        )
        assert locked.session.customer_locked is True
        assert locked.session.status == "active"
        assert locked.session.session_id == session_id, "locking must not create a new session"
        assert locked.session.scheduled_end_at == original_scheduled_end_at, "locking must not change scheduled_end_at (no pause/extend)"

        persisted = session_service.repository.get_session(session_id)
        assert persisted.customer_locked is True, "lock state must be persisted, not just returned"
        assert persisted.status == "active", "the session itself remains active while locked"

        # --- PC must stay unavailable to a brand-new session while locked ---
        try:
            asyncio.run(
                start_session(StartSessionRequest(pc_name="PC-LOCK", duration_seconds=60))
            )
        except HTTPException as error:
            assert error.status_code == 409
            assert "already has an active session" in error.detail
        else:
            raise AssertionError("A locked PC must not accept a brand-new session")

        # --- wrong PIN fails and does not clear the lock ---
        try:
            asyncio.run(
                client_session_unlock(
                    ClientSessionUnlockRequest(
                        pc_name="PC-LOCK",
                        ip_address="127.0.0.1",
                        pin="0000",
                    )
                )
            )
        except HTTPException as error:
            assert error.status_code == 403
        else:
            raise AssertionError("Wrong PIN must not unlock the session")
        assert session_service.repository.get_session(session_id).customer_locked is True

        # --- PIN failure limits remain intact (existing member_repository lockout) ---
        # One wrong attempt already happened above; member_repository locks
        # out after 5 total failures, so 4 more here reaches that threshold
        # and the next (6th) attempt below must see the 429 lockout.
        for _ in range(4):
            try:
                asyncio.run(
                    client_session_unlock(
                        ClientSessionUnlockRequest(
                            pc_name="PC-LOCK",
                            ip_address="127.0.0.1",
                            pin="0000",
                        )
                    )
                )
            except HTTPException as error:
                assert error.status_code in {403, 429}
        try:
            asyncio.run(
                client_session_unlock(
                    ClientSessionUnlockRequest(
                        pc_name="PC-LOCK",
                        ip_address="127.0.0.1",
                        pin="0000",
                    )
                )
            )
        except HTTPException as error:
            assert error.status_code == 429, "5+ failed PIN attempts must trip the existing lockout"
        else:
            raise AssertionError("PIN lockout did not engage after repeated failures")

        # Reset the lockout the same way an operator/time-based expiry would
        # (directly via the repository, since we don't want to sleep 5 real
        # minutes in a smoke test) so the correct-PIN case below is clean.
        from server.database import get_connection

        with get_connection() as connection:
            connection.execute(
                "UPDATE members SET pin_failed_attempts = 0, pin_locked_until = NULL WHERE member_id = ?",
                (member.member_id,),
            )

        # --- correct PIN unlocks, keeps the same session, does not reset anything ---
        unlocked = asyncio.run(
            client_session_unlock(
                ClientSessionUnlockRequest(
                    pc_name="PC-LOCK",
                    ip_address="127.0.0.1",
                    pin="1357",
                )
            )
        )
        assert unlocked.session.customer_locked is False
        assert unlocked.session.session_id == session_id
        assert unlocked.session.scheduled_end_at == original_scheduled_end_at
        assert unlocked.member_display_name == "Locker Member"

        # --- lock again, then verify the cashier override clears it without a PIN ---
        asyncio.run(
            client_session_lock(
                ClientSessionLockRequest(
                    pc_name="PC-LOCK",
                    ip_address="127.0.0.1",
                    session_id=session_id,
                )
            )
        )
        assert session_service.repository.get_session(session_id).customer_locked is True
        override_result = asyncio.run(clear_customer_lock(session_id, cashier_user))
        assert override_result.session.customer_locked is False
        assert override_result.session.session_id == session_id
        assert any(
            entry["type"] == "clear_customer_lock" and entry["payload"]["session_id"] == session_id
            for entry in sent_commands
        ), "cashier override must push a clear_customer_lock command to the client"

        # Clearing an already-unlocked session must fail loudly, not silently no-op.
        try:
            asyncio.run(clear_customer_lock(session_id, cashier_user))
        except HTTPException as error:
            assert error.status_code == 409
        else:
            raise AssertionError("Clearing a non-locked session should be rejected")

        # --- ending the session automatically clears the lock ---
        asyncio.run(
            client_session_lock(
                ClientSessionLockRequest(
                    pc_name="PC-LOCK",
                    ip_address="127.0.0.1",
                    session_id=session_id,
                )
            )
        )
        assert session_service.repository.get_session(session_id).customer_locked is True
        ended = asyncio.run(end_session(session_id, cashier_user))
        assert ended.session.customer_locked is False, "ending a session must clear a stale customer lock"
        assert ended.session.status in {"completed", "settlement_pending"}

        # --- expiry while locked does not leave the lock flag dangling either ---
        register_pc(PCRegistration(pc_name="PC-EXPIRE", ip_address="127.0.0.3"))
        expiring_login = asyncio.run(
            client_member_login(
                ClientMemberLoginRequest(
                    pc_name="PC-EXPIRE",
                    ip_address="127.0.0.3",
                    member_username="Locker.Member",
                    member_pin="1357",
                )
            )
        )
        expiring_session_id = expiring_login.session.session_id
        locked_before_expiry = asyncio.run(
            client_session_lock(
                ClientSessionLockRequest(
                    pc_name="PC-EXPIRE",
                    ip_address="127.0.0.3",
                    session_id=expiring_session_id,
                )
            )
        )
        assert locked_before_expiry.session.customer_locked is True
        grace_session = session_service.begin_expiration_grace(expiring_session_id)
        assert grace_session is not None
        assert grace_session.status == "grace_period"
        # The grace/shutdown screen takes over client-side regardless of
        # customer_locked (see client.py adopt_server_session) -- the
        # backend contract is simply that finishing expiration always
        # clears the now-meaningless lock flag, same as any other end path.
        finalized = session_service.finalize_ended_session(grace_session, "timer_finished", None)
        assert finalized.customer_locked is False, "expiry must not leave the desktop permanently locked/unreachable"

        print("Customer lock smoke test passed.")


if __name__ == "__main__":
    main()
