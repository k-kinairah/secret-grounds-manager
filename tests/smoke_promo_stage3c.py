"""Stage 3C verification: three requirements not yet covered by earlier
promo-maker tests -- exercised through the real FastAPI dependency chain
and real route/service functions, not standalone modules or mocks.

1. POST /sessions/promo-quote auth (real get_current_user/require_role
   dependency functions, no DB writes).
2. The public POST /payments/{payment_id}/void path for a promo-applied
   session (atomicity, snapshot preservation, limit-slot release,
   idempotency, failed-void no-op).
3. Attribution snapshots (pc/income-group/operator) through the real
   start_prepaid_session and add_prepaid_time paths, using a non-default
   income group so this can't pass on null defaults.
4. SessionCommandResponse.command/acknowledgements optional-on-retry is
   safely consumed by existing dashboard callers.
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


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module
    from server.schemas import PCLayoutUpdate, PCRegistration

    main_module.pc_registry.clear()
    main_module.register_pc(PCRegistration(pc_name="PC-PROMO", ip_address="127.0.0.1"))
    main_module.register_pc(PCRegistration(pc_name="PC-PROMO-2", ip_address="127.0.0.1"))
    # Non-default income group ("tyrone", one of the four seeded groups) --
    # proves attribution snapshots aren't accidentally passing on None/default.
    for pc_name in ("PC-PROMO", "PC-PROMO-2"):
        main_module.pc_layout_repository.upsert_layout(
            pc_name,
            PCLayoutUpdate(display_name=pc_name, area_label="TEST", layout_order=1, is_vip=False, is_enabled=True, income_group_id="tyrone"),
        )

    main_module.session_service.require_available_client = lambda pc_name: main_module.pc_registry[pc_name]

    async def acknowledge_command(*_: object) -> list[object]:
        return []

    main_module.session_service.send_required_command = acknowledge_command
    return main_module


def make_standard_promo(main_module, **overrides):
    from server.schemas import PromoCreate, PromoStateTransition

    now = datetime.now(timezone.utc)
    fields = {
        "name": "Stage 3C Standard Promo",
        "audience": "standard",
        "amount_mode": "exact",
        "price": Decimal("20.00"),
        "granted_seconds": 6000,  # normal P20 = 4800s
        "station_scope": "standard",
        "applies_to": "start_session",
        "starts_at": now - timedelta(hours=1),
        "ends_at": now + timedelta(days=7),
        "total_redemption_limit": 1,
    }
    fields.update(overrides)
    promo = main_module.session_service.promo_repository.create(PromoCreate(**fields))
    main_module.session_service.promo_repository.transition_state(
        promo.promo_id, PromoStateTransition(owner_state="enabled")
    )
    return promo


def create_users(main_module):
    from server.user_repository import UserRepository

    user_repository = UserRepository()
    from server.schemas import UserCreateRequest

    owner = user_repository.create_user(
        UserCreateRequest(username="owner3c", display_name="Owner 3C", password="owner-pass", role="owner")
    )
    admin = user_repository.create_user(
        UserCreateRequest(username="admin3c", display_name="Admin 3C", password="admin-pass", role="admin")
    )
    cashier = user_repository.create_user(
        UserCreateRequest(username="cashier3c", display_name="Cashier 3C", password="cashier-pass", role="cashier")
    )
    return owner, admin, cashier


def login(main_module, username: str, password: str) -> str:
    from server.auth import authenticate_login, auth_session_repository, set_auth_cookie
    from fastapi import Response
    from server.schemas import LoginRequest

    user = authenticate_login(LoginRequest(username=username, password=password))
    token, _ = auth_session_repository.create_session(user.user_id, 3600)
    set_auth_cookie(Response(), token)
    return token


# --- 1. Quote-route authentication through the real dependency chain ------


def test_quote_route_rejects_unauthenticated_request(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "quote-auth-unauth.sqlite3")
    from server.auth import get_current_user

    try:
        get_current_user(sg_session=None)
        raise AssertionError("missing session cookie must be rejected")
    except HTTPException as error:
        assert error.status_code == 401


def test_quote_route_accepts_authorized_cashier_and_owner_operator(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "quote-auth-ok.sqlite3")
    from server.auth import get_current_user, require_role
    from server.schemas import PromoQuoteRequest

    make_standard_promo(main_module)
    create_users(main_module)
    cashier_token = login(main_module, "cashier3c", "cashier-pass")

    # Drives the exact same functions FastAPI's Depends() graph calls for
    # this route: get_current_user(cookie) -> require_role("cashier")(user).
    current_user = get_current_user(sg_session=cashier_token)
    authorized_user = require_role("cashier")(current_user)
    response = main_module.quote_standard_or_vip_promo(
        PromoQuoteRequest(pc_name="PC-PROMO", action="start_session", amount=Decimal("20.00")),
        authorized_user,
    )
    assert response.source_type == "promo"

    # Family "quick operators" are seeded with role=owner (rank above
    # cashier), so they must also pass this cashier-minimum gate.
    from server.user_repository import UserRepository

    mama_record = UserRepository().get_user("operator_mama")
    assert mama_record is not None
    assert mama_record.role == "owner"
    mama_authorized = require_role("cashier")(mama_record)
    mama_response = main_module.quote_standard_or_vip_promo(
        PromoQuoteRequest(pc_name="PC-PROMO", action="start_session", amount=Decimal("20.00")),
        mama_authorized,
    )
    assert mama_response.source_type == "promo"


def test_quote_route_rejects_deactivated_users_session(temp_directory: str) -> None:
    """This system defines only owner/admin/cashier, and the quote route's
    require_role("cashier") is the floor rank -- every authenticated role
    already passes it, so there is no *authenticated* role this endpoint
    rejects. The real "unauthorized" case the existing policy actually
    enforces is a revoked/deactivated account: get_current_user re-checks
    is_active on every call, so a previously valid cookie stops working
    the moment access is revoked."""
    main_module = create_env(Path(temp_directory) / "quote-auth-revoked.sqlite3")
    from server.auth import get_current_user
    from server.user_repository import UserRepository
    from server.schemas import UserStatusUpdate

    owner, admin, cashier = create_users(main_module)
    token = login(main_module, "cashier3c", "cashier-pass")
    assert get_current_user(sg_session=token).user_id == cashier.user_id

    user_repository = UserRepository()
    user_repository.set_active(cashier.user_id, UserStatusUpdate(is_active=False))

    try:
        get_current_user(sg_session=token)
        raise AssertionError("a deactivated account's existing session must be rejected")
    except HTTPException as error:
        assert error.status_code == 401


def test_quote_route_performs_no_database_writes(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "quote-no-writes.sqlite3")
    from server.auth import get_current_user, require_role
    from server.schemas import PromoQuoteRequest
    from server.database import get_connection

    make_standard_promo(main_module)
    create_users(main_module)
    token = login(main_module, "cashier3c", "cashier-pass")
    authorized_user = require_role("cashier")(get_current_user(sg_session=token))

    def counts():
        with get_connection() as connection:
            return (
                connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM promo_redemptions").fetchone()[0],
                connection.execute("SELECT COUNT(*) FROM promo_audit_events").fetchone()[0],
            )

    before = counts()
    main_module.quote_standard_or_vip_promo(
        PromoQuoteRequest(pc_name="PC-PROMO", action="start_session", amount=Decimal("20.00")), authorized_user
    )
    main_module.quote_standard_or_vip_promo(
        PromoQuoteRequest(pc_name="PC-PROMO", action="start_session", amount=Decimal("999.00")), authorized_user
    )
    after = counts()
    assert before == after, "quoting must never write to sessions/payments/promo_redemptions/promo_audit_events"


# --- 2. Public payment-void path -------------------------------------------


def test_public_void_route_voids_payment_and_redemption_atomically(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "void-atomic.sqlite3")
    from server.schemas import PaymentCreateRequest, VoidPaymentRequest
    from server.auth import get_current_user, require_role, get_actor_user_id
    from server.database import get_connection

    promo = make_standard_promo(main_module)
    owner, admin, cashier = create_users(main_module)
    admin_user = require_role("admin")(get_current_user(sg_session=login(main_module, "admin3c", "admin-pass")))

    response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
        )
    )
    with get_connection() as connection:
        payment_id = connection.execute(
            "SELECT payment_id FROM payments WHERE session_id = ?", (response.session.session_id,)
        ).fetchone()["payment_id"]
        redemption_snapshot_before = dict(
            connection.execute("SELECT * FROM promo_redemptions WHERE payment_id = ?", (payment_id,)).fetchone()
        )

    billing = main_module.void_payment(
        payment_id, VoidPaymentRequest(reason="Stage 3C atomicity test"), admin_user
    )
    assert billing.payment_status in {"voided", "unpaid", "partial"}

    with get_connection() as connection:
        payment_row = connection.execute("SELECT status FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        redemption_row = dict(
            connection.execute("SELECT * FROM promo_redemptions WHERE payment_id = ?", (payment_id,)).fetchone()
        )
    assert payment_row["status"] == "voided"
    assert redemption_row["voided_at"] is not None
    assert redemption_row["void_reason"] == "Stage 3C atomicity test"

    # Snapshot fields must be untouched by the void.
    for key in (
        "promo_name_snapshot", "audience_snapshot", "amount_mode_snapshot", "price_snapshot",
        "granted_seconds_snapshot", "station_scope_snapshot", "applies_to_snapshot",
        "starts_at_snapshot", "ends_at_snapshot", "pc_name_snapshot",
        "income_group_id_snapshot", "income_group_name_snapshot",
    ):
        assert redemption_row[key] == redemption_snapshot_before[key], f"{key} must survive the void unchanged"

    # Total-limit slot freed: the same promo (limit=1) is claimable again
    # (a different PC, since PC-PROMO's first session is still active).
    second = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO-2",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
        )
    )
    assert second.session.total_seconds == 6000, "voiding must free the total_redemption_limit slot"


def test_public_void_route_is_idempotent_on_repeat(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "void-idempotent.sqlite3")
    from server.schemas import PaymentCreateRequest, VoidPaymentRequest
    from server.auth import get_current_user, require_role
    from server.database import get_connection

    make_standard_promo(main_module)
    create_users(main_module)
    admin_user = require_role("admin")(get_current_user(sg_session=login(main_module, "admin3c", "admin-pass")))

    response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
        )
    )
    with get_connection() as connection:
        payment_id = connection.execute(
            "SELECT payment_id FROM payments WHERE session_id = ?", (response.session.session_id,)
        ).fetchone()["payment_id"]

    main_module.void_payment(payment_id, VoidPaymentRequest(reason="first void"), admin_user)
    with get_connection() as connection:
        redemption_after_first = dict(
            connection.execute("SELECT * FROM promo_redemptions WHERE payment_id = ?", (payment_id,)).fetchone()
        )

    try:
        main_module.void_payment(payment_id, VoidPaymentRequest(reason="second void"), admin_user)
        raise AssertionError("re-voiding an already-voided payment must be rejected, not silently reprocessed")
    except HTTPException as error:
        assert error.status_code == 409

    with get_connection() as connection:
        redemption_after_second_attempt = dict(
            connection.execute("SELECT * FROM promo_redemptions WHERE payment_id = ?", (payment_id,)).fetchone()
        )
    assert redemption_after_first == redemption_after_second_attempt, (
        "a rejected repeat void must leave the already-voided redemption byte-for-byte unchanged "
        "(voided_at/void_reason not overwritten a second time)"
    )


def test_public_void_route_failed_void_leaves_state_unchanged(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "void-failed-noop.sqlite3")
    from server.schemas import PaymentCreateRequest, VoidPaymentRequest
    from server.auth import get_current_user, require_role
    from server.database import get_connection
    from pydantic import ValidationError

    make_standard_promo(main_module)
    create_users(main_module)
    admin_user = require_role("admin")(get_current_user(sg_session=login(main_module, "admin3c", "admin-pass")))

    response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
        )
    )
    with get_connection() as connection:
        payment_id = connection.execute(
            "SELECT payment_id FROM payments WHERE session_id = ?", (response.session.session_id,)
        ).fetchone()["payment_id"]
        payment_before = dict(connection.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone())
        redemption_before = dict(
            connection.execute("SELECT * FROM promo_redemptions WHERE payment_id = ?", (payment_id,)).fetchone()
        )

    try:
        VoidPaymentRequest(reason="")
        raise AssertionError("an empty reason must fail schema validation before reaching billing_service")
    except ValidationError:
        pass

    # Also exercise billing_service's own defense-in-depth check (a
    # whitespace-only reason that would pass min_length=1 but still fail
    # the service's .strip() check) to prove the failure path leaves both
    # rows untouched.
    try:
        main_module.billing_service.void_payment(payment_id, "   ", admin_user.user_id)
        raise AssertionError("a whitespace-only void reason must be rejected")
    except HTTPException as error:
        assert error.status_code == 400

    with get_connection() as connection:
        payment_after = dict(connection.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone())
        redemption_after = dict(
            connection.execute("SELECT * FROM promo_redemptions WHERE payment_id = ?", (payment_id,)).fetchone()
        )
    assert payment_before == payment_after
    assert redemption_before == redemption_after


# --- 3. Attribution snapshots through real start/add-time paths -----------


def test_start_session_promo_preserves_attribution_snapshot(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "attribution-start.sqlite3")
    from server.schemas import PaymentCreateRequest
    from server.database import get_connection

    make_standard_promo(main_module)
    owner, admin, cashier = create_users(main_module)

    response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
            actor_user_id=cashier.user_id,
        )
    )
    with get_connection() as connection:
        payment_row = dict(
            connection.execute(
                "SELECT * FROM payments WHERE session_id = ?", (response.session.session_id,)
            ).fetchone()
        )
        redemption_row = dict(
            connection.execute(
                "SELECT * FROM promo_redemptions WHERE session_id = ?", (response.session.session_id,)
            ).fetchone()
        )

    assert payment_row["pc_name_snapshot"] == "PC-PROMO"
    assert payment_row["income_group_id_snapshot"] == "tyrone"
    assert payment_row["income_group_name_snapshot"] == "Tyrone"
    assert redemption_row["pc_name_snapshot"] == "PC-PROMO"
    assert redemption_row["income_group_id_snapshot"] == "tyrone"
    assert redemption_row["income_group_name_snapshot"] == "Tyrone"
    assert redemption_row["operator_user_id"] == cashier.user_id


def test_add_time_promo_preserves_attribution_snapshot(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "attribution-add-time.sqlite3")
    from server.schemas import PaymentCreateRequest
    from server.database import get_connection

    make_standard_promo(main_module, applies_to="add_time", granted_seconds=7200)
    owner, admin, cashier = create_users(main_module)

    start_response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("15.00"), cash_received=Decimal("15.00")),
            amount=Decimal("15.00"),
            actor_user_id=cashier.user_id,
        )
    )
    add_response = asyncio.run(
        main_module.session_service.add_prepaid_time(
            start_response.session.session_id,
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
            actor_user_id=admin.user_id,
        )
    )
    with get_connection() as connection:
        add_time_payment = dict(
            connection.execute(
                "SELECT * FROM payments WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                (add_response.session.session_id,),
            ).fetchone()
        )
        redemption_row = dict(
            connection.execute(
                "SELECT * FROM promo_redemptions WHERE payment_id = ?", (add_time_payment["payment_id"],)
            ).fetchone()
        )

    assert add_time_payment["income_group_id_snapshot"] == "tyrone"
    assert add_time_payment["income_group_name_snapshot"] == "Tyrone"
    assert redemption_row["income_group_id_snapshot"] == "tyrone"
    assert redemption_row["operator_user_id"] == admin.user_id


def test_operator_activity_reports_promo_payment_correctly(temp_directory: str) -> None:
    """Existing operator/shift sales behavior (list_operator_activity) must
    see a promo-applied payment exactly like any other payment -- correct
    amount, PC, and operator attribution, unaffected by the promo."""
    main_module = create_env(Path(temp_directory) / "operator-activity.sqlite3")
    from server.schemas import PaymentCreateRequest
    from server.auth import get_current_user, require_role

    make_standard_promo(main_module)
    owner, admin, cashier = create_users(main_module)
    owner_user = require_role("owner")(get_current_user(sg_session=login(main_module, "owner3c", "owner-pass")))

    from server.schemas import ShiftOpenRequest

    main_module.open_shift(ShiftOpenRequest(opening_cash=Decimal("0.00")), owner_user)

    response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
            actor_user_id=cashier.user_id,
        )
    )
    activity = main_module.list_operator_activity("all", None, "payment", 50, owner_user)
    # Both the session-event log entry ("Collected prepaid start payment")
    # and the raw payments-table entry ("Collected payment") surface here
    # by design -- assert on their shared, promo-unaffected content.
    matching = [item for item in activity if item.pc_name == "PC-PROMO"]
    assert len(matching) == 2
    assert all(item.amount == Decimal("20.00") for item in matching)
    assert all(item.payment_method == "cash" for item in matching)
    assert all(item.operator_id == cashier.user_id for item in matching)


# --- 4. SessionCommandResponse optional fields on idempotent retry --------


def test_dashboard_callers_handle_optional_command_on_retry(temp_directory: str) -> None:
    main_module = create_env(Path(temp_directory) / "optional-command.sqlite3")
    from server.schemas import PaymentCreateRequest

    make_standard_promo(main_module)
    key = "stage3c-idempotency-key"

    first = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00"), idempotency_key=key),
            amount=Decimal("20.00"),
        )
    )
    second = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-PROMO",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00"), idempotency_key=key),
            amount=Decimal("20.00"),
        )
    )
    assert second.command is None
    assert second.acknowledgements == []

    # The only response route consumers of SessionCommandResponse are the
    # main.py route handlers, which just return the Pydantic model as-is
    # (FastAPI serializes it via response_model) -- there is no downstream
    # Python code that dereferences .command unconditionally. Prove that at
    # the schema level: model_dump()/JSON-serializing a None command must
    # not raise (this is what any dashboard fetch() JSON.parse would see).
    dumped = second.model_dump(mode="json")
    assert dumped["command"] is None
    assert dumped["acknowledgements"] == []

    import json

    serialized = second.model_dump_json()
    reparsed = json.loads(serialized)
    assert reparsed["command"] is None


def main() -> None:
    tests = [
        test_quote_route_rejects_unauthenticated_request,
        test_quote_route_accepts_authorized_cashier_and_owner_operator,
        test_quote_route_rejects_deactivated_users_session,
        test_quote_route_performs_no_database_writes,
        test_public_void_route_voids_payment_and_redemption_atomically,
        test_public_void_route_is_idempotent_on_repeat,
        test_public_void_route_failed_void_leaves_state_unchanged,
        test_start_session_promo_preserves_attribution_snapshot,
        test_add_time_promo_preserves_attribution_snapshot,
        test_operator_activity_reports_promo_payment_correctly,
        test_dashboard_callers_handle_optional_command_on_retry,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Promo Maker Stage 3C (auth, void, attribution) smoke tests passed.")


if __name__ == "__main__":
    main()
