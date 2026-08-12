"""Stage 2 (resolve + owner-assignment API) tests.

Covers the new public POST /stations/resolve endpoint and the new
owner-only /stations/unclaimed, /stations/assigned, /stations/{mac}/assign,
/stations/{mac}/reassign, /stations/{mac}/revoke endpoints in server/main.py.

No client changes, no pc_name-optional config, no wired-NIC detection, and
no dashboard UI exist yet -- this only proves the new server-side protocol
is correct, safe, and non-destructive of pc_layouts/pc_metadata/register/
heartbeat.
"""

import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException
from pydantic import ValidationError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module

    main_module.pc_registry.clear()

    from server.schemas import UserCreateRequest

    owner_public = main_module.user_repository.create_user(
        UserCreateRequest(username="sa-owner", display_name="SA Owner", password="owner-pass", role="owner")
    )
    admin_public = main_module.user_repository.create_user(
        UserCreateRequest(username="sa-admin", display_name="SA Admin", password="admin-pass", role="admin")
    )
    cashier_public = main_module.user_repository.create_user(
        UserCreateRequest(username="sa-cashier", display_name="SA Cashier", password="cashier-pass", role="cashier")
    )
    owner = main_module.user_repository.get_user(owner_public.user_id)
    admin = main_module.user_repository.get_user(admin_public.user_id)
    cashier = main_module.user_repository.get_user(cashier_public.user_id)
    assert owner is not None and admin is not None and cashier is not None
    return main_module, owner, admin, cashier


def configure_pc(main_module, pc_name: str, **overrides):
    from server.schemas import PCLayoutUpdate

    fields = dict(
        display_name=pc_name,
        area_label="TEST AREA",
        layout_order=10,
        is_vip=False,
        is_enabled=True,
        income_group_id=None,
    )
    fields.update(overrides)
    return main_module.pc_layout_repository.upsert_layout(pc_name, PCLayoutUpdate(**fields))


def resolve(main_module, mac_address: str, ip_address: str = "192.168.0.20"):
    from server.schemas import StationResolveRequest

    return main_module.resolve_station(StationResolveRequest(mac_address=mac_address, ip_address=ip_address))


def assign(main_module, mac_address: str, pc_name: str, actor):
    from server.schemas import StationAssignRequest

    return main_module.assign_station(mac_address, StationAssignRequest(pc_name=pc_name), actor)


def reassign(main_module, mac_address: str, pc_name: str, actor):
    from server.schemas import StationAssignRequest

    return main_module.reassign_station(mac_address, StationAssignRequest(pc_name=pc_name), actor)


# --- POST /stations/resolve -------------------------------------------------


def test_resolve_unknown_mac_creates_unclaimed_sighting(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "resolve-unknown.sqlite3")

    response = resolve(main_module, "AA:BB:CC:DD:EE:01", "192.168.0.30")
    assert response.status == "unclaimed"
    assert response.pc_name is None
    assert response.display_name is None

    record = main_module.station_repository.get_by_mac("AA:BB:CC:DD:EE:01")
    assert record is not None
    assert record.status == "unclaimed"
    assert record.last_seen_ip == "192.168.0.30"
    assert record.first_seen_at == record.last_seen_at


def test_resolve_repeated_updates_last_seen_without_duplicating(temp_directory: str) -> None:
    main_module, _, _, _ = create_env(Path(temp_directory) / "resolve-repeat.sqlite3")

    first = resolve(main_module, "AA:BB:CC:DD:EE:02", "192.168.0.31")
    second = resolve(main_module, "AA:BB:CC:DD:EE:02", "192.168.0.32")

    assert first.status == second.status == "unclaimed"
    all_rows = main_module.station_repository.list_all()
    assert len(all_rows) == 1
    assert all_rows[0].last_seen_ip == "192.168.0.32"


def test_resolve_assigned_mac_returns_authoritative_pc_name(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "resolve-assigned.sqlite3")
    configure_pc(main_module, "PC-11", display_name="Front Row 11")

    assign(main_module, "AA:BB:CC:DD:EE:03", "PC-11", owner)
    response = resolve(main_module, "AA:BB:CC:DD:EE:03", "192.168.0.33")

    assert response.status == "assigned"
    assert response.pc_name == "PC-11"
    assert response.display_name == "Front Row 11"


def test_resolve_cannot_be_told_its_own_pc_name(temp_directory: str) -> None:
    """A caller sending mac_address + a smuggled pc_name must have no way
    to influence the outcome: the request schema rejects the extra field
    outright, and even independent of that, resolve() only ever reflects
    the server's own station_assignments state."""
    main_module, owner, _, _ = create_env(Path(temp_directory) / "resolve-no-self-assign.sqlite3")
    configure_pc(main_module, "PC-11")
    configure_pc(main_module, "PC-15")
    assign(main_module, "AA:BB:CC:DD:EE:04", "PC-11", owner)

    from server.schemas import StationResolveRequest

    try:
        StationResolveRequest.model_validate(
            {"mac_address": "AA:BB:CC:DD:EE:04", "ip_address": "192.168.0.34", "pc_name": "PC-15"}
        )
        raise AssertionError("expected rejection of an unexpected pc_name field")
    except ValidationError:
        pass

    # Even calling resolve() legitimately (no pc_name field at all) for a
    # MAC that already belongs to PC-11 can never come back as PC-15.
    response = resolve(main_module, "AA:BB:CC:DD:EE:04", "192.168.0.34")
    assert response.pc_name == "PC-11"


def test_resolve_revoked_mac_does_not_regain_old_assignment(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "resolve-revoked.sqlite3")
    configure_pc(main_module, "PC-12")

    assign(main_module, "AA:BB:CC:DD:EE:05", "PC-12", owner)
    main_module.revoke_station("AA:BB:CC:DD:EE:05", owner)

    response = resolve(main_module, "AA:BB:CC:DD:EE:05", "192.168.0.35")
    assert response.status == "unclaimed"
    assert response.pc_name is None

    # Audit state must survive even though the public response hid it.
    record = main_module.station_repository.get_by_mac("AA:BB:CC:DD:EE:05")
    assert record.status == "revoked"
    assert record.previous_pc_name == "PC-12"


def test_resolve_malformed_or_missing_mac_rejected(temp_directory: str) -> None:
    main_module, _, _, _ = create_env(Path(temp_directory) / "resolve-malformed.sqlite3")

    from server.schemas import StationResolveRequest

    for bad_mac in ["", "not-a-mac", "AA:BB:CC:DD:EE", "GG:HH:II:JJ:KK:LL"]:
        try:
            StationResolveRequest(mac_address=bad_mac, ip_address="192.168.0.1")
            raise AssertionError(f"expected rejection for {bad_mac!r}")
        except ValidationError:
            pass

    try:
        StationResolveRequest(mac_address="AA:BB:CC:DD:EE:06", ip_address="   ")
        raise AssertionError("expected rejection for blank ip_address")
    except ValidationError:
        pass


# --- Owner-only list endpoints -----------------------------------------


def test_owner_can_list_unclaimed_and_assigned(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "owner-lists.sqlite3")
    configure_pc(main_module, "PC-13")

    resolve(main_module, "AA:BB:CC:DD:EE:07", "192.168.0.40")
    assign(main_module, "AA:BB:CC:DD:EE:08", "PC-13", owner)

    unclaimed = main_module.list_unclaimed_stations(owner)
    assigned = main_module.list_assigned_stations(owner)

    assert [row.mac_address for row in unclaimed] == ["AA:BB:CC:DD:EE:07"]
    assert [row.mac_address for row in assigned] == ["AA:BB:CC:DD:EE:08"]

    assigned_row = assigned[0]
    assert assigned_row.pc_name == "PC-13"
    assert assigned_row.status == "assigned"
    assert assigned_row.first_seen_at is not None
    assert assigned_row.last_seen_at is not None
    assert assigned_row.assigned_at is not None
    assert assigned_row.assigned_by_user_id == owner.user_id


def test_non_owner_cannot_access_management_endpoints(temp_directory: str) -> None:
    main_module, owner, admin, cashier = create_env(Path(temp_directory) / "non-owner.sqlite3")

    from server.auth import require_owner

    assert require_owner(owner).user_id == owner.user_id

    for non_owner in (admin, cashier):
        try:
            require_owner(non_owner)
            raise AssertionError(f"expected 403 for role={non_owner.role}")
        except HTTPException as error:
            assert error.status_code == 403


# --- assign ------------------------------------------------------------


def test_assign_unclaimed_mac_to_existing_pc_succeeds(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "assign-success.sqlite3")
    configure_pc(main_module, "PC-14")

    record = assign(main_module, "AA:BB:CC:DD:EE:09", "PC-14", owner)
    assert record.status == "assigned"
    assert record.pc_name == "PC-14"
    assert record.assigned_by_user_id == owner.user_id


def test_assign_rejects_unconfigured_pc_name(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "assign-unconfigured.sqlite3")

    try:
        assign(main_module, "AA:BB:CC:DD:EE:10", "PC-999", owner)
        raise AssertionError("expected a 400 rejection for an unconfigured pc_name")
    except HTTPException as error:
        assert error.status_code == 400

    assert main_module.station_repository.get_by_mac("AA:BB:CC:DD:EE:10") is None


def test_assignment_preserves_all_existing_pc_layout_fields(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "assign-preserves-layout.sqlite3")
    configure_pc(
        main_module,
        "PC-15",
        is_vip=True,
        income_group_id=None,
        area_label="VIP ROOM",
        layout_order=150,
        is_enabled=False,
    )
    before = main_module.pc_layout_repository.get_layout("PC-15")

    assign(main_module, "AA:BB:CC:DD:EE:11", "PC-15", owner)

    after = main_module.pc_layout_repository.get_layout("PC-15")
    assert after.is_vip == before.is_vip is True
    assert after.area_label == before.area_label == "VIP ROOM"
    assert after.layout_order == before.layout_order == 150
    assert after.is_enabled == before.is_enabled is False
    assert after.income_group_id == before.income_group_id
    assert after.display_name == before.display_name
    assert after.updated_at == before.updated_at, "assignment must not touch pc_layouts at all"


def test_second_mac_assigning_same_pc_returns_409(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "assign-duplicate.sqlite3")
    configure_pc(main_module, "PC-16")

    assign(main_module, "AA:BB:CC:DD:EE:12", "PC-16", owner)
    try:
        assign(main_module, "AA:BB:CC:DD:EE:13", "PC-16", owner)
        raise AssertionError("expected a 409 rejection")
    except HTTPException as error:
        assert error.status_code == 409

    assert main_module.station_repository.get_by_pc_name("PC-16").mac_address == "AA:BB:CC:DD:EE:12"


def test_same_assignment_retry_is_idempotent(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "assign-idempotent.sqlite3")
    configure_pc(main_module, "PC-17")

    first = assign(main_module, "AA:BB:CC:DD:EE:14", "PC-17", owner)
    second = assign(main_module, "AA:BB:CC:DD:EE:14", "PC-17", owner)

    assert first.pc_name == second.pc_name == "PC-17"
    assert len(main_module.station_repository.list_assigned()) == 1


# --- reassign / revoke ---------------------------------------------------


def test_reassign_rejects_unconfigured_pc_name(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "reassign-unconfigured.sqlite3")

    try:
        reassign(main_module, "AA:BB:CC:DD:EE:15", "PC-999", owner)
        raise AssertionError("expected a 400 rejection for an unconfigured pc_name")
    except HTTPException as error:
        assert error.status_code == 400


def test_reassign_revokes_old_mac_and_assigns_new_mac(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "reassign-nic-swap.sqlite3")
    configure_pc(main_module, "PC-18")

    assign(main_module, "AA:BB:CC:DD:EE:16", "PC-18", owner)  # OLD-MAC
    new_record = reassign(main_module, "AA:BB:CC:DD:EE:17", "PC-18", owner)  # NEW-MAC

    assert new_record.status == "assigned"
    assert new_record.pc_name == "PC-18"

    old_record = main_module.station_repository.get_by_mac("AA:BB:CC:DD:EE:16")
    assert old_record.status == "revoked"
    assert old_record.pc_name is None
    assert old_record.previous_pc_name == "PC-18"

    assert resolve(main_module, "AA:BB:CC:DD:EE:16").status == "unclaimed"
    new_resolution = resolve(main_module, "AA:BB:CC:DD:EE:17")
    assert new_resolution.status == "assigned"
    assert new_resolution.pc_name == "PC-18"


def test_revoke_removes_runtime_resolution(temp_directory: str) -> None:
    main_module, owner, _, _ = create_env(Path(temp_directory) / "revoke-resolution.sqlite3")
    configure_pc(main_module, "PC-19")

    assign(main_module, "AA:BB:CC:DD:EE:18", "PC-19", owner)
    assert resolve(main_module, "AA:BB:CC:DD:EE:18").status == "assigned"

    revoked = main_module.revoke_station("AA:BB:CC:DD:EE:18", owner)
    assert revoked.status == "revoked"
    assert revoked.previous_pc_name == "PC-19"

    assert resolve(main_module, "AA:BB:CC:DD:EE:18").status == "unclaimed"
    assert main_module.station_repository.get_by_pc_name("PC-19") is None


# --- persistence / isolation from pc_registry -----------------------------


def test_station_assignment_survives_registry_reset_and_reinit(temp_directory: str) -> None:
    """station_assignments (persistent physical identity) must never be
    coupled to pc_registry (ephemeral online state, wiped on every server
    restart) or lost across a re-run of initialize_database()."""
    database_path = Path(temp_directory) / "restart-durability.sqlite3"
    main_module, owner, _, _ = create_env(database_path)
    configure_pc(main_module, "PC-20")

    assign(main_module, "AA:BB:CC:DD:EE:19", "PC-20", owner)

    # Simulate a server restart: pc_registry is in-memory only and starts
    # empty again; station_assignments must not be affected by this at all.
    main_module.pc_registry.clear()
    assert main_module.pc_registry == {}
    response_after_registry_reset = resolve(main_module, "AA:BB:CC:DD:EE:19")
    assert response_after_registry_reset.status == "assigned"
    assert response_after_registry_reset.pc_name == "PC-20"

    # Simulate the database layer being re-initialized on process restart.
    from server.database import initialize_database

    initialize_database()
    record = main_module.station_repository.get_by_mac("AA:BB:CC:DD:EE:19")
    assert record is not None
    assert record.status == "assigned"
    assert record.pc_name == "PC-20"


def test_register_and_heartbeat_unaffected_by_station_assignments(temp_directory: str) -> None:
    """/register and /heartbeat must keep working exactly as before,
    whether or not station_assignments has any data at all -- Stage 2 adds
    a new, independent lookup path and must not couple into the legacy one."""
    main_module, owner, _, _ = create_env(Path(temp_directory) / "register-heartbeat-unaffected.sqlite3")
    configure_pc(main_module, "PC-21")

    from server.schemas import PCHeartbeat, PCRegistration

    # No station_assignments data at all yet.
    response = main_module.register_pc(PCRegistration(pc_name="PC-21", display_name="PC-21", ip_address="10.0.0.5"))
    assert response.pc_name == "PC-21"
    assert response.status == "online"
    heartbeat_response = main_module.heartbeat(
        PCHeartbeat(pc_name="PC-21", display_name="PC-21", ip_address="10.0.0.5")
    )
    assert heartbeat_response.status == "online"

    # Now with an unrelated MAC assigned to a different pc_name -- legacy
    # register/heartbeat for PC-21 must be completely unaffected.
    assign(main_module, "AA:BB:CC:DD:EE:20", "PC-21", owner)
    response_again = main_module.register_pc(
        PCRegistration(pc_name="PC-21", display_name="PC-21", ip_address="10.0.0.6")
    )
    assert response_again.pc_name == "PC-21"
    assert response_again.ip_address == "10.0.0.6"


def main() -> None:
    tests = [
        test_resolve_unknown_mac_creates_unclaimed_sighting,
        test_resolve_repeated_updates_last_seen_without_duplicating,
        test_resolve_assigned_mac_returns_authoritative_pc_name,
        test_resolve_cannot_be_told_its_own_pc_name,
        test_resolve_revoked_mac_does_not_regain_old_assignment,
        test_resolve_malformed_or_missing_mac_rejected,
        test_owner_can_list_unclaimed_and_assigned,
        test_non_owner_cannot_access_management_endpoints,
        test_assign_unclaimed_mac_to_existing_pc_succeeds,
        test_assign_rejects_unconfigured_pc_name,
        test_assignment_preserves_all_existing_pc_layout_fields,
        test_second_mac_assigning_same_pc_returns_409,
        test_same_assignment_retry_is_idempotent,
        test_reassign_rejects_unconfigured_pc_name,
        test_reassign_revokes_old_mac_and_assigns_new_mac,
        test_revoke_removes_runtime_resolution,
        test_station_assignment_survives_registry_reset_and_reinit,
        test_register_and_heartbeat_unaffected_by_station_assignments,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("station API (Stage 2) smoke tests passed.")


if __name__ == "__main__":
    main()
