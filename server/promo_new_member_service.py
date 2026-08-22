"""Stage 5: New Member promotional account-creation offers.

Mirrors the Stage 3/4 pattern (server/promo_quote_service.py,
server/promo_member_topup_service.py): quoting is advisory only (no
writes, no reservation); the atomic claim re-verifies everything from
scratch inside one BEGIN IMMEDIATE transaction and is the only place
redemption limits are actually enforced.

Unlike Member Top-up, a New Member promo's granted_seconds is the
member's ENTIRE initial balance, never an additive bonus on top of a
separate base credit -- exactly one initial-membership ledger row is ever
written (see claim_new_member_promo). Comparison against a same-price
permanent initial_membership product uses the Stage 3 "real baseline"
pattern (base_granted_seconds=matching product's time_seconds), not
Stage 4's "always additive" pattern (base_granted_seconds=0) -- the promo
must strictly beat the permanent product's normal time to be selected;
if no permanent product exists at that exact price, any positive,
otherwise-eligible promo wins (base_granted_seconds=None -> effective 0).

Never creates, edits, or deactivates a membership_products row: a promo
with no matching permanent product simply acts as its own temporary
account-creation offer, and naturally stops being selectable the moment
it expires or is disabled -- nothing to clean up.
"""

import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Callable
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
    NewMemberPromoQuoteResponse,
    PaymentRecord,
    UserRecord,
)

# MemberRepository is intentionally never imported here (only referenced
# via string type hints below) -- member_repository.py imports THIS
# module to call build_new_member_promo_quote/claim_new_member_promo, so
# a real import here would be circular.


def resolve_normal_new_member_product(
    member_repository: "MemberRepository", amount: Decimal  # noqa: F821
) -> MembershipProductRecord | None:
    """The active permanent initial_membership product whose price
    matches `amount` exactly -- read-only, never recomputed or altered
    here. None when no such product exists (the promo may then act as
    its own temporary offer -- see module docstring)."""
    for product in member_repository.list_products(include_inactive=False):
        if product.product_type == "initial_membership" and product.price == amount:
            return product
    return None


def build_new_member_promo_quote(
    *,
    promo_repository: PromoRepository,
    member_repository: "MemberRepository",  # noqa: F821
    amount: Decimal,
    now: datetime,
) -> NewMemberPromoQuoteResponse:
    normal_product = resolve_normal_new_member_product(member_repository, amount)
    base_seconds = normal_product.time_seconds if normal_product is not None else None

    candidates = [promo for promo in promo_repository.list(owner_states=["enabled"]) if promo.audience == "new_member"]
    total_counts = {promo.promo_id: promo_repository.count_total_redemptions(promo.promo_id) for promo in candidates}

    result = select_best_promo(
        candidates,
        audience="new_member",
        amount=amount,
        now=now,
        total_redemption_counts=total_counts,
        base_granted_seconds=base_seconds,
    )

    if result.selected_promo is not None:
        return NewMemberPromoQuoteResponse(
            amount=amount,
            normal_product_id=normal_product.product_id if normal_product is not None else None,
            normal_product_name=normal_product.name if normal_product is not None else None,
            normal_seconds=base_seconds,
            promo_id=result.snapshot.promo_id,
            promo_name=result.snapshot.promo_name_snapshot,
            final_seconds=result.granted_seconds,
            benefit_seconds=result.customer_benefit_seconds,
            promo_ends_at=result.snapshot.ends_at_snapshot,
            quoted_at=now,
            source_type="promo",
        )

    return NewMemberPromoQuoteResponse(
        amount=amount,
        normal_product_id=normal_product.product_id if normal_product is not None else None,
        normal_product_name=normal_product.name if normal_product is not None else None,
        normal_seconds=base_seconds,
        promo_id=None,
        promo_name=None,
        final_seconds=base_seconds or 0,
        benefit_seconds=0,
        promo_ends_at=None,
        quoted_at=now,
        source_type="normal_product",
    )


class NewMemberPromoClaimOutcome:
    def __init__(
        self,
        payment: PaymentRecord,
        base_transaction: MemberTimeTransactionRecord,
        promo_id: str,
        promo_name: str,
        redemption_id: str,
    ) -> None:
        self.payment = payment
        self.base_transaction = base_transaction
        self.promo_id = promo_id
        self.promo_name = promo_name
        self.redemption_id = redemption_id


def claim_new_member_promo(
    *,
    member_repository: "MemberRepository",  # noqa: F821
    payment_repository: PaymentRepository,
    promo_repository: PromoRepository,
    promo_id: str,
    amount: Decimal,
    member_id: str,
    member_insert: Callable[[sqlite3.Connection], None],
    payment: PaymentRecord,
    actor: UserRecord | None,
    attribution: AttributionSnapshot,
    now: datetime,
) -> NewMemberPromoClaimOutcome:
    """Atomically re-verifies `promo_id` is still the winning New Member
    promo and, if so, creates the member row (via `member_insert`, the
    caller's already-built INSERT closure -- see
    MemberRepository.create_member), the payment, exactly ONE
    initial-membership ledger row (never base+bonus), and the
    promo_redemptions row together inside one BEGIN IMMEDIATE
    transaction. Raises HTTPException (404/409) and writes NOTHING if the
    promo is no longer available, or if the username/phone uniqueness
    check inside `member_insert` fails (surfaces as the same 409 that
    non-promo account creation already raises).
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
        recheck = select_best_promo(
            [fresh_promo],
            audience="new_member",
            amount=amount,
            now=now,
            total_redemption_counts={fresh_promo.promo_id: total_count},
            base_granted_seconds=0,  # any positive eligible match is accepted here; the
            # caller (member_repository) already confirmed this promo beat the normal
            # initial_membership product (if any) at quote time using the real baseline.
        )
        if recheck.selected_promo is None:
            raise HTTPException(
                status_code=409,
                detail="This promo is no longer available. Please refresh and try again.",
            )

        member_insert(connection)
        payment_repository.create_payment(payment, connection=connection)

        base_transaction = member_repository.apply_balance_delta(
            member_id,
            "membership_purchase",
            fresh_promo.granted_seconds,
            actor.user_id if actor else None,
            amount_paid=amount,
            related_payment_id=payment.payment_id,
            notes=fresh_promo.name,
            balance_type="standard",
            connection=connection,
            promo_redemption_id=redemption_id,
        )

        promo_repository.claim_redemption_for_new_member(
            connection,
            fresh_promo,
            redemption_id=redemption_id,
            member_id=member_id,
            payment_id=payment.payment_id,
            base_transaction_id=base_transaction.transaction_id,
            pc_name_snapshot=attribution.pc_name_snapshot,
            income_group_id_snapshot=attribution.income_group_id_snapshot,
            income_group_name_snapshot=attribution.income_group_name_snapshot,
            operator_user_id=actor.user_id if actor else None,
            now=now,
        )

    return NewMemberPromoClaimOutcome(
        payment=payment,
        base_transaction=base_transaction,
        promo_id=fresh_promo.promo_id,
        promo_name=fresh_promo.name,
        redemption_id=redemption_id,
    )
