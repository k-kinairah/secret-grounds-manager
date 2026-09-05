import json
import sqlite3
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from server.balance_conversion import STANDARD_SECONDS_PER_VIP_SECOND, finalize_vip_usage
from server.billing_service import money
from server.database import get_connection
from server.income_attribution import (
    AttributionSnapshot,
    attribution_for_known_pc,
    attribution_pending_unknown_pc,
    is_balance_type_usable_on_station,
)
from server.payment_repository import PaymentRepository
from server.passwords import hash_password, verify_password
from server.pc_layout_repository import IncomeGroupRepository, PCLayoutRepository
from server.promo_eligibility import select_best_promo
from server.promo_member_topup_service import (
    build_member_topup_quote,
    claim_member_topup_promo,
    is_member_topup_promo_eligible,
)
from server.promo_new_member_service import build_new_member_promo_quote, claim_new_member_promo
from server.promo_repository import PromoRepository
from server.schemas import (
    AppliedPromoSummary,
    CalculatedTimeResponse,
    MemberCreateRequest,
    MemberCustomTopupRequest,
    MemberPurchaseRefundRequest,
    MemberPurchaseRefundResponse,
    MemberPurchaseRequest,
    MemberPurchaseResponse,
    MemberRecord,
    MembershipProductCreate,
    MembershipProductRecord,
    MembershipProductUpdate,
    MemberStatusUpdate,
    MemberTimeTransactionRecord,
    MemberTopupPromoQuoteResponse,
    MemberUpdateRequest,
    NewMemberPromoQuoteResponse,
    PaymentCreateRequest,
    PaymentRecord,
    UserRecord,
)
from server.session_repository import SessionRepository
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
        vip_balance_seconds=row["vip_balance_seconds"] if "vip_balance_seconds" in keys else 0,
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
        balance_type=row["balance_type"] if "balance_type" in row.keys() else "standard",
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
        balance_type=row["balance_type"] if "balance_type" in row.keys() else "standard",
        seconds_delta=row["seconds_delta"],
        balance_before_seconds=row["balance_before_seconds"],
        balance_after_seconds=row["balance_after_seconds"],
        amount_paid=Decimal(row["amount_paid"]) if row["amount_paid"] is not None else None,
        related_session_id=row["related_session_id"],
        related_payment_id=row["related_payment_id"],
        notes=row["notes"],
        performed_by_user_id=row["performed_by_user_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
        promo_redemption_id=row["promo_redemption_id"] if "promo_redemption_id" in row.keys() else None,
    )


class MemberRepository:
    """Digital membership records and balance ledger operations."""

    def __init__(
        self,
        payment_repository: PaymentRepository,
        shift_repository: ShiftRepository,
        session_repository: SessionRepository | None = None,
        pc_layout_repository: PCLayoutRepository | None = None,
        income_group_repository: IncomeGroupRepository | None = None,
        promo_repository: PromoRepository | None = None,
    ) -> None:
        self.payment_repository = payment_repository
        self.shift_repository = shift_repository
        self.session_repository = session_repository or SessionRepository()
        self.pc_layout_repository = pc_layout_repository or PCLayoutRepository()
        self.income_group_repository = income_group_repository or IncomeGroupRepository()
        self.promo_repository = promo_repository or PromoRepository()

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
        if request.payment_method is None:
            # Zero-balance account (staff comps, corrections, automated
            # setup). A cashier must always collect the membership fee;
            # admin/owner and internal (actor-less) callers may skip it.
            if actor is not None and actor.role == "cashier":
                raise HTTPException(
                    status_code=403,
                    detail="Payment is required to create a member",
                )
            return self._create_member_plain(request, actor)
        return self._create_member_paid(request, actor)

    def _resolve_membership_fee_product(self) -> MembershipProductRecord:
        """The product a new account is billed for. The curated pricing
        editor (server/pricing_service.py) always writes the fee to the
        fixed 'membership-fee' row; fall back to any active
        initial_membership product so a hand-edited catalog still works."""
        product = self.get_product("membership-fee")
        if product is not None and product.is_active:
            return product
        for candidate in self.list_products(include_inactive=False):
            if candidate.product_type == "initial_membership":
                return candidate
        raise HTTPException(
            status_code=409,
            detail="No active membership fee product is configured",
        )

    def _resolve_payment_fields(
        self,
        payment_method: str,
        amount: Decimal,
        cash_received: Decimal | None,
        reference_number: str | None,
    ) -> tuple[Decimal | None, Decimal | None]:
        """Validate cash/reference inputs against `amount` and return
        (cash_received, change_given). Same rules a top-up purchase applies
        (see MemberRepository.purchase)."""
        cash_received = money(cash_received) if cash_received is not None else None
        change_given: Decimal | None = None
        if payment_method == "cash":
            if cash_received is not None:
                if cash_received < amount:
                    raise HTTPException(status_code=400, detail="Cash received cannot be lower than payment amount")
                change_given = money(cash_received - amount)
        else:
            if cash_received is not None:
                raise HTTPException(status_code=400, detail="Cash received is only valid for cash payments")
        return cash_received, change_given

    def _create_member_plain(
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

    def _create_member_paid(
        self,
        request: MemberCreateRequest,
        actor: UserRecord | None,
    ) -> MemberRecord:
        """Bill the current Membership Fee product and create the member
        with its hours credited to the Standard balance -- member row,
        payment, and the one membership_purchase ledger row all commit
        together inside one BEGIN IMMEDIATE transaction, or nothing does."""
        product = self._resolve_membership_fee_product()

        # Idempotency short-circuit -- before any writes, so a retried
        # request with the same key returns the original member instead of
        # creating a second account or taking a second payment.
        if request.idempotency_key:
            existing_payment = self.payment_repository.get_payment_by_idempotency_key(request.idempotency_key)
            if existing_payment is not None:
                base_transaction, _ = self.resolve_purchase_transactions(existing_payment.payment_id)
                if base_transaction is None:
                    raise HTTPException(
                        status_code=409, detail="Duplicate transaction key belongs to another payment"
                    )
                member = self.get_member(base_transaction.member_id)
                if member is None:
                    raise HTTPException(status_code=500, detail="Member disappeared after creation")
                return self._attach_creation_metadata(member, existing_payment, base_transaction, 0)

        now = current_utc_time()
        cash_received, change_given = self._resolve_payment_fields(
            request.payment_method, product.price, request.cash_received, request.reference_number
        )

        username = request.username.strip()
        username_normalized = normalize_username(username)
        display_name = clean_display_name(request.display_name)
        pin_hash = hash_password(request.pin)
        member_id = str(uuid4())
        # A brand-new member has no active session -- attribution is always
        # pending, same as the promo signup path.
        attribution = attribution_pending_unknown_pc()
        open_shift = self.shift_repository.get_open_shift_for_user(actor.user_id) if actor else None
        shift_id = open_shift.shift_id if open_shift is not None else None

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
            recorded_by_user_id=actor.user_id if actor else None,
            idempotency_key=request.idempotency_key,
            status="completed",
            created_at=now,
            pc_name_snapshot=attribution.pc_name_snapshot,
            income_group_id_snapshot=attribution.income_group_id_snapshot,
            income_group_name_snapshot=attribution.income_group_name_snapshot,
            attribution_status=attribution.status,  # type: ignore[arg-type]
            attribution_assigned_at=attribution.assigned_at,
            attribution_session_id=attribution.session_id,
        )

        def _insert_member(connection: sqlite3.Connection) -> None:
            member_code = self.generate_member_code(connection)
            member = MemberRecord(
                member_id=member_id,
                member_code=member_code,
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
                        member_id, member_code, username, username_normalized, full_name,
                        phone_number, pin_hash, requires_account_setup, pin_failed_attempts,
                        pin_locked_until, status, time_balance_seconds, joined_at, expires_at,
                        created_by_user_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        member.member_id, member.member_code, member.username, username_normalized,
                        member.display_name or "", member.phone_number, pin_hash, 0, 0, None,
                        member.status, member.time_balance_seconds, member.joined_at.isoformat(),
                        member.expires_at.isoformat() if member.expires_at else None,
                        member.created_by_user_id, member.created_at.isoformat(), member.updated_at.isoformat(),
                    ),
                )
                self.record_member_audit(connection, "member_created", actor, member)
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="Username already exists") from error

        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            _insert_member(connection)
            self.payment_repository.create_payment(payment, connection=connection)
            base_transaction = self.apply_balance_delta(
                member_id,
                "membership_purchase",
                product.time_seconds,
                actor.user_id if actor else None,
                amount_paid=product.price,
                related_payment_id=payment.payment_id,
                notes=product.name,
                balance_type="standard",
                connection=connection,
            )

        member = self.get_member(member_id)
        if member is None:
            raise HTTPException(status_code=500, detail="Member disappeared after creation")
        return self._attach_creation_metadata(member, payment, base_transaction, 0)

    def quote_new_member_promo(self, amount: Decimal) -> NewMemberPromoQuoteResponse:
        return build_new_member_promo_quote(
            promo_repository=self.promo_repository,
            member_repository=self,
            amount=amount,
            now=current_utc_time(),
        )

    def _new_member_benefit_seconds(self, amount: Decimal, granted_seconds: int) -> int:
        from server.promo_new_member_service import resolve_normal_new_member_product

        normal_product = resolve_normal_new_member_product(self, amount)
        baseline = normal_product.time_seconds if normal_product is not None else 0
        return granted_seconds - baseline

    def _attach_creation_metadata(
        self,
        member: MemberRecord,
        payment: PaymentRecord,
        base_transaction: MemberTimeTransactionRecord,
        benefit_seconds: int,
    ) -> MemberRecord:
        applied_promo = self._applied_promo_summary_for_redemption(base_transaction.promo_redemption_id)
        return member.model_copy(
            update={
                "creation_payment": payment,
                "creation_applied_promo": applied_promo,
                "creation_base_seconds": base_transaction.seconds_delta,
                "creation_benefit_seconds": benefit_seconds,
            }
        )

    def _create_member_with_new_member_promo(
        self,
        request: MemberCreateRequest,
        actor: UserRecord | None,
    ) -> MemberRecord:
        # DORMANT: account creation now always charges the flat Membership
        # Fee (see create_member -> _create_member_paid). This promo-claim
        # path is kept only so promo_new_member_service does not rot; it is
        # no longer reachable from any route. Rewire create_member if the
        # New Member promo entry point is ever restored.
        if request.payment_method is None:
            raise HTTPException(status_code=400, detail="payment_method is required when amount is supplied")

        # Idempotency short-circuit -- before any promo resolution or
        # writes, so a retried request with the same idempotency_key never
        # re-evaluates promo eligibility (which could legitimately differ
        # between two real calls) or claims a second redemption.
        if request.idempotency_key:
            existing_payment = self.payment_repository.get_payment_by_idempotency_key(request.idempotency_key)
            if existing_payment is not None:
                base_transaction, _ = self.resolve_purchase_transactions(existing_payment.payment_id)
                if base_transaction is None:
                    raise HTTPException(
                        status_code=409, detail="Duplicate transaction key belongs to another payment"
                    )
                member = self.get_member(base_transaction.member_id)
                if member is None:
                    raise HTTPException(status_code=500, detail="Member disappeared after creation")
                benefit_seconds = self._new_member_benefit_seconds(existing_payment.amount, base_transaction.seconds_delta)
                return self._attach_creation_metadata(member, existing_payment, base_transaction, benefit_seconds)

        now = current_utc_time()
        quote = build_new_member_promo_quote(
            promo_repository=self.promo_repository, member_repository=self, amount=request.amount, now=now
        )
        resolved_promo_id = quote.promo_id
        if request.expected_promo_id is not None and request.expected_promo_id != resolved_promo_id:
            raise HTTPException(
                status_code=409,
                detail="The expected promo is no longer available. Please refresh and try again.",
            )
        if resolved_promo_id is None:
            # No ordinary (non-promo) paid signup path exists here -- amount
            # is 100% promo-gated. A cashier who does not want the promo
            # uses the existing 2-step flow (bare POST /members, then a
            # separate membership-fee purchase) unchanged.
            raise HTTPException(
                status_code=409,
                detail="No active New Member promo currently matches this amount.",
            )

        cash_received = money(request.cash_received) if request.cash_received is not None else None
        change_given: Decimal | None = None
        if request.payment_method == "cash":
            if cash_received is not None:
                if cash_received < request.amount:
                    raise HTTPException(status_code=400, detail="Cash received cannot be lower than payment amount")
                change_given = money(cash_received - request.amount)
        else:
            if cash_received is not None:
                raise HTTPException(status_code=400, detail="Cash received is only valid for cash payments")

        username = request.username.strip()
        username_normalized = normalize_username(username)
        display_name = clean_display_name(request.display_name)
        pin_hash = hash_password(request.pin)
        member_id = str(uuid4())
        # A brand-new member has no active session anywhere -- attribution
        # is always pending, same as any other purchase resolved against a
        # member with no active session (see _resolve_purchase_attribution).
        attribution = attribution_pending_unknown_pc()
        open_shift = self.shift_repository.get_open_shift_for_user(actor.user_id) if actor else None
        shift_id = open_shift.shift_id if open_shift is not None else None

        payment = PaymentRecord(
            payment_id=str(uuid4()),
            session_id=None,
            amount=request.amount,
            payment_method=request.payment_method,
            cash_received=cash_received,
            change_given=change_given,
            reference_number=request.reference_number,
            notes=request.notes,
            action_type="manual",
            related_command_id=None,
            review_required=False,
            shift_id=shift_id,
            recorded_by_user_id=actor.user_id if actor else None,
            idempotency_key=request.idempotency_key,
            status="completed",
            created_at=now,
            pc_name_snapshot=attribution.pc_name_snapshot,
            income_group_id_snapshot=attribution.income_group_id_snapshot,
            income_group_name_snapshot=attribution.income_group_name_snapshot,
            attribution_status=attribution.status,  # type: ignore[arg-type]
            attribution_assigned_at=attribution.assigned_at,
            attribution_session_id=attribution.session_id,
        )

        def _insert_member(connection: sqlite3.Connection) -> None:
            member_code = self.generate_member_code(connection)
            member = MemberRecord(
                member_id=member_id,
                member_code=member_code,
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
                        member_id, member_code, username, username_normalized, full_name,
                        phone_number, pin_hash, requires_account_setup, pin_failed_attempts,
                        pin_locked_until, status, time_balance_seconds, joined_at, expires_at,
                        created_by_user_id, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        member.member_id, member.member_code, member.username, username_normalized,
                        member.display_name or "", member.phone_number, pin_hash, 0, 0, None,
                        member.status, member.time_balance_seconds, member.joined_at.isoformat(),
                        member.expires_at.isoformat() if member.expires_at else None,
                        member.created_by_user_id, member.created_at.isoformat(), member.updated_at.isoformat(),
                    ),
                )
                self.record_member_audit(connection, "member_created", actor, member)
            except sqlite3.IntegrityError as error:
                raise HTTPException(status_code=409, detail="Username already exists") from error

        # BEGIN IMMEDIATE opens and commits entirely inside claim_new_member_promo
        # -- member creation, promo recheck, payment, the one initial-membership
        # ledger row, and the redemption insert are all-or-nothing.
        outcome = claim_new_member_promo(
            member_repository=self,
            payment_repository=self.payment_repository,
            promo_repository=self.promo_repository,
            promo_id=resolved_promo_id,
            amount=request.amount,
            member_id=member_id,
            member_insert=_insert_member,
            payment=payment,
            actor=actor,
            attribution=attribution,
            now=now,
        )
        member = self.get_member(member_id)
        if member is None:
            raise HTTPException(status_code=500, detail="Member disappeared after creation")
        benefit_seconds = self._new_member_benefit_seconds(request.amount, outcome.base_transaction.seconds_delta)
        return self._attach_creation_metadata(member, outcome.payment, outcome.base_transaction, benefit_seconds)

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

    def get_transaction(self, transaction_id: str) -> MemberTimeTransactionRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM member_time_transactions WHERE transaction_id = ?",
                (transaction_id,),
            ).fetchone()
        return row_to_transaction(row) if row is not None else None

    def _validate_redemption_linkage(
        self,
        redemption: dict,
        base_transaction: MemberTimeTransactionRecord | None,
        bonus_transaction: MemberTimeTransactionRecord | None,
        payment_id: str,
    ) -> None:
        """Fails loudly (500 -- this is data corruption, never a normal
        business conflict) BEFORE any caller has a chance to mutate a
        payment or balance off a mismatched row. Every check here mirrors
        an invariant claim_member_topup_promo already establishes at
        write time; this is the read-time re-verification of that same
        invariant. Deliberately does NOT check "does this member_id match
        who the caller expected" -- that's an ordinary idempotency-key
        collision, the same ambiguous-caller situation the non-promo path
        already returns a normal transaction for and lets the caller turn
        into its own 409, not a 500."""
        problems: list[str] = []
        if base_transaction is None:
            problems.append("base_transaction_id does not exist")
        if redemption["bonus_transaction_id"] is not None and bonus_transaction is None:
            problems.append("bonus_transaction_id does not exist")
        for label, transaction in (("base", base_transaction), ("bonus", bonus_transaction)):
            if transaction is None:
                continue
            if transaction.related_payment_id != payment_id:
                problems.append(f"{label} transaction does not belong to this payment")
            if transaction.member_id != redemption["member_id"]:
                problems.append(f"{label} transaction does not belong to this member")
            if transaction.promo_redemption_id != redemption["redemption_id"]:
                problems.append(f"{label} transaction is not linked to this redemption")
        if problems:
            raise HTTPException(
                status_code=500,
                detail="Promo redemption linkage is inconsistent: " + "; ".join(problems),
            )

    def resolve_purchase_transactions(
        self,
        payment_id: str,
    ) -> tuple[MemberTimeTransactionRecord | None, MemberTimeTransactionRecord | None]:
        """Deterministically resolves the base (and, for a promotional
        purchase, bonus) member_time_transactions row(s) for a purchase
        payment -- NEVER by ordering/limiting on created_at, since a
        promo purchase's base and bonus rows can legitimately share the
        same timestamp and SQL gives no tie-break guarantee. When a
        promo_redemptions row exists for this payment, base/bonus are
        looked up by their exact stored transaction ids and the linkage
        is validated (raises 500 before any mutation on corruption).
        Otherwise falls back to a single deterministic (created_at,
        transaction_id) lookup for the ordinary non-promo case -- more
        than one candidate row with no redemption to disambiguate them is
        itself a corruption signal, not something to silently resolve by
        picking one.

        Returns (None, None) when no purchase transaction exists for this
        payment at all (not an error -- callers decide what that means).
        A member-identity check against a caller's expected member_id
        (e.g. idempotency-key reuse across members) is deliberately left
        to the caller, on the returned base_transaction -- see
        _validate_redemption_linkage's docstring for why."""
        redemption = self.promo_repository.get_redemption_by_payment_id(payment_id)
        if redemption is not None and redemption["base_transaction_id"] is not None:
            base_transaction = self.get_transaction(redemption["base_transaction_id"])
            bonus_transaction = (
                self.get_transaction(redemption["bonus_transaction_id"])
                if redemption["bonus_transaction_id"] is not None
                else None
            )
            self._validate_redemption_linkage(redemption, base_transaction, bonus_transaction, payment_id)
            return base_transaction, bonus_transaction

        with get_connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM member_time_transactions
                WHERE related_payment_id = ?
                  AND transaction_type IN ('membership_purchase', 'promo_topup')
                ORDER BY created_at ASC, transaction_id ASC
                """,
                (payment_id,),
            ).fetchall()
        if not rows:
            return None, None
        if len(rows) > 1:
            raise HTTPException(
                status_code=500,
                detail="Ambiguous member purchase transactions for this payment (no promo redemption to disambiguate).",
            )
        base_transaction = row_to_transaction(rows[0])
        return base_transaction, None

    def get_refund_transaction_by_payment_id(self, payment_id: str) -> MemberTimeTransactionRecord | None:
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT * FROM member_time_transactions
                WHERE related_payment_id = ?
                  AND transaction_type = 'purchase_refund'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (payment_id,),
            ).fetchone()
        return row_to_transaction(row) if row is not None else None

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
        balance_type: str = "standard",
        connection: sqlite3.Connection | None = None,
        promo_redemption_id: str | None = None,
    ) -> MemberTimeTransactionRecord:
        """Credits/debits exactly one balance (Standard or VIP -- never
        both) and records one ledger row for it. When `connection` is
        supplied, the update/insert join the caller's transaction (no
        commit/close here) so this can be made atomic with, e.g., the
        payment that funds it -- see MemberRepository.purchase().

        `promo_redemption_id` links this row to the promo_redemptions row
        it belongs to (Stage 4) -- shared between a promo purchase's base
        and bonus rows, NULL for every ordinary (non-promo) transaction,
        exactly like before this column existed."""
        zero_delta_types = {
            "session_reservation_created",
            "session_reservation_released",
        }
        if seconds_delta == 0 and transaction_type not in zero_delta_types:
            raise HTTPException(status_code=400, detail="Balance change cannot be zero")
        now = current_utc_time()
        transaction_id = str(uuid4())
        balance_column = "vip_balance_seconds" if balance_type == "vip" else "time_balance_seconds"

        def _apply(active_connection: sqlite3.Connection) -> tuple[int, int]:
            row = active_connection.execute(
                f"SELECT {balance_column} FROM members WHERE member_id = ?",
                (member_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="Member not found")
            before = int(row[balance_column])
            after = before + seconds_delta
            if after < 0:
                raise HTTPException(status_code=409, detail="Insufficient member time balance")
            active_connection.execute(
                f"""
                UPDATE members
                SET {balance_column} = ?, updated_at = ?
                WHERE member_id = ?
                """,
                (after, now.isoformat(), member_id),
            )
            active_connection.execute(
                """
                INSERT INTO member_time_transactions (
                    transaction_id,
                    member_id,
                    transaction_type,
                    balance_type,
                    seconds_delta,
                    balance_before_seconds,
                    balance_after_seconds,
                    amount_paid,
                    related_session_id,
                    related_payment_id,
                    notes,
                    performed_by_user_id,
                    created_at,
                    promo_redemption_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transaction_id,
                    member_id,
                    transaction_type,
                    balance_type,
                    seconds_delta,
                    before,
                    after,
                    decimal_to_storage(amount_paid),
                    related_session_id,
                    related_payment_id,
                    notes,
                    actor_user_id,
                    now.isoformat(),
                    promo_redemption_id,
                ),
            )
            return before, after

        if connection is not None:
            # Shared, not-yet-committed transaction: a fresh connection
            # (e.g. via list_ledger) would not see this row yet, so build
            # the record directly from the values just written instead of
            # re-querying.
            before, after = _apply(connection)
            return MemberTimeTransactionRecord(
                transaction_id=transaction_id,
                member_id=member_id,
                transaction_type=transaction_type,
                balance_type=balance_type,
                seconds_delta=seconds_delta,
                balance_before_seconds=before,
                balance_after_seconds=after,
                amount_paid=amount_paid,
                related_session_id=related_session_id,
                related_payment_id=related_payment_id,
                notes=notes,
                performed_by_user_id=actor_user_id,
                created_at=now,
                promo_redemption_id=promo_redemption_id,
            )

        with get_connection() as owned_connection:
            _apply(owned_connection)
        return self.list_ledger(member_id)[-1]

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
        reserved_vip_seconds: int = 0,
    ) -> None:
        """reserved_seconds is always expressed in Standard-balance seconds
        (unchanged meaning) -- on a VIP PC this is already the 2:1-converted
        amount (see server.balance_conversion.split_vip_session), so this
        function never needs to know the station type itself. Balance is
        NOT decremented here (see finalize_reservation_usage for why); this
        only checks availability and claims the single active-reservation
        slot for the member."""
        if reserved_seconds < 0 or reserved_vip_seconds < 0:
            raise HTTPException(status_code=400, detail="Reserved time must not be negative")
        if reserved_seconds == 0 and reserved_vip_seconds == 0:
            raise HTTPException(status_code=400, detail="Reserved time must be positive")
        if member.time_balance_seconds < reserved_seconds:
            raise HTTPException(status_code=409, detail="Insufficient member time balance")
        if member.vip_balance_seconds < reserved_vip_seconds:
            raise HTTPException(status_code=409, detail="Insufficient member VIP balance")
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
                        reserved_vip_seconds,
                        consumed_vip_seconds,
                        status,
                        created_at
                    )
                    VALUES (?, ?, ?, ?, 0, ?, 0, 'active', ?)
                    """,
                    (
                        str(uuid4()),
                        member.member_id,
                        session_id,
                        reserved_seconds,
                        reserved_vip_seconds,
                        now.isoformat(),
                    ),
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
            notes=f"Reserved up to {reserved_seconds} Standard + {reserved_vip_seconds} VIP seconds",
        )

    def get_active_reservation_totals(self, member_id: str, session_id: str) -> tuple[int, int] | None:
        """Returns (reserved_seconds, reserved_vip_seconds) for the
        member's active reservation on this session, or None if there is
        none. Used to compute how much headroom remains before an Add Time
        extension on a VIP-PC session."""
        with get_connection() as connection:
            row = connection.execute(
                """
                SELECT reserved_seconds, reserved_vip_seconds FROM member_time_reservations
                WHERE member_id = ? AND session_id = ? AND status = 'active'
                """,
                (member_id, session_id),
            ).fetchone()
        if row is None:
            return None
        return int(row["reserved_seconds"]), int(row["reserved_vip_seconds"])

    def extend_reservation(
        self,
        member: MemberRecord,
        session_id: str,
        additional_seconds: int,
        actor_user_id: str | None,
        additional_vip_seconds: int = 0,
    ) -> tuple[int, int]:
        """Returns (next_reserved_seconds, next_reserved_vip_seconds)."""
        if additional_seconds < 0 or additional_vip_seconds < 0:
            raise HTTPException(status_code=400, detail="Additional reserved time must not be negative")
        if additional_seconds == 0 and additional_vip_seconds == 0:
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
            next_reserved_vip = row["reserved_vip_seconds"] + additional_vip_seconds
            if member.time_balance_seconds < next_reserved:
                raise HTTPException(status_code=409, detail="Insufficient member time balance")
            if member.vip_balance_seconds < next_reserved_vip:
                raise HTTPException(status_code=409, detail="Insufficient member VIP balance")
            connection.execute(
                """
                UPDATE member_time_reservations
                SET reserved_seconds = ?, reserved_vip_seconds = ?
                WHERE reservation_id = ?
                """,
                (next_reserved, next_reserved_vip, row["reservation_id"]),
            )
        self.apply_balance_delta(
            member.member_id,
            "session_reservation_created",
            0,
            actor_user_id,
            related_session_id=session_id,
            notes=f"Extended reservation by {additional_seconds} Standard + {additional_vip_seconds} VIP seconds",
        )
        return next_reserved, next_reserved_vip

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
        is_vip_station: bool = False,
    ) -> tuple[int, int]:
        """Deducts balance for exactly the real time actually played,
        capped at the reservation -- never the full reservation itself.
        Returns (standard_seconds_consumed, vip_seconds_consumed).

        Idempotent: a reservation already 'finalized' or 'released' returns
        its previously recorded consumption (or zero) without touching
        either balance again, so a repeated call (retry, reconnect race,
        double force-end) can never double-deduct.

        On a VIP-PC session (is_vip_station=True), consumption is computed
        by server.balance_conversion.finalize_vip_usage: VIP balance first,
        Standard balance for any real-time remainder at 2:1. On a Standard
        PC, consumption stays the original 1:1 behavior, unchanged.
        """
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
                return int(row["consumed_seconds"]), int(row["consumed_vip_seconds"])
            if row["status"] == "released":
                return 0, 0

            reserved_standard = int(row["reserved_seconds"])
            reserved_vip = int(row["reserved_vip_seconds"])

            if is_vip_station:
                reserved_effective_seconds = reserved_vip + reserved_standard // STANDARD_SECONDS_PER_VIP_SECOND
                vip_consumed, standard_consumed = finalize_vip_usage(
                    actual_elapsed_seconds, reserved_effective_seconds, reserved_vip
                )
            else:
                vip_consumed = 0
                standard_consumed = max(0, min(int(actual_elapsed_seconds), reserved_standard))

            connection.execute(
                """
                UPDATE member_time_reservations
                SET status = 'finalized', consumed_seconds = ?, consumed_vip_seconds = ?, finalized_at = ?
                WHERE reservation_id = ?
                """,
                (standard_consumed, vip_consumed, finalized_at.isoformat(), row["reservation_id"]),
            )

        if standard_consumed <= 0 and vip_consumed <= 0:
            self.apply_balance_delta(
                member_id,
                "session_reservation_released",
                0,
                actor_user_id,
                related_session_id=session_id,
                notes="Released unused member reservation with no usage",
            )
            return standard_consumed, vip_consumed

        # Two independent ledger entries when both pools were drawn on --
        # each balance's deduction is recorded against its own balance_type,
        # never combined into a single ambiguous row.
        if vip_consumed > 0:
            self.apply_balance_delta(
                member_id,
                "session_usage_finalized",
                -vip_consumed,
                actor_user_id,
                related_session_id=session_id,
                notes=f"Finalized actual member usage: {vip_consumed} VIP seconds",
                balance_type="vip",
            )
        if standard_consumed > 0:
            self.apply_balance_delta(
                member_id,
                "session_usage_finalized",
                -standard_consumed,
                actor_user_id,
                related_session_id=session_id,
                notes=(
                    f"Finalized actual member usage: {standard_consumed} Standard seconds"
                    + (" (2:1 VIP conversion)" if is_vip_station else "")
                ),
                balance_type="standard",
            )
        return standard_consumed, vip_consumed

    def _resolve_purchase_attribution(
        self,
        member_id: str,
        product: MembershipProductRecord,
        now: datetime,
    ) -> AttributionSnapshot:
        """Decide immediate-vs-pending income attribution for a membership
        purchase, without ever asking the cashier for a PC.

        A purchase is attributed immediately only if the member already
        has an active session AND that session's station can actually use
        the balance type being purchased: Standard balance is usable on a
        Standard or a VIP station (2:1 conversion), but VIP balance is
        usable only on a VIP station. Attributing a VIP-only purchase to
        a Standard PC just because the member happens to be playing there
        would misattribute revenue -- it stays 'pending' instead, and
        this method takes no part in choosing where it resolves later.
        """
        active_session = self.session_repository.get_active_member_session(member_id)
        if active_session is None:
            return attribution_pending_unknown_pc()

        layout = self.pc_layout_repository.get_layout(active_session.pc_name)
        is_vip_station = bool(layout.is_vip) if layout is not None else False
        if not is_balance_type_usable_on_station(product.balance_type, is_vip_station):
            return attribution_pending_unknown_pc()

        return attribution_for_known_pc(
            self.pc_layout_repository,
            self.income_group_repository,
            active_session.pc_name,
            active_session.session_id,
            now,
        )

    def _applied_promo_summary_for_redemption(self, redemption_id: str | None) -> AppliedPromoSummary | None:
        if redemption_id is None:
            return None
        with get_connection() as connection:
            row = connection.execute(
                "SELECT * FROM promo_redemptions WHERE redemption_id = ?", (redemption_id,)
            ).fetchone()
        if row is None:
            return None
        return AppliedPromoSummary(
            redemption_id=row["redemption_id"],
            promo_id=row["promo_id"],
            promo_name_snapshot=row["promo_name_snapshot"],
            audience_snapshot=row["audience_snapshot"],
            granted_seconds_snapshot=row["granted_seconds_snapshot"],
            price_snapshot=Decimal(row["price_snapshot"]),
            voided_at=datetime.fromisoformat(row["voided_at"]) if row["voided_at"] else None,
        )

    def _build_purchase_response(
        self,
        member: MemberRecord,
        product: MembershipProductRecord,
        payment: PaymentRecord,
        base_transaction: MemberTimeTransactionRecord,
        bonus_seconds: int = 0,
    ) -> MemberPurchaseResponse:
        applied_promo = self._applied_promo_summary_for_redemption(base_transaction.promo_redemption_id)
        base_seconds = base_transaction.seconds_delta
        return MemberPurchaseResponse(
            member=member,
            product=product,
            payment=payment,
            transaction=base_transaction,
            applied_promo=applied_promo,
            base_seconds=base_seconds,
            bonus_seconds=bonus_seconds,
            total_seconds_credited=base_seconds + bonus_seconds,
        )

    def quote_member_topup_promo(self, member_id: str, product_id: str) -> MemberTopupPromoQuoteResponse:
        self.require_usable_member(member_id)
        product = self.get_product(product_id)
        if product is None or not product.is_active:
            raise HTTPException(status_code=404, detail="Membership product not found")
        return build_member_topup_quote(
            promo_repository=self.promo_repository,
            product=product,
            member_id=member_id,
            now=current_utc_time(),
        )

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

        if request.idempotency_key:
            existing_payment = self.payment_repository.get_payment_by_idempotency_key(
                request.idempotency_key
            )
            if existing_payment is not None:
                base_transaction, bonus_transaction = self.resolve_purchase_transactions(
                    existing_payment.payment_id
                )
                if base_transaction is None or base_transaction.member_id != member_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Duplicate transaction key belongs to another payment",
                    )
                updated_member = self.get_member(member_id)
                if updated_member is None:
                    raise HTTPException(status_code=500, detail="Member disappeared after purchase")
                bonus_seconds = bonus_transaction.seconds_delta if bonus_transaction is not None else 0
                return self._build_purchase_response(
                    updated_member, product, existing_payment, base_transaction, bonus_seconds
                )

        open_shift = self.shift_repository.get_open_shift_for_user(actor.user_id)
        shift_id = open_shift.shift_id if open_shift is not None else None

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
            if cash_received is not None:
                if cash_received < product.price:
                    raise HTTPException(status_code=400, detail="Cash received cannot be lower than payment amount")
                change_given = money(cash_received - product.price)
        else:
            if cash_received is not None:
                raise HTTPException(status_code=400, detail="Cash received is only valid for cash payments")

        payment_created_at = current_utc_time()
        attribution = self._resolve_purchase_attribution(member.member_id, product, payment_created_at)

        # Stage 4: advisory Member Top-up promo resolution -- only ever
        # considered for eligible products (product_type == "member_topup"
        # and balance_type == "standard"; see is_member_topup_promo_eligible).
        # This never touches VIP-specific products, the ₱250 VIP product,
        # initial_membership, or disabled products.
        promo_selection = None
        if is_member_topup_promo_eligible(product):
            candidates = [
                promo for promo in self.promo_repository.list(owner_states=["enabled"])
                if promo.audience == "member_topup"
            ]
            total_counts = {
                promo.promo_id: self.promo_repository.count_total_redemptions(promo.promo_id)
                for promo in candidates
            }
            member_counts = {
                promo.promo_id: self.promo_repository.count_member_redemptions(promo.promo_id, member_id)
                for promo in candidates
            }
            promo_selection = select_best_promo(
                candidates,
                audience="member_topup",
                amount=product.price,
                now=payment_created_at,
                member_id=member_id,
                total_redemption_counts=total_counts,
                member_redemption_counts=member_counts,
                base_granted_seconds=0,
            )

        resolved_promo_id = (
            promo_selection.snapshot.promo_id
            if promo_selection is not None and promo_selection.selected_promo is not None
            else None
        )
        if request.expected_promo_id is not None and request.expected_promo_id != resolved_promo_id:
            raise HTTPException(
                status_code=409,
                detail="The expected promo is no longer available. Please refresh and try again.",
            )

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
            idempotency_key=request.idempotency_key,
            status="completed",
            created_at=payment_created_at,
            pc_name_snapshot=attribution.pc_name_snapshot,
            income_group_id_snapshot=attribution.income_group_id_snapshot,
            income_group_name_snapshot=attribution.income_group_name_snapshot,
            attribution_status=attribution.status,  # type: ignore[arg-type]
            attribution_assigned_at=attribution.assigned_at,
            attribution_session_id=attribution.session_id,
        )

        if resolved_promo_id is not None:
            # BEGIN IMMEDIATE opens and commits entirely inside this call --
            # re-verifies the promo from scratch (never trusts the advisory
            # selection above) before writing payment/base/bonus/redemption
            # together, all-or-nothing.
            outcome = claim_member_topup_promo(
                member_repository=self,
                payment_repository=self.payment_repository,
                promo_repository=self.promo_repository,
                member_id=member_id,
                product=product,
                promo_id=resolved_promo_id,
                payment=payment,
                actor=actor,
                attribution=attribution,
                now=payment_created_at,
            )
            updated_member = self.get_member(member_id)
            if updated_member is None:
                raise HTTPException(status_code=500, detail="Member disappeared after purchase")
            return self._build_purchase_response(
                updated_member,
                product,
                outcome.payment,
                outcome.base_transaction,
                outcome.bonus_transaction.seconds_delta,
            )

        # Atomic: the payment and the balance it funds are recorded in a
        # single SQLite transaction. If either step fails, nothing commits
        # -- there is no window where money/sale exists without the
        # matching balance credit, or vice versa. Unchanged from before
        # Stage 4 -- no promo applied, so behavior is identical.
        with get_connection() as connection:
            self.payment_repository.create_payment(payment, connection=connection)
            transaction = self.apply_balance_delta(
                member.member_id,
                "membership_purchase" if product.product_type == "initial_membership" else "promo_topup",
                product.time_seconds,
                actor.user_id,
                amount_paid=product.price,
                related_payment_id=payment.payment_id,
                notes=product.name,
                balance_type=product.balance_type,
                connection=connection,
            )
        updated_member = self.get_member(member_id)
        if updated_member is None:
            raise HTTPException(status_code=500, detail="Member disappeared after purchase")
        return self._build_purchase_response(updated_member, product, payment, transaction, 0)

    def _standard_topup_product(
        self, calculated_time: CalculatedTimeResponse, created_at: datetime | None = None
    ) -> MembershipProductRecord:
        """A synthetic, never-persisted MembershipProductRecord standing in
        for a custom Standard-rate cash top-up (see purchase_custom_amount)
        -- lets it reuse _resolve_purchase_attribution/_build_purchase_response
        exactly like the catalog purchase() path, with no membership_products
        row to look up."""
        now = created_at or current_utc_time()
        return MembershipProductRecord(
            product_id="standard-topup",
            name="Standard top-up",
            price=calculated_time.amount,
            time_seconds=calculated_time.duration_seconds,
            product_type="member_topup",
            balance_type="standard",
            is_active=True,
            sort_order=0,
            created_at=now,
            updated_at=now,
        )

    def purchase_custom_amount(
        self,
        member_id: str,
        calculated_time: CalculatedTimeResponse,
        request: MemberCustomTopupRequest,
        actor: UserRecord,
    ) -> MemberPurchaseResponse:
        """Same payment/ledger/attribution guarantees as purchase(), but for
        a cash amount priced off the active metered (Standard) rate instead
        of a fixed MembershipProduct -- for a member wanting less time than
        the smallest catalog package covers. Always credits Standard
        balance; never promo-eligible (promos are scoped to catalog
        products only, see is_member_topup_promo_eligible)."""
        member = self.require_usable_member(member_id)

        if request.idempotency_key:
            existing_payment = self.payment_repository.get_payment_by_idempotency_key(
                request.idempotency_key
            )
            if existing_payment is not None:
                base_transaction, _ = self.resolve_purchase_transactions(existing_payment.payment_id)
                if base_transaction is None or base_transaction.member_id != member_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Duplicate transaction key belongs to another payment",
                    )
                updated_member = self.get_member(member_id)
                if updated_member is None:
                    raise HTTPException(status_code=500, detail="Member disappeared after purchase")
                product = self._standard_topup_product(calculated_time)
                return self._build_purchase_response(
                    updated_member, product, existing_payment, base_transaction, 0
                )

        open_shift = self.shift_repository.get_open_shift_for_user(actor.user_id)
        shift_id = open_shift.shift_id if open_shift is not None else None

        cash_received = money(request.cash_received) if request.cash_received is not None else None
        change_given: Decimal | None = None
        if request.payment_method == "cash":
            if cash_received is not None:
                if cash_received < calculated_time.amount:
                    raise HTTPException(status_code=400, detail="Cash received cannot be lower than payment amount")
                change_given = money(cash_received - calculated_time.amount)
        else:
            if cash_received is not None:
                raise HTTPException(status_code=400, detail="Cash received is only valid for cash payments")

        payment_created_at = current_utc_time()
        product = self._standard_topup_product(calculated_time, created_at=payment_created_at)
        attribution = self._resolve_purchase_attribution(member.member_id, product, payment_created_at)

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
            idempotency_key=request.idempotency_key,
            status="completed",
            created_at=payment_created_at,
            pc_name_snapshot=attribution.pc_name_snapshot,
            income_group_id_snapshot=attribution.income_group_id_snapshot,
            income_group_name_snapshot=attribution.income_group_name_snapshot,
            attribution_status=attribution.status,  # type: ignore[arg-type]
            attribution_assigned_at=attribution.assigned_at,
            attribution_session_id=attribution.session_id,
        )

        # Atomic, same as purchase(): the payment and the balance it funds
        # commit in one SQLite transaction, or neither does.
        with get_connection() as connection:
            self.payment_repository.create_payment(payment, connection=connection)
            transaction = self.apply_balance_delta(
                member.member_id,
                "promo_topup",
                product.time_seconds,
                actor.user_id,
                amount_paid=product.price,
                related_payment_id=payment.payment_id,
                notes=product.name,
                balance_type="standard",
                connection=connection,
            )
        updated_member = self.get_member(member_id)
        if updated_member is None:
            raise HTTPException(status_code=500, detail="Member disappeared after purchase")
        return self._build_purchase_response(updated_member, product, payment, transaction, 0)

    def _build_refund_response(
        self,
        member: MemberRecord,
        payment: PaymentRecord,
        reversal_transaction: MemberTimeTransactionRecord,
        base_seconds: int,
        bonus_seconds: int,
    ) -> MemberPurchaseRefundResponse:
        applied_promo = self._applied_promo_summary_for_redemption(reversal_transaction.promo_redemption_id)
        return MemberPurchaseRefundResponse(
            member=member,
            payment=payment,
            reversal_transaction=reversal_transaction,
            applied_promo=applied_promo,
            base_seconds=base_seconds,
            bonus_seconds=bonus_seconds,
            total_seconds_reversed=base_seconds + bonus_seconds,
        )

    def refund_purchase(
        self,
        payment_id: str,
        request: MemberPurchaseRefundRequest,
        actor: UserRecord,
    ) -> MemberPurchaseRefundResponse:
        payment = self.payment_repository.get_payment(payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment.status != "completed":
            existing_refund = self.get_refund_transaction_by_payment_id(payment_id)
            if existing_refund is not None:
                member = self.get_member(existing_refund.member_id)
                if member is None:
                    raise HTTPException(status_code=500, detail="Member disappeared after refund")
                # existing_refund.seconds_delta is already the combined
                # (base + bonus) reversal for a promo refund -- reported as
                # base_seconds here with bonus_seconds=0 since the two are
                # not separately recoverable from one combined ledger row,
                # same total either way.
                return self._build_refund_response(
                    member, payment, existing_refund, -existing_refund.seconds_delta, 0
                )
            raise HTTPException(status_code=409, detail="This transaction has already been voided")

        # Stage 4.1: base (and, for a promo purchase, bonus) are resolved
        # by exact stored transaction id via the linked promo_redemptions
        # row -- never by an ORDER BY created_at tie-break, since a promo
        # purchase's base and bonus rows can legitimately share the same
        # timestamp. Raises 500 before any mutation if the linkage is
        # corrupted (see resolve_purchase_transactions's docstring).
        original_transaction, bonus_transaction = self.resolve_purchase_transactions(payment_id)
        if original_transaction is None:
            raise HTTPException(status_code=409, detail="Payment is not a member purchase")
        # Owner-confirmed rule: VIP promo purchases are non-refundable, full
        # stop, regardless of usage state. This is distinct from an atomic
        # purchase failing before it ever completes (handled entirely by
        # MemberRepository.purchase()'s single-transaction commit/rollback,
        # which never leaves a completed payment needing a refund) -- this
        # check only ever runs against an already-completed VIP purchase.
        if original_transaction.balance_type == "vip":
            raise HTTPException(status_code=409, detail="VIP promo purchases are non-refundable.")
        member = self.get_member(original_transaction.member_id)
        if member is None:
            raise HTTPException(status_code=404, detail="Member not found")
        if self.get_refund_transaction_by_payment_id(payment_id) is not None:
            raise HTTPException(status_code=409, detail="This transaction has already been refunded")
        if self.has_active_reservation(member.member_id):
            raise HTTPException(
                status_code=409,
                detail="This purchase cannot be refunded while the member has an active session",
            )

        bonus_seconds = bonus_transaction.seconds_delta if bonus_transaction is not None else 0
        redemption_id = original_transaction.promo_redemption_id
        base_seconds = original_transaction.seconds_delta
        total_seconds = base_seconds + bonus_seconds

        if member.time_balance_seconds < total_seconds:
            raise HTTPException(
                status_code=409,
                detail="This purchase cannot be fully refunded because some of the time has already been used",
            )

        notes = request.reason
        if request.notes and request.notes.strip():
            notes = f"{notes}: {request.notes.strip()}"

        # Atomic: payment void, balance reversal, and promo-redemption void
        # (when applicable) all commit together or not at all -- mirrors
        # the Stage 3 billing_service.void_payment pattern.
        with get_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            voided_payment = self.payment_repository.void_payment(
                payment_id,
                actor.user_id,
                notes,
                connection=connection,
            )
            if voided_payment is None:
                raise HTTPException(status_code=404, detail="Payment not found")
            reversal_transaction = self.apply_balance_delta(
                member.member_id,
                "purchase_refund",
                -total_seconds,
                actor.user_id,
                amount_paid=-payment.amount,
                related_payment_id=payment.payment_id,
                notes=notes,
                connection=connection,
                promo_redemption_id=redemption_id,
            )
            if redemption_id is not None:
                self.promo_repository.void_redemption_for_payment(connection, payment_id, notes, current_utc_time())

        updated_member = self.get_member(member.member_id)
        if updated_member is None:
            raise HTTPException(status_code=500, detail="Member disappeared after refund")
        with get_connection() as connection:
            self.record_member_audit(
                connection,
                "member_purchase_refunded",
                actor,
                updated_member,
                {
                    "payment_id": payment.payment_id,
                    "amount": str(payment.amount),
                    "seconds_delta": total_seconds,
                    "reason": request.reason,
                },
            )
        return self._build_refund_response(updated_member, voided_payment, reversal_transaction, base_seconds, bonus_seconds)
