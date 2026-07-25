from datetime import datetime
from decimal import Decimal
import json
from typing import Any
from uuid import uuid4

from server.database import get_connection
from server.schemas import RateProfile, RateProfileCreate, RateProfileUpdate
from server.time_utils import current_utc_time


def decimal_to_storage(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def row_to_rate(row: Any) -> RateProfile:
    return RateProfile(
        rate_id=row["rate_id"],
        name=row["name"],
        duration_seconds=row["duration_seconds"],
        price=Decimal(row["price"]),
        rate_type=row["rate_type"],
        is_active=bool(row["is_active"]),
        sort_order=row["sort_order"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def rate_to_audit_dict(rate: RateProfile | None) -> dict[str, object] | None:
    if rate is None:
        return None
    return {
        "rate_id": rate.rate_id,
        "name": rate.name,
        "duration_seconds": rate.duration_seconds,
        "price": decimal_to_storage(rate.price),
        "rate_type": rate.rate_type,
        "is_active": rate.is_active,
        "sort_order": rate.sort_order,
    }


class RateRepository:
    """SQLite persistence boundary for editable duration-and-price packages."""

    def list_rates(self, include_inactive: bool = False) -> list[RateProfile]:
        query = "SELECT * FROM rate_profiles"
        parameters: tuple[object, ...] = ()

        if not include_inactive:
            query += " WHERE is_active = 1"

        query += " ORDER BY sort_order ASC, name ASC"

        with get_connection() as connection:
            rows = connection.execute(query, parameters).fetchall()

        return [row_to_rate(row) for row in rows]

    def get_rate(self, rate_id: str) -> RateProfile | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM rate_profiles WHERE rate_id = ?",
                (rate_id,),
            ).fetchone()

        return row_to_rate(row) if row is not None else None

    def get_active_metered_rate(self) -> RateProfile | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM rate_profiles
                WHERE rate_type = 'metered' AND is_active = 1
                ORDER BY updated_at DESC
                LIMIT 1
                """
            ).fetchone()

        return row_to_rate(row) if row is not None else None

    def deactivate_other_metered_rates(
        self,
        connection: Any,
        active_rate_id: str,
        updated_at: datetime,
    ) -> None:
        connection.execute(
            """
            UPDATE rate_profiles
            SET is_active = 0, updated_at = ?
            WHERE rate_type = 'metered' AND rate_id != ? AND is_active = 1
            """,
            (updated_at.isoformat(), active_rate_id),
        )

    def add_audit_event(
        self,
        rate_id: str,
        action: str,
        old_values: dict[str, object] | None,
        new_values: dict[str, object] | None,
        performed_by_user_id: str | None,
    ) -> None:
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO rate_audit_events (
                    event_id,
                    rate_id,
                    action,
                    old_values,
                    new_values,
                    performed_by_user_id,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    rate_id,
                    action,
                    json.dumps(old_values) if old_values is not None else None,
                    json.dumps(new_values) if new_values is not None else None,
                    performed_by_user_id,
                    current_utc_time().isoformat(),
                ),
            )

    def create_rate(
        self,
        request: RateProfileCreate,
        performed_by_user_id: str | None = None,
    ) -> RateProfile:
        now = current_utc_time()
        rate = RateProfile(
            rate_id=str(uuid4()),
            name=request.name.strip(),
            duration_seconds=request.duration_seconds,
            price=request.price,
            rate_type=request.rate_type,
            is_active=request.is_active,
            sort_order=request.sort_order,
            created_at=now,
            updated_at=now,
        )

        with get_connection() as connection:
            if rate.rate_type == "metered" and rate.is_active:
                self.deactivate_other_metered_rates(
                    connection,
                    rate.rate_id,
                    rate.updated_at,
                )
            connection.execute(
                """
                INSERT INTO rate_profiles (
                    rate_id,
                    name,
                    duration_seconds,
                    price,
                    rate_type,
                    is_active,
                    sort_order,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rate.rate_id,
                    rate.name,
                    rate.duration_seconds,
                    decimal_to_storage(rate.price),
                    rate.rate_type,
                    1 if rate.is_active else 0,
                    rate.sort_order,
                    rate.created_at.isoformat(),
                    rate.updated_at.isoformat(),
                ),
            )

        self.add_audit_event(
            rate.rate_id,
            "created",
            None,
            rate_to_audit_dict(rate),
            performed_by_user_id,
        )
        return rate

    def update_rate(
        self,
        rate_id: str,
        request: RateProfileUpdate,
        performed_by_user_id: str | None = None,
    ) -> RateProfile | None:
        existing = self.get_rate(rate_id)
        if existing is None:
            return None

        updated = existing.model_copy(
            update={
                "name": request.name.strip(),
                "duration_seconds": request.duration_seconds,
                "price": request.price,
                "rate_type": request.rate_type,
                "sort_order": request.sort_order,
                "updated_at": current_utc_time(),
            }
        )

        with get_connection() as connection:
            if updated.rate_type == "metered" and updated.is_active:
                self.deactivate_other_metered_rates(
                    connection,
                    updated.rate_id,
                    updated.updated_at,
                )
            connection.execute(
                """
                UPDATE rate_profiles
                SET name = ?, duration_seconds = ?, price = ?, rate_type = ?, sort_order = ?, updated_at = ?
                WHERE rate_id = ?
                """,
                (
                    updated.name,
                    updated.duration_seconds,
                    decimal_to_storage(updated.price),
                    updated.rate_type,
                    updated.sort_order,
                    updated.updated_at.isoformat(),
                    updated.rate_id,
                ),
            )

        self.add_audit_event(
            updated.rate_id,
            "updated",
            rate_to_audit_dict(existing),
            rate_to_audit_dict(updated),
            performed_by_user_id,
        )
        return updated

    def set_active(
        self,
        rate_id: str,
        is_active: bool,
        performed_by_user_id: str | None = None,
    ) -> RateProfile | None:
        existing = self.get_rate(rate_id)
        if existing is None:
            return None

        updated = existing.model_copy(
            update={
                "is_active": is_active,
                "updated_at": current_utc_time(),
            }
        )

        with get_connection() as connection:
            if updated.rate_type == "metered" and updated.is_active:
                self.deactivate_other_metered_rates(
                    connection,
                    updated.rate_id,
                    updated.updated_at,
                )
            connection.execute(
                """
                UPDATE rate_profiles
                SET is_active = ?, updated_at = ?
                WHERE rate_id = ?
                """,
                (
                    1 if is_active else 0,
                    updated.updated_at.isoformat(),
                    updated.rate_id,
                ),
            )

        self.add_audit_event(
            updated.rate_id,
            "enabled" if updated.is_active else "disabled",
            rate_to_audit_dict(existing),
            rate_to_audit_dict(updated),
            performed_by_user_id,
        )
        return updated
