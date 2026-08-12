"""Stage 1 (schema + repository only) tests for server-owned station
identity: server/database.py's station_assignments table and
server/station_repository.py.StationAssignmentRepository.

No /stations/* routes, no client changes, no wired-NIC detection exist yet
-- this only proves the persistence foundation is correct, idempotent, and
non-destructive of pc_layouts/pc_metadata.
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def create_pre_stage1_database(database_path: Path) -> None:
    """Baseline shape immediately before this stage: pc_layouts/pc_metadata
    exist and are populated, but station_assignments does not exist at all."""
    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            CREATE TABLE pc_layouts (
                pc_name TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                area_label TEXT NOT NULL,
                layout_order INTEGER NOT NULL DEFAULT 0,
                is_vip INTEGER NOT NULL DEFAULT 0,
                is_enabled INTEGER NOT NULL DEFAULT 1,
                income_group_id TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE pc_metadata (
                pc_name TEXT PRIMARY KEY,
                mac_address TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        now = "2026-07-25T00:00:00+00:00"
        connection.execute(
            """
            INSERT INTO pc_layouts (
                pc_name, display_name, area_label, layout_order,
                is_vip, is_enabled, income_group_id, updated_at
            )
            VALUES ('PC-11', 'PC 11', 'ROGENE', 110, 1, 1, 'gene', ?)
            """,
            (now,),
        )
        connection.execute(
            """
            INSERT INTO pc_metadata (pc_name, mac_address, updated_at)
            VALUES ('PC-11', 'AA:BB:CC:DD:EE:01', ?)
            """,
            (now,),
        )
        connection.commit()
    finally:
        connection.close()


def fresh_env(database_path: Path):
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)
    from server.database import initialize_database

    initialize_database()

    from server.station_repository import StationAssignmentRepository

    return StationAssignmentRepository()


def table_columns(database_path: Path, table_name: str) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        return {row[1] for row in connection.execute(f"PRAGMA table_info({table_name})").fetchall()}
    finally:
        connection.close()


# --- Migration -------------------------------------------------------------


def test_fresh_database_creates_station_assignments_table() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        database_path = Path(temp_directory) / "fresh.sqlite3"
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)

        from server.database import initialize_database

        initialize_database()

        columns = table_columns(database_path, "station_assignments")
        assert columns == {
            "mac_address",
            "pc_name",
            "status",
            "first_seen_at",
            "last_seen_at",
            "last_seen_ip",
            "assigned_at",
            "assigned_by_user_id",
            "previous_pc_name",
        }, columns

        connection = sqlite3.connect(database_path)
        try:
            assert connection.execute("SELECT COUNT(*) FROM station_assignments").fetchone()[0] == 0
            index_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'idx_station_assignments_status'"
            ).fetchone()
            assert index_row is not None
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            connection.close()

    print("Fresh-database station_assignments migration test passed.")


def test_migration_is_idempotent_and_does_not_touch_pc_layouts_or_pc_metadata() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        database_path = Path(temp_directory) / "upgrade.sqlite3"
        create_pre_stage1_database(database_path)
        os.environ["SECRET_GROUNDS_DB_PATH"] = str(database_path)

        from server.database import initialize_database

        def snapshot_pc11():
            connection = sqlite3.connect(database_path)
            connection.row_factory = sqlite3.Row
            try:
                layout = dict(
                    connection.execute("SELECT * FROM pc_layouts WHERE pc_name = 'PC-11'").fetchone()
                )
                metadata = dict(
                    connection.execute("SELECT * FROM pc_metadata WHERE pc_name = 'PC-11'").fetchone()
                )
                return layout, metadata
            finally:
                connection.close()

        before_layout, before_metadata = snapshot_pc11()

        initialize_database()
        after_first_layout, after_first_metadata = snapshot_pc11()
        assert after_first_layout == before_layout
        assert after_first_metadata == before_metadata

        connection = sqlite3.connect(database_path)
        try:
            assert connection.execute("SELECT COUNT(*) FROM station_assignments").fetchone()[0] == 0
        finally:
            connection.close()

        # Second init: idempotent, no errors, still no rows (Stage 1 does
        # not backfill), pc_layouts/pc_metadata still untouched.
        initialize_database()
        after_second_layout, after_second_metadata = snapshot_pc11()
        assert after_second_layout == before_layout
        assert after_second_metadata == before_metadata

        connection = sqlite3.connect(database_path)
        try:
            assert connection.execute("SELECT COUNT(*) FROM station_assignments").fetchone()[0] == 0
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            connection.close()

    print("Migration idempotency / pc_layouts+pc_metadata non-interference test passed.")


# --- record_sighting ---------------------------------------------------------


def test_record_sighting_inserts_unclaimed() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "sighting.sqlite3")

        record = repository.record_sighting("AA:BB:CC:DD:EE:01", "192.168.0.50")
        assert record.status == "unclaimed"
        assert record.pc_name is None
        assert record.mac_address == "AA:BB:CC:DD:EE:01"
        assert record.last_seen_ip == "192.168.0.50"
        assert record.first_seen_at == record.last_seen_at

        assert [row.mac_address for row in repository.list_unclaimed()] == ["AA:BB:CC:DD:EE:01"]

    print("record_sighting unclaimed-insertion test passed.")


def test_record_sighting_updates_last_seen_without_disturbing_first_seen() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "resighting.sqlite3")

        first = repository.record_sighting("AA:BB:CC:DD:EE:02", "192.168.0.51")
        second = repository.record_sighting("AA:BB:CC:DD:EE:02", "192.168.0.52")

        assert second.first_seen_at == first.first_seen_at
        assert second.last_seen_ip == "192.168.0.52"
        assert second.status == "unclaimed"

        all_rows = repository.list_all()
        assert len(all_rows) == 1, "re-sighting the same MAC must update in place, not duplicate"

    print("record_sighting last-seen update test passed.")


def test_record_sighting_does_not_disturb_an_assigned_station() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "sighting-assigned.sqlite3")

        repository.record_sighting("AA:BB:CC:DD:EE:03", "192.168.0.10")
        repository.assign("AA:BB:CC:DD:EE:03", "PC-01", assigned_by_user_id="owner-1")

        reseen = repository.record_sighting("AA:BB:CC:DD:EE:03", "192.168.0.11")
        assert reseen.status == "assigned"
        assert reseen.pc_name == "PC-01"
        assert reseen.last_seen_ip == "192.168.0.11"

    print("record_sighting preserves an existing assignment test passed.")


# --- MAC normalization -------------------------------------------------------


def test_mac_normalization_treats_equivalent_formats_as_the_same_station() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "normalize.sqlite3")

        repository.record_sighting("aa-bb-cc-dd-ee-04", "192.168.0.20")
        repository.record_sighting("AA:BB:CC:DD:EE:04", "192.168.0.21")
        repository.record_sighting("aabb.ccdd.ee04", "192.168.0.22")

        all_rows = repository.list_all()
        assert len(all_rows) == 1, "differently-formatted equivalents must collapse to one row"
        assert all_rows[0].mac_address == "AA:BB:CC:DD:EE:04"
        assert all_rows[0].last_seen_ip == "192.168.0.22"

    print("MAC normalization test passed.")


def test_invalid_or_missing_mac_address_rejected() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "invalid-mac.sqlite3")

        for bad_value in ["", "   ", "not-a-mac", "AA:BB:CC:DD:EE", "GG:HH:II:JJ:KK:LL"]:
            try:
                repository.record_sighting(bad_value, None)
                raise AssertionError(f"expected rejection for {bad_value!r}")
            except ValueError:
                pass

        try:
            repository.assign(None, "PC-01", assigned_by_user_id=None)  # type: ignore[arg-type]
            raise AssertionError("expected rejection for None mac_address")
        except ValueError:
            pass

    print("Invalid/missing MAC address rejection test passed.")


# --- assign ------------------------------------------------------------------


def test_assign_binds_mac_to_pc_name() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "assign.sqlite3")

        repository.record_sighting("AA:BB:CC:DD:EE:05", "192.168.0.30")
        record = repository.assign("AA:BB:CC:DD:EE:05", "PC-02", assigned_by_user_id="owner-1")

        assert record.status == "assigned"
        assert record.pc_name == "PC-02"
        assert record.assigned_at is not None
        assert record.assigned_by_user_id == "owner-1"
        assert record.previous_pc_name is None

        assert repository.list_unclaimed() == []
        assert [row.mac_address for row in repository.list_assigned()] == ["AA:BB:CC:DD:EE:05"]
        assert repository.get_by_pc_name("PC-02").mac_address == "AA:BB:CC:DD:EE:05"

    print("assign() binds MAC to pc_name test passed.")


def test_assign_without_a_prior_sighting_still_works() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "assign-no-sighting.sqlite3")

        record = repository.assign("AA:BB:CC:DD:EE:06", "PC-03", assigned_by_user_id="owner-1")
        assert record.status == "assigned"
        assert record.pc_name == "PC-03"

    print("assign() without a prior sighting test passed.")


def test_assign_is_idempotent_for_the_same_binding() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "assign-idempotent.sqlite3")

        first = repository.assign("AA:BB:CC:DD:EE:07", "PC-04", assigned_by_user_id="owner-1")
        second = repository.assign("AA:BB:CC:DD:EE:07", "PC-04", assigned_by_user_id="owner-1")

        assert first.mac_address == second.mac_address
        assert first.pc_name == second.pc_name == "PC-04"
        assert len(repository.list_assigned()) == 1

    print("assign() idempotency test passed.")


def test_duplicate_pc_assignment_rejected() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "duplicate-pc.sqlite3")

        repository.assign("AA:BB:CC:DD:EE:08", "PC-05", assigned_by_user_id="owner-1")

        try:
            repository.assign("AA:BB:CC:DD:EE:09", "PC-05", assigned_by_user_id="owner-1")
            raise AssertionError("expected a 409 rejection")
        except HTTPException as error:
            assert error.status_code == 409

        # Rejection must not have disturbed the existing binding.
        assert repository.get_by_pc_name("PC-05").mac_address == "AA:BB:CC:DD:EE:08"
        second_mac = repository.get_by_mac("AA:BB:CC:DD:EE:09")
        assert second_mac is None or second_mac.status != "assigned"

    print("Duplicate pc_name assignment rejection test passed.")


def test_assign_mac_already_assigned_elsewhere_rejected() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "mac-elsewhere.sqlite3")

        repository.assign("AA:BB:CC:DD:EE:10", "PC-06", assigned_by_user_id="owner-1")

        try:
            repository.assign("AA:BB:CC:DD:EE:10", "PC-07", assigned_by_user_id="owner-1")
            raise AssertionError("expected a 409 rejection")
        except HTTPException as error:
            assert error.status_code == 409

        assert repository.get_by_mac("AA:BB:CC:DD:EE:10").pc_name == "PC-06"
        assert repository.get_by_pc_name("PC-07") is None

    print("MAC-already-assigned-elsewhere rejection test passed.")


# --- revoke --------------------------------------------------------------


def test_revoke_clears_binding_and_tracks_previous_pc_name() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "revoke.sqlite3")

        repository.assign("AA:BB:CC:DD:EE:11", "PC-08", assigned_by_user_id="owner-1")
        revoked = repository.revoke("AA:BB:CC:DD:EE:11")

        assert revoked.status == "revoked"
        assert revoked.pc_name is None
        assert revoked.previous_pc_name == "PC-08"
        assert repository.get_by_pc_name("PC-08") is None
        assert revoked.mac_address not in [row.mac_address for row in repository.list_unclaimed()]
        assert revoked.mac_address not in [row.mac_address for row in repository.list_assigned()]

    print("revoke() clears binding and tracks previous_pc_name test passed.")


def test_revoke_is_a_no_op_for_already_unclaimed_mac() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "revoke-noop.sqlite3")

        repository.record_sighting("AA:BB:CC:DD:EE:12", "192.168.0.40")
        record = repository.revoke("AA:BB:CC:DD:EE:12")
        assert record.status == "unclaimed"
        assert record.previous_pc_name is None

    print("revoke() no-op-for-unclaimed test passed.")


def test_revoke_unknown_mac_raises_404() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "revoke-unknown.sqlite3")

        try:
            repository.revoke("AA:BB:CC:DD:EE:13")
            raise AssertionError("expected a 404 rejection")
        except HTTPException as error:
            assert error.status_code == 404

    print("revoke() unknown-MAC 404 test passed.")


# --- reassign ------------------------------------------------------------


def test_reassign_moves_a_pc_name_from_one_mac_to_another() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "reassign-nic-swap.sqlite3")

        repository.assign("AA:BB:CC:DD:EE:14", "PC-09", assigned_by_user_id="owner-1")
        new_record = repository.reassign("AA:BB:CC:DD:EE:15", "PC-09", assigned_by_user_id="owner-1")

        assert new_record.status == "assigned"
        assert new_record.pc_name == "PC-09"
        assert new_record.previous_pc_name is None  # this MAC never held anything before

        old_record = repository.get_by_mac("AA:BB:CC:DD:EE:14")
        assert old_record.status == "revoked"
        assert old_record.pc_name is None
        assert old_record.previous_pc_name == "PC-09"

        assert repository.get_by_pc_name("PC-09").mac_address == "AA:BB:CC:DD:EE:15"

    print("reassign() NIC-swap test passed.")


def test_reassign_same_mac_to_a_different_pc_name() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "reassign-move.sqlite3")

        repository.assign("AA:BB:CC:DD:EE:16", "PC-10", assigned_by_user_id="owner-1")
        moved = repository.reassign("AA:BB:CC:DD:EE:16", "PC-11", assigned_by_user_id="owner-1")

        assert moved.pc_name == "PC-11"
        assert moved.previous_pc_name == "PC-10"
        assert repository.get_by_pc_name("PC-10") is None
        assert repository.get_by_pc_name("PC-11").mac_address == "AA:BB:CC:DD:EE:16"

    print("reassign() same-MAC-different-station test passed.")


def test_reassign_is_idempotent_for_the_same_binding() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        repository = fresh_env(Path(temp_directory) / "reassign-idempotent.sqlite3")

        repository.assign("AA:BB:CC:DD:EE:17", "PC-12", assigned_by_user_id="owner-1")
        repeated = repository.reassign("AA:BB:CC:DD:EE:17", "PC-12", assigned_by_user_id="owner-1")

        assert repeated.pc_name == "PC-12"
        assert repeated.previous_pc_name is None
        assert len(repository.list_assigned()) == 1

    print("reassign() idempotency test passed.")


# --- pc_layouts / pc_metadata isolation ----------------------------------


def test_station_assignment_operations_never_touch_pc_layouts_or_pc_metadata() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        database_path = Path(temp_directory) / "isolation.sqlite3"
        repository = fresh_env(database_path)

        from server.database import get_connection
        from server.pc_layout_repository import PCLayoutRepository
        from server.schemas import PCLayoutUpdate

        layout_repository = PCLayoutRepository()
        layout_repository.upsert_layout(
            "PC-13",
            PCLayoutUpdate(
                display_name="PC 13",
                area_label="MAMA",
                layout_order=130,
                is_vip=True,
                is_enabled=True,
                mac_address="AA:BB:CC:DD:EE:18",
                income_group_id=None,
            ),
        )
        before_layout = layout_repository.get_layout("PC-13")
        with get_connection() as connection:
            before_metadata = dict(
                connection.execute("SELECT * FROM pc_metadata WHERE pc_name = 'PC-13'").fetchone()
            )

        # Station-assignment traffic for an unrelated MAC/PC binding.
        repository.record_sighting("AA:BB:CC:DD:EE:19", "192.168.0.60")
        repository.assign("AA:BB:CC:DD:EE:19", "PC-14", assigned_by_user_id="owner-1")
        repository.revoke("AA:BB:CC:DD:EE:19")
        repository.reassign("AA:BB:CC:DD:EE:19", "PC-14", assigned_by_user_id="owner-1")

        after_layout = layout_repository.get_layout("PC-13")
        with get_connection() as connection:
            after_metadata = dict(
                connection.execute("SELECT * FROM pc_metadata WHERE pc_name = 'PC-13'").fetchone()
            )

        assert after_layout == before_layout
        assert after_layout.is_vip is True
        assert after_layout.mac_address == "AA:BB:CC:DD:EE:18"
        assert after_metadata == before_metadata

    print("pc_layouts/pc_metadata isolation test passed.")


def main() -> None:
    tests = [
        test_fresh_database_creates_station_assignments_table,
        test_migration_is_idempotent_and_does_not_touch_pc_layouts_or_pc_metadata,
        test_record_sighting_inserts_unclaimed,
        test_record_sighting_updates_last_seen_without_disturbing_first_seen,
        test_record_sighting_does_not_disturb_an_assigned_station,
        test_mac_normalization_treats_equivalent_formats_as_the_same_station,
        test_invalid_or_missing_mac_address_rejected,
        test_assign_binds_mac_to_pc_name,
        test_assign_without_a_prior_sighting_still_works,
        test_assign_is_idempotent_for_the_same_binding,
        test_duplicate_pc_assignment_rejected,
        test_assign_mac_already_assigned_elsewhere_rejected,
        test_revoke_clears_binding_and_tracks_previous_pc_name,
        test_revoke_is_a_no_op_for_already_unclaimed_mac,
        test_revoke_unknown_mac_raises_404,
        test_reassign_moves_a_pc_name_from_one_mac_to_another,
        test_reassign_same_mac_to_a_different_pc_name,
        test_reassign_is_idempotent_for_the_same_binding,
        test_station_assignment_operations_never_touch_pc_layouts_or_pc_metadata,
    ]
    for test in tests:
        test()
    print("station_assignments schema/repository smoke tests passed.")


if __name__ == "__main__":
    main()
