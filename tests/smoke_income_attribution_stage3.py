"""Stage 3 tests: pending-attribution resolution + income-group config.

Stage 3A: member-session pending-purchase sweep (balance-type eligible,
idempotent, self-healing, never touches already-assigned rows).
Stage 3B/3C: PC income-group configuration API and the resolution of
known-PC pending payments when a PC's group becomes configured.
"""

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

PC_NAMES = [("PC-11", False), ("PC-13", False), ("PC-15", True), ("PC-99-CUSTOM", False)]


# --- Stage 3A: member-session sweep -----------------------------------


def create_member_sweep_service(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    from server.billing_service import BillingService
    from server.member_repository import MemberRepository
    from server.payment_repository import PaymentRepository
    from server.pc_layout_repository import IncomeGroupRepository, PCLayoutRepository
    from server.prepared_session_repository import PreparedSessionRepository
    from server.rate_repository import RateRepository
    from server.schemas import CommandAcknowledgement, PCRecord
    from server.session_repository import SessionRepository
    from server.session_service import SessionService
    from server.shift_repository import ShiftRepository

    pc_layout_repository = PCLayoutRepository()
    income_group_repository = IncomeGroupRepository()
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
        payment_repository=payment_repository,
        income_group_repository=income_group_repository,
    )

    async def acknowledge_command(*_: object) -> list[object]:
        return [
            CommandAcknowledgement(command_id="test", status="received"),
            CommandAcknowledgement(command_id="test", status="completed"),
        ]

    service.send_required_command = acknowledge_command  # type: ignore[method-assign]
    return {
        "service": service,
        "session_repository": session_repository,
        "member_repository": member_repository,
        "payment_repository": payment_repository,
        "pc_layout_repository": pc_layout_repository,
        "income_group_repository": income_group_repository,
    }


def create_test_member(member_repository, username: str):
    from server.schemas import MemberCreateRequest

    return member_repository.create_member(
        MemberCreateRequest(username=username, display_name=username, pin="1234", confirm_pin="1234"),
        None,
    )


def make_purchase_request(product_id: str, idempotency_key: str | None = None):
    from server.schemas import MemberPurchaseRequest

    return MemberPurchaseRequest(
        product_id=product_id,
        payment_method="cash",
        cash_received="1000.00",
        idempotency_key=idempotency_key,
    )


def system_user_for_purchases(member_repository):
    """purchase() needs a UserRecord actor; reuse the member repository's
    own dependency-free path by fabricating a minimal cashier record."""
    from server.schemas import UserCreateRequest
    from server.user_repository import UserRepository

    user_repository = UserRepository()
    public = user_repository.create_user(
        UserCreateRequest(username="sweep-cashier", display_name="Sweep Cashier", password="cashier-pass", role="cashier")
    )
    actor = user_repository.get_user(public.user_id)
    assert actor is not None
    return actor


def test_pending_standard_purchase_assigned_by_standard_session(temp_directory: str) -> None:
    repos = create_member_sweep_service(Path(temp_directory) / "sweep-standard.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    service = repos["service"]
    actor = system_user_for_purchases(member_repository)
    member = create_test_member(member_repository, "sweep.standard")

    purchase = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    assert purchase.payment.attribution_status == "pending"

    started = asyncio.run(service.start_member_session("PC-11", "sweep.standard", None, "1234"))

    resolved = payment_repository.get_payment(purchase.payment.payment_id)
    assert resolved is not None
    assert resolved.attribution_status == "assigned"
    assert resolved.pc_name_snapshot == "PC-11"
    assert resolved.income_group_id_snapshot == "gene"
    assert resolved.attribution_session_id == started.session.session_id
    assert resolved.attribution_assigned_at is not None


def test_pending_vip_purchase_remains_pending_after_standard_session(temp_directory: str) -> None:
    repos = create_member_sweep_service(Path(temp_directory) / "sweep-vip-on-standard.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    service = repos["service"]
    actor = system_user_for_purchases(member_repository)
    member = create_test_member(member_repository, "sweep.vip.standard")

    purchase = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)
    # Fund the Standard session itself via a plain balance adjustment (not
    # a purchase) so this test isolates the one thing it's checking: that
    # the pending VIP purchase stays pending, unaffected by an unrelated
    # Standard balance being available to actually start the session.
    member_repository.apply_balance_delta(member.member_id, "manual_adjustment", 3600, actor.user_id, balance_type="standard")

    asyncio.run(service.start_member_session("PC-11", "sweep.vip.standard", None, "1234"))

    resolved = payment_repository.get_payment(purchase.payment.payment_id)
    assert resolved is not None
    assert resolved.attribution_status == "pending"
    assert resolved.pc_name_snapshot is None
    assert resolved.income_group_id_snapshot is None


def test_standard_assigned_vip_pending_same_standard_session(temp_directory: str) -> None:
    repos = create_member_sweep_service(Path(temp_directory) / "sweep-mixed-standard.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    service = repos["service"]
    actor = system_user_for_purchases(member_repository)
    member = create_test_member(member_repository, "sweep.mixed.standard")

    standard_purchase = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    vip_purchase = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)

    asyncio.run(service.start_member_session("PC-11", "sweep.mixed.standard", None, "1234"))

    resolved_standard = payment_repository.get_payment(standard_purchase.payment.payment_id)
    resolved_vip = payment_repository.get_payment(vip_purchase.payment.payment_id)
    assert resolved_standard.attribution_status == "assigned"
    assert resolved_standard.income_group_id_snapshot == "gene"
    assert resolved_vip.attribution_status == "pending"
    assert resolved_vip.pc_name_snapshot is None


def test_both_assigned_by_vip_session(temp_directory: str) -> None:
    repos = create_member_sweep_service(Path(temp_directory) / "sweep-mixed-vip.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    service = repos["service"]
    actor = system_user_for_purchases(member_repository)
    member = create_test_member(member_repository, "sweep.mixed.vip")

    standard_purchase = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    vip_purchase = member_repository.purchase(member.member_id, make_purchase_request("vip-promo-250"), actor)

    started = asyncio.run(service.start_member_session("PC-15", "sweep.mixed.vip", None, "1234"))

    resolved_standard = payment_repository.get_payment(standard_purchase.payment.payment_id)
    resolved_vip = payment_repository.get_payment(vip_purchase.payment.payment_id)
    for resolved in (resolved_standard, resolved_vip):
        assert resolved.attribution_status == "assigned"
        assert resolved.pc_name_snapshot == "PC-15"
        assert resolved.income_group_id_snapshot == "tyrone"
        assert resolved.attribution_session_id == started.session.session_id


def test_multiple_standard_purchases_all_assign_together(temp_directory: str) -> None:
    repos = create_member_sweep_service(Path(temp_directory) / "sweep-multiple.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    service = repos["service"]
    actor = system_user_for_purchases(member_repository)
    member = create_test_member(member_repository, "sweep.multiple")

    first = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    second = member_repository.purchase(member.member_id, make_purchase_request("member-topup-100"), actor)

    started = asyncio.run(service.start_member_session("PC-13", "sweep.multiple", None, "1234"))

    for purchase in (first, second):
        resolved = payment_repository.get_payment(purchase.payment.payment_id)
        assert resolved.attribution_status == "assigned"
        assert resolved.pc_name_snapshot == "PC-13"
        assert resolved.income_group_id_snapshot == "mama-lala"
        assert resolved.attribution_session_id == started.session.session_id


def test_account_creation_pending_assigns_on_first_eligible_session(temp_directory: str) -> None:
    repos = create_member_sweep_service(Path(temp_directory) / "sweep-account-creation.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    service = repos["service"]
    actor = system_user_for_purchases(member_repository)
    member = create_test_member(member_repository, "sweep.new.account")

    creation = member_repository.purchase(member.member_id, make_purchase_request("membership-fee"), actor)
    assert creation.payment.attribution_status == "pending"

    asyncio.run(service.start_member_session("PC-11", "sweep.new.account", None, "1234"))

    resolved = payment_repository.get_payment(creation.payment.payment_id)
    assert resolved.attribution_status == "assigned"
    assert resolved.income_group_id_snapshot == "gene"


def test_sweep_is_idempotent(temp_directory: str) -> None:
    repos = create_member_sweep_service(Path(temp_directory) / "sweep-idempotent.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    service = repos["service"]
    actor = system_user_for_purchases(member_repository)
    member = create_test_member(member_repository, "sweep.idempotent")

    purchase = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    started = asyncio.run(service.start_member_session("PC-11", "sweep.idempotent", None, "1234"))

    first_pass = payment_repository.get_payment(purchase.payment.payment_id)
    assert first_pass.attribution_status == "assigned"

    updated_count = service.sweep_pending_purchase_attribution_for_new_session(
        member.member_id, "PC-11", False, started.session.session_id, datetime.now(timezone.utc)
    )
    assert updated_count == 0, "Re-running the sweep must be a no-op once a row is assigned"

    second_pass = payment_repository.get_payment(purchase.payment.payment_id)
    assert second_pass.attribution_assigned_at == first_pass.attribution_assigned_at
    assert second_pass.attribution_session_id == first_pass.attribution_session_id


def test_reconnect_retry_does_not_reassign(temp_directory: str) -> None:
    repos = create_member_sweep_service(Path(temp_directory) / "sweep-reconnect.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    service = repos["service"]
    actor = system_user_for_purchases(member_repository)
    member = create_test_member(member_repository, "sweep.reconnect")

    purchase = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    started = asyncio.run(service.start_member_session("PC-11", "sweep.reconnect", None, "1234"))
    first_pass = payment_repository.get_payment(purchase.payment.payment_id)
    assert first_pass.pc_name_snapshot == "PC-11"

    # Simulate a retried/duplicate sweep trigger (e.g. a reconnect) quoting
    # a DIFFERENT session id -- must never move an already-assigned row.
    updated_count = service.sweep_pending_purchase_attribution_for_new_session(
        member.member_id, "PC-13", False, "retry-session-id", datetime.now(timezone.utc)
    )
    assert updated_count == 0

    second_pass = payment_repository.get_payment(purchase.payment.payment_id)
    assert second_pass.pc_name_snapshot == "PC-11"
    assert second_pass.income_group_id_snapshot == "gene"
    assert second_pass.attribution_session_id == started.session.session_id


def test_voided_pending_purchase_ignored_by_sweep(temp_directory: str) -> None:
    from server.schemas import MemberPurchaseRefundRequest

    repos = create_member_sweep_service(Path(temp_directory) / "sweep-voided.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    service = repos["service"]
    actor = system_user_for_purchases(member_repository)
    member = create_test_member(member_repository, "sweep.voided")

    purchase = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    member_repository.refund_purchase(
        purchase.payment.payment_id, MemberPurchaseRefundRequest(reason="Customer cancellation"), actor
    )
    # The refund correctly reverses the balance it funded; give the member
    # a separate, unrelated balance to actually start a session with, so
    # this test isolates what it's checking: the voided row is ignored.
    member_repository.apply_balance_delta(member.member_id, "manual_adjustment", 3600, actor.user_id, balance_type="standard")

    asyncio.run(service.start_member_session("PC-11", "sweep.voided", None, "1234"))

    resolved = payment_repository.get_payment(purchase.payment.payment_id)
    assert resolved.status == "voided"
    assert resolved.attribution_status == "pending"
    assert resolved.pc_name_snapshot is None


def test_assigned_row_never_moves_on_later_session(temp_directory: str) -> None:
    repos = create_member_sweep_service(Path(temp_directory) / "sweep-no-move.sqlite3")
    member_repository = repos["member_repository"]
    payment_repository = repos["payment_repository"]
    session_repository = repos["session_repository"]
    service = repos["service"]
    actor = system_user_for_purchases(member_repository)
    member = create_test_member(member_repository, "sweep.no.move")
    member_repository.apply_balance_delta(member.member_id, "manual_adjustment", 7200, actor.user_id, balance_type="standard")

    first_session = asyncio.run(service.start_member_session("PC-11", "sweep.no.move", 3600, "1234"))
    purchase = member_repository.purchase(member.member_id, make_purchase_request("member-promo"), actor)
    assert purchase.payment.attribution_status == "assigned"
    assert purchase.payment.pc_name_snapshot == "PC-11"

    asyncio.run(service.end_session(first_session.session.session_id))

    second_session = asyncio.run(service.start_member_session("PC-13", "sweep.no.move", 3600, "1234"))

    unchanged = payment_repository.get_payment(purchase.payment.payment_id)
    assert unchanged.pc_name_snapshot == "PC-11"
    assert unchanged.income_group_id_snapshot == "gene"
    assert unchanged.attribution_session_id == first_session.session.session_id
    assert unchanged.attribution_session_id != second_session.session.session_id


# --- Stage 3B/3C: PC income-group configuration ------------------------


def create_layout_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module

    main_module.pc_registry.clear()

    from server.schemas import UserCreateRequest

    admin_public = main_module.user_repository.create_user(
        UserCreateRequest(username="layout-admin", display_name="Layout Admin", password="admin-pass", role="admin")
    )
    cashier_public = main_module.user_repository.create_user(
        UserCreateRequest(username="layout-cashier", display_name="Layout Cashier", password="cashier-pass", role="cashier")
    )
    admin = main_module.user_repository.get_user(admin_public.user_id)
    cashier = main_module.user_repository.get_user(cashier_public.user_id)
    assert admin is not None and cashier is not None
    return main_module, admin, cashier


def create_unconfigured_pc(main_module, pc_name: str) -> None:
    from server.schemas import PCLayoutUpdate

    main_module.pc_layout_repository.upsert_layout(
        pc_name,
        PCLayoutUpdate(display_name=pc_name, area_label="UNCONFIGURED", layout_order=999, is_vip=False, is_enabled=True),
    )


def create_guest_session_and_pay(main_module, session_id: str, pc_name: str, amount: Decimal, actor):
    from server.schemas import PaymentCreateRequest, SessionRecord

    now = datetime.now(timezone.utc)
    main_module.session_repository.create_session(
        SessionRecord(
            session_id=session_id,
            pc_name=pc_name,
            status="active",
            started_at=now,
            scheduled_end_at=now,
            original_seconds=3600,
            total_seconds=3600,
            created_at=now,
            base_amount=amount,
            total_amount=amount,
            session_mode="prepaid",
        )
    )
    billing = main_module.billing_service.record_payment(
        session_id,
        PaymentCreateRequest(payment_method="cash", amount=amount, cash_received=amount),
        actor_user_id=actor.user_id,
    )
    return billing.payments[0]


def test_four_income_groups_list_correctly(temp_directory: str) -> None:
    main_module, admin, _ = create_layout_env(Path(temp_directory) / "layout-groups.sqlite3")
    groups = main_module.list_income_groups(admin)
    assert {g.income_group_id: g.name for g in groups} == {
        "united": "United",
        "gene": "Gene",
        "mama-lala": "Mama/Lala",
        "tyrone": "Tyrone",
    }


def test_pc_layout_update_persists_income_group_id(temp_directory: str) -> None:
    from server.schemas import PCLayoutUpdate

    main_module, admin, _ = create_layout_env(Path(temp_directory) / "layout-persist.sqlite3")
    updated = main_module.update_pc_layout(
        "PC-11",
        PCLayoutUpdate(display_name="PC 11", area_label="ROGENE", layout_order=110, is_vip=False, is_enabled=True, income_group_id="united"),
        admin,
    )
    assert updated.income_group_id == "united"
    reloaded = main_module.pc_layout_repository.get_layout("PC-11")
    assert reloaded.income_group_id == "united"


def test_is_vip_unaffected_by_income_group_update(temp_directory: str) -> None:
    from server.schemas import PCLayoutUpdate

    main_module, admin, _ = create_layout_env(Path(temp_directory) / "layout-vip-unaffected.sqlite3")
    updated = main_module.update_pc_layout(
        "PC-15",
        PCLayoutUpdate(display_name="PC 15", area_label="VIP", layout_order=150, is_vip=True, is_enabled=True, income_group_id="tyrone"),
        admin,
    )
    assert updated.is_vip is True
    assert updated.income_group_id == "tyrone"


def test_unknown_income_group_rejected(temp_directory: str) -> None:
    from server.schemas import PCLayoutUpdate

    main_module, admin, _ = create_layout_env(Path(temp_directory) / "layout-unknown-group.sqlite3")
    try:
        main_module.update_pc_layout(
            "PC-11",
            PCLayoutUpdate(display_name="PC 11", area_label="ROGENE", layout_order=110, is_vip=False, is_enabled=True, income_group_id="does-not-exist"),
            admin,
        )
    except HTTPException as error:
        assert error.status_code == 400
    else:
        raise AssertionError("An unknown income_group_id must be rejected")


def test_admin_edited_assignment_survives_restart(temp_directory: str) -> None:
    from server.schemas import PCLayoutUpdate

    database_path = Path(temp_directory) / "layout-restart.sqlite3"
    main_module, admin, _ = create_layout_env(database_path)
    main_module.update_pc_layout(
        "PC-01",
        PCLayoutUpdate(display_name="PC 1", area_label="SECRET GROUND SHOP", layout_order=10, is_vip=False, is_enabled=True, income_group_id="tyrone"),
        admin,
    )

    from server.database import initialize_database

    initialize_database()  # simulates a backend restart's migration pass

    reloaded = main_module.pc_layout_repository.get_layout("PC-01")
    assert reloaded.income_group_id == "tyrone", "restart/migration must never reset an admin-chosen assignment"


def test_unconfigured_pc_guest_payment_remains_pending(temp_directory: str) -> None:
    main_module, _, cashier = create_layout_env(Path(temp_directory) / "config-pending-guest.sqlite3")
    create_unconfigured_pc(main_module, "PC-17")

    payment = create_guest_session_and_pay(main_module, "session-pc17", "PC-17", Decimal("10.00"), cashier)
    assert payment.attribution_status == "pending"
    assert payment.pc_name_snapshot == "PC-17"
    assert payment.income_group_id_snapshot is None


def test_assign_pc_group_resolves_pending_guest_payment(temp_directory: str) -> None:
    from server.schemas import PCLayoutUpdate

    main_module, admin, cashier = create_layout_env(Path(temp_directory) / "config-resolve-guest.sqlite3")
    create_unconfigured_pc(main_module, "PC-17")
    payment = create_guest_session_and_pay(main_module, "session-pc17b", "PC-17", Decimal("10.00"), cashier)
    assert payment.attribution_status == "pending"

    main_module.update_pc_layout(
        "PC-17",
        PCLayoutUpdate(display_name="PC 17", area_label="UNCONFIGURED", layout_order=999, is_vip=False, is_enabled=True, income_group_id="united"),
        admin,
    )

    resolved = main_module.payment_repository.get_payment(payment.payment_id)
    assert resolved.attribution_status == "assigned"
    assert resolved.pc_name_snapshot == "PC-17"
    assert resolved.income_group_id_snapshot == "united"
    assert resolved.income_group_name_snapshot == "United"
    assert resolved.attribution_session_id is None, "no session start caused this assignment"
    assert resolved.attribution_assigned_at is not None


def test_pending_member_purchase_with_known_pc_resolves_on_configuration(temp_directory: str) -> None:
    from server.schemas import MemberCreateRequest, MemberPurchaseRequest, PCLayoutUpdate, SessionRecord

    main_module, admin, cashier = create_layout_env(Path(temp_directory) / "config-resolve-member.sqlite3")
    create_unconfigured_pc(main_module, "PC-18")

    member = main_module.member_repository.create_member(
        MemberCreateRequest(username="config.player", display_name="config.player", pin="1234", confirm_pin="1234"),
        None,
    )
    now = datetime.now(timezone.utc)
    main_module.session_repository.create_session(
        SessionRecord(
            session_id="member-session-pc18",
            pc_name="PC-18",
            status="active",
            started_at=now,
            scheduled_end_at=now,
            original_seconds=3600,
            total_seconds=3600,
            created_at=now,
            session_mode="member_balance",
            member_id=member.member_id,
        )
    )
    purchase = main_module.member_repository.purchase(
        member.member_id,
        MemberPurchaseRequest(product_id="member-promo", payment_method="cash", cash_received="1000.00"),
        cashier,
    )
    assert purchase.payment.attribution_status == "pending"
    assert purchase.payment.pc_name_snapshot == "PC-18"

    main_module.update_pc_layout(
        "PC-18",
        PCLayoutUpdate(display_name="PC 18", area_label="UNCONFIGURED", layout_order=999, is_vip=False, is_enabled=True, income_group_id="mama-lala"),
        admin,
    )

    resolved = main_module.payment_repository.get_payment(purchase.payment.payment_id)
    assert resolved.attribution_status == "assigned"
    assert resolved.income_group_id_snapshot == "mama-lala"


def test_multiple_pending_payments_on_same_pc_all_resolve(temp_directory: str) -> None:
    from server.schemas import PCLayoutUpdate

    main_module, admin, cashier = create_layout_env(Path(temp_directory) / "config-resolve-multiple.sqlite3")
    create_unconfigured_pc(main_module, "PC-19")

    first = create_guest_session_and_pay(main_module, "session-pc19-a", "PC-19", Decimal("10.00"), cashier)
    second = create_guest_session_and_pay(main_module, "session-pc19-b", "PC-19", Decimal("15.00"), cashier)

    main_module.update_pc_layout(
        "PC-19",
        PCLayoutUpdate(display_name="PC 19", area_label="UNCONFIGURED", layout_order=999, is_vip=False, is_enabled=True, income_group_id="gene"),
        admin,
    )

    for payment in (first, second):
        resolved = main_module.payment_repository.get_payment(payment.payment_id)
        assert resolved.attribution_status == "assigned"
        assert resolved.income_group_id_snapshot == "gene"


def test_already_assigned_payment_unaffected_by_pc_configuration(temp_directory: str) -> None:
    from server.schemas import PCLayoutUpdate

    main_module, admin, cashier = create_layout_env(Path(temp_directory) / "config-already-assigned.sqlite3")

    already_assigned = create_guest_session_and_pay(main_module, "session-pc11-assigned", "PC-11", Decimal("15.00"), cashier)
    assert already_assigned.attribution_status == "assigned"
    assert already_assigned.income_group_id_snapshot == "gene"

    # Reconfiguring PC-11 (already had a group) must be a no-op for a
    # payment that already has a group snapshot.
    main_module.update_pc_layout(
        "PC-11",
        PCLayoutUpdate(display_name="PC 11", area_label="ROGENE", layout_order=110, is_vip=False, is_enabled=True, income_group_id="gene"),
        admin,
    )
    unchanged = main_module.payment_repository.get_payment(already_assigned.payment_id)
    assert unchanged.income_group_id_snapshot == "gene"
    assert unchanged.attribution_assigned_at == already_assigned.attribution_assigned_at


def test_later_pc_reassignment_does_not_rewrite_old_snapshots_new_payments_use_new_group(temp_directory: str) -> None:
    from server.schemas import PCLayoutUpdate

    main_module, admin, cashier = create_layout_env(Path(temp_directory) / "config-reassign.sqlite3")

    old_payment = create_guest_session_and_pay(main_module, "session-reassign-old", "PC-11", Decimal("15.00"), cashier)
    assert old_payment.income_group_id_snapshot == "gene"

    main_module.update_pc_layout(
        "PC-11",
        PCLayoutUpdate(display_name="PC 11", area_label="ROGENE", layout_order=110, is_vip=False, is_enabled=True, income_group_id="united"),
        admin,
    )

    new_payment = create_guest_session_and_pay(main_module, "session-reassign-new", "PC-11", Decimal("20.00"), cashier)
    assert new_payment.income_group_id_snapshot == "united"

    reloaded_old = main_module.payment_repository.get_payment(old_payment.payment_id)
    assert reloaded_old.income_group_id_snapshot == "gene", "reassignment must never rewrite an already-assigned historical payment"


def main() -> None:
    tests = [
        test_pending_standard_purchase_assigned_by_standard_session,
        test_pending_vip_purchase_remains_pending_after_standard_session,
        test_standard_assigned_vip_pending_same_standard_session,
        test_both_assigned_by_vip_session,
        test_multiple_standard_purchases_all_assign_together,
        test_account_creation_pending_assigns_on_first_eligible_session,
        test_sweep_is_idempotent,
        test_reconnect_retry_does_not_reassign,
        test_voided_pending_purchase_ignored_by_sweep,
        test_assigned_row_never_moves_on_later_session,
        test_four_income_groups_list_correctly,
        test_pc_layout_update_persists_income_group_id,
        test_is_vip_unaffected_by_income_group_update,
        test_unknown_income_group_rejected,
        test_admin_edited_assignment_survives_restart,
        test_unconfigured_pc_guest_payment_remains_pending,
        test_assign_pc_group_resolves_pending_guest_payment,
        test_pending_member_purchase_with_known_pc_resolves_on_configuration,
        test_multiple_pending_payments_on_same_pc_all_resolve,
        test_already_assigned_payment_unaffected_by_pc_configuration,
        test_later_pc_reassignment_does_not_rewrite_old_snapshots_new_payments_use_new_group,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
    print("Stage 3 pending-attribution + income-group configuration smoke tests passed.")


if __name__ == "__main__":
    main()
