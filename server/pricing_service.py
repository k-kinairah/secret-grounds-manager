"""Phase 1 curated pricing editor.

A thin, allowlisted orchestrator over the two existing pricing stores --
``rate_profiles`` and ``membership_products``. It introduces NO new table and
NO generic CRUD: only the fixed row ids below can be edited, and the linked
rows (the VIP 2h/3h/4h tiers, and the VIP promo guest-package <-> member-
product pair) are always rewritten together inside a single SQLite
transaction so they can never end up partially updated.

Nothing here changes how prices/durations are *consumed*. ``resolve_session_package``,
``calculate_metered_time``, ``resolve_normal_vip_seconds``,
``resolve_normal_standard_seconds``, ``balance_conversion`` and the member
purchase path all keep reading the same rows they read today -- this module
only rewrites the values in those rows.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from server.balance_conversion import STANDARD_SECONDS_PER_VIP_SECOND
from server.database import get_connection
from server.schemas import (
    PricingMoneyTime,
    PricingSnapshot,
    PricingUpdateRequest,
    PricingVipTierView,
)
from server.time_utils import current_utc_time

# --- Allowlisted rows. Nothing outside this set is touchable here. ----------
STANDARD_METERED_RATE_ID = "rate-regular-metered"
VIP_BASE_RATE_ID = "rate-vip-guest-1hr"
# multiple -> rate_id. Rewritten as exact multiples of the VIP base on save.
VIP_TIER_RATE_IDS: dict[int, str] = {
    2: "rate-vip-guest-2hr",
    3: "rate-vip-guest-3hr",
    4: "rate-vip-guest-4hr",
}
VIP_PROMO_RATE_ID = "rate-vip-promo-10hr"

MEMBER_NEW_ACCOUNT_PRODUCT_ID = "membership-fee"
MEMBER_TOPUP_SMALL_PRODUCT_ID = "member-promo"
MEMBER_TOPUP_LARGE_PRODUCT_ID = "member-topup-100"
VIP_PROMO_PRODUCT_ID = "vip-promo-250"

_ALL_RATE_IDS = [
    STANDARD_METERED_RATE_ID,
    VIP_BASE_RATE_ID,
    *VIP_TIER_RATE_IDS.values(),
    VIP_PROMO_RATE_ID,
]
_ALL_PRODUCT_IDS = [
    MEMBER_NEW_ACCOUNT_PRODUCT_ID,
    MEMBER_TOPUP_SMALL_PRODUCT_ID,
    MEMBER_TOPUP_LARGE_PRODUCT_ID,
    VIP_PROMO_PRODUCT_ID,
]


def _money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _money_str(value: Any) -> str:
    return str(_money(value))


def _rate_row(connection: Any, rate_id: str) -> Any:
    row = connection.execute(
        """
        SELECT rate_id, name, price, duration_seconds, rate_type, is_active, sort_order
        FROM rate_profiles WHERE rate_id = ?
        """,
        (rate_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Pricing row '{rate_id}' is missing.")
    return row


def _product_row(connection: Any, product_id: str) -> Any:
    row = connection.execute(
        "SELECT product_id, name, price, time_seconds FROM membership_products WHERE product_id = ?",
        (product_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Membership product '{product_id}' is missing.")
    return row


def _rate_audit_payload(row: Any) -> dict[str, object]:
    """Same shape as rate_repository.rate_to_audit_dict, so rate_audit_events
    rows written here are indistinguishable from ones written by the rate
    CRUD endpoints."""
    return {
        "rate_id": row["rate_id"],
        "name": row["name"],
        "duration_seconds": int(row["duration_seconds"]),
        "price": _money_str(row["price"]),
        "rate_type": row["rate_type"],
        "is_active": bool(row["is_active"]),
        "sort_order": int(row["sort_order"]),
    }


def _snapshot_from_connection(connection: Any) -> PricingSnapshot:
    metered = _rate_row(connection, STANDARD_METERED_RATE_ID)
    vip_base = _rate_row(connection, VIP_BASE_RATE_ID)
    promo_rate = _rate_row(connection, VIP_PROMO_RATE_ID)
    new_account = _product_row(connection, MEMBER_NEW_ACCOUNT_PRODUCT_ID)
    topup_small = _product_row(connection, MEMBER_TOPUP_SMALL_PRODUCT_ID)
    topup_large = _product_row(connection, MEMBER_TOPUP_LARGE_PRODUCT_ID)

    tiers: list[PricingVipTierView] = []
    for multiple, rate_id in sorted(VIP_TIER_RATE_IDS.items()):
        tier = _rate_row(connection, rate_id)
        tiers.append(
            PricingVipTierView(
                rate_id=rate_id,
                multiple=multiple,
                price=_money(tier["price"]),
                seconds=int(tier["duration_seconds"]),
            )
        )

    return PricingSnapshot(
        standard_guest=PricingMoneyTime(
            price=_money(metered["price"]), seconds=int(metered["duration_seconds"])
        ),
        standard_member_new_account=PricingMoneyTime(
            price=_money(new_account["price"]), seconds=int(new_account["time_seconds"])
        ),
        standard_member_topup_small=PricingMoneyTime(
            price=_money(topup_small["price"]), seconds=int(topup_small["time_seconds"])
        ),
        standard_member_topup_large=PricingMoneyTime(
            price=_money(topup_large["price"]), seconds=int(topup_large["time_seconds"])
        ),
        vip_guest_hourly=PricingMoneyTime(
            price=_money(vip_base["price"]), seconds=int(vip_base["duration_seconds"])
        ),
        promo=PricingMoneyTime(
            price=_money(promo_rate["price"]), seconds=int(promo_rate["duration_seconds"])
        ),
        vip_guest_tiers=tiers,
        vip_member_conversion_ratio=STANDARD_SECONDS_PER_VIP_SECOND,
    )


def get_editable_pricing() -> PricingSnapshot:
    with get_connection() as connection:
        return _snapshot_from_connection(connection)


def _update_rate(
    connection: Any,
    rate_id: str,
    *,
    price: Decimal,
    seconds: int,
    actor_user_id: str | None,
    now_iso: str,
) -> None:
    before = _rate_row(connection, rate_id)
    if int(before["duration_seconds"]) == int(seconds) and _money(before["price"]) == _money(price):
        return  # genuine no-op -- do not write an audit row for it
    connection.execute(
        "UPDATE rate_profiles SET price = ?, duration_seconds = ?, updated_at = ? WHERE rate_id = ?",
        (_money_str(price), int(seconds), now_iso, rate_id),
    )
    after = _rate_row(connection, rate_id)
    connection.execute(
        """
        INSERT INTO rate_audit_events (
            event_id, rate_id, action, old_values, new_values, performed_by_user_id, created_at
        )
        VALUES (?, ?, 'updated', ?, ?, ?, ?)
        """,
        (
            str(uuid4()),
            rate_id,
            json.dumps(_rate_audit_payload(before)),
            json.dumps(_rate_audit_payload(after)),
            actor_user_id,
            now_iso,
        ),
    )


def _update_product(
    connection: Any,
    product_id: str,
    *,
    price: Decimal,
    seconds: int,
    now_iso: str,
) -> None:
    before = _product_row(connection, product_id)  # 404 before any write if it is missing
    if int(before["time_seconds"]) == int(seconds) and _money(before["price"]) == _money(price):
        return  # genuine no-op -- leave the row (and its updated_at) untouched
    connection.execute(
        "UPDATE membership_products SET price = ?, time_seconds = ?, updated_at = ? WHERE product_id = ?",
        (_money_str(price), int(seconds), now_iso, product_id),
    )


def apply_pricing_update(
    request: PricingUpdateRequest, actor_user_id: str | None
) -> PricingSnapshot:
    """Rewrite every allowlisted pricing row in one transaction.

    Atomicity: all statements run on a single ``get_connection()`` context,
    which commits only on a clean exit. Any validation failure (a missing
    allowlisted row) or DB error raises before/while inside the block, the
    context exits via the exception, ``commit()`` is skipped and the
    connection is closed -- so a partially-applied VIP tier set or an
    unmirrored promo pair is impossible.
    """
    now_iso = current_utc_time().isoformat()
    vip_base_price = _money(request.vip_guest_hourly.price)
    vip_base_seconds = int(request.vip_guest_hourly.seconds)

    with get_connection() as connection:
        # Fail fast: every allowlisted row must exist before we touch anything.
        for rate_id in _ALL_RATE_IDS:
            _rate_row(connection, rate_id)
        for product_id in _ALL_PRODUCT_IDS:
            _product_row(connection, product_id)

        # Standard guest -- the authoritative linear/metered rate.
        _update_rate(
            connection,
            STANDARD_METERED_RATE_ID,
            price=_money(request.standard_guest.price),
            seconds=int(request.standard_guest.seconds),
            actor_user_id=actor_user_id,
            now_iso=now_iso,
        )

        # VIP guest base + regenerated x2 / x3 / x4 tiers. The tier rows stay
        # in rate_profiles because the cashier SessionDialog chips and
        # resolve_normal_vip_seconds (exact price match) still consume them.
        _update_rate(
            connection,
            VIP_BASE_RATE_ID,
            price=vip_base_price,
            seconds=vip_base_seconds,
            actor_user_id=actor_user_id,
            now_iso=now_iso,
        )
        for multiple, rate_id in VIP_TIER_RATE_IDS.items():
            _update_rate(
                connection,
                rate_id,
                price=_money(vip_base_price * multiple),
                seconds=vip_base_seconds * multiple,
                actor_user_id=actor_user_id,
                now_iso=now_iso,
            )

        # VIP promo: the guest package row and the member product are always
        # written to the same (price, seconds) pair, in this same transaction.
        _update_rate(
            connection,
            VIP_PROMO_RATE_ID,
            price=_money(request.promo.price),
            seconds=int(request.promo.seconds),
            actor_user_id=actor_user_id,
            now_iso=now_iso,
        )
        _update_product(
            connection,
            VIP_PROMO_PRODUCT_ID,
            price=_money(request.promo.price),
            seconds=int(request.promo.seconds),
            now_iso=now_iso,
        )

        # Standard member products.
        _update_product(
            connection,
            MEMBER_NEW_ACCOUNT_PRODUCT_ID,
            price=_money(request.standard_member_new_account.price),
            seconds=int(request.standard_member_new_account.seconds),
            now_iso=now_iso,
        )
        _update_product(
            connection,
            MEMBER_TOPUP_SMALL_PRODUCT_ID,
            price=_money(request.standard_member_topup_small.price),
            seconds=int(request.standard_member_topup_small.seconds),
            now_iso=now_iso,
        )
        _update_product(
            connection,
            MEMBER_TOPUP_LARGE_PRODUCT_ID,
            price=_money(request.standard_member_topup_large.price),
            seconds=int(request.standard_member_topup_large.seconds),
            now_iso=now_iso,
        )

        return _snapshot_from_connection(connection)
