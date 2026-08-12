import sqlite3
from datetime import datetime
from typing import Any

from fastapi import HTTPException

from server.database import get_connection
from server.schemas import StationAssignmentRecord, normalize_mac_address
from server.time_utils import current_utc_time


def row_to_station_assignment(row: Any) -> StationAssignmentRecord:
    return StationAssignmentRecord(
        mac_address=row["mac_address"],
        pc_name=row["pc_name"],
        status=row["status"],
        first_seen_at=datetime.fromisoformat(row["first_seen_at"]),
        last_seen_at=datetime.fromisoformat(row["last_seen_at"]),
        last_seen_ip=row["last_seen_ip"],
        assigned_at=datetime.fromisoformat(row["assigned_at"]) if row["assigned_at"] else None,
        assigned_by_user_id=row["assigned_by_user_id"],
        previous_pc_name=row["previous_pc_name"],
    )


def require_mac_address(mac_address: str) -> str:
    normalized = normalize_mac_address(mac_address)
    if normalized is None:
        raise ValueError("mac_address is required")
    return normalized


def require_pc_name(pc_name: str) -> str:
    cleaned = pc_name.strip()
    if not cleaned:
        raise ValueError("pc_name is required")
    return cleaned


class StationAssignmentRepository:
    """Server-owned mac_address -> pc_name identity binding.

    Deliberately independent of pc_layout_repository/PCMetadataRepository:
    those stay keyed by pc_name and untouched by this repository. This is
    the only write path for "which physical machine is this station."
    """

    def get_by_mac(self, mac_address: str) -> StationAssignmentRecord | None:
        normalized_mac = normalize_mac_address(mac_address)
        if normalized_mac is None:
            return None
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM station_assignments WHERE mac_address = ?",
                (normalized_mac,),
            ).fetchone()
        return row_to_station_assignment(row) if row is not None else None

    def get_by_pc_name(self, pc_name: str) -> StationAssignmentRecord | None:
        cleaned_pc_name = pc_name.strip()
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM station_assignments WHERE pc_name = ? AND status = 'assigned'",
                (cleaned_pc_name,),
            ).fetchone()
        return row_to_station_assignment(row) if row is not None else None

    def list_unclaimed(self) -> list[StationAssignmentRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM station_assignments WHERE status = 'unclaimed' ORDER BY first_seen_at ASC"
            ).fetchall()
        return [row_to_station_assignment(row) for row in rows]

    def list_assigned(self) -> list[StationAssignmentRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM station_assignments WHERE status = 'assigned' ORDER BY pc_name ASC"
            ).fetchall()
        return [row_to_station_assignment(row) for row in rows]

    def list_all(self) -> list[StationAssignmentRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM station_assignments ORDER BY last_seen_at DESC"
            ).fetchall()
        return [row_to_station_assignment(row) for row in rows]

    def record_sighting(self, mac_address: str, ip_address: str | None) -> StationAssignmentRecord:
        """Passive bookkeeping only: refreshes first/last-seen and last-seen
        IP for a MAC. Never touches status, pc_name, or assigned_* fields --
        safe to call regardless of current assignment state."""
        normalized_mac = require_mac_address(mac_address)
        now = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO station_assignments (
                    mac_address, pc_name, status, first_seen_at, last_seen_at, last_seen_ip
                )
                VALUES (?, NULL, 'unclaimed', ?, ?, ?)
                ON CONFLICT(mac_address) DO UPDATE SET
                    last_seen_at = excluded.last_seen_at,
                    last_seen_ip = excluded.last_seen_ip
                """,
                (normalized_mac, now.isoformat(), now.isoformat(), ip_address),
            )
            row = connection.execute(
                "SELECT * FROM station_assignments WHERE mac_address = ?",
                (normalized_mac,),
            ).fetchone()
        assert row is not None
        return row_to_station_assignment(row)

    def assign(
        self,
        mac_address: str,
        pc_name: str,
        assigned_by_user_id: str | None,
    ) -> StationAssignmentRecord:
        """Bind an unclaimed or revoked MAC to pc_name. Rejects (409) a MAC
        that is already actively assigned elsewhere, and rejects (409) a
        pc_name that is already actively held by a different MAC -- both
        cases must go through reassign() instead, as a deliberate action."""
        normalized_mac = require_mac_address(mac_address)
        cleaned_pc_name = require_pc_name(pc_name)
        now = current_utc_time()

        with get_connection() as connection:
            existing_mac_row = connection.execute(
                "SELECT * FROM station_assignments WHERE mac_address = ?",
                (normalized_mac,),
            ).fetchone()

            if existing_mac_row is not None and existing_mac_row["status"] == "assigned":
                if existing_mac_row["pc_name"] == cleaned_pc_name:
                    return row_to_station_assignment(existing_mac_row)
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{normalized_mac} is already assigned to "
                        f"{existing_mac_row['pc_name']}. Reassign it explicitly instead."
                    ),
                )

            conflicting_pc_row = connection.execute(
                "SELECT mac_address FROM station_assignments WHERE pc_name = ? AND status = 'assigned'",
                (cleaned_pc_name,),
            ).fetchone()
            if conflicting_pc_row is not None and conflicting_pc_row["mac_address"] != normalized_mac:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"{cleaned_pc_name} is already assigned to "
                        f"{conflicting_pc_row['mac_address']}. Reassign it explicitly instead."
                    ),
                )

            try:
                connection.execute(
                    """
                    INSERT INTO station_assignments (
                        mac_address, pc_name, status, first_seen_at, last_seen_at,
                        last_seen_ip, assigned_at, assigned_by_user_id, previous_pc_name
                    )
                    VALUES (?, ?, 'assigned', ?, ?, NULL, ?, ?, NULL)
                    ON CONFLICT(mac_address) DO UPDATE SET
                        pc_name = excluded.pc_name,
                        status = 'assigned',
                        assigned_at = excluded.assigned_at,
                        assigned_by_user_id = excluded.assigned_by_user_id
                    """,
                    (
                        normalized_mac,
                        cleaned_pc_name,
                        now.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                        assigned_by_user_id,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(
                    status_code=409,
                    detail=f"{cleaned_pc_name} was just claimed by another station.",
                ) from error

            row = connection.execute(
                "SELECT * FROM station_assignments WHERE mac_address = ?",
                (normalized_mac,),
            ).fetchone()
        assert row is not None
        return row_to_station_assignment(row)

    def reassign(
        self,
        mac_address: str,
        pc_name: str,
        assigned_by_user_id: str | None,
    ) -> StationAssignmentRecord:
        """Force-bind mac_address to pc_name: revokes whatever OTHER MAC
        currently holds pc_name (NIC-swap / re-pointing a station at new
        hardware), and revokes whatever OTHER pc_name this MAC previously
        held, recording both as previous_pc_name history."""
        normalized_mac = require_mac_address(mac_address)
        cleaned_pc_name = require_pc_name(pc_name)
        now = current_utc_time()

        with get_connection() as connection:
            other_holder = connection.execute(
                "SELECT mac_address FROM station_assignments WHERE pc_name = ? AND status = 'assigned'",
                (cleaned_pc_name,),
            ).fetchone()
            if other_holder is not None and other_holder["mac_address"] != normalized_mac:
                connection.execute(
                    """
                    UPDATE station_assignments
                    SET status = 'revoked', pc_name = NULL, previous_pc_name = ?
                    WHERE mac_address = ?
                    """,
                    (cleaned_pc_name, other_holder["mac_address"]),
                )

            existing_mac_row = connection.execute(
                "SELECT * FROM station_assignments WHERE mac_address = ?",
                (normalized_mac,),
            ).fetchone()
            if (
                existing_mac_row is not None
                and existing_mac_row["status"] == "assigned"
                and existing_mac_row["pc_name"] != cleaned_pc_name
            ):
                previous_pc_name_for_this_mac = existing_mac_row["pc_name"]
            elif existing_mac_row is not None:
                previous_pc_name_for_this_mac = existing_mac_row["previous_pc_name"]
            else:
                previous_pc_name_for_this_mac = None

            try:
                connection.execute(
                    """
                    INSERT INTO station_assignments (
                        mac_address, pc_name, status, first_seen_at, last_seen_at,
                        last_seen_ip, assigned_at, assigned_by_user_id, previous_pc_name
                    )
                    VALUES (?, ?, 'assigned', ?, ?, NULL, ?, ?, ?)
                    ON CONFLICT(mac_address) DO UPDATE SET
                        pc_name = excluded.pc_name,
                        status = 'assigned',
                        assigned_at = excluded.assigned_at,
                        assigned_by_user_id = excluded.assigned_by_user_id,
                        previous_pc_name = excluded.previous_pc_name
                    """,
                    (
                        normalized_mac,
                        cleaned_pc_name,
                        now.isoformat(),
                        now.isoformat(),
                        now.isoformat(),
                        assigned_by_user_id,
                        previous_pc_name_for_this_mac,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(
                    status_code=409,
                    detail=f"{cleaned_pc_name} was just claimed by another station.",
                ) from error

            row = connection.execute(
                "SELECT * FROM station_assignments WHERE mac_address = ?",
                (normalized_mac,),
            ).fetchone()
        assert row is not None
        return row_to_station_assignment(row)

    def revoke(self, mac_address: str) -> StationAssignmentRecord:
        """Explicitly revoke a MAC's current assignment without binding it
        to anything new. No-op (returns the existing record) if the MAC is
        already unclaimed/revoked or has never been seen -- raises 404 only
        when the MAC has genuinely never been recorded."""
        normalized_mac = require_mac_address(mac_address)

        with get_connection() as connection:
            existing = connection.execute(
                "SELECT * FROM station_assignments WHERE mac_address = ?",
                (normalized_mac,),
            ).fetchone()
            if existing is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No station assignment found for {normalized_mac}",
                )
            if existing["status"] != "assigned":
                return row_to_station_assignment(existing)

            connection.execute(
                """
                UPDATE station_assignments
                SET status = 'revoked', pc_name = NULL, previous_pc_name = ?
                WHERE mac_address = ?
                """,
                (existing["pc_name"], normalized_mac),
            )
            row = connection.execute(
                "SELECT * FROM station_assignments WHERE mac_address = ?",
                (normalized_mac,),
            ).fetchone()
        assert row is not None
        return row_to_station_assignment(row)
