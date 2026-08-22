"""Stage 2: pure promo eligibility filtering and deterministic best-promo
selection. No session/rate/payment/member integration, no API routes, and
no redemption writes exist yet -- this module only decides *which* promo
(if any) would apply to a hypothetical transaction, given data the caller
already has in hand.

Deliberately pure: `now` and every redemption count are passed in
explicitly (see select_best_promo's parameters) rather than read from the
clock or the database here, so this module is trivially unit-testable and
never has a hidden dependency on wall-clock time or DB state.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Mapping, Sequence

from server.promo_status import derive_promo_status
from server.schemas import Promo, PromoAppliesTo, PromoAudience, PromoStationScope

SelectionAction = Literal["start_session", "add_time"]

NoPromoReason = Literal["no_candidates", "no_eligible_promo", "not_better_than_base"]


@dataclass(frozen=True)
class PromoSnapshot:
    """Exactly the *_snapshot columns server/database.py's promo_redemptions
    table already has (Stage 1) -- built here so a later redemption-writing
    stage can persist it as-is without re-deriving anything from the live
    `promos` row (which may have since been edited)."""

    promo_id: str
    promo_name_snapshot: str
    audience_snapshot: PromoAudience
    amount_mode_snapshot: str
    price_snapshot: Decimal
    granted_seconds_snapshot: int
    station_scope_snapshot: PromoStationScope | None
    applies_to_snapshot: PromoAppliesTo | None
    starts_at_snapshot: datetime
    ends_at_snapshot: datetime


@dataclass(frozen=True)
class PromoSelectionResult:
    selected_promo: Promo | None
    granted_seconds: int | None
    base_granted_seconds: int
    customer_benefit_seconds: int
    reason: Literal["selected"] | NoPromoReason
    snapshot: PromoSnapshot | None


def _amount_matches(promo: Promo, amount: Decimal) -> bool:
    """Decimal-only comparison, never float -- `amount` and `promo.price`
    are both already-normalized Decimal values (see
    normalize_positive_money in server/schemas.py)."""
    if promo.amount_mode == "exact":
        return amount == promo.price
    # amount_mode == "minimum" -- Stage 1 validation forbids this on any
    # audience other than member_topup, so no audience check is needed here.
    return amount >= promo.price


def _is_eligible(
    promo: Promo,
    now: datetime,
    audience: PromoAudience,
    action: SelectionAction | None,
    station_scope: PromoStationScope | None,
    amount: Decimal,
    member_id: str | None,
    total_redemption_counts: Mapping[str, int],
    member_redemption_counts: Mapping[str, int],
) -> bool:
    if promo.audience != audience:
        return False

    # Draft/Scheduled/Disabled/Ended (explicit or auto-expired) are all
    # ineligible -- only a currently-Active promo may ever be selected.
    # starts_at inclusive / ends_at exclusive is enforced inside
    # derive_promo_status, not reimplemented here.
    if derive_promo_status(promo.owner_state, promo.starts_at, promo.ends_at, now) != "Active":
        return False

    if not _amount_matches(promo, amount):
        return False

    if audience in ("standard", "vip"):
        # Walk-in/member identity is never required for these audiences --
        # per_member_redemption_limit is already forbidden for them at
        # Stage 1's schema-validation layer, so no per-member check applies.
        if promo.station_scope != station_scope:
            return False
        # applies_to is always set for standard/vip (Stage 1 requires it) --
        # a caller that omits `action` skips this check entirely.
        if action is not None and promo.applies_to not in (action, "both"):
            return False
    elif audience == "member_topup":
        if member_id is None:
            return False
    # audience == "new_member": no pre-existing member identity is
    # required or possible before the account exists yet.

    total_count = total_redemption_counts.get(promo.promo_id, 0)
    if promo.total_redemption_limit is not None and total_count >= promo.total_redemption_limit:
        return False

    if promo.per_member_redemption_limit is not None and member_id is not None:
        member_count = member_redemption_counts.get(promo.promo_id, 0)
        if member_count >= promo.per_member_redemption_limit:
            return False

    return True


def _build_snapshot(promo: Promo) -> PromoSnapshot:
    return PromoSnapshot(
        promo_id=promo.promo_id,
        promo_name_snapshot=promo.name,
        audience_snapshot=promo.audience,
        amount_mode_snapshot=promo.amount_mode,
        price_snapshot=promo.price,
        granted_seconds_snapshot=promo.granted_seconds,
        station_scope_snapshot=promo.station_scope,
        applies_to_snapshot=promo.applies_to,
        starts_at_snapshot=promo.starts_at,
        ends_at_snapshot=promo.ends_at,
    )


def select_best_promo(
    candidates: Sequence[Promo],
    *,
    audience: PromoAudience,
    amount: Decimal,
    now: datetime,
    action: SelectionAction | None = None,
    station_scope: PromoStationScope | None = None,
    member_id: str | None = None,
    total_redemption_counts: Mapping[str, int] | None = None,
    member_redemption_counts: Mapping[str, int] | None = None,
    base_granted_seconds: int | None = None,
) -> PromoSelectionResult:
    """Filters `candidates` to those eligible for one hypothetical
    transaction, then deterministically picks the single best one (no
    stacking -- exactly one promo, or none, is ever returned).

    Parameters
    ----------
    candidates: every promo row the caller has already loaded that might
        apply -- this function does not query the database.
    audience: which of the four promo audiences this transaction is for.
    amount: the actual paid/entered amount, as an exact Decimal.
    now: explicit timezone-aware UTC instant -- never read from the clock
        inside this module.
    action: "start_session" or "add_time", required for standard/vip
        applies_to filtering; ignored for member_topup/new_member.
    station_scope: "standard" or "vip", required for standard/vip station
        matching; ignored for member_topup/new_member.
    member_id: required for member_topup eligibility; irrelevant for
        standard/vip (per-member limits are impossible there, Stage 1
        already forbids configuring one); not yet meaningful for
        new_member (the account doesn't exist yet).
    total_redemption_counts / member_redemption_counts: promo_id -> count
        of that promo's non-voided redemptions (overall, and for this
        member respectively). These are ADVISORY inputs for eligibility
        filtering only -- see PromoRepository.count_total_redemptions's
        docstring for why the actual limit check at redemption-write time
        must be redone inside a BEGIN IMMEDIATE transaction, not trusted
        from a value computed earlier and passed in here.
    base_granted_seconds: the normal/base seconds this same transaction
        would grant with no promo at all (e.g. the linear Standard rate's
        result for `amount`, or the VIP package's duration, or the
        matching permanent new_member offer's initial balance). A promo is
        only ever selected when it strictly beats this. Pass None (or 0)
        when there is no such baseline to beat -- member_topup's bonus
        baseline is always 0 by definition (the purchased product's own
        ordinary seconds are separate and untouched by this comparison),
        and new_member has no baseline when no matching permanent offer
        exists; either case just means any positive, otherwise-eligible
        promo can win.
    """
    total_redemption_counts = total_redemption_counts or {}
    member_redemption_counts = member_redemption_counts or {}
    effective_base = base_granted_seconds if base_granted_seconds is not None else 0

    if not candidates:
        return PromoSelectionResult(None, None, effective_base, 0, "no_candidates", None)

    eligible = [
        candidate
        for candidate in candidates
        if _is_eligible(
            candidate,
            now,
            audience,
            action,
            station_scope,
            amount,
            member_id,
            total_redemption_counts,
            member_redemption_counts,
        )
    ]
    if not eligible:
        return PromoSelectionResult(None, None, effective_base, 0, "no_eligible_promo", None)

    # Deterministic, independent of input order: greatest granted_seconds
    # first; ties broken by priority (descending), then created_at
    # (ascending -- oldest configured wins), then promo_id (ascending) as
    # the final, always-unique stable tie-break. Never seconds-per-peso --
    # every eligible candidate here is being compared against the same
    # actual paid amount, so raw granted_seconds already is the value
    # comparison.
    best = sorted(
        eligible,
        key=lambda promo: (-promo.granted_seconds, -promo.priority, promo.created_at, promo.promo_id),
    )[0]

    benefit = best.granted_seconds - effective_base
    if benefit <= 0:
        # Equal or worse than the normal rate/package/offer -- never spend
        # a redemption slot for no customer benefit. The normal rate is
        # preserved by the caller simply doing nothing with this result.
        return PromoSelectionResult(None, None, effective_base, 0, "not_better_than_base", None)

    return PromoSelectionResult(
        selected_promo=best,
        granted_seconds=best.granted_seconds,
        base_granted_seconds=effective_base,
        customer_benefit_seconds=benefit,
        reason="selected",
        snapshot=_build_snapshot(best),
    )
