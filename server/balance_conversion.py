"""Pure calculation helpers for VIP/Standard balance consumption on VIP PCs.

Confirmed owner rules:
  - On a VIP PC, VIP promo balance is consumed first, 1:1 with real seconds
    played.
  - Once VIP balance is exhausted, Standard balance converts at 2 Standard-
    balance seconds per 1 real second (4 Standard hours = 2 VIP playable
    hours).
  - Standard PCs never touch VIP balance and always consume Standard 1:1
    (unchanged, handled entirely by the existing member_repository
    reservation/finalization code -- nothing here applies to a Standard-PC
    session).

These functions are pure and have no database or session-service
dependencies so the conversion math can be verified in isolation from the
reservation/finalization plumbing that calls it.
"""

STANDARD_SECONDS_PER_VIP_SECOND = 2


def playable_vip_seconds(vip_balance_seconds: int, standard_balance_seconds: int) -> int:
    """Total real seconds playable on a VIP PC from the two balances combined.

    Floors the Standard contribution so a customer is never granted more
    real time than their balance justifies (same rounding bias as
    calculate_metered_time's ROUND_FLOOR) -- any odd leftover Standard
    second stays in the balance untouched.
    """
    if vip_balance_seconds < 0 or standard_balance_seconds < 0:
        raise ValueError("Balances cannot be negative")
    return vip_balance_seconds + standard_balance_seconds // STANDARD_SECONDS_PER_VIP_SECOND


def split_vip_session(
    vip_balance_seconds: int,
    standard_balance_seconds: int,
    requested_cap_seconds: int | None = None,
) -> tuple[int, int, int]:
    """Compute the reservation for starting or extending a VIP-PC member session.

    Returns (effective_real_seconds, vip_seconds_to_reserve,
    standard_seconds_to_reserve). standard_seconds_to_reserve is expressed
    in Standard-balance seconds (already multiplied by the 2:1 conversion),
    ready to check against and reserve from member.time_balance_seconds
    directly. VIP balance is always claimed first.
    """
    max_playable = playable_vip_seconds(vip_balance_seconds, standard_balance_seconds)
    effective = max_playable if requested_cap_seconds is None else min(requested_cap_seconds, max_playable)
    if effective < 0:
        raise ValueError("requested_cap_seconds cannot be negative")
    vip_part = min(effective, vip_balance_seconds)
    remainder_real_seconds = effective - vip_part
    standard_part = remainder_real_seconds * STANDARD_SECONDS_PER_VIP_SECOND
    return effective, vip_part, standard_part


def finalize_vip_usage(
    actual_elapsed_real_seconds: int,
    reserved_effective_seconds: int,
    reserved_vip_seconds: int,
) -> tuple[int, int]:
    """Compute actual consumption when finalizing a VIP-PC member session.

    Returns (vip_seconds_consumed, standard_seconds_consumed). Consumption
    is capped at the reservation so a session can never draw more than what
    was checked available at start/add-time, and VIP balance is always
    drawn down before Standard -- the same-session VIP-to-Standard
    transition falls out of this automatically with no separate event.
    """
    if actual_elapsed_real_seconds < 0 or reserved_effective_seconds < 0 or reserved_vip_seconds < 0:
        raise ValueError("Seconds cannot be negative")
    used_real_seconds = min(actual_elapsed_real_seconds, reserved_effective_seconds)
    vip_consumed = min(used_real_seconds, reserved_vip_seconds)
    remainder_real_seconds = used_real_seconds - vip_consumed
    standard_consumed = remainder_real_seconds * STANDARD_SECONDS_PER_VIP_SECOND
    return vip_consumed, standard_consumed
