"""Regression tests: /register and /heartbeat must never erase or modify a
PC's owner-configured income_group_id (or any other owner-controlled layout
field). Covers the defect found during the sales/accounting acceptance
walkthrough: upsert_client_display_name rebuilt PCLayoutUpdate from the
existing layout row without carrying forward income_group_id, so every
routine client check-in silently reset the PC's revenue-owner assignment
back to unconfigured.
"""

import os
import sys
import tempfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module

    main_module.pc_registry.clear()

    from server.schemas import UserCreateRequest

    admin_public = main_module.user_repository.create_user(
        UserCreateRequest(username="rh-admin", display_name="RH Admin", password="admin-pass", role="admin")
    )
    cashier_public = main_module.user_repository.create_user(
        UserCreateRequest(username="rh-cashier", display_name="RH Cashier", password="cashier-pass", role="cashier")
    )
    admin = main_module.user_repository.get_user(admin_public.user_id)
    cashier = main_module.user_repository.get_user(cashier_public.user_id)
    assert admin is not None and cashier is not None
    return main_module, admin, cashier


def register(main_module, pc_name: str, ip_address: str = "127.0.0.1", display_name: str | None = None, mac_address: str | None = None):
    from server.schemas import PCRegistration

    return main_module.register_pc(
        PCRegistration(pc_name=pc_name, display_name=display_name or pc_name, ip_address=ip_address, mac_address=mac_address)
    )


def heartbeat(main_module, pc_name: str, ip_address: str = "127.0.0.1", display_name: str | None = None):
    from server.schemas import PCHeartbeat

    return main_module.heartbeat(PCHeartbeat(pc_name=pc_name, display_name=display_name or pc_name, ip_address=ip_address))


def configure_layout(main_module, admin, pc_name: str, income_group_id: str | None, **overrides):
    from server.schemas import PCLayoutUpdate

    fields = dict(
        display_name=pc_name,
        area_label="ROGENE",
        layout_order=110,
        is_vip=False,
        is_enabled=True,
        income_group_id=income_group_id,
    )
    fields.update(overrides)
    return main_module.update_pc_layout(pc_name, PCLayoutUpdate(**fields), admin)


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


# --- The primary named regression test ----------------------------------


def test_register_and_heartbeat_preserve_income_group_assignment(temp_directory: str) -> None:
    main_module, admin, _ = create_env(Path(temp_directory) / "preserve-income-group.sqlite3")

    configure_layout(main_module, admin, "PC-11", "gene")
    assert main_module.pc_layout_repository.get_layout("PC-11").income_group_id == "gene"

    register(main_module, "PC-11")
    assert main_module.pc_layout_repository.get_layout("PC-11").income_group_id == "gene", (
        "register() must not erase income_group_id"
    )

    for _ in range(3):
        heartbeat(main_module, "PC-11")
        assert main_module.pc_layout_repository.get_layout("PC-11").income_group_id == "gene", (
            "repeated heartbeat() must not erase income_group_id"
        )

    configure_layout(main_module, admin, "PC-11", "united")
    assert main_module.pc_layout_repository.get_layout("PC-11").income_group_id == "united"

    heartbeat(main_module, "PC-11")
    assert main_module.pc_layout_repository.get_layout("PC-11").income_group_id == "united", (
        "heartbeat() after reassignment must not revert or erase the new group"
    )

    # Simulate reconnect / re-register (e.g. client restart, server restart
    # followed by client registration).
    register(main_module, "PC-11")
    assert main_module.pc_layout_repository.get_layout("PC-11").income_group_id == "united", (
        "re-register() after reassignment must not revert or erase the new group"
    )


# --- Supporting focused checks -------------------------------------------


def test_is_vip_preserved_across_register_and_heartbeat(temp_directory: str) -> None:
    main_module, admin, _ = create_env(Path(temp_directory) / "preserve-is-vip.sqlite3")

    configure_layout(main_module, admin, "PC-15", "tyrone", is_vip=True, area_label="VIP", layout_order=150)
    register(main_module, "PC-15")
    heartbeat(main_module, "PC-15")
    heartbeat(main_module, "PC-15")

    layout = main_module.pc_layout_repository.get_layout("PC-15")
    assert layout.is_vip is True
    assert layout.income_group_id == "tyrone"


def test_other_owner_controlled_fields_preserved(temp_directory: str) -> None:
    main_module, admin, _ = create_env(Path(temp_directory) / "preserve-other-fields.sqlite3")

    configure_layout(
        main_module,
        admin,
        "PC-13",
        "mama-lala",
        area_label="MAMA CUSTOM AREA",
        layout_order=777,
        is_enabled=False,
        mac_address="AA:BB:CC:DD:EE:FF",
    )
    before = main_module.pc_layout_repository.get_layout("PC-13")

    register(main_module, "PC-13", mac_address="AA:BB:CC:DD:EE:FF")
    heartbeat(main_module, "PC-13")
    heartbeat(main_module, "PC-13")

    after = main_module.pc_layout_repository.get_layout("PC-13")
    assert after.area_label == before.area_label == "MAMA CUSTOM AREA"
    assert after.layout_order == before.layout_order == 777
    assert after.is_enabled == before.is_enabled is False
    assert after.mac_address == before.mac_address == "AA:BB:CC:DD:EE:FF"
    assert after.income_group_id == before.income_group_id == "mama-lala"


def test_display_name_still_updates_from_register_and_heartbeat(temp_directory: str) -> None:
    """The fix must not turn register/heartbeat into a complete no-op --
    display_name is genuine client/runtime info and must keep updating."""
    main_module, admin, _ = create_env(Path(temp_directory) / "display-name-still-updates.sqlite3")

    configure_layout(main_module, admin, "PC-11", "gene", display_name="PC 11")
    register(main_module, "PC-11", display_name="PC Eleven Renamed")
    layout = main_module.pc_layout_repository.get_layout("PC-11")
    assert layout.display_name == "PC Eleven Renamed"
    assert layout.income_group_id == "gene"

    heartbeat(main_module, "PC-11", display_name="PC Eleven Renamed Again")
    layout = main_module.pc_layout_repository.get_layout("PC-11")
    assert layout.display_name == "PC Eleven Renamed Again"
    assert layout.income_group_id == "gene"


def test_existing_assigned_payment_snapshot_unchanged_by_heartbeat(temp_directory: str) -> None:
    main_module, admin, cashier = create_env(Path(temp_directory) / "payment-snapshot-unchanged.sqlite3")
    configure_layout(main_module, admin, "PC-11", "gene")
    register(main_module, "PC-11")

    old_payment = create_guest_session_and_pay(main_module, "session-old", "PC-11", Decimal("15.00"), cashier)
    assert old_payment.income_group_id_snapshot == "gene"

    for _ in range(3):
        heartbeat(main_module, "PC-11")

    reloaded_old = main_module.payment_repository.get_payment(old_payment.payment_id)
    assert reloaded_old.income_group_id_snapshot == "gene", "heartbeat must never rewrite a historical payment snapshot"

    # A payment made AFTER the heartbeats must also still resolve to Gene --
    # this is what would have broken before the fix (heartbeat nulled the
    # live layout, so this payment would have gone pending instead).
    new_payment = create_guest_session_and_pay(main_module, "session-new", "PC-11", Decimal("8.00"), cashier)
    assert new_payment.income_group_id_snapshot == "gene"
    assert new_payment.attribution_status == "assigned"


def test_heartbeat_and_register_never_trigger_attribution_resolution(temp_directory: str) -> None:
    """register()/heartbeat() must never invoke income-attribution
    resolution as a side effect. Proven here on an UNCONFIGURED PC: a
    pending payment must stay pending across repeated register/heartbeat
    calls, since only an explicit Layout PUT (never register/heartbeat) is
    allowed to resolve pending attribution."""
    main_module, admin, cashier = create_env(Path(temp_directory) / "no-attribution-side-effect.sqlite3")

    from server.schemas import PCLayoutUpdate

    main_module.pc_layout_repository.upsert_layout(
        "PC-20",
        PCLayoutUpdate(display_name="PC-20", area_label="UNCONFIGURED", layout_order=999, is_vip=False, is_enabled=True),
    )
    pending_payment = create_guest_session_and_pay(main_module, "session-pending", "PC-20", Decimal("12.00"), cashier)
    assert pending_payment.attribution_status == "pending"

    register(main_module, "PC-20")
    for _ in range(3):
        heartbeat(main_module, "PC-20")

    reloaded = main_module.payment_repository.get_payment(pending_payment.payment_id)
    assert reloaded.attribution_status == "pending", "register/heartbeat must never resolve pending attribution"
    assert reloaded.income_group_id_snapshot is None
    assert main_module.pc_layout_repository.get_layout("PC-20").income_group_id is None, (
        "register/heartbeat must never assign a group to an unconfigured PC"
    )


def main() -> None:
    tests = [
        test_register_and_heartbeat_preserve_income_group_assignment,
        test_is_vip_preserved_across_register_and_heartbeat,
        test_other_owner_controlled_fields_preserved,
        test_display_name_still_updates_from_register_and_heartbeat,
        test_existing_assigned_payment_snapshot_unchanged_by_heartbeat,
        test_heartbeat_and_register_never_trigger_attribution_resolution,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
    print("register/heartbeat income-group preservation regression tests passed.")


if __name__ == "__main__":
    main()
