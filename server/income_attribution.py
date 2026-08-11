"""Shared payment-time income-group attribution resolution.

Used by both BillingService (session-linked payments, PC always known)
and MemberRepository (membership purchases, PC known only if the member
is already playing). Never invents an owner for an unconfigured PC --
see resolve_income_group_for_pc.
"""

from dataclasses import dataclass
from datetime import datetime

from server.pc_layout_repository import IncomeGroupRepository, PCLayoutRepository


@dataclass(frozen=True)
class AttributionSnapshot:
    status: str  # "assigned" | "pending"
    pc_name_snapshot: str | None
    income_group_id_snapshot: str | None
    income_group_name_snapshot: str | None
    assigned_at: datetime | None
    session_id: str | None


def resolve_income_group_for_pc(
    pc_layout_repository: PCLayoutRepository,
    income_group_repository: IncomeGroupRepository,
    pc_name: str,
) -> tuple[str | None, str | None]:
    """(income_group_id, income_group_name) for the PC's current live
    assignment, or (None, None) if the PC is unknown or has no income
    group configured yet. Never invents an owner -- callers treat a
    (None, None) result as 'pending', not as a false assignment."""
    layout = pc_layout_repository.get_layout(pc_name)
    if layout is None or layout.income_group_id is None:
        return None, None

    income_group = income_group_repository.get_income_group(layout.income_group_id)
    if income_group is None:
        return None, None

    return income_group.income_group_id, income_group.name


def attribution_for_known_pc(
    pc_layout_repository: PCLayoutRepository,
    income_group_repository: IncomeGroupRepository,
    pc_name: str,
    session_id: str | None,
    now: datetime,
) -> AttributionSnapshot:
    """For a payment whose PC is already known at payment time (a
    session-linked payment, or a member purchase made while the member is
    already playing). The PC name is always snapshotted here since it is
    genuinely known; if that PC has no income group configured yet, the
    payment still completes and is recorded 'pending' rather than
    guessing an owner -- the retained pc_name_snapshot lets a later
    income-group assignment for that PC find and resolve it."""
    income_group_id, income_group_name = resolve_income_group_for_pc(
        pc_layout_repository, income_group_repository, pc_name
    )
    if income_group_id is None:
        return AttributionSnapshot(
            status="pending",
            pc_name_snapshot=pc_name,
            income_group_id_snapshot=None,
            income_group_name_snapshot=None,
            assigned_at=None,
            session_id=None,
        )

    return AttributionSnapshot(
        status="assigned",
        pc_name_snapshot=pc_name,
        income_group_id_snapshot=income_group_id,
        income_group_name_snapshot=income_group_name,
        assigned_at=now,
        session_id=session_id,
    )


def attribution_pending_unknown_pc() -> AttributionSnapshot:
    """For a payment whose PC cannot be determined at payment time at all
    (a member purchase with no active session). Every snapshot field
    stays NULL -- there is nothing yet to snapshot."""
    return AttributionSnapshot(
        status="pending",
        pc_name_snapshot=None,
        income_group_id_snapshot=None,
        income_group_name_snapshot=None,
        assigned_at=None,
        session_id=None,
    )


def is_balance_type_usable_on_station(balance_type: str, is_vip_station: bool) -> bool:
    """The single source of truth for the owner-approved Standard/VIP
    eligibility rule: Standard balance is usable on a Standard OR a VIP
    station (2:1 conversion); VIP balance is usable only on a VIP
    station. Used both for the immediate-attribution decision at
    purchase time (MemberRepository) and for the pending-attribution
    sweep at member-session start (below) so the rule is never
    duplicated."""
    return balance_type == "standard" or is_vip_station


def sweep_pending_purchases_for_member(
    payment_repository,
    pc_layout_repository: PCLayoutRepository,
    income_group_repository: IncomeGroupRepository,
    member_id: str,
    pc_name: str,
    is_vip_station: bool,
    session_id: str,
    now: datetime,
) -> int:
    """Called after a member session has been successfully created.
    Resolves every completed, still-'PC not yet known' pending purchase
    for this member (pc_name_snapshot IS NULL) that this station is
    eligible to fund -- Standard purchases always, VIP purchases only on
    a VIP station. Idempotent and self-healing by construction: only
    rows still matching 'pending' + 'pc_name_snapshot IS NULL' are ever
    touched, so re-running this (or running it again on a later session)
    can never re-assign or duplicate an already-resolved row. If the
    station has no income group configured yet, swept rows land in the
    already-approved 'pending + pc_name_snapshot=<PC>' substate instead
    of 'assigned' -- they stay eligible only for the PC-configuration
    resolution path (see resolve_pending_payments_for_configured_pc),
    never for another member-session sweep.

    Returns the number of payment rows updated."""
    income_group_id, income_group_name = resolve_income_group_for_pc(
        pc_layout_repository, income_group_repository, pc_name
    )
    assigned = income_group_id is not None

    return payment_repository.assign_pending_known_pc_purchases_for_member(
        member_id=member_id,
        include_vip=is_vip_station,
        pc_name=pc_name,
        income_group_id_snapshot=income_group_id,
        income_group_name_snapshot=income_group_name,
        attribution_status="assigned" if assigned else "pending",
        attribution_assigned_at=now if assigned else None,
        attribution_session_id=session_id if assigned else None,
    )


def resolve_pending_payments_for_configured_pc(
    payment_repository,
    pc_name: str,
    income_group_id: str,
    income_group_name: str,
    now: datetime,
) -> int:
    """Called whenever a PC's income_group_id is (re)configured to a real
    value (Setup/Layout). Resolves any completed, still-pending payments
    that already know this exact PC (pc_name_snapshot = pc_name) but had
    no group to assign at the time -- never touches pc_name_snapshot
    itself, never touches a payment that already has a group snapshot
    (assigned or otherwise), and never touches a different PC. This is
    the only path that can resolve the 'pending + pc_name_snapshot=<PC>'
    substate; a payment already 'assigned' here is never revisited, so a
    later reassignment of this same PC to a different group can never
    rewrite historical income ownership.

    attribution_session_id is intentionally left untouched (stays NULL)
    for rows resolved this way -- no session start caused this
    assignment, a PC-configuration change did, so there is no session id
    that would be honest to record here.

    Returns the number of payment rows updated."""
    return payment_repository.assign_pending_payments_for_configured_pc(
        pc_name=pc_name,
        income_group_id_snapshot=income_group_id,
        income_group_name_snapshot=income_group_name,
        attribution_assigned_at=now,
    )
