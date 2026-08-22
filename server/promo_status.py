"""Pure derivation of a promo's display status from its stored owner_state
plus the current time. Never persisted -- Draft/Scheduled/Active/Ended/
Disabled is computed fresh on every read, so it can never go stale merely
because time passed without anyone touching the row.

Stored owner_state (server/schemas.py's PromoOwnerState) is the only value
ever written to promos.owner_state: 'draft' | 'enabled' | 'disabled' |
'ended'. This module maps that plus (starts_at, ends_at, now) to one of the
five display statuses.
"""

from datetime import datetime

from server.schemas import PromoDisplayStatus, PromoOwnerState


def derive_promo_status(
    owner_state: PromoOwnerState,
    starts_at: datetime,
    ends_at: datetime,
    now: datetime,
) -> PromoDisplayStatus:
    """starts_at is inclusive, ends_at is exclusive -- identical to the
    existing rate eligibility window convention in
    server/rate_eligibility.py's is_within_eligibility_window, reused here
    rather than reinvented.

    Explicit owner_state values always win over date math: 'draft',
    'disabled', and 'ended' are never overridden by the current time. Only
    'enabled' derives a time-dependent status (Scheduled/Active/Ended).
    """
    if owner_state == "draft":
        return "Draft"
    if owner_state == "disabled":
        return "Disabled"
    if owner_state == "ended":
        return "Ended"

    # owner_state == "enabled"
    if now < starts_at:
        return "Scheduled"
    if now < ends_at:
        return "Active"
    return "Ended"
