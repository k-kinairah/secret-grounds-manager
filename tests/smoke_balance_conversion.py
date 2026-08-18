import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> None:
    from server.balance_conversion import (
        finalize_vip_usage,
        playable_vip_seconds,
        split_vip_session,
    )

    # Owner's worked example: 2 VIP hrs + 6 Standard hrs -> 5 hrs playable.
    assert playable_vip_seconds(2 * 3600, 6 * 3600) == 5 * 3600

    # Confirmed rule stated directly: 4 Standard hours = 2 VIP playable hours.
    assert playable_vip_seconds(0, 4 * 3600) == 2 * 3600

    # Owner's literal example: 2 hours member (Standard) balance allows
    # exactly 1 hour of maximum VIP playtime.
    assert playable_vip_seconds(0, 2 * 3600) == 3600

    # Pure VIP balance, no Standard fallback needed.
    assert playable_vip_seconds(3600, 0) == 3600

    # Zero everything.
    assert playable_vip_seconds(0, 0) == 0

    # Odd leftover Standard second is floored away, never granted as free time.
    assert playable_vip_seconds(0, 4 * 3600 + 1) == 2 * 3600

    for bad_vip, bad_standard in [(-1, 0), (0, -1)]:
        try:
            playable_vip_seconds(bad_vip, bad_standard)
        except ValueError:
            pass
        else:
            raise AssertionError("Negative balances must be rejected")

    # split_vip_session: no cap, uses the full playable amount, VIP first.
    effective, vip_part, standard_part = split_vip_session(2 * 3600, 6 * 3600, None)
    assert effective == 5 * 3600
    assert vip_part == 2 * 3600
    assert standard_part == 6 * 3600  # entire Standard balance, evenly converted

    # split_vip_session: cap fully satisfied from VIP balance alone.
    effective, vip_part, standard_part = split_vip_session(2 * 3600, 6 * 3600, 3600)
    assert effective == 3600
    assert vip_part == 3600
    assert standard_part == 0

    # split_vip_session: cap requires crossing into Standard.
    effective, vip_part, standard_part = split_vip_session(2 * 3600, 6 * 3600, 3 * 3600)
    assert effective == 3 * 3600
    assert vip_part == 2 * 3600
    assert standard_part == 2 * 3600  # 1 real hour of Standard fallback * 2

    # split_vip_session: cap above what balances can cover is clamped down.
    effective, vip_part, standard_part = split_vip_session(1800, 0, 3600)
    assert effective == 1800
    assert vip_part == 1800
    assert standard_part == 0

    try:
        split_vip_session(0, 0, -1)
    except ValueError:
        pass
    else:
        raise AssertionError("Negative requested_cap_seconds must be rejected")

    # finalize_vip_usage: the prompt's exact mid-session boundary-crossing
    # example -- 30 min VIP balance + 2 Standard hours reserved as one
    # session (1.5h effective), then 45 real minutes actually played.
    effective, vip_part, standard_part = split_vip_session(1800, 2 * 3600, None)
    assert effective == 5400
    assert vip_part == 1800
    assert standard_part == 7200

    vip_consumed, standard_consumed = finalize_vip_usage(2700, effective, vip_part)
    assert vip_consumed == 1800  # the full 30 min of VIP balance
    assert standard_consumed == 1800  # 15 real minutes * 2 = 30 Standard-minutes

    # finalize_vip_usage: elapsed entirely within the VIP portion.
    vip_consumed, standard_consumed = finalize_vip_usage(900, effective, vip_part)
    assert vip_consumed == 900
    assert standard_consumed == 0

    # finalize_vip_usage: elapsed at/beyond the full reservation is capped,
    # never over-draws either balance.
    vip_consumed, standard_consumed = finalize_vip_usage(999999, effective, vip_part)
    assert vip_consumed == 1800
    assert standard_consumed == 7200

    # finalize_vip_usage: zero elapsed deducts nothing.
    vip_consumed, standard_consumed = finalize_vip_usage(0, effective, vip_part)
    assert vip_consumed == 0
    assert standard_consumed == 0

    for args in [(-1, 10, 5), (10, -1, 5), (10, 10, -1)]:
        try:
            finalize_vip_usage(*args)
        except ValueError:
            pass
        else:
            raise AssertionError("Negative seconds must be rejected")

    # Owner's literal examples: a member with pure Standard balance (no VIP
    # balance) playing on the VIP PC is charged Standard time at 2x actual
    # use, and ending early only deducts for the actual time consumed.
    effective, vip_part, standard_part = split_vip_session(0, 2 * 3600, None)
    assert effective == 3600  # 2 Standard hours -> 1 hour playable
    assert vip_part == 0
    assert standard_part == 2 * 3600

    # 30 real minutes actually used deducts 1 hour from the member balance.
    vip_consumed, standard_consumed = finalize_vip_usage(1800, effective, vip_part)
    assert vip_consumed == 0
    assert standard_consumed == 3600

    # 45 real minutes actually used deducts 1 hour 30 minutes.
    vip_consumed, standard_consumed = finalize_vip_usage(2700, effective, vip_part)
    assert vip_consumed == 0
    assert standard_consumed == 5400

    # Ending early (well short of the full 1-hour reservation) deducts only
    # 2x the actual time consumed, never the full reserved amount.
    vip_consumed, standard_consumed = finalize_vip_usage(600, effective, vip_part)
    assert vip_consumed == 0
    assert standard_consumed == 1200

    print("Balance conversion smoke test passed.")


if __name__ == "__main__":
    main()
