"""Stage 1: promo CRUD, owner-state transitions, and audit trail only.

No eligibility resolution, no redemption application, no session/member/
payment integration, and no API routes exist yet -- see server/promo_status.py
for the pure Draft/Scheduled/Active/Ended/Disabled derivation used by the
`status` field this repository populates on every read.

IMPORTANT -- future redemption-application boundary (not implemented here):

When a later stage adds the operation that actually records a promo
redemption (crediting/spending against per_member_redemption_limit and
total_redemption_limit), that operation MUST:

    1. Open a connection and explicitly `connection.execute("BEGIN IMMEDIATE")`
       -- NOT rely on get_connection()'s default deferred transaction, which
       only acquires SQLite's write lock at the first write statement. Two
       concurrent redemption attempts against the same promo could otherwise
       both pass a SELECT COUNT(*) limit check before either has written,
       both then insert, and silently exceed the configured limit.
    2. Within that same BEGIN IMMEDIATE transaction, COUNT(*) non-voided
       rows in promo_redemptions for the promo (and for the member, when a
       per-member limit applies).
    3. Validate the count against total_redemption_limit /
       per_member_redemption_limit.
    4. INSERT the promo_redemptions row.
    5. COMMIT.

A plain SELECT-count followed by an INSERT across separate/deferred
transactions is NOT sufficient and must not be used for that operation.
This repository does not implement or expose any redemption-writing method
yet -- this comment exists purely to mark the boundary for the stage that does.
"""

import sqlite3
from datetime import datetime
from decimal import Decimal
import json
from typing import Any, Iterable
from uuid import uuid4

from fastapi import HTTPException

from server.database import get_connection
from server.promo_status import derive_promo_status
from server.schemas import (
    Promo,
    PromoAuditAction,
    PromoAuditEvent,
    PromoCreate,
    PromoOwnerState,
    PromoStateTransition,
    PromoUpdate,
)
from server.time_utils import current_utc_time


def decimal_to_storage(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def row_to_promo(row: Any, now: datetime) -> Promo:
    starts_at = datetime.fromisoformat(row["starts_at"])
    ends_at = datetime.fromisoformat(row["ends_at"])
    owner_state: PromoOwnerState = row["owner_state"]
    return Promo(
        promo_id=row["promo_id"],
        name=row["name"],
        audience=row["audience"],
        owner_state=owner_state,
        status=derive_promo_status(owner_state, starts_at, ends_at, now),
        amount_mode=row["amount_mode"],
        price=Decimal(row["price"]),
        granted_seconds=row["granted_seconds"],
        station_scope=row["station_scope"],
        applies_to=row["applies_to"],
        starts_at=starts_at,
        ends_at=ends_at,
        priority=row["priority"],
        per_member_redemption_limit=row["per_member_redemption_limit"],
        total_redemption_limit=row["total_redemption_limit"],
        created_by_user_id=row["created_by_user_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def row_to_audit_event(row: Any) -> PromoAuditEvent:
    return PromoAuditEvent(
        event_id=row["event_id"],
        promo_id=row["promo_id"],
        action=row["action"],
        old_values=json.loads(row["old_values"]) if row["old_values"] is not None else None,
        new_values=json.loads(row["new_values"]) if row["new_values"] is not None else None,
        performed_by_user_id=row["performed_by_user_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def promo_to_audit_dict(promo: Promo) -> dict[str, object]:
    """Excludes the derived `status` field deliberately -- audit snapshots
    record only what was actually stored (owner_state), never a value
    computed at read time."""
    return {
        "name": promo.name,
        "audience": promo.audience,
        "owner_state": promo.owner_state,
        "amount_mode": promo.amount_mode,
        "price": decimal_to_storage(promo.price),
        "granted_seconds": promo.granted_seconds,
        "station_scope": promo.station_scope,
        "applies_to": promo.applies_to,
        "starts_at": promo.starts_at.isoformat(),
        "ends_at": promo.ends_at.isoformat(),
        "priority": promo.priority,
        "per_member_redemption_limit": promo.per_member_redemption_limit,
        "total_redemption_limit": promo.total_redemption_limit,
    }


class PromoRepository:
    """SQLite persistence boundary for owner-managed promos. Never mutates
    promo_redemptions -- editing or transitioning a promo's state must
    never rewrite the frozen snapshot of any redemption already recorded
    against it (no such writes exist yet in Stage 1 regardless)."""

    def create(self, request: PromoCreate, performed_by_user_id: str | None = None) -> Promo:
        now = current_utc_time()
        promo_id = str(uuid4())

        with get_connection() as connection:
            connection.execute(
                """
                INSERT INTO promos (
                    promo_id, name, audience, owner_state, amount_mode, price,
                    granted_seconds, station_scope, applies_to, starts_at, ends_at,
                    priority, per_member_redemption_limit, total_redemption_limit,
                    created_by_user_id, created_at, updated_at
                )
                VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    promo_id,
                    request.name.strip(),
                    request.audience,
                    request.amount_mode,
                    decimal_to_storage(request.price),
                    request.granted_seconds,
                    request.station_scope,
                    request.applies_to,
                    request.starts_at.isoformat(),
                    request.ends_at.isoformat(),
                    request.priority,
                    request.per_member_redemption_limit,
                    request.total_redemption_limit,
                    performed_by_user_id,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM promos WHERE promo_id = ?", (promo_id,)
            ).fetchone()
            promo = row_to_promo(row, now)
            self._add_audit_event(
                connection, promo_id, "created", None, promo_to_audit_dict(promo), performed_by_user_id, now
            )

        return promo

    def get(self, promo_id: str) -> Promo | None:
        now = current_utc_time()
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM promos WHERE promo_id = ?", (promo_id,)
            ).fetchone()
        return row_to_promo(row, now) if row is not None else None

    def list(self, owner_states: Iterable[PromoOwnerState] | None = None) -> list[Promo]:
        """Deterministic ordering (created_at then promo_id, both stable
        and never reused) regardless of filter. Defaults to every state --
        an owner managing promos needs to see drafts and ended promos
        alongside active ones, not just a subset."""
        now = current_utc_time()
        query = "SELECT * FROM promos"
        parameters: tuple[object, ...] = ()

        if owner_states is not None:
            states = list(owner_states)
            if not states:
                return []
            placeholders = ", ".join("?" for _ in states)
            query += f" WHERE owner_state IN ({placeholders})"
            parameters = tuple(states)

        query += " ORDER BY created_at ASC, promo_id ASC"

        with get_connection() as connection:
            rows = connection.execute(query, parameters).fetchall()

        return [row_to_promo(row, now) for row in rows]

    def update(
        self, promo_id: str, request: PromoUpdate, performed_by_user_id: str | None = None
    ) -> Promo:
        now = current_utc_time()
        with get_connection() as connection:
            existing_row = connection.execute(
                "SELECT * FROM promos WHERE promo_id = ?", (promo_id,)
            ).fetchone()
            if existing_row is None:
                raise HTTPException(status_code=404, detail="Promo not found")
            existing = row_to_promo(existing_row, now)

            connection.execute(
                """
                UPDATE promos
                SET name = ?, audience = ?, amount_mode = ?, price = ?,
                    granted_seconds = ?, station_scope = ?, applies_to = ?,
                    starts_at = ?, ends_at = ?, priority = ?,
                    per_member_redemption_limit = ?, total_redemption_limit = ?,
                    updated_at = ?
                WHERE promo_id = ?
                """,
                (
                    request.name.strip(),
                    request.audience,
                    request.amount_mode,
                    decimal_to_storage(request.price),
                    request.granted_seconds,
                    request.station_scope,
                    request.applies_to,
                    request.starts_at.isoformat(),
                    request.ends_at.isoformat(),
                    request.priority,
                    request.per_member_redemption_limit,
                    request.total_redemption_limit,
                    now.isoformat(),
                    promo_id,
                ),
            )
            updated_row = connection.execute(
                "SELECT * FROM promos WHERE promo_id = ?", (promo_id,)
            ).fetchone()
            updated = row_to_promo(updated_row, now)
            self._add_audit_event(
                connection,
                promo_id,
                "updated",
                promo_to_audit_dict(existing),
                promo_to_audit_dict(updated),
                performed_by_user_id,
                now,
            )

        return updated

    def transition_state(
        self, promo_id: str, request: PromoStateTransition, performed_by_user_id: str | None = None
    ) -> Promo:
        now = current_utc_time()
        with get_connection() as connection:
            existing_row = connection.execute(
                "SELECT * FROM promos WHERE promo_id = ?", (promo_id,)
            ).fetchone()
            if existing_row is None:
                raise HTTPException(status_code=404, detail="Promo not found")
            existing = row_to_promo(existing_row, now)

            is_reactivation = existing.owner_state == "ended" and request.owner_state == "enabled"

            if request.ends_at is not None and not is_reactivation:
                raise HTTPException(
                    status_code=400,
                    detail="ends_at may only be supplied when reactivating a promo from 'ended' back to 'enabled'",
                )

            new_ends_at = existing.ends_at
            if is_reactivation:
                if request.ends_at is None:
                    raise HTTPException(
                        status_code=400,
                        detail="Reactivating an ended promo requires a new ends_at strictly in the future",
                    )
                if request.ends_at <= now:
                    raise HTTPException(
                        status_code=409,
                        detail="ends_at must be strictly in the future to reactivate a promo",
                    )
                new_ends_at = request.ends_at

            if request.owner_state == existing.owner_state:
                # Idempotent no-op: same state requested again writes no
                # duplicate audit event and leaves updated_at untouched.
                return existing

            action: PromoAuditAction = "reactivated" if is_reactivation else request.owner_state

            connection.execute(
                """
                UPDATE promos
                SET owner_state = ?, ends_at = ?, updated_at = ?
                WHERE promo_id = ?
                """,
                (request.owner_state, new_ends_at.isoformat(), now.isoformat(), promo_id),
            )
            updated_row = connection.execute(
                "SELECT * FROM promos WHERE promo_id = ?", (promo_id,)
            ).fetchone()
            updated = row_to_promo(updated_row, now)
            self._add_audit_event(
                connection,
                promo_id,
                action,
                promo_to_audit_dict(existing),
                promo_to_audit_dict(updated),
                performed_by_user_id,
                now,
            )

        return updated

    def count_total_redemptions(self, promo_id: str) -> int:
        """Non-voided promo_redemptions count for this promo. ADVISORY
        ONLY -- suitable as a Stage 2 eligibility-filtering input (see
        server/promo_eligibility.py's total_redemption_counts parameter),
        never as the actual limit enforcement. Between this read and a
        later write, another redemption could still be inserted; the real
        check at redemption-creation time must re-COUNT inside the same
        BEGIN IMMEDIATE transaction as the INSERT (see this module's
        docstring above) rather than trust a count read earlier like this
        one."""
        with get_connection() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM promo_redemptions WHERE promo_id = ? AND voided_at IS NULL",
                (promo_id,),
            ).fetchone()
        return row[0]

    def count_member_redemptions(self, promo_id: str, member_id: str) -> int:
        """Same advisory-only caveat as count_total_redemptions -- not safe
        for final per-member limit enforcement, only for Stage 2
        eligibility filtering."""
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) FROM promo_redemptions
                WHERE promo_id = ? AND member_id = ? AND voided_at IS NULL
                """,
                (promo_id, member_id),
            ).fetchone()
        return row[0]

    def get_for_update(self, connection: sqlite3.Connection, promo_id: str) -> Promo | None:
        """Reads the promo using the CALLER's already-open connection
        (expected to be inside a BEGIN IMMEDIATE transaction) rather than
        opening a new one -- this is the "re-read the promo" step Stage 3's
        final-confirmation recheck requires, using the same transaction
        that will go on to (maybe) insert the redemption, so nothing can
        change the row in between."""
        now = current_utc_time()
        row = connection.execute(
            "SELECT * FROM promos WHERE promo_id = ?", (promo_id,)
        ).fetchone()
        return row_to_promo(row, now) if row is not None else None

    def claim_redemption_for_standard_or_vip(
        self,
        connection: sqlite3.Connection,
        promo: Promo,
        *,
        session_id: str,
        payment_id: str,
        pc_name_snapshot: str | None,
        income_group_id_snapshot: str | None,
        income_group_name_snapshot: str | None,
        operator_user_id: str | None,
        now: datetime,
    ) -> None:
        """MUST be called with `connection` already inside a BEGIN
        IMMEDIATE transaction opened by the caller (see
        server/promo_session_integration.py) -- this method only counts
        and inserts, it never opens or commits a transaction itself. This
        is the one and only place Standard/VIP redemption limits are
        actually enforced; Stage 2's PromoRepository.count_total_redemptions
        is advisory-only and must never be trusted for this decision.

        No per-member limit check here -- Stage 1 validation already
        forbids configuring one for standard/vip audiences.
        """
        if promo.total_redemption_limit is not None:
            total_count = connection.execute(
                "SELECT COUNT(*) FROM promo_redemptions WHERE promo_id = ? AND voided_at IS NULL",
                (promo.promo_id,),
            ).fetchone()[0]
            if total_count >= promo.total_redemption_limit:
                raise HTTPException(
                    status_code=409,
                    detail="This promo's redemption limit was just reached -- please refresh and try again.",
                )

        connection.execute(
            """
            INSERT INTO promo_redemptions (
                redemption_id, promo_id, promo_name_snapshot, audience_snapshot,
                amount_mode_snapshot, price_snapshot, granted_seconds_snapshot,
                station_scope_snapshot, applies_to_snapshot, starts_at_snapshot,
                ends_at_snapshot, member_id, session_id, payment_id,
                base_transaction_id, bonus_transaction_id,
                pc_name_snapshot, income_group_id_snapshot, income_group_name_snapshot,
                operator_user_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                promo.promo_id,
                promo.name,
                promo.audience,
                promo.amount_mode,
                decimal_to_storage(promo.price),
                promo.granted_seconds,
                promo.station_scope,
                promo.applies_to,
                promo.starts_at.isoformat(),
                promo.ends_at.isoformat(),
                session_id,
                payment_id,
                pc_name_snapshot,
                income_group_id_snapshot,
                income_group_name_snapshot,
                operator_user_id,
                now.isoformat(),
            ),
        )

    def claim_redemption_for_new_member(
        self,
        connection: sqlite3.Connection,
        promo: Promo,
        *,
        redemption_id: str,
        member_id: str,
        payment_id: str,
        base_transaction_id: str,
        pc_name_snapshot: str | None,
        income_group_id_snapshot: str | None,
        income_group_name_snapshot: str | None,
        operator_user_id: str | None,
        now: datetime,
    ) -> None:
        """MUST be called with `connection` already inside a BEGIN
        IMMEDIATE transaction opened by the caller -- this is the one and
        only place New Member redemption limits are actually enforced.
        Only a total limit ever applies here (Stage 5 forbids
        per_member_redemption_limit for this audience -- the member does
        not exist until this exact redemption creates them, so there is
        no prior identity to count a per-member limit against).
        bonus_transaction_id and session_id are always NULL: the
        promotional granted_seconds IS the member's total initial
        balance, never an additional top-up on top of separate base time.
        """
        if promo.total_redemption_limit is not None:
            total_count = connection.execute(
                "SELECT COUNT(*) FROM promo_redemptions WHERE promo_id = ? AND voided_at IS NULL",
                (promo.promo_id,),
            ).fetchone()[0]
            if total_count >= promo.total_redemption_limit:
                raise HTTPException(
                    status_code=409,
                    detail="This promo's redemption limit was just reached -- please refresh and try again.",
                )

        connection.execute(
            """
            INSERT INTO promo_redemptions (
                redemption_id, promo_id, promo_name_snapshot, audience_snapshot,
                amount_mode_snapshot, price_snapshot, granted_seconds_snapshot,
                station_scope_snapshot, applies_to_snapshot, starts_at_snapshot,
                ends_at_snapshot, member_id, session_id, payment_id,
                base_transaction_id, bonus_transaction_id,
                pc_name_snapshot, income_group_id_snapshot, income_group_name_snapshot,
                operator_user_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, NULL, ?, ?, NULL, ?, ?, ?, ?, ?)
            """,
            (
                redemption_id,
                promo.promo_id,
                promo.name,
                promo.audience,
                promo.amount_mode,
                decimal_to_storage(promo.price),
                promo.granted_seconds,
                promo.starts_at.isoformat(),
                promo.ends_at.isoformat(),
                member_id,
                payment_id,
                base_transaction_id,
                pc_name_snapshot,
                income_group_id_snapshot,
                income_group_name_snapshot,
                operator_user_id,
                now.isoformat(),
            ),
        )

    def claim_redemption_for_member_topup(
        self,
        connection: sqlite3.Connection,
        promo: Promo,
        *,
        redemption_id: str,
        member_id: str,
        payment_id: str,
        base_transaction_id: str,
        bonus_transaction_id: str,
        pc_name_snapshot: str | None,
        income_group_id_snapshot: str | None,
        income_group_name_snapshot: str | None,
        operator_user_id: str | None,
        now: datetime,
    ) -> None:
        """MUST be called with `connection` already inside a BEGIN
        IMMEDIATE transaction opened by the caller -- this is the one and
        only place Member Top-up redemption limits (both total and
        per-member) are actually enforced; Stage 2's
        count_total_redemptions/count_member_redemptions are advisory-only
        and must never be trusted for this decision.

        `redemption_id` is generated by the caller (not here) so the same
        id can be written onto the base/bonus member_time_transactions rows
        (promo_redemption_id) before this insert exists to reference.
        """
        if promo.total_redemption_limit is not None:
            total_count = connection.execute(
                "SELECT COUNT(*) FROM promo_redemptions WHERE promo_id = ? AND voided_at IS NULL",
                (promo.promo_id,),
            ).fetchone()[0]
            if total_count >= promo.total_redemption_limit:
                raise HTTPException(
                    status_code=409,
                    detail="This promo's redemption limit was just reached -- please refresh and try again.",
                )
        if promo.per_member_redemption_limit is not None:
            member_count = connection.execute(
                "SELECT COUNT(*) FROM promo_redemptions WHERE promo_id = ? AND member_id = ? AND voided_at IS NULL",
                (promo.promo_id, member_id),
            ).fetchone()[0]
            if member_count >= promo.per_member_redemption_limit:
                raise HTTPException(
                    status_code=409,
                    detail="This member has already used this promo the maximum number of times.",
                )

        connection.execute(
            """
            INSERT INTO promo_redemptions (
                redemption_id, promo_id, promo_name_snapshot, audience_snapshot,
                amount_mode_snapshot, price_snapshot, granted_seconds_snapshot,
                station_scope_snapshot, applies_to_snapshot, starts_at_snapshot,
                ends_at_snapshot, member_id, session_id, payment_id,
                base_transaction_id, bonus_transaction_id,
                pc_name_snapshot, income_group_id_snapshot, income_group_name_snapshot,
                operator_user_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                redemption_id,
                promo.promo_id,
                promo.name,
                promo.audience,
                promo.amount_mode,
                decimal_to_storage(promo.price),
                promo.granted_seconds,
                promo.station_scope,
                promo.applies_to,
                promo.starts_at.isoformat(),
                promo.ends_at.isoformat(),
                member_id,
                payment_id,
                base_transaction_id,
                bonus_transaction_id,
                pc_name_snapshot,
                income_group_id_snapshot,
                income_group_name_snapshot,
                operator_user_id,
                now.isoformat(),
            ),
        )

    def get_redemption_by_payment_id(self, payment_id: str) -> dict | None:
        """Read-only lookup used by refund flows to discover whether a
        payment funded a promo redemption at all, and if so, its linked
        base/bonus transaction ids. Returns the raw row as a dict (not a
        Promo/Pydantic model -- this reads promo_redemptions, not promos)."""
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM promo_redemptions WHERE payment_id = ?", (payment_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def void_redemption_for_payment(
        self,
        connection: sqlite3.Connection,
        payment_id: str,
        void_reason: str,
        now: datetime,
    ) -> None:
        """No-op (0 rows affected) when this payment has no promo
        redemption, or it is already voided -- callers never need to
        branch on whether a promo was actually involved. Never deletes:
        the frozen snapshot survives, only voided_at/void_reason are set,
        which also frees the promo's total-redemption slot for future
        COUNT(*)-based limit checks."""
        connection.execute(
            """
            UPDATE promo_redemptions
            SET voided_at = ?, void_reason = ?
            WHERE payment_id = ? AND voided_at IS NULL
            """,
            (now.isoformat(), void_reason, payment_id),
        )

    def list_audit_events(self, promo_id: str) -> list[PromoAuditEvent]:
        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM promo_audit_events
                WHERE promo_id = ?
                ORDER BY created_at ASC, event_id ASC
                """,
                (promo_id,),
            ).fetchall()
        return [row_to_audit_event(row) for row in rows]

    def _add_audit_event(
        self,
        connection: Any,
        promo_id: str,
        action: PromoAuditAction,
        old_values: dict[str, object] | None,
        new_values: dict[str, object] | None,
        performed_by_user_id: str | None,
        now: datetime,
    ) -> None:
        """Written on the same connection/transaction as the promos
        mutation that triggered it -- never a separate commit, so a
        crash between the two can never leave one without the other."""
        connection.execute(
            """
            INSERT INTO promo_audit_events (
                event_id, promo_id, action, old_values, new_values,
                performed_by_user_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid4()),
                promo_id,
                action,
                json.dumps(old_values) if old_values is not None else None,
                json.dumps(new_values) if new_values is not None else None,
                performed_by_user_id,
                now.isoformat(),
            ),
        )
