from datetime import datetime
from decimal import Decimal
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
        is_active=bool(row["is_active"]),
        sort_order=row["sort_order"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


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

    def create_rate(self, request: RateProfileCreate) -> RateProfile:
        now = current_utc_time()
        rate = RateProfile(
            rate_id=str(uuid4()),
            name=request.name.strip(),
            duration_seconds=request.duration_seconds,
            price=request.price,
            is_active=request.is_active,
            sort_order=request.sort_order,
            created_at=now,
            updated_at=now,
        )

        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO rate_profiles (
                    rate_id,
                    name,
                    duration_seconds,
                    price,
                    is_active,
                    sort_order,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rate.rate_id,
                    rate.name,
                    rate.duration_seconds,
                    decimal_to_storage(rate.price),
                    1 if rate.is_active else 0,
                    rate.sort_order,
                    rate.created_at.isoformat(),
                    rate.updated_at.isoformat(),
                ),
            )

        return rate

    def update_rate(self, rate_id: str, request: RateProfileUpdate) -> RateProfile | None:
        existing = self.get_rate(rate_id)
        if existing is None:
            return None

        updated = existing.model_copy(
            update={
                "name": request.name.strip(),
                "duration_seconds": request.duration_seconds,
                "price": request.price,
                "sort_order": request.sort_order,
                "updated_at": current_utc_time(),
            }
        )

        with get_connection() as connection:
            connection.execute(
                """
                UPDATE rate_profiles
                SET name = ?, duration_seconds = ?, price = ?, sort_order = ?, updated_at = ?
                WHERE rate_id = ?
                """,
                (
                    updated.name,
                    updated.duration_seconds,
                    decimal_to_storage(updated.price),
                    updated.sort_order,
                    updated.updated_at.isoformat(),
                    updated.rate_id,
                ),
            )

        return updated

    def set_active(self, rate_id: str, is_active: bool) -> RateProfile | None:
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

        return updated
