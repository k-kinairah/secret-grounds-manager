"""Stage 4: Member Top-up promo quoting and atomic purchase claim.

Mirrors the Stage 3 Standard/VIP pattern (server/promo_quote_service.py,
server/promo_session_integration.py) but for member balance top-ups:
- Quoting is advisory only, no writes, no redemption-slot reservation.
- The atomic claim re-verifies everything from scratch inside one
  BEGIN IMMEDIATE transaction and is the only place redemption limits are
  actually enforced.

Eligible products only: product_type == "member_topup" and
balance_type == "standard" (see is_member_topup_promo_eligible below).
This is the exact rule Stage 4 requires: use existing product_type/
balance_type metadata, never product names. It naturally excludes the
₱250 VIP membership product (balance_type == "vip"), every VIP-specific
product, every initial_membership (Create Account) product, and any
disabled product.

Both base and bonus seconds are always credited to the member's ordinary
(Standard) balance -- no VIP 2:1 arithmetic happens here. That conversion
only ever applies at session-consumption time (server/balance_conversion.py),
which this module never touches.
"""

import sqlite3
from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from fastapi import HTTPException

from server.database import get_connection
from server.income_attribution import AttributionSnapshot
from server.payment_repository import PaymentRepository
from server.promo_eligibility import select_best_promo
from server.promo_repository import PromoRepository
from server.schemas import (
    MembershipProductRecord,
    MemberTimeTransactionRecord,
    MemberTopupPromoQuoteResponse,
    PaymentRecord,
    UserRecord,
)
from server.time_utils import current_utc_time


def is_member_topup_promo_eligible(product: MembershipProductRecord) -> bool:
    return (
        product.is_active
        and product.product_type == "member_topup"
        and product.balance_type == "standard"
    )


def build_member_topup_quote(
    *,
    promo_repository: PromoRepository,
    product: MembershipProductRecord,
    member_id: str,
    now: datetime,
) -> MemberTopupPromoQuoteResponse:
    """Advisory only -- no database writes, no redemption-slot reservation.
    Caller (member_repository/main.py route) is responsible for member/
    product existence and active-status validation before calling this;
    an ineligible product simply gets bonus_seconds=0 back here rather
    than raising, so a cashier browsing products sees "no promo" instead
    of an error for every non-eligible product."""
    base_seconds = product.time_seconds
    if not is_member_topup_promo_eligible(product):
        return MemberTopupPromoQuoteResponse(
            product_id=product.product_id,
            product_name=product.name,
            product_price=product.price,
            base_seconds=base_seconds,
            promo_id=None,
            promo_name=None,
            bonus_seconds=0,
            total_seconds=base_seconds,
            promo_ends_at=None,
            quoted_at=now,
        )

    candidates = [
        promo for promo in promo_repository.list(owner_states=["enabled"]) if promo.audience == "member_topup"
    ]
    total_counts = {promo.promo_id: promo_repository.count_total_redemptions(promo.promo_id) for promo in candidates}
    member_counts = {
        promo.promo_id: promo_repository.count_member_redemptions(promo.promo_id, member_id) for promo in candidates
    }
    result = select_best_promo(
        candidates,
        audience="member_topup",
        amount=product.price,
        now=now,
        member_id=member_id,
        total_redemption_counts=total_counts,
        member_redemption_counts=member_counts,
        base_granted_seconds=0,  # purely additive -- any positive eligible bonus wins
    )

    if result.selected_promo is None:
        return MemberTopupPromoQuoteResponse(
            product_id=product.product_id,
            product_name=product.name,
            product_price=product.price,
            base_seconds=base_seconds,
            promo_id=None,
            promo_name=None,
            bonus_seconds=0,
            total_seconds=base_seconds,
            promo_ends_at=None,
            quoted_at=now,
        )

    bonus_seconds = result.customer_benefit_seconds
    return MemberTopupPromoQuoteResponse(
        product_id=product.product_id,
        product_name=product.name,
        product_price=product.price,
        base_seconds=base_seconds,
        promo_id=result.snapshot.promo_id,
        promo_name=result.snapshot.promo_name_snapshot,
        bonus_seconds=bonus_seconds,
        total_seconds=base_seconds + bonus_seconds,
        promo_ends_at=result.snapshot.ends_at_snapshot,
        quoted_at=now,
    )


class MemberTopupClaimOutcome:
    def __init__(
        self,
        payment: PaymentRecord,
        base_transaction: MemberTimeTransactionRecord,
        bonus_transaction: MemberTimeTransactionRecord,
        promo_id: str,
        promo_name: str,
        redemption_id: str,
    ) -> None:
        self.payment = payment
        self.base_transaction = base_transaction
        self.bonus_transaction = bonus_transaction
        self.promo_id = promo_id
        self.promo_name = promo_name
        self.redemption_id = redemption_id


def claim_member_topup_promo(
    *,
    member_repository: "MemberRepository",  # noqa: F821 -- see local import below
    payment_repository: PaymentRepository,
    promo_repository: PromoRepository,
    member_id: str,
    product: MembershipProductRecord,
    promo_id: str,
    payment: PaymentRecord,
    actor: UserRecord,
    attribution: AttributionSnapshot,
    now: datetime,
) -> MemberTopupClaimOutcome:
    """Atomically re-verifies `promo_id` is still the winning promo and, if
    so, creates the payment, the base ledger row, the bonus ledger row,
    and the promo_redemptions row together inside one BEGIN IMMEDIATE
    transaction. Raises HTTPException (404/409) and writes NOTHING if the
    promo is no longer available.

    Base transaction_type follows the existing rule unchanged
    ("membership_purchase" for initial_membership, "promo_topup"
    otherwise -- member_topup products are always "promo_topup", promo or
    not). The bonus row reuses that same existing "promo_topup" type
    (Stage 1 deliberately avoided adding a new transaction_type / CHECK
    rebuild) -- base vs. bonus is distinguished by notes and by the shared
    promo_redemption_id linking both to one promo_redemptions row.
    """
    redemption_id = str(uuid4())
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")

        fresh_promo = promo_repository.get_for_update(connection, promo_id)
        if fresh_promo is None:
            raise HTTPException(status_code=404, detail="Promo not found")

        total_count = connection.execute(
            "SELECT COUNT(*) FROM promo_redemptions WHERE promo_id = ? AND voided_at IS NULL",
            (fresh_promo.promo_id,),
        ).fetchone()[0]
        member_count = connection.execute(
            "SELECT COUNT(*) FROM promo_redemptions WHERE promo_id = ? AND member_id = ? AND voided_at IS NULL",
            (fresh_promo.promo_id, member_id),
        ).fetchone()[0]
        recheck = select_best_promo(
            [fresh_promo],
            audience="member_topup",
            amount=product.price,
            now=now,
            member_id=member_id,
            total_redemption_counts={fresh_promo.promo_id: total_count},
            member_redemption_counts={fresh_promo.promo_id: member_count},
            base_granted_seconds=0,
        )
        if recheck.selected_promo is None:
            raise HTTPException(
                status_code=409,
                detail="This promo is no longer available. Please refresh and try again.",
            )

        base_seconds = product.time_seconds
        bonus_seconds = recheck.customer_benefit_seconds

        payment_repository.create_payment(payment, connection=connection)

        base_transaction_type = "membership_purchase" if product.product_type == "initial_membership" else "promo_topup"
        base_transaction = member_repository.apply_balance_delta(
            member_id,
            base_transaction_type,
            base_seconds,
            actor.user_id,
            amount_paid=product.price,
            related_payment_id=payment.payment_id,
            notes=product.name,
            balance_type="standard",
            connection=connection,
            promo_redemption_id=redemption_id,
        )
        bonus_transaction = member_repository.apply_balance_delta(
            member_id,
            "promo_topup",
            bonus_seconds,
            actor.user_id,
            amount_paid=Decimal("0.00"),
            related_payment_id=payment.payment_id,
            notes=f"Promo bonus: {fresh_promo.name}",
            balance_type="standard",
            connection=connection,
            promo_redemption_id=redemption_id,
        )

        promo_repository.claim_redemption_for_member_topup(
            connection,
            fresh_promo,
            redemption_id=redemption_id,
            member_id=member_id,
            payment_id=payment.payment_id,
            base_transaction_id=base_transaction.transaction_id,
            bonus_transaction_id=bonus_transaction.transaction_id,
            pc_name_snapshot=attribution.pc_name_snapshot,
            income_group_id_snapshot=attribution.income_group_id_snapshot,
            income_group_name_snapshot=attribution.income_group_name_snapshot,
            operator_user_id=actor.user_id,
            now=now,
        )

    return MemberTopupClaimOutcome(
        payment=payment,
        base_transaction=base_transaction,
        bonus_transaction=bonus_transaction,
        promo_id=fresh_promo.promo_id,
        promo_name=fresh_promo.name,
        redemption_id=redemption_id,
    )
