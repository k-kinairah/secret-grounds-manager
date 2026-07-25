import os
from datetime import datetime
from decimal import Decimal, ROUND_CEILING
from uuid import uuid4

from fastapi import HTTPException

from server.payment_repository import PaymentRepository
from server.schemas import (
    BillingPaymentStatus,
    BillingSummary,
    PaymentCreateRequest,
    PaymentRecord,
    SessionEventRecord,
    SessionRecord,
)
from server.session_repository import SessionRepository
from server.shift_repository import ShiftRepository
from server.time_utils import current_utc_time
from server.user_repository import UserRepository


def money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def get_payment_status(total_amount: Decimal, total_paid: Decimal) -> BillingPaymentStatus:
    normalized_total = money(total_amount)
    normalized_paid = money(total_paid)

    if normalized_paid == Decimal("0.00"):
        return "paid" if normalized_total == Decimal("0.00") else "unpaid"
    if normalized_paid < normalized_total:
        return "partially_paid"
    if normalized_paid == normalized_total:
        return "paid"
    return "overpaid"


def calculate_open_time_estimate(
    started_at: datetime,
    as_of: datetime,
    rate_price: Decimal,
    rate_duration_seconds: int,
) -> tuple[int, int, Decimal]:
    actual_elapsed_seconds = max(0, int((as_of - started_at).total_seconds()))
    if actual_elapsed_seconds == 0:
        return 0, 0, money(Decimal("0.00"))

    billing_units = int(
        (Decimal(actual_elapsed_seconds) / Decimal(rate_duration_seconds))
        .to_integral_value(rounding=ROUND_CEILING)
    )
    billed_seconds = billing_units * rate_duration_seconds
    estimated_total = money(rate_price * Decimal(billing_units))
    return actual_elapsed_seconds, billed_seconds, estimated_total


class BillingService:
    """Derived billing rules for sessions and auditable payment records."""

    def __init__(
        self,
        session_repository: SessionRepository,
        payment_repository: PaymentRepository,
        shift_repository: ShiftRepository | None = None,
        user_repository: UserRepository | None = None,
    ) -> None:
        self.session_repository = session_repository
        self.payment_repository = payment_repository
        self.shift_repository = shift_repository
        self.user_repository = user_repository or UserRepository()

    def get_payment_shift_id(self, actor_user_id: str | None) -> str | None:
        if actor_user_id is None or self.shift_repository is None:
            return None

        actor = self.user_repository.get_user(actor_user_id)
        if actor is None:
            raise HTTPException(status_code=401, detail="Authenticated user was not found")

        open_shift = self.shift_repository.get_open_shift_for_user(actor.user_id)
        if open_shift is not None:
            return open_shift.shift_id

        admin_override_enabled = (
            os.getenv("SECRET_GROUNDS_ALLOW_ADMIN_PAYMENTS_WITHOUT_SHIFT", "false")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
        if actor.role in {"owner", "admin"} and admin_override_enabled:
            return None

        return self.shift_repository.require_payment_shift(actor)

    def summarize_session(self, session: SessionRecord) -> BillingSummary:
        payments = self.payment_repository.list_payments(session.session_id)
        total_paid = money(
            sum(
                (payment.amount for payment in payments if payment.status == "completed"),
                Decimal("0.00"),
            )
        )
        display_total = session.total_amount
        estimated_balance_due: Decimal | None = None
        estimated_total_amount: Decimal | None = None
        estimated_billed_seconds: int | None = session.billed_seconds
        estimated_elapsed_seconds: int | None = session.actual_elapsed_seconds

        if (
            session.session_mode == "open_time"
            and session.status == "active"
            and session.metered_rate_price_snapshot is not None
            and session.metered_rate_duration_seconds_snapshot is not None
        ):
            (
                estimated_elapsed_seconds,
                estimated_billed_seconds,
                estimated_total_amount,
            ) = calculate_open_time_estimate(
                session.started_at,
                current_utc_time(),
                session.metered_rate_price_snapshot,
                session.metered_rate_duration_seconds_snapshot,
            )
            display_total = estimated_total_amount
            estimated_balance_due = money(max(display_total - total_paid, Decimal("0.00")))
        elif (
            session.session_mode == "member_balance"
            and session.status == "active"
            and session.member_balance_before_seconds is not None
        ):
            estimated_elapsed_seconds = max(
                0,
                int((current_utc_time() - session.started_at).total_seconds()),
            )
            estimated_billed_seconds = min(
                estimated_elapsed_seconds,
                session.total_seconds,
                session.member_balance_before_seconds,
            )

        balance_due = money(max(display_total - total_paid, Decimal("0.00")))
        payment_status = get_payment_status(display_total, total_paid)
        enriched_session = session.model_copy(
            update={
                "total_paid": total_paid,
                "balance_due": balance_due,
                "payment_status": payment_status,
                "actual_elapsed_seconds": estimated_elapsed_seconds,
                "billed_seconds": estimated_billed_seconds,
                "member_balance_after_seconds": (
                    max(session.member_balance_before_seconds - estimated_billed_seconds, 0)
                    if session.session_mode == "member_balance"
                    and session.member_balance_before_seconds is not None
                    and estimated_billed_seconds is not None
                    else session.member_balance_after_seconds
                ),
                "estimated_total_amount": estimated_total_amount,
                "estimated_balance_due": estimated_balance_due,
            }
        )

        return BillingSummary(
            session=enriched_session,
            base_amount=session.base_amount,
            additional_amount=session.additional_amount,
            total_amount=display_total,
            total_paid=total_paid,
            balance_due=balance_due,
            payment_status=payment_status,
            payments=payments,
        )

    def get_billing(self, session_id: str) -> BillingSummary:
        session = self.session_repository.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")

        return self.summarize_session(session)

    def enrich_session(self, session: SessionRecord) -> SessionRecord:
        return self.summarize_session(session).session

    def record_payment(
        self,
        session_id: str,
        request: PaymentCreateRequest,
        action_type: str = "manual",
        related_command_id: str | None = None,
        max_amount: Decimal | None = None,
        actor_user_id: str | None = None,
    ) -> BillingSummary:
        billing = self.get_billing(session_id)
        amount = money(request.amount)
        limit = money(max_amount) if max_amount is not None else billing.balance_due
        shift_id = self.get_payment_shift_id(actor_user_id)

        if amount > limit:
            raise HTTPException(
                status_code=400,
                detail="Payment amount cannot exceed the amount due",
            )

        cash_received = money(request.cash_received) if request.cash_received is not None else None
        change_given: Decimal | None = None

        if request.payment_method == "cash":
            if cash_received is None:
                cash_received = amount
            if cash_received < amount:
                raise HTTPException(
                    status_code=400,
                    detail="Cash received cannot be lower than payment amount",
                )
            change_given = money(cash_received - amount)
        elif cash_received is not None:
            raise HTTPException(
                status_code=400,
                detail="Cash received is only valid for cash payments",
            )

        payment = PaymentRecord(
            payment_id=str(uuid4()),
            session_id=session_id,
            amount=amount,
            payment_method=request.payment_method,
            cash_received=cash_received,
            change_given=change_given,
            reference_number=request.reference_number,
            notes=request.notes,
            action_type=action_type,  # type: ignore[arg-type]
            related_command_id=related_command_id,
            review_required=False,
            shift_id=shift_id,
            recorded_by_user_id=actor_user_id,
            status="completed",
            created_at=current_utc_time(),
        )
        self.payment_repository.create_payment(payment)
        self.session_repository.add_event(
            SessionEventRecord(
                event_id=str(uuid4()),
                session_id=session_id,
                event_type="payment_recorded",
                amount=amount,
                message="Payment recorded",
                created_at=current_utc_time(),
                payment_id=payment.payment_id,
                payment_method=payment.payment_method,
                performed_by_user_id=actor_user_id,
            )
        )
        updated_billing = self.get_billing(session_id)
        if (
            updated_billing.session.session_mode == "open_time"
            and updated_billing.session.settlement_pending
            and updated_billing.balance_due == Decimal("0.00")
        ):
            settled_session = updated_billing.session.model_copy(
                update={
                    "status": "completed",
                    "settlement_pending": False,
                }
            )
            self.session_repository.update_session(settled_session)
            self.session_repository.add_event(
                SessionEventRecord(
                    event_id=str(uuid4()),
                    session_id=session_id,
                    event_type="open_time_settled",
                    amount=amount,
                    message="Open-time settlement paid",
                    created_at=current_utc_time(),
                    payment_id=payment.payment_id,
                    payment_method=payment.payment_method,
                    performed_by_user_id=actor_user_id,
                )
            )
            return self.get_billing(session_id)

        return updated_billing

    def validate_payment_for_amount(
        self,
        request: PaymentCreateRequest,
        amount_due: Decimal,
    ) -> None:
        amount = money(request.amount)
        limit = money(amount_due)
        if amount <= Decimal("0.00"):
            raise HTTPException(status_code=400, detail="Payment amount must be positive")
        if amount > limit:
            raise HTTPException(
                status_code=400,
                detail="Payment amount cannot exceed the amount due",
            )
        if request.payment_method == "cash":
            cash_received = money(request.cash_received or amount)
            if cash_received < amount:
                raise HTTPException(
                    status_code=400,
                    detail="Cash received cannot be lower than payment amount",
                )
        elif request.cash_received is not None:
            raise HTTPException(
                status_code=400,
                detail="Cash received is only valid for cash payments",
            )

    def mark_payment_review_required(
        self,
        payment_id: str,
        related_command_id: str | None = None,
    ) -> None:
        self.payment_repository.mark_review_required(payment_id, related_command_id)

    def void_payment(
        self,
        payment_id: str,
        reason: str,
        actor_user_id: str | None = None,
    ) -> BillingSummary:
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise HTTPException(status_code=400, detail="Void reason is required")

        payment = self.payment_repository.get_payment(payment_id)
        if payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")
        if payment.status == "voided":
            raise HTTPException(status_code=409, detail="Payment is already voided")

        voided_payment = self.payment_repository.void_payment(
            payment_id,
            actor_user_id,
            cleaned_reason,
        )
        if voided_payment is None:
            raise HTTPException(status_code=404, detail="Payment not found")

        self.session_repository.add_event(
            SessionEventRecord(
                event_id=str(uuid4()),
                session_id=payment.session_id,
                event_type="payment_voided",
                amount=payment.amount,
                message=cleaned_reason,
                created_at=current_utc_time(),
                payment_id=payment.payment_id,
                payment_method=payment.payment_method,
                performed_by_user_id=actor_user_id,
            )
        )

        return self.get_billing(payment.session_id)
