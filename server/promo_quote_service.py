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
from server.promo_time_based import select_active_time_based_rate
from server.rate_calculator import calculate_metered_time
from server.rate_eligibility import DEFAULT_SHOP_TIMEZONE
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
    timezone_name: str = DEFAULT_SHOP_TIMEZONE,
) -> PromoQuoteResponse:
    audience = "vip" if is_vip_station else "standard"
    station_scope = "vip" if is_vip_station else "standard"

    base_seconds = (
        resolve_normal_vip_seconds(rate_repository, amount)
        if is_vip_station
        else resolve_normal_standard_seconds(rate_repository, amount)
    )

    enabled_promos = promo_repository.list(owner_states=["enabled"])

    # Stage 7: a Time-Based Rate promo temporarily REPLACES the Standard
    # linear amount->time conversion for the length of its recurring
    # window. Standard-only; it never applies on a VIP station, never
    # touches packages, and is resolved entirely independently of the
    # purchase-promo selection below. `time_based_result` is non-None only
    # when its promotional rate strictly beats `base_seconds`.
    time_based_result = None
    if not is_vip_station:
        time_based_candidates = [
            promo for promo in enabled_promos if promo.promo_type == "time_based_rate"
        ]
        time_based_result = select_active_time_based_rate(
            time_based_candidates,
            amount=amount,
            now_utc=now,
            timezone_name=timezone_name,
            normal_seconds=base_seconds,
        )
    time_based_seconds = time_based_result.promo_seconds if time_based_result is not None else None

    # The baseline a purchase promo must strictly beat. When no Time-Based
    # Rate promo is active this is exactly `base_seconds` (max(x, 0) -> x;
    # max(None-as-0, 0) -> 0 -> None), so purchase-promo behaviour is
    # unchanged. When one IS active, the promotional rate becomes the
    # baseline, so a purchase promo wins only if it beats the promo rate.
    _effective = max(base_seconds or 0, time_based_seconds or 0)
    effective_base = _effective if _effective > 0 else None

    candidates = [
        promo
        for promo in enabled_promos
        if promo.promo_type == "purchase" and promo.audience == audience
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
        base_granted_seconds=effective_base,
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

    if time_based_result is not None:
        return PromoQuoteResponse(
            pc_name=pc_name,
            action=action,
            amount=amount,
            station_scope=station_scope,
            base_seconds=base_seconds,
            final_seconds=time_based_result.promo_seconds,
            benefit_seconds=time_based_result.benefit_seconds,
            source_type="time_based_rate",
            promo_id=None,
            promo_name=None,
            time_based_promo_id=time_based_result.promo.promo_id,
            time_based_promo_name=time_based_result.promo.name,
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
