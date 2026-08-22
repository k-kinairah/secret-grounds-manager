"""Stage 3: the one place Standard/VIP promo-applied Start Session / Add
Time actually claims a redemption. A quote (server/promo_quote_service.py)
is advisory only -- everything here re-reads and re-verifies from scratch
inside one BEGIN IMMEDIATE transaction, so nothing the client sent (amount
aside) is ever trusted.

The outer transaction here covers exactly session creation/update, payment
creation, and the promo_redemptions insert -- never the async command sent
to the PC client afterward, which cannot participate in a SQL transaction
and is left to the existing post-write recovery pattern
(session_service.py's "review_required"/"failed" handling), unchanged.
"""

from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException

from server.database import get_connection
from server.income_attribution import attribution_for_known_pc
from server.payment_repository import PaymentRepository
from server.pc_layout_repository import IncomeGroupRepository, PCLayoutRepository
from server.promo_eligibility import SelectionAction, select_best_promo
from server.promo_repository import PromoRepository
from server.schemas import PaymentRecord, SessionRecord
from server.session_repository import SessionRepository


class PromoClaimOutcome:
    def __init__(self, session: SessionRecord, payment: PaymentRecord, promo_id: str, promo_name: str) -> None:
        self.session = session
        self.payment = payment
        self.promo_id = promo_id
        self.promo_name = promo_name


def claim_standard_or_vip_promo(
    *,
    session_repository: SessionRepository,
    payment_repository: PaymentRepository,
    promo_repository: PromoRepository,
    pc_layout_repository: PCLayoutRepository,
    income_group_repository: IncomeGroupRepository,
    session: SessionRecord,
    payment: PaymentRecord,
    promo_id: str,
    audience: str,
    action: SelectionAction,
    station_scope: str,
    amount: Decimal,
    is_update: bool,
    now: datetime,
    actor_user_id: str | None = None,
) -> PromoClaimOutcome:
    """Atomically re-verifies `promo_id` is still the winning promo for
    this exact transaction and, if so, creates/updates the session, the
    payment, and the promo_redemptions row together. Raises HTTPException
    (404/409) and writes NOTHING if the promo is no longer available --
    the caller must not fall back silently after a customer has already
    been shown promotional time; the dashboard must requote.

    `is_update` selects create_session (Start Session) vs. update_session
    (Add Time, session already exists and was mutated in memory by the
    caller before this call).
    """
    with get_connection() as connection:
        connection.execute("BEGIN IMMEDIATE")

        fresh_promo = promo_repository.get_for_update(connection, promo_id)
        if fresh_promo is None:
            raise HTTPException(status_code=404, detail="Promo not found")

        # Re-run the exact same pure selection logic used for the quote,
        # against a single candidate, so "still the best/only eligible
        # option" is re-derived rather than assumed.
        recheck = select_best_promo(
            [fresh_promo],
            audience=audience,
            amount=amount,
            now=now,
            action=action,
            station_scope=station_scope,
            total_redemption_counts={
                fresh_promo.promo_id: connection.execute(
                    "SELECT COUNT(*) FROM promo_redemptions WHERE promo_id = ? AND voided_at IS NULL",
                    (fresh_promo.promo_id,),
                ).fetchone()[0]
            },
            base_granted_seconds=0,  # any positive eligible match is accepted here; the
            # caller (session_service) already confirmed this promo beat the normal
            # rate/package at quote time using the real baseline.
        )
        if recheck.selected_promo is None:
            raise HTTPException(
                status_code=409,
                detail="This promo is no longer available. Please refresh and try again.",
            )

        attribution = attribution_for_known_pc(
            pc_layout_repository, income_group_repository, session.pc_name, session.session_id, now
        )
        finalized_payment = payment.model_copy(
            update={
                "pc_name_snapshot": attribution.pc_name_snapshot,
                "income_group_id_snapshot": attribution.income_group_id_snapshot,
                "income_group_name_snapshot": attribution.income_group_name_snapshot,
                "attribution_status": attribution.status,
                "attribution_assigned_at": attribution.assigned_at,
                "attribution_session_id": attribution.session_id,
            }
        )

        if is_update:
            session_repository.update_session(session, connection=connection)
        else:
            session_repository.create_session(session, connection=connection)
        payment_repository.create_payment(finalized_payment, connection=connection)
        promo_repository.claim_redemption_for_standard_or_vip(
            connection,
            fresh_promo,
            session_id=session.session_id,
            payment_id=finalized_payment.payment_id,
            pc_name_snapshot=attribution.pc_name_snapshot,
            income_group_id_snapshot=attribution.income_group_id_snapshot,
            income_group_name_snapshot=attribution.income_group_name_snapshot,
            # The actor performing THIS action (matches payment's
            # recorded_by_user_id), not the session's original starter --
            # those differ whenever Add Time is processed by someone other
            # than whoever ran Start Session. Falls back to the session
            # starter only if no actor was supplied (e.g. Start Session,
            # where they're the same person by construction).
            operator_user_id=actor_user_id if actor_user_id is not None else session.created_by_user_id,
            now=now,
        )

    return PromoClaimOutcome(session, finalized_payment, fresh_promo.promo_id, fresh_promo.name)
