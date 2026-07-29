import json
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from server.billing_service import money
from server.database import get_connection
from server.payment_repository import PaymentRepository
from server.passwords import hash_password, verify_password
from server.schemas import (
    MemberCreateRequest,
    MemberPurchaseRequest,
    MemberPurchaseResponse,
    MemberRecord,
    MembershipProductCreate,
    MembershipProductRecord,
    MembershipProductUpdate,
    MemberStatusUpdate,
    MemberTimeTransactionRecord,
    MemberUpdateRequest,
    PaymentCreateRequest,
    PaymentRecord,
    UserRecord,
)
from server.shift_repository import ShiftRepository
from server.time_utils import current_utc_time


def decimal_to_storage(value: Decimal | None) -> str | None:
    return str(money(value)) if value is not None else None


def normalize_code(value: str) -> str:
    return value.strip().upper()


def normalize_username(value: str) -> str:
    return value.strip().casefold()


def clean_display_name(value: str | None) -> str | None:
    display_name = (value or "").strip()
    return display_name or None


def display_name_for(username: str | None, display_name: str | None, member_code: str) -> str:
    return display_name or username or member_code


def row_to_member(row: Any) -> MemberRecord:
    keys = set(row.keys())
    username = row["username"] if "username" in keys else None
    stored_display_name = row["full_name"].strip() if row["full_name"] else None
    has_pin = bool(row["pin_hash"]) if "pin_hash" in keys else False
    requires_setup = (
        bool(row["requires_account_setup"])
        if "requires_account_setup" in keys
        else not bool(username and has_pin)
    )
    return MemberRecord(
        member_id=row["member_id"],
        member_code=row["member_code"],
        username=username,
        display_name=stored_display_name,
        full_name=display_name_for(username, stored_display_name, row["member_code"]),
        phone_number=row["phone_number"],
        has_pin=has_pin,
        requires_account_setup=requires_setup or not bool(username and has_pin),
        status=row["status"],
        time_balance_seconds=row["time_balance_seconds"],
        joined_at=datetime.fromisoformat(row["joined_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        created_by_user_id=row["created_by_user_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def row_to_product(row: Any) -> MembershipProductRecord:
    return MembershipProductRecord(
        product_id=row["product_id"],
        name=row["name"],
        price=Decimal(row["price"]),
        time_seconds=row["time_seconds"],
        product_type=row["product_type"],
        is_active=bool(row["is_active"]),
        sort_order=row["sort_order"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def row_to_transaction(row: Any) -> MemberTimeTransactionRecord:
    return MemberTimeTransactionRecord(
        transaction_id=row["transaction_id"],
        member_id=row["member_id"],
        transaction_type=row["transaction_type"],
        seconds_delta=row["seconds_delta"],
        balance_before_seconds=row["balance_before_seconds"],
        balance_after_seconds=row["balance_after_seconds"],
        amount_paid=Decimal(row["amount_paid"]) if row["amount_paid"] is not None else None,
        related_session_id=row["related_session_id"],
        related_payment_id=row["related_payment_id"],
        notes=row["notes"],
        performed_by_user_id=row["performed_by_user_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class MemberRepository:
    """Digital membership records and balance ledger operations."""

    def __init__(
        self,
        payment_repository: PaymentRepository,
        shift_repository: ShiftRepository,
    ) -> None:
        self.payment_repository = payment_repository
        self.shift_repository = shift_repository

    def list_members(self, query: str | None = None) -> list[MemberRecord]:
        search = f"%{(query or '').strip()}%"
        with get_connection() as connection:
            if query and query.strip():
                rows = connection.execute(
                    """
                    SELECT * FROM members
                    WHERE member_code LIKE ?
                       OR username LIKE ?
                       OR username_normalized LIKE ?
                       OR full_name LIKE ?
                       OR phone_number LIKE ?
                    ORDER BY COALESCE(NULLIF(full_name, ''), username, member_code) ASC
                    """,
                    (search, search, search.casefold(), search, search),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM members ORDER BY COALESCE(NULLIF(full_name, ''), username, member_code) ASC"
                ).fetchall()
        return [row_to_member(row) for row in rows]

    def get_member(self, member_id: str) -> MemberRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM members WHERE member_id = ?",
                (member_id,),
            ).fetchone()
        return row_to_member(row) if row else None

    def get_member_by_username(self, username: str) -> MemberRecord | None:
        username_normalized = normalize_username(username)
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM members WHERE username_normalized = ?",
                (username_normalized,),
            ).fetchone()
        return row_to_member(row) if row else None

    def require_usable_member(self, member_id: str) -> MemberRecord:
        member = self.get_member(member_id)
        if member is None:
            raise HTTPException(status_code=404, detail="Member not found")
        return self.require_member_ready(member)

    def require_usable_member_by_username(self, username: str) -> MemberRecord:
        member = self.get_member_by_username(username)
        if member is None:
            raise HTTPException(status_code=403, detail="Invalid member username or PIN")
        return self.require_member_ready(member)

    def require_member_ready(self, member: MemberRecord) -> MemberRecord:
        if member.status != "active":
            raise HTTPException(status_code=409, detail="Member is inactive")
        if member.expires_at is not None and member.expires_at <= current_utc_time():
            raise HTTPException(status_code=409, detail="Member is expired")
        if member.requires_account_setup or not member.username or not member.has_pin:
            raise HTTPException(status_code=409, detail="Member account setup is required")
        return member

    def generate_member_code(self, connection: sqlite3.Connection) -> str:
        while True:
            member_code = f"SG-{uuid4().hex[:8].upper()}"
            row = connection.execute(
                "SELECT 1 FROM members WHERE member_code = ?",
                (member_code,),
            ).fetchone()
            if row is None:
                return member_code

    def record_member_audit(
        self,
        connection: sqlite3.Connection,
        event_type: str,
        actor: UserRecord | None,
        member: MemberRecord,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if actor is None:
            return
        safe_metadata = {
            "member_id": member.member_id,
            "member_code": member.member_code,
            "username": member.username,
            **(metadata or {}),
        }
        connection.execute(
            """
            INSERT INTO operator_audit_events (
                event_id,
                event_type,
                operator_user_id,
                operator_display_name,
                selected_by_user_id,
                created_at,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                event_type,
                actor.user_id,
                actor.display_name,
                None,
                current_utc_time().isoformat(),
                json.dumps(safe_metadata, sort_keys=True),
            ),
        )

    def create_member(
        self,
        request: MemberCreateRequest,
        actor: UserRecord | None,
    ) -> MemberRecord:
        now = current_utc_time()
        username = request.username.strip()
        username_normalized = normalize_username(username)
        display_name = clean_display_name(request.display_name)
        pin_hash = hash_password(request.pin)
        with get_connection() as connection:
            member = MemberRecord(
                member_id=str(uuid4()),
                member_code=self.generate_member_code(connection),
                username=username,
                display_name=display_name,
                full_name=display_name_for(username, display_name, ""),
                phone_number=(request.phone_number or "").strip() or None,
                has_pin=True,
                requires_account_setup=False,
                status="active",
                time_balance_seconds=0,
                joined_at=now,
                expires_at=request.expires_at,
                created_by_user_id=actor.user_id if actor else None,
                created_at=now,
                updated_at=now,
            )
            try:
                connection.execute(
                    """
                    INSERT INTO members (
                        member_id,
                        member_code,
                        username,
                        username_normalized,
                        full_name,
                        phone_number,
                        pin_hash,
                        requires_account_setup,
                        pin_failed_attempts,
                        pin_locked_until,
                        status,
                        time_balance_seconds,
                        joined_at,
                        expires_at,
                        created_by_user_id,
                        created_at,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        member.member_id,
                        member.member_code,
                        member.username,
                        username_normalized,
                        member.display_name or "",
                        member.phone_number,
                        pin_hash,
                        0,
                        0,
                        None,
                        member.status,
                        member.time_balance_seconds,
                        member.joined_at.isoformat(),
                        member.expires_at.isoformat() if member.expires_at else None,
                        member.created_by_user_id,
                        member.created_at.isoformat(),
                        member.updated_at.isoformat(),
                    ),
                )
                self.record_member_audit(connection, "member_created", actor, member)
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="Username already exists") from error
        return member

    def update_member(
        self,
        member_id: str,
        request: MemberUpdateRequest,
        actor: UserRecord | None = None,
    ) -> MemberRecord | None:
        existing = self.get_member(member_id)
        if existing is None:
            return None
        username = request.username.strip()
        username_normalized = normalize_username(username)
        display_name = clean_display_name(request.display_name)
        requires_setup = not bool(username and existing.has_pin)
        updated = existing.model_copy(
            update={
                "username": username,
                "display_name": display_name,
                "full_name": display_name_for(username, display_name, existing.member_code),
                "phone_number": (request.phone_number or "").strip() or None,
                "expires_at": request.expires_at,
                "requires_account_setup": requires_setup,
                "updated_at": current_utc_time(),
            }
        )
        with get_connection() as connection:
            try:
                connection.execute(
                    """
                    UPDATE members
                    SET username = ?,
                        username_normalized = ?,
                        full_name = ?,
                        phone_number = ?,
                        expires_at = ?,
                        requires_account_setup = ?,
                        updated_at = ?
                    WHERE member_id = ?
                    """,
                    (
                        updated.username,
                        username_normalized,
                        updated.display_name or "",
                        updated.phone_number,
                        updated.expires_at.isoformat() if updated.expires_at else None,
                        1 if requires_setup else 0,
                        updated.updated_at.isoformat(),
                        member_id,
                    ),
                )
                if normalize_username(existing.username or "") != username_normalized:
                    self.record_member_audit(
                        connection,
                        "member_username_changed",
                        actor,
                        updated,
                        {"previous_username": existing.username},
                    )
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="Username already exists") from error
        return updated

    def set_member_status(self, member_id: str, request: MemberStatusUpdate) -> MemberRecord | None:
        existing = self.get_member(member_id)
        if existing is None:
            return None
        updated_at = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                "UPDATE members SET status = ?, updated_at = ? WHERE member_id = ?",
                (request.status, updated_at.isoformat(), member_id),
            )
        return existing.model_copy(update={"status": request.status, "updated_at": updated_at})

    def set_member_pin(
        self,
        member_id: str,
        pin: str,
        actor: UserRecord | None = None,
    ) -> MemberRecord | None:
        existing = self.get_member(member_id)
        if existing is None:
            return None

        updated_at = current_utc_time()
        pin_hash = hash_password(pin)
        requires_setup = not bool(existing.username)
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE members
                SET pin_hash = ?,
                    requires_account_setup = ?,
                    pin_failed_attempts = 0,
                    pin_locked_until = NULL,
                    updated_at = ?
                WHERE member_id = ?
                """,
                (pin_hash, 1 if requires_setup else 0, updated_at.isoformat(), member_id),
            )
            updated = existing.model_copy(
                update={
                    "has_pin": True,
                    "requires_account_setup": requires_setup,
                    "updated_at": updated_at,
                }
            )
            self.record_member_audit(connection, "member_pin_reset", actor, updated)
        return updated

    def verify_member_pin(self, member_id: str, provided_pin: str | None) -> None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT pin_hash, requires_account_setup, pin_failed_attempts, pin_locked_until
                FROM members
                WHERE member_id = ?
                """,
                (member_id,),
            ).fetchone()

            pin_hash = row["pin_hash"] if row else None
            if row is None or not pin_hash or bool(row["requires_account_setup"]):
                raise HTTPException(status_code=403, detail="Invalid member username or PIN")
            now = current_utc_time()
            locked_until = datetime.fromisoformat(row["pin_locked_until"]) if row["pin_locked_until"] else None
            if locked_until is not None and locked_until > now:
                raise HTTPException(status_code=429, detail="Too many PIN attempts. Try again shortly.")
            if not provided_pin:
                self.record_failed_pin_attempt(connection, member_id, int(row["pin_failed_attempts"]))
                connection.commit()
                raise HTTPException(status_code=403, detail="Invalid member username or PIN")
            if not verify_password(provided_pin, pin_hash):
                self.record_failed_pin_attempt(connection, member_id, int(row["pin_failed_attempts"]))
                connection.commit()
                raise HTTPException(status_code=403, detail="Invalid member username or PIN")
            connection.execute(
                """
                UPDATE members
                SET pin_failed_attempts = 0,
                    pin_locked_until = NULL,
                    updated_at = ?
                WHERE member_id = ?
                """,
                (now.isoformat(), member_id),
            )

    def verify_member_pin_if_required(self, member_id: str, provided_pin: str | None) -> None:
        self.verify_member_pin(member_id, provided_pin)

    def record_failed_pin_attempt(
        self,
        connection: sqlite3.Connection,
        member_id: str,
        failed_attempts: int,
    ) -> None:
        now = current_utc_time()
        next_attempts = failed_attempts + 1
        locked_until = now + timedelta(minutes=5) if next_attempts >= 5 else None
        connection.execute(
            """
            UPDATE members
            SET pin_failed_attempts = ?,
                pin_locked_until = ?,
                updated_at = ?
            WHERE member_id = ?
            """,
            (
                next_attempts,
                locked_until.isoformat() if locked_until else None,
                now.isoformat(),
                member_id,
            ),
        )

    def list_products(self, include_inactive: bool = False) -> list[MembershipProductRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM membership_products
                WHERE is_active = 1 OR ? = 1
                ORDER BY sort_order ASC, name ASC
                """,
                (1 if include_inactive else 0,),
            ).fetchall()
        return [row_to_product(row) for row in rows]

    def get_product(self, product_id: str) -> MembershipProductRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM membership_products WHERE product_id = ?",
                (product_id,),
            ).fetchone()
        return row_to_product(row) if row else None

    def create_product(self, request: MembershipProductCreate) -> MembershipProductRecord:
        now = current_utc_time()
        product = MembershipProductRecord(
            product_id=str(uuid4()),
            name=request.name.strip(),
            price=request.price,
            time_seconds=request.time_seconds,
            product_type=request.product_type,
            is_active=request.is_active,
            sort_order=request.sort_order,
            created_at=now,
            updated_at=now,
        )
        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO membership_products (
                    product_id, name, price, time_seconds, product_type,
                    is_active, sort_order, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product.product_id,
                    product.name,
                    decimal_to_storage(product.price),
                    product.time_seconds,
                    product.product_type,
                    1 if product.is_active else 0,
                    product.sort_order,
                    product.created_at.isoformat(),
                    product.updated_at.isoformat(),
                ),
            )
        return product

    def update_product(
        self,
        product_id: str,
        request: MembershipProductUpdate,
    ) -> MembershipProductRecord | None:
        existing = self.get_product(product_id)
        if existing is None:
            return None
        updated_at = current_utc_time()
        with get_connection() as connection:
            connection.execute(
                """
                UPDATE membership_products
                SET name = ?, price = ?, time_seconds = ?, product_type = ?, sort_order = ?, updated_at = ?
                WHERE product_id = ?
                """,
                (
                    request.name.strip(),
                    decimal_to_storage(request.price),
                    request.time_seconds,
                    request.product_type,
                    request.sort_order,
                    updated_at.isoformat(),
                    product_id,
                ),
            )
        return self.get_product(product_id)

    def set_product_active(
        self,
        product_id: str,
        is_active: bool,
    ) -> MembershipProductRecord | None:
        existing = self.get_product(product_id)
        if existing is None:
            return None
        with get_connection() as connection:
            connection.execute(
                "UPDATE membership_products SET is_active = ?, updated_at = ? WHERE product_id = ?",
                (1 if is_active else 0, current_utc_time().isoformat(), product_id),
            )
        return self.get_product(product_id)

    def list_ledger(self, member_id: str) -> list[MemberTimeTransactionRecord]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM member_time_transactions
                WHERE member_id = ?
                ORDER BY created_at ASC
                """,
                (member_id,),
            ).fetchall()
        return [row_to_transaction(row) for row in rows]

    def apply_balance_delta(
        self,
        member_id: str,
        transaction_type: str,
        seconds_delta: int,
        actor_user_id: str | None,
        amount_paid: Decimal | None = None,
        related_session_id: str | None = None,
        related_payment_id: str | None = None,
        notes: str | None = None,
    ) -> MemberTimeTransactionRecord:
        zero_delta_types = {
            "session_reservation_created",
            "session_reservation_released",
        }
        if seconds_delta == 0 and transaction_type not in zero_delta_types:
            raise HTTPException(status_code=400, detail="Balance change cannot be zero")
        now = current_utc_time()
        transaction_id = str(uuid4())
        with get_connection() as connection:
            row = connection.execute(
                "SELECT time_balance_seconds FROM members WHERE member_id = ?",
                (member_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Member not found")
            before = int(row["time_balance_seconds"])
            after = before + seconds_delta
            if after < 0:
                raise HTTPException(status_code=409, detail="Insufficient member time balance")
            connection.execute(
                """
                UPDATE members
                SET time_balance_seconds = ?, updated_at = ?
                WHERE member_id = ?
                """,
                (after, now.isoformat(), member_id),
            )
            connection.execute(
                """
                INSERT INTO member_time_transactions (
                    transaction_id,
                    member_id,
                    transaction_type,
                    seconds_delta,
                    balance_before_seconds,
                    balance_after_seconds,
                    amount_paid,
                    related_session_id,
                    related_payment_id,
                    notes,
                    performed_by_user_id,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    member_id,
                    transaction_type,
                    seconds_delta,
                    before,
                    after,
                    decimal_to_storage(amount_paid),
                    related_session_id,
                    related_payment_id,
                    notes,
                    actor_user_id,
                    now.isoformat(),
                ),
            )
        transaction = self.list_ledger(member_id)[-1]
        return transaction

    def has_active_reservation(self, member_id: str) -> bool:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT 1 FROM member_time_reservations
                WHERE member_id = ? AND status = 'active'
                LIMIT 1
                """,
                (member_id,),
            ).fetchone()
        return row is not None

    def create_reservation(
        self,
        member: MemberRecord,
        session_id: str,
        reserved_seconds: int,
        actor_user_id: str | None,
    ) -> None:
        if reserved_seconds <= 0:
            raise HTTPException(status_code=400, detail="Reserved time must be positive")
        if member.time_balance_seconds < reserved_seconds:
            raise HTTPException(status_code=409, detail="Insufficient member time balance")
        now = current_utc_time()
        with get_connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO member_time_reservations (
                        reservation_id,
                        member_id,
                        session_id,
                        reserved_seconds,
                        consumed_seconds,
                        status,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, 0, 'active', ?)
                    """,
                    (str(uuid4()), member.member_id, session_id, reserved_seconds, now.isoformat()),
                )
            except sqlite3.IntegrityError as error:
                raise HTTPException(
                    status_code=409,
                    detail="Member already has an active reserved session",
                ) from error
        self.apply_balance_delta(
            member.member_id,
            "session_reservation_created",
            0,
            actor_user_id,
            related_session_id=session_id,
            notes=f"Reserved up to {reserved_seconds} seconds",
        )

    def extend_reservation(
        self,
        member: MemberRecord,
        session_id: str,
        additional_seconds: int,
        actor_user_id: str | None,
    ) -> int:
        if additional_seconds <= 0:
            raise HTTPException(status_code=400, detail="Additional reserved time must be positive")
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM member_time_reservations
                WHERE session_id = ? AND member_id = ? AND status = 'active'
                """,
                (session_id, member.member_id),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Active member reservation not found")
            next_reserved = row["reserved_seconds"] + additional_seconds
            if member.time_balance_seconds < next_reserved:
                raise HTTPException(status_code=409, detail="Insufficient member time balance")
            connection.execute(
                """
                UPDATE member_time_reservations
                SET reserved_seconds = ?
                WHERE reservation_id = ?
                """,
                (next_reserved, row["reservation_id"]),
            )
        self.apply_balance_delta(
            member.member_id,
            "session_reservation_created",
            0,
            actor_user_id,
            related_session_id=session_id,
            notes=f"Extended reservation by {additional_seconds} seconds",
        )
        return next_reserved

    def release_reservation(
        self,
        member_id: str,
        session_id: str,
        actor_user_id: str | None,
        notes: str,
    ) -> None:
        finalized_at = current_utc_time()
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM member_time_reservations
                WHERE member_id = ? AND session_id = ? AND status = 'active'
                """,
                (member_id, session_id),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                UPDATE member_time_reservations
                SET status = 'released', finalized_at = ?
                WHERE reservation_id = ?
                """,
                (finalized_at.isoformat(), row["reservation_id"]),
            )
        self.apply_balance_delta(
            member_id,
            "session_reservation_released",
            0,
            actor_user_id,
            related_session_id=session_id,
            notes=notes,
        )

    def finalize_reservation_usage(
        self,
        member_id: str,
        session_id: str,
        actual_elapsed_seconds: int,
        actor_user_id: str | None,
    ) -> tuple[int, MemberTimeTransactionRecord | None]:
        finalized_at = current_utc_time()
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM member_time_reservations
                WHERE member_id = ? AND session_id = ?
                """,
                (member_id, session_id),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Member reservation not found")
            if row["status"] == "finalized":
                return row["consumed_seconds"], None
            if row["status"] == "released":
                return 0, None

            consumed_seconds = max(
                0,
                min(int(actual_elapsed_seconds), int(row["reserved_seconds"])),
            )
            connection.execute(
                """
                UPDATE member_time_reservations
                SET status = 'finalized', consumed_seconds = ?, finalized_at = ?
                WHERE reservation_id = ?
                """,
                (consumed_seconds, finalized_at.isoformat(), row["reservation_id"]),
            )
        transaction = (
            self.apply_balance_delta(
                member_id,
                "session_usage_finalized",
                -consumed_seconds,
                actor_user_id,
                related_session_id=session_id,
                notes=f"Finalized actual member usage: {consumed_seconds} seconds",
            )
            if consumed_seconds > 0
            else self.apply_balance_delta(
                member_id,
                "session_reservation_released",
                0,
                actor_user_id,
                related_session_id=session_id,
                notes="Released unused member reservation with no usage",
            )
        )
        return consumed_seconds, transaction

    def purchase(
        self,
        member_id: str,
        request: MemberPurchaseRequest,
        actor: UserRecord,
    ) -> MemberPurchaseResponse:
        member = self.require_usable_member(member_id)
        product = self.get_product(request.product_id)
        if product is None or not product.is_active:
            raise HTTPException(status_code=404, detail="Membership product not found")
        shift_id = self.shift_repository.require_payment_shift(actor)

        payment_request = PaymentCreateRequest(
            payment_method=request.payment_method,
            amount=product.price,
            cash_received=request.cash_received,
            reference_number=request.reference_number,
            notes=request.notes,
        )
        cash_received = money(payment_request.cash_received) if payment_request.cash_received is not None else None
        change_given: Decimal | None = None
        if payment_request.payment_method == "cash":
            if cash_received is None:
                cash_received = product.price
            if cash_received < product.price:
                raise HTTPException(status_code=400, detail="Cash received cannot be lower than payment amount")
            change_given = money(cash_received - product.price)
        elif cash_received is not None:
            raise HTTPException(status_code=400, detail="Cash received is only valid for cash payments")

        payment = PaymentRecord(
            payment_id=str(uuid4()),
            session_id=None,
            amount=product.price,
            payment_method=request.payment_method,
            cash_received=cash_received,
            change_given=change_given,
            reference_number=request.reference_number,
            notes=request.notes,
            action_type="manual",
            related_command_id=None,
            review_required=False,
            shift_id=shift_id,
            recorded_by_user_id=actor.user_id,
            status="completed",
            created_at=current_utc_time(),
        )
        self.payment_repository.create_payment(payment)
        transaction = self.apply_balance_delta(
            member.member_id,
            "membership_purchase" if product.product_type == "initial_membership" else "promo_topup",
            product.time_seconds,
            actor.user_id,
            amount_paid=product.price,
            related_payment_id=payment.payment_id,
            notes=product.name,
        )
        updated_member = self.get_member(member_id)
        if updated_member is None:
            raise HTTPException(status_code=500, detail="Member disappeared after purchase")
        return MemberPurchaseResponse(
            member=updated_member,
            product=product,
            payment=payment,
            transaction=transaction,
        )
