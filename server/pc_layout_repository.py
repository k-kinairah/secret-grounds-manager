from datetime import datetime
from typing import Any

from server.database import get_connection
from server.schemas import IncomeGroupRecord, OnDutyOperatorRecord, PCLayoutRecord, PCLayoutUpdate, UserPublic
from server.time_utils import current_utc_time


def row_to_layout(row: Any, mac_address: str | None = None) -> PCLayoutRecord:
    keys = row.keys()
    return PCLayoutRecord(
        pc_name=row["pc_name"],
        display_name=row["display_name"],
        area_label=row["area_label"],
        layout_order=row["layout_order"],
        is_vip=bool(row["is_vip"]),
        is_enabled=bool(row["is_enabled"]),
        mac_address=mac_address,
        income_group_id=row["income_group_id"] if "income_group_id" in keys else None,
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def row_to_income_group(row: Any) -> IncomeGroupRecord:
    return IncomeGroupRecord(
        income_group_id=row["income_group_id"],
        name=row["name"],
        is_active=bool(row["is_active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


class PCLayoutRepository:
    """Configurable cashier-facing physical PC layout."""

    def list_layouts(self, include_disabled: bool = False) -> list[PCLayoutRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT l.*, m.mac_address
                FROM pc_layouts l
                LEFT JOIN pc_metadata m ON m.pc_name = l.pc_name
                WHERE l.is_enabled = 1 OR ? = 1
                ORDER BY l.layout_order ASC, l.pc_name ASC
                """,
                (1 if include_disabled else 0,),
            ).fetchall()
        return [row_to_layout(row, row["mac_address"]) for row in rows]

    def get_layout(self, pc_name: str) -> PCLayoutRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT l.*, m.mac_address
                FROM pc_layouts l
                LEFT JOIN pc_metadata m ON m.pc_name = l.pc_name
                WHERE l.pc_name = ?
                """,
                (pc_name,),
            ).fetchone()
        return row_to_layout(row, row["mac_address"]) if row else None

    def upsert_layout(self, pc_name: str, request: PCLayoutUpdate) -> PCLayoutRecord:
        now = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO pc_layouts (
                    pc_name, display_name, area_label, layout_order,
                    is_vip, is_enabled, income_group_id, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(pc_name) DO UPDATE SET
                    display_name = excluded.display_name,
                    area_label = excluded.area_label,
                    layout_order = excluded.layout_order,
                    is_vip = excluded.is_vip,
                    is_enabled = excluded.is_enabled,
                    income_group_id = excluded.income_group_id,
                    updated_at = excluded.updated_at
                """,
                (
                    pc_name,
                    request.display_name.strip(),
                    request.area_label.strip(),
                    request.layout_order,
                    1 if request.is_vip else 0,
                    1 if request.is_enabled else 0,
                    request.income_group_id,
                    now.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO pc_metadata (pc_name, mac_address, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(pc_name) DO UPDATE SET
                    mac_address = excluded.mac_address,
                    updated_at = excluded.updated_at
                """,
                (pc_name, request.mac_address, now.isoformat()),
            )
        layout = self.get_layout(pc_name)
        if layout is None:
            raise RuntimeError("PC layout disappeared after save")
        return layout


class IncomeGroupRepository:
    """Read access to the shop's configured revenue-owner groups."""

    def get_income_group(self, income_group_id: str) -> IncomeGroupRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM income_groups WHERE income_group_id = ?",
                (income_group_id,),
            ).fetchone()
        return row_to_income_group(row) if row is not None else None

    def list_income_groups(self) -> list[IncomeGroupRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM income_groups ORDER BY name ASC"
            ).fetchall()
        return [row_to_income_group(row) for row in rows]


class OperatorStateRepository:
    """Current on-duty person selected inside an authenticated shop session."""

    def get_current(self, user_lookup) -> OnDutyOperatorRecord:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM operator_state WHERE state_id = 'current'"
            ).fetchone()
        if row is None or row["operator_user_id"] is None:
            return OnDutyOperatorRecord(operator=None)

        user = user_lookup(row["operator_user_id"])
        operator = (
            UserPublic(
                user_id=user.user_id,
                username=user.username,
                display_name=user.display_name,
                role=user.role,
            )
            if user is not None
            else None
        )
        return OnDutyOperatorRecord(
            operator=operator,
            selected_by_user_id=row["selected_by_user_id"],
            selected_at=(
                datetime.fromisoformat(row["selected_at"])
                if row["selected_at"] is not None
                else None
            ),
        )

    def set_current(
        self,
        operator_user_id: str,
        selected_by_user_id: str | None,
    ) -> None:
        now = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO operator_state (
                    state_id,
                    operator_user_id,
                    selected_by_user_id,
                    selected_at
                )
                VALUES ('current', ?, ?, ?)
                ON CONFLICT(state_id) DO UPDATE SET
                    operator_user_id = excluded.operator_user_id,
                    selected_by_user_id = excluded.selected_by_user_id,
                    selected_at = excluded.selected_at
                """,
                (operator_user_id, selected_by_user_id, now.isoformat()),
            )

    def clear_if_current(self, operator_user_id: str) -> bool:
        """Resets the singleton to "nobody on duty" (operator_user_id =
        NULL -- the same convention get_current already treats as unset)
        but only if it still points at operator_user_id. A single
        conditional UPDATE, not read-then-write, so this can never race
        with or clobber a different operator who logged in in the
        meantime. selected_by_user_id/selected_at are left as the last
        selection's audit breadcrumb; only get_current's operator_user_id
        check governs "who is on duty". Returns whether a row was
        actually cleared."""
        with get_connection() as connection:
            cursor = connection.execute(
                """
                UPDATE operator_state
                SET operator_user_id = NULL
                WHERE state_id = 'current' AND operator_user_id = ?
                """,
                (operator_user_id,),
            )
            return cursor.rowcount > 0
