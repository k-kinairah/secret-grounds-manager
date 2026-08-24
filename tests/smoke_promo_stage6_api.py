"""Stage 6 smoke tests: the owner-facing HTTP/API surface for promo
management (GET/POST/PATCH /promos, state transitions, audit, redemption
history). Route/integration tests -- exercised through the real
server.main route functions plus the real auth dependency functions
(get_current_user/require_owner/require_role), not mocks, matching the
established convention (see tests/smoke_promo_stage3c.py).

Does NOT re-test promo eligibility/selection, atomic claim transactions,
redemption-limit enforcement, idempotency, refund, or attribution --
those are already covered by tests/smoke_promos.py,
smoke_promo_eligibility.py, smoke_promo_session_integration.py,
smoke_promo_route_integration.py, smoke_promo_stage3c.py,
smoke_promo_member_topup.py, and smoke_promo_new_member.py, and Stage 6
deliberately does not touch any of that logic.
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
    from server.schemas import UserCreateRequest
    from server.user_repository import UserRepository

    user_repository = UserRepository()
    owner = user_repository.create_user(
        UserCreateRequest(username="owner6", display_name="Owner 6", password="password1", role="owner")
    )
    admin = user_repository.create_user(
        UserCreateRequest(username="admin6", display_name="Admin 6", password="password2", role="admin")
    )
    cashier = user_repository.create_user(
        UserCreateRequest(username="cashier6", display_name="Cashier 6", password="password3", role="cashier")
    )
    return main_module, owner, admin, cashier


def login(main_module, username: str, password: str) -> str:
    from fastapi import Response
    from server.auth import authenticate_login, auth_session_repository, set_auth_cookie
    from server.schemas import LoginRequest

    user = authenticate_login(LoginRequest(username=username, password=password))
    token, _ = auth_session_repository.create_session(user.user_id, 3600)
    set_auth_cookie(Response(), token)
    return token


def resolve_owner_user(main_module, username: str, password: str):
    """Drives the exact functions FastAPI's Depends() graph calls for an
    owner-only route: get_current_user(cookie) -> require_owner(user)."""
    from server.auth import get_current_user, require_owner

    token = login(main_module, username, password)
    return require_owner(get_current_user(sg_session=token))


def standard_promo_create_fields(**overrides):
    from decimal import Decimal as D

    now = datetime.now(timezone.utc)
    fields = {
        "name": "Stage 6 API Promo",
        "audience": "standard",
        "amount_mode": "exact",
        "price": D("20.00"),
        "granted_seconds": 6000,
        "station_scope": "standard",
        "applies_to": "start_session",
        "starts_at": now - timedelta(hours=1),
        "ends_at": now + timedelta(days=7),
    }
    fields.update(overrides)
    return fields


# --- 1/2/3. list / get / create --------------------------------------------


def test_owner_can_create_and_list_and_get_promo(temp_directory: str) -> None:
    from server.schemas import PromoCreate

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "list-get-create.sqlite3")
    owner_user = resolve_owner_user(main_module, "owner6", "password1")

    created = main_module.create_promo(PromoCreate(**standard_promo_create_fields()), owner_user)
    assert created.status == "Draft"
    assert created.created_by_user_id == owner.user_id

    listed = main_module.list_promos(None, owner_user)
    assert len(listed) == 1
    assert listed[0].promo_id == created.promo_id

    fetched = main_module.get_promo(created.promo_id, owner_user)
    assert fetched.name == "Stage 6 API Promo"
    assert fetched.promo_id == created.promo_id


def test_create_promo_never_trusts_client_supplied_server_fields(temp_directory: str) -> None:
    """PromoCreate has no created_by_user_id/status/owner_state-override
    field at all -- created_by_user_id always comes from the authenticated
    actor, owner_state is always 'draft' at creation, status is always
    derived. Confirms the route passes get_actor_user_id(current_user),
    never anything from the request body."""
    from server.schemas import PromoCreate

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "no-trust-client.sqlite3")
    owner_user = resolve_owner_user(main_module, "owner6", "password1")

    assert "created_by_user_id" not in PromoCreate.model_fields
    assert "owner_state" not in PromoCreate.model_fields
    assert "status" not in PromoCreate.model_fields

    created = main_module.create_promo(PromoCreate(**standard_promo_create_fields()), owner_user)
    assert created.owner_state == "draft"
    assert created.created_by_user_id == owner.user_id


def test_list_promos_supports_owner_states_filter(temp_directory: str) -> None:
    from server.schemas import PromoCreate, PromoStateTransition

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "owner-states-filter.sqlite3")
    owner_user = resolve_owner_user(main_module, "owner6", "password1")

    draft = main_module.create_promo(PromoCreate(**standard_promo_create_fields(name="Draft One")), owner_user)
    to_enable = main_module.create_promo(PromoCreate(**standard_promo_create_fields(name="Will Enable")), owner_user)
    main_module.transition_promo_state(to_enable.promo_id, PromoStateTransition(owner_state="enabled"), owner_user)

    only_drafts = main_module.list_promos(["draft"], owner_user)
    assert [p.promo_id for p in only_drafts] == [draft.promo_id]

    only_enabled = main_module.list_promos(["enabled"], owner_user)
    assert [p.promo_id for p in only_enabled] == [to_enable.promo_id]

    both = main_module.list_promos(["draft", "enabled"], owner_user)
    assert {p.promo_id for p in both} == {draft.promo_id, to_enable.promo_id}


# --- 4. update ----------------------------------------------------------


def test_owner_can_update_promo(temp_directory: str) -> None:
    from server.schemas import PromoCreate, PromoUpdate

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "update.sqlite3")
    owner_user = resolve_owner_user(main_module, "owner6", "password1")

    created = main_module.create_promo(PromoCreate(**standard_promo_create_fields()), owner_user)
    update_fields = standard_promo_create_fields(name="Renamed Promo", price=Decimal("25.00"), granted_seconds=7200)
    updated = main_module.update_promo(created.promo_id, PromoUpdate(**update_fields), owner_user)

    assert updated.name == "Renamed Promo"
    assert updated.price == Decimal("25.00")
    assert updated.granted_seconds == 7200

    refetched = main_module.get_promo(created.promo_id, owner_user)
    assert refetched.name == "Renamed Promo"


# --- 5/6. state transitions -----------------------------------------------


def test_owner_can_perform_valid_state_transitions(temp_directory: str) -> None:
    from server.schemas import PromoCreate, PromoStateTransition

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "valid-transitions.sqlite3")
    owner_user = resolve_owner_user(main_module, "owner6", "password1")

    created = main_module.create_promo(PromoCreate(**standard_promo_create_fields()), owner_user)
    assert created.status == "Draft"

    enabled = main_module.transition_promo_state(created.promo_id, PromoStateTransition(owner_state="enabled"), owner_user)
    assert enabled.status == "Active"

    disabled = main_module.transition_promo_state(created.promo_id, PromoStateTransition(owner_state="disabled"), owner_user)
    assert disabled.status == "Disabled"

    ended = main_module.transition_promo_state(disabled.promo_id, PromoStateTransition(owner_state="ended"), owner_user)
    assert ended.status == "Ended"

    future_ends_at = datetime.now(timezone.utc) + timedelta(days=3)
    reactivated = main_module.transition_promo_state(
        ended.promo_id, PromoStateTransition(owner_state="enabled", ends_at=future_ends_at), owner_user
    )
    assert reactivated.status == "Active"
    assert reactivated.ends_at == future_ends_at


def test_invalid_state_transitions_are_rejected(temp_directory: str) -> None:
    from server.schemas import PromoCreate, PromoStateTransition

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "invalid-transitions.sqlite3")
    owner_user = resolve_owner_user(main_module, "owner6", "password1")

    created = main_module.create_promo(PromoCreate(**standard_promo_create_fields()), owner_user)

    # ends_at supplied on a non-reactivation transition is rejected.
    try:
        main_module.transition_promo_state(
            created.promo_id,
            PromoStateTransition(owner_state="enabled", ends_at=datetime.now(timezone.utc) + timedelta(days=1)),
            owner_user,
        )
        raise AssertionError("ends_at outside a reactivation must be rejected")
    except HTTPException as error:
        assert error.status_code == 400

    main_module.transition_promo_state(created.promo_id, PromoStateTransition(owner_state="enabled"), owner_user)
    main_module.transition_promo_state(created.promo_id, PromoStateTransition(owner_state="ended"), owner_user)

    # Reactivating without a new ends_at is rejected.
    try:
        main_module.transition_promo_state(created.promo_id, PromoStateTransition(owner_state="enabled"), owner_user)
        raise AssertionError("reactivation without ends_at must be rejected")
    except HTTPException as error:
        assert error.status_code == 400

    # Reactivating with a past ends_at is rejected.
    try:
        main_module.transition_promo_state(
            created.promo_id,
            PromoStateTransition(owner_state="enabled", ends_at=datetime.now(timezone.utc) - timedelta(days=1)),
            owner_user,
        )
        raise AssertionError("reactivation with a past ends_at must be rejected")
    except HTTPException as error:
        assert error.status_code == 409


# --- 7. authorization -------------------------------------------------------


def test_non_owner_mutations_are_rejected(temp_directory: str) -> None:
    from server.auth import get_current_user, require_owner, require_role
    from server.schemas import PromoCreate, PromoStateTransition, PromoUpdate

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "non-owner-rejected.sqlite3")
    owner_user = resolve_owner_user(main_module, "owner6", "password1")
    created = main_module.create_promo(PromoCreate(**standard_promo_create_fields()), owner_user)

    admin_token = login(main_module, "admin6", "password2")
    admin_record = get_current_user(sg_session=admin_token)
    cashier_token = login(main_module, "cashier6", "password3")
    cashier_record = get_current_user(sg_session=cashier_token)

    for non_owner in (admin_record, cashier_record):
        try:
            require_owner(non_owner)
            raise AssertionError(f"role {non_owner.role} must not pass require_owner")
        except HTTPException as error:
            assert error.status_code == 403

    # Cashier passes the cashier-minimum quote routes (unaffected by
    # Stage 6) but must not pass require_owner for management routes --
    # confirms the two authorization tiers are genuinely different, not
    # the same check applied inconsistently.
    assert require_role("cashier")(cashier_record).user_id == cashier.user_id
    try:
        require_owner(cashier_record)
        raise AssertionError("cashier must not pass require_owner")
    except HTTPException as error:
        assert error.status_code == 403

    # No promo/audit/redemption row was created by any of the rejected attempts.
    assert len(main_module.list_promos(None, owner_user)) == 1
    assert len(main_module.list_promo_audit_events(created.promo_id, owner_user)) == 1


# --- 8. audit ---------------------------------------------------------------


def test_audit_events_can_be_retrieved(temp_directory: str) -> None:
    from server.schemas import PromoCreate, PromoStateTransition, PromoUpdate

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "audit.sqlite3")
    owner_user = resolve_owner_user(main_module, "owner6", "password1")

    created = main_module.create_promo(PromoCreate(**standard_promo_create_fields()), owner_user)
    main_module.update_promo(created.promo_id, PromoUpdate(**standard_promo_create_fields(name="Edited")), owner_user)
    main_module.transition_promo_state(created.promo_id, PromoStateTransition(owner_state="enabled"), owner_user)

    events = main_module.list_promo_audit_events(created.promo_id, owner_user)
    assert [e.action for e in events] == ["created", "updated", "enabled"]
    assert all(e.performed_by_user_id == owner.user_id for e in events)
    assert events[0].old_values is None
    assert events[1].new_values["name"] == "Edited"


# --- 9. redemption history ---------------------------------------------------


def test_redemption_history_can_be_retrieved_and_is_read_only(temp_directory: str) -> None:
    from server.schemas import PaymentCreateRequest, PromoCreate, PromoStateTransition, PCRegistration

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "redemption-history.sqlite3")
    owner_user = resolve_owner_user(main_module, "owner6", "password1")

    created = main_module.create_promo(PromoCreate(**standard_promo_create_fields()), owner_user)
    main_module.transition_promo_state(created.promo_id, PromoStateTransition(owner_state="enabled"), owner_user)

    # Actually claim a redemption through the real, unmodified atomic path
    # (Stage 3), so this test also covers requirement 11 (existing
    # quote/claim behavior unchanged by Stage 6).
    main_module.pc_registry.clear()
    main_module.register_pc(PCRegistration(pc_name="PC-STAGE6", ip_address="127.0.0.1"))
    main_module.session_service.require_available_client = lambda pc_name: main_module.pc_registry[pc_name]

    async def acknowledge_command(*_: object) -> list[object]:
        return []

    main_module.session_service.send_required_command = acknowledge_command

    response = asyncio.run(
        main_module.session_service.start_prepaid_session(
            "PC-STAGE6",
            PaymentCreateRequest(payment_method="cash", amount=Decimal("20.00"), cash_received=Decimal("20.00")),
            amount=Decimal("20.00"),
        )
    )
    assert response.session.total_seconds == 6000  # the promo, not the normal 4800s P20 rate

    redemptions = main_module.list_promo_redemptions(created.promo_id, owner_user)
    assert len(redemptions) == 1
    assert redemptions[0].session_id == response.session.session_id
    assert redemptions[0].granted_seconds_snapshot == 6000
    assert redemptions[0].voided_at is None

    # Read-only: calling it again returns the identical row, nothing new written.
    redemptions_again = main_module.list_promo_redemptions(created.promo_id, owner_user)
    assert redemptions_again == redemptions


# --- 10. 404 behavior ---------------------------------------------------


def test_missing_promo_ids_return_404(temp_directory: str) -> None:
    from server.schemas import PromoStateTransition, PromoUpdate

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "missing-404.sqlite3")
    owner_user = resolve_owner_user(main_module, "owner6", "password1")
    missing_id = "does-not-exist"

    for call in (
        lambda: main_module.get_promo(missing_id, owner_user),
        lambda: main_module.list_promo_audit_events(missing_id, owner_user),
        lambda: main_module.list_promo_redemptions(missing_id, owner_user),
        lambda: main_module.update_promo(missing_id, PromoUpdate(**standard_promo_create_fields()), owner_user),
        lambda: main_module.transition_promo_state(
            missing_id, PromoStateTransition(owner_state="enabled"), owner_user
        ),
    ):
        try:
            call()
            raise AssertionError("expected 404 for a missing promo_id")
        except HTTPException as error:
            assert error.status_code == 404, f"expected 404, got {error.status_code}"


# --- 11. existing quote/claim behavior unchanged ---------------------------


def test_existing_quote_and_claim_behavior_unchanged(temp_directory: str) -> None:
    """Stage 6 only adds routes/reads; the underlying quote/eligibility/
    claim modules must behave identically to before this stage."""
    from server.schemas import PromoCreate, PromoQuoteRequest, PromoStateTransition, PCRegistration

    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "quote-unchanged.sqlite3")
    owner_user = resolve_owner_user(main_module, "owner6", "password1")

    created = main_module.create_promo(PromoCreate(**standard_promo_create_fields()), owner_user)
    main_module.transition_promo_state(created.promo_id, PromoStateTransition(owner_state="enabled"), owner_user)

    main_module.pc_registry.clear()
    main_module.register_pc(PCRegistration(pc_name="PC-STAGE6B", ip_address="127.0.0.1"))

    quote = main_module.quote_standard_or_vip_promo(
        PromoQuoteRequest(pc_name="PC-STAGE6B", action="start_session", amount=Decimal("20.00")), owner_user
    )
    assert quote.source_type == "promo"
    assert quote.promo_id == created.promo_id
    assert quote.final_seconds == 6000
    assert quote.base_seconds == 4800
    assert quote.benefit_seconds == 1200

    no_promo_quote = main_module.quote_standard_or_vip_promo(
        PromoQuoteRequest(pc_name="PC-STAGE6B", action="start_session", amount=Decimal("15.00")), owner_user
    )
    assert no_promo_quote.source_type == "normal_rate"
    assert no_promo_quote.promo_id is None


def main() -> None:
    tests = [
        test_owner_can_create_and_list_and_get_promo,
        test_create_promo_never_trusts_client_supplied_server_fields,
        test_list_promos_supports_owner_states_filter,
        test_owner_can_update_promo,
        test_owner_can_perform_valid_state_transitions,
        test_invalid_state_transitions_are_rejected,
        test_non_owner_mutations_are_rejected,
        test_audit_events_can_be_retrieved,
        test_redemption_history_can_be_retrieved_and_is_read_only,
        test_missing_promo_ids_return_404,
        test_existing_quote_and_claim_behavior_unchanged,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Promo Maker Stage 6 (owner-facing API) smoke tests passed.")


if __name__ == "__main__":
    main()
