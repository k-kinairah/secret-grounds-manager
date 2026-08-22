"""Stage 3: promo-aware quoting for Standard/VIP prepaid Start Session and
Add Time. A quote is advisory only -- see server/promo_session_integration.py
for the atomic final-confirmation recheck that actually claims a redemption.
Never inserts into rate_profiles and never merges promos into GET /rates;
permanent rates/packages are read-only inputs to the comparison here.
"""

from datetime import datetime
from decimal import Decimal

from server.promo_eligibility import SelectionAction, select_best_promo
from server.promo_repository import PromoRepository
from server.rate_calculator import calculate_metered_time
from server.rate_repository import RateRepository
from server.schemas import PromoQuoteResponse


def resolve_normal_standard_seconds(rate_repository: RateRepository, amount: Decimal) -> int | None:
    """The permanent ₱5=20min metered rate (or whatever the shop's single
    active metered rate currently is) -- read-only, never recomputed or
    altered here."""
    rate = rate_repository.get_active_metered_rate()
    if rate is None:
        return None
    try:
        return calculate_metered_time(amount, rate).duration_seconds
    except Exception:
        return None


def resolve_normal_vip_seconds(rate_repository: RateRepository, amount: Decimal) -> int | None:
    """The normal eligible VIP package (e.g. the permanent ₱30/hour or
    ₱250/10-hour rows) whose price matches `amount` exactly -- read-only,
    never recomputed or altered here."""
    for rate in rate_repository.list_rates(include_inactive=False):
        if rate.rate_type == "package" and rate.station_scope in ("vip", "any") and rate.price == amount:
            return rate.duration_seconds
    return None


def build_promo_quote(
    *,
    promo_repository: PromoRepository,
    rate_repository: RateRepository,
    pc_name: str,
    is_vip_station: bool,
    action: SelectionAction,
    amount: Decimal,
    now: datetime,
) -> PromoQuoteResponse:
    audience = "vip" if is_vip_station else "standard"
    station_scope = "vip" if is_vip_station else "standard"

    base_seconds = (
        resolve_normal_vip_seconds(rate_repository, amount)
        if is_vip_station
        else resolve_normal_standard_seconds(rate_repository, amount)
    )

    candidates = [
        promo
        for promo in promo_repository.list(owner_states=["enabled"])
        if promo.audience == audience
    ]
    total_counts = {
        promo.promo_id: promo_repository.count_total_redemptions(promo.promo_id) for promo in candidates
    }

    result = select_best_promo(
        candidates,
        audience=audience,
        amount=amount,
        now=now,
        action=action,
        station_scope=station_scope,
        total_redemption_counts=total_counts,
        base_granted_seconds=base_seconds,
    )

    if result.selected_promo is not None:
        return PromoQuoteResponse(
            pc_name=pc_name,
            action=action,
            amount=amount,
            station_scope=station_scope,
            base_seconds=base_seconds,
            final_seconds=result.granted_seconds,
            benefit_seconds=result.customer_benefit_seconds,
            source_type="promo",
            promo_id=result.selected_promo.promo_id,
            promo_name=result.selected_promo.name,
            quoted_at=now,
        )

    if base_seconds is not None:
        return PromoQuoteResponse(
            pc_name=pc_name,
            action=action,
            amount=amount,
            station_scope=station_scope,
            base_seconds=base_seconds,
            final_seconds=base_seconds,
            benefit_seconds=0,
            source_type="normal_package" if is_vip_station else "normal_rate",
            promo_id=None,
            promo_name=None,
            quoted_at=now,
        )

    return PromoQuoteResponse(
        pc_name=pc_name,
        action=action,
        amount=amount,
        station_scope=station_scope,
        base_seconds=None,
        final_seconds=None,
        benefit_seconds=0,
        source_type="none",
        promo_id=None,
        promo_name=None,
        quoted_at=now,
    )
