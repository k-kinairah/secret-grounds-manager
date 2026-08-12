"""Stage 4 (owner-facing Station Assignment UI) backend verification.

Stage 4 adds no new backend routes/schema -- it is a dashboard UI reusing
the existing Stage 2 /stations/* endpoints exactly as-is. This file exists
to prove, end to end, the exact workflow the new UI drives: a newly-
detected MAC appears in the unclaimed list, assigning it moves it to the
assigned list, and a full assign -> reassign -> revoke cycle never
disturbs a station's owner-controlled Setup/Layout fields -- this time
with a REAL (non-None) income_group_id, which server/tests/
smoke_station_api.py's equivalent layout-preservation test did not cover.
"""

import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_env(database_path: Path):
    import os

    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    import server.main as main_module

    main_module.pc_registry.clear()

    from server.schemas import UserCreateRequest

    owner_public = main_module.user_repository.create_user(
        UserCreateRequest(username="s4-owner", display_name="S4 Owner", password="owner-pass", role="owner")
    )
    owner = main_module.user_repository.get_user(owner_public.user_id)
    assert owner is not None
    return main_module, owner


def resolve(main_module, mac_address: str, ip_address: str = "192.168.0.60"):
    from server.schemas import StationResolveRequest

    return main_module.resolve_station(StationResolveRequest(mac_address=mac_address, ip_address=ip_address))


def assign(main_module, mac_address: str, pc_name: str, actor):
    from server.schemas import StationAssignRequest

    return main_module.assign_station(mac_address, StationAssignRequest(pc_name=pc_name), actor)


def reassign(main_module, mac_address: str, pc_name: str, actor):
    from server.schemas import StationAssignRequest

    return main_module.reassign_station(mac_address, StationAssignRequest(pc_name=pc_name), actor)


def test_unclaimed_station_appears_then_assignment_moves_it_to_assigned(temp_directory: str) -> None:
    main_module, owner = create_env(Path(temp_directory) / "appears-and-moves.sqlite3")
    from server.schemas import PCLayoutUpdate

    main_module.pc_layout_repository.upsert_layout(
        "PC-40",
        PCLayoutUpdate(
            display_name="PC 40", area_label="TEST", layout_order=40, is_vip=False, is_enabled=True, income_group_id=None
        ),
    )

    # Nothing detected yet: appears in neither list.
    assert main_module.list_unclaimed_stations(owner) == []
    assert main_module.list_assigned_stations(owner) == []

    # A booting client's first /stations/resolve makes it a "newly detected
    # computer" -- exactly what the dashboard's Unclaimed group reads.
    resolve(main_module, "AA:BB:CC:DD:EE:40", "192.168.0.61")
    unclaimed = main_module.list_unclaimed_stations(owner)
    assert [row.mac_address for row in unclaimed] == ["AA:BB:CC:DD:EE:40"]
    assert unclaimed[0].last_seen_ip == "192.168.0.61"
    assert main_module.list_assigned_stations(owner) == []

    # Owner assigns it (the dashboard's "Assign" action) -- moves lists.
    assign(main_module, "AA:BB:CC:DD:EE:40", "PC-40", owner)
    assert main_module.list_unclaimed_stations(owner) == []
    assigned = main_module.list_assigned_stations(owner)
    assert [row.mac_address for row in assigned] == ["AA:BB:CC:DD:EE:40"]
    assert assigned[0].pc_name == "PC-40"


def test_duplicate_pc_assignment_rejected_via_dashboard_flow(temp_directory: str) -> None:
    main_module, owner = create_env(Path(temp_directory) / "duplicate-dashboard.sqlite3")
    from server.schemas import PCLayoutUpdate

    main_module.pc_layout_repository.upsert_layout(
        "PC-41",
        PCLayoutUpdate(display_name="PC 41", area_label="TEST", layout_order=41, is_vip=False, is_enabled=True),
    )
    assign(main_module, "AA:BB:CC:DD:EE:41", "PC-41", owner)
    try:
        assign(main_module, "AA:BB:CC:DD:EE:42", "PC-41", owner)
        raise AssertionError("expected a 409 rejection")
    except HTTPException as error:
        assert error.status_code == 409
    assert main_module.station_repository.get_by_pc_name("PC-41").mac_address == "AA:BB:CC:DD:EE:41"


def test_full_assign_reassign_revoke_cycle_never_touches_real_income_group_or_vip(temp_directory: str) -> None:
    main_module, owner = create_env(Path(temp_directory) / "full-cycle-layout-preserved.sqlite3")
    from server.schemas import PCLayoutUpdate

    # A REAL income group this time (not None), plus VIP -- exactly what
    # Setup/Layout would have configured before Station Assignment is ever
    # touched.
    main_module.pc_layout_repository.upsert_layout(
        "PC-42",
        PCLayoutUpdate(
            display_name="PC 42",
            area_label="VIP ROOM",
            layout_order=42,
            is_vip=True,
            is_enabled=True,
            income_group_id="united",
        ),
    )
    before = main_module.pc_layout_repository.get_layout("PC-42")
    assert before.income_group_id == "united"
    assert before.is_vip is True

    def assert_layout_unchanged() -> None:
        current = main_module.pc_layout_repository.get_layout("PC-42")
        assert current.income_group_id == before.income_group_id == "united"
        assert current.is_vip == before.is_vip is True
        assert current.area_label == before.area_label == "VIP ROOM"
        assert current.layout_order == before.layout_order == 42
        assert current.is_enabled == before.is_enabled is True
        assert current.display_name == before.display_name
        assert current.updated_at == before.updated_at

    # Assign (dashboard "Assign" action).
    assign(main_module, "AA:BB:CC:DD:EE:43", "PC-42", owner)
    assert_layout_unchanged()

    # Reassign (dashboard "Reassign" action, e.g. NIC replacement).
    reassign(main_module, "AA:BB:CC:DD:EE:44", "PC-42", owner)
    assert_layout_unchanged()
    old_mac = main_module.station_repository.get_by_mac("AA:BB:CC:DD:EE:43")
    assert old_mac.status == "revoked"
    assert old_mac.previous_pc_name == "PC-42"

    # Revoke (dashboard "Unassign" action) -- returns to a real revoked
    # state (never silently deleted), and Setup/Layout is still untouched.
    revoked = main_module.revoke_station("AA:BB:CC:DD:EE:44", owner)
    assert revoked.status == "revoked"
    assert revoked.pc_name is None
    assert revoked.previous_pc_name == "PC-42"
    assert main_module.station_repository.get_by_pc_name("PC-42") is None
    assert_layout_unchanged()

    # PC-42 is now unassigned again (no active binding), but this is NOT
    # the same as "unclaimed" -- a revoked MAC does not silently reappear
    # in the newly-detected list either.
    assert "AA:BB:CC:DD:EE:44" not in [row.mac_address for row in main_module.list_unclaimed_stations(owner)]
    assert "AA:BB:CC:DD:EE:44" not in [row.mac_address for row in main_module.list_assigned_stations(owner)]


def main() -> None:
    tests = [
        test_unclaimed_station_appears_then_assignment_moves_it_to_assigned,
        test_duplicate_pc_assignment_rejected_via_dashboard_flow,
        test_full_assign_reassign_revoke_cycle_never_touches_real_income_group_or_vip,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Station Assignment dashboard-backend (Stage 4) smoke tests passed.")


if __name__ == "__main__":
    main()
