from decimal import Decimal, ROUND_FLOOR

from fastapi import HTTPException

from server.schemas import CalculatedTimeResponse, RateProfile


def format_backend_money(value: Decimal) -> str:
    return f"₱{value.quantize(Decimal('0.01'))}"


def format_snapshot_duration(seconds: int) -> str:
    minutes = seconds // 60
    if minutes and seconds % 60 == 0:
        return f"{minutes} minute" if minutes == 1 else f"{minutes} minutes"

    return f"{seconds} second" if seconds == 1 else f"{seconds} seconds"


def metered_rate_snapshot(rate: RateProfile) -> str:
    return (
        f"{format_backend_money(rate.price)} per "
        f"{format_snapshot_duration(rate.duration_seconds)}"
    )


def vip_effective_metered_rate(rate: RateProfile, multiplier: Decimal) -> RateProfile:
    """The shop has one Standard-scoped metered rate. For an arbitrary cash
    top-up on a VIP PC we price it off that rate scaled by `multiplier`
    (>= 1): the price per block goes up, so the same peso buys fewer
    minutes. The result is always VIP-scoped so it passes the station
    eligibility gate (a Standard-scoped rate is rejected on a VIP PC), and
    its scaled price flows into the session's metered-rate snapshot for
    auditing. multiplier == 1 keeps the price but still re-scopes.
    """
    scaled_price = (rate.price * multiplier).quantize(Decimal("0.01"))
    suffix = "" if multiplier == Decimal("1") else f" (VIP {multiplier.normalize()}x)"
    return rate.model_copy(
        update={
            "price": scaled_price,
            "station_scope": "vip",
            "name": f"{rate.name}{suffix}",
        }
    )


def calculate_metered_time(amount: Decimal, rate: RateProfile) -> CalculatedTimeResponse:
    """Convert money to time using Decimal math, rounding down to whole seconds.

    Formula:
        floor((entered_amount / metered_price) * metered_duration_seconds)

    Rounding down guarantees the client is never granted more time than was paid for.
    """

    normalized_amount = amount.quantize(Decimal("0.01"))
    if normalized_amount <= Decimal("0.00"):
        raise HTTPException(status_code=400, detail="Amount must be greater than zero")

    if rate.rate_type != "metered" or not rate.is_active:
        raise HTTPException(status_code=409, detail="Active metered rate is missing")

    if rate.price <= Decimal("0.00") or rate.duration_seconds <= 0:
        raise HTTPException(status_code=409, detail="Active metered rate is invalid")

    calculated_seconds = int(
        ((normalized_amount * Decimal(rate.duration_seconds)) / rate.price).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )

    if calculated_seconds < 1:
        raise HTTPException(
            status_code=400,
            detail="Entered amount buys less than one second",
        )

    return CalculatedTimeResponse(
        amount=normalized_amount,
        duration_seconds=calculated_seconds,
        rate_name=rate.name,
        rate_snapshot=metered_rate_snapshot(rate),
    )
