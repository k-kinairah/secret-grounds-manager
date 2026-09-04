"""DELETE /pcs/{pc_name}/layout -- hard-remove a leftover / auto-detected
layout slot.

Covers the new pc_layout_repository.delete_layout() guard rules and the
delete_pc_layout route in server/main.py. Only never-used slots may be
hard-deleted; anything with an assigned station, a session, a payment, or a
live registration must fall back to Hide (is_enabled = 0). No changes to
station resolution, registration, pricing, or session logic.
"""

import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException

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
        UserCreateRequest(username="ld-admin", display_name="LD Admin", password="admin-pass", role="admin")
    )
    admin = main_module.user_repository.get_user(admin_public.user_id)
    assert admin is not None
    return main_module, admin


def make_slot(main_module, pc_name: str, **overrides):
    from server.schemas import PCLayoutUpdate

    fields = dict(
        display_name=pc_name,
        area_label="CLIENT",
        layout_order=10_000,
        is_vip=False,
        is_enabled=True,
        income_group_id=None,
    )
    fields.update(overrides)
    return main_module.pc_layout_repository.upsert_layout(pc_name, PCLayoutUpdate(**fields))


def test_delete_removes_a_never_used_auto_detected_slot(temp_directory: str) -> None:
    main_module, admin = create_env(Path(temp_directory) / "d1.sqlite3")
    make_slot(main_module, "PC-JUNK-1")
    assert main_module.pc_layout_repository.get_layout("PC-JUNK-1") is not None

    result = main_module.delete_pc_layout("PC-JUNK-1", admin)
    assert result["pc_name"] == "PC-JUNK-1"
    assert main_module.pc_layout_repository.get_layout("PC-JUNK-1") is None


def test_delete_unknown_slot_is_404(temp_directory: str) -> None:
    main_module, admin = create_env(Path(temp_directory) / "d2.sqlite3")
    try:
        main_module.delete_pc_layout("PC-NOPE", admin)
        raise AssertionError("expected 404")
    except HTTPException as error:
        assert error.status_code == 404


def test_delete_blocked_while_client_is_online(temp_directory: str) -> None:
    main_module, admin = create_env(Path(temp_directory) / "d3.sqlite3")
    from server.schemas import PCRegistration

    make_slot(main_module, "PC-LIVE")
    main_module.register_pc(PCRegistration(pc_name="PC-LIVE", display_name="PC-LIVE", ip_address="10.0.0.9"))
    assert main_module.pc_registry["PC-LIVE"].status == "online"

    try:
        main_module.delete_pc_layout("PC-LIVE", admin)
        raise AssertionError("expected 409 for an online station")
    except HTTPException as error:
        assert error.status_code == 409
        assert "online" in error.detail.lower()
    assert main_module.pc_layout_repository.get_layout("PC-LIVE") is not None


def test_delete_blocked_when_a_station_is_assigned(temp_directory: str) -> None:
    main_module, admin = create_env(Path(temp_directory) / "d4.sqlite3")
    from server.schemas import StationAssignRequest

    make_slot(main_module, "PC-ASSIGNED")
    main_module.station_repository.record_sighting("AA:BB:CC:DD:EE:01", "10.0.0.10")
    main_module.assign_station("AA:BB:CC:DD:EE:01", StationAssignRequest(pc_name="PC-ASSIGNED"), admin)

    try:
        main_module.delete_pc_layout("PC-ASSIGNED", admin)
        raise AssertionError("expected 409 for an assigned station")
    except HTTPException as error:
        assert error.status_code == 409
    assert main_module.pc_layout_repository.get_layout("PC-ASSIGNED") is not None


def test_delete_blocked_when_session_history_exists(temp_directory: str) -> None:
    main_module, admin = create_env(Path(temp_directory) / "d5.sqlite3")
    from server.database import get_connection
    from server.time_utils import current_utc_time

    make_slot(main_module, "PC-USED")
    now = current_utc_time().isoformat()
    with get_connection() as connection:
        connection.execute(
            """
            INSERT INTO sessions (
                session_id, pc_name, status, started_at, scheduled_end_at,
                original_seconds, total_seconds, created_at
            ) VALUES (?, ?, 'completed', ?, ?, 60, 60, ?)
            """,
            ("sess-guard-1", "PC-USED", now, now, now),
        )

    try:
        main_module.delete_pc_layout("PC-USED", admin)
        raise AssertionError("expected 409 for a slot with session history")
    except HTTPException as error:
        assert error.status_code == 409
        assert "history" in error.detail.lower()
    assert main_module.pc_layout_repository.get_layout("PC-USED") is not None


def test_delete_does_not_touch_other_slots_or_seeded_layout(temp_directory: str) -> None:
    main_module, admin = create_env(Path(temp_directory) / "d6.sqlite3")
    make_slot(main_module, "PC-JUNK-A")
    make_slot(main_module, "PC-JUNK-B")

    main_module.delete_pc_layout("PC-JUNK-A", admin)

    assert main_module.pc_layout_repository.get_layout("PC-JUNK-A") is None
    assert main_module.pc_layout_repository.get_layout("PC-JUNK-B") is not None
    # Seeded PC-01..PC-15 still intact.
    seeded = {s.pc_name for s in main_module.pc_layout_repository.list_layouts(include_disabled=True)}
    for i in range(1, 16):
        assert f"PC-{i:02d}" in seeded


def main() -> None:
    tests = [
        test_delete_removes_a_never_used_auto_detected_slot,
        test_delete_unknown_slot_is_404,
        test_delete_blocked_while_client_is_online,
        test_delete_blocked_when_a_station_is_assigned,
        test_delete_blocked_when_session_history_exists,
        test_delete_does_not_touch_other_slots_or_seeded_layout,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("pc_layout delete smoke tests passed.")


if __name__ == "__main__":
    main()
