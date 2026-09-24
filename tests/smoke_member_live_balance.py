"""Members page live balance.

A member's stored balance is only deducted when their session ends, so while
they are playing the dashboard used to show what they had at the START (6h on
the members page against 3h on the customer overlay). MemberRepository
.with_live_balances() subtracts what the in-progress session has used so far,
with the same rules finalize_reservation_usage applies at the end. These tests
drive real sessions against a real database and check the live figure, and
that the stored balance -- the one billing uses -- is never changed by it.
"""

import asyncio
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "tests"))

from smoke_vip_consumption import create_service, create_test_member, rewind_session_start  # noqa: E402

HOUR = 3600


def live(member_repository, member_id):
    return member_repository.with_live_balances([member_repository.get_member(member_id)])[0]


def test_standard_pc_shows_what_is_left_while_playing(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "std.sqlite3")
    member = create_test_member(member_repository, "live.std", vip_seconds=0, standard_seconds=6 * HOUR)
    started = asyncio.run(service.start_member_session("PC-STD", "live.std", None, "1234"))
    rewind_session_start(session_repository, started.session.session_id, 3 * HOUR)

    shown = live(member_repository, member.member_id)
    assert shown.in_session is True
    assert shown.live_time_balance_seconds == 3 * HOUR, shown.live_time_balance_seconds
    assert shown.time_balance_seconds == 6 * HOUR, "the stored balance must not change while playing"
    assert shown.live_vip_balance_seconds == 0

    # What is shown now is what is actually left once the session ends.
    asyncio.run(service.end_session(started.session.session_id))
    after = live(member_repository, member.member_id)
    assert after.in_session is False
    assert after.time_balance_seconds == after.live_time_balance_seconds
    assert abs(after.time_balance_seconds - 3 * HOUR) <= 5, after.time_balance_seconds


def test_member_not_in_a_session_shows_the_stored_balance(temp_directory: str) -> None:
    _, _, member_repository = create_service(Path(temp_directory) / "idle.sqlite3")
    member = create_test_member(member_repository, "live.idle", vip_seconds=2 * HOUR, standard_seconds=6 * HOUR)

    shown = live(member_repository, member.member_id)
    assert shown.in_session is False
    assert shown.live_time_balance_seconds == 6 * HOUR
    assert shown.live_vip_balance_seconds == 2 * HOUR


def test_vip_pc_converts_standard_time_two_to_one(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "vip.sqlite3")
    member = create_test_member(member_repository, "live.vip", vip_seconds=0, standard_seconds=4 * HOUR)
    started = asyncio.run(service.start_member_session("PC-VIP", "live.vip", None, "1234"))
    rewind_session_start(session_repository, started.session.session_id, 1 * HOUR)

    shown = live(member_repository, member.member_id)
    assert shown.in_session is True
    assert shown.live_time_balance_seconds == 4 * HOUR - 2 * HOUR, "1 real hour on a VIP PC costs 2 Standard hours"
    assert shown.time_balance_seconds == 4 * HOUR


def test_live_balance_never_goes_below_zero(temp_directory: str) -> None:
    service, session_repository, member_repository = create_service(Path(temp_directory) / "cap.sqlite3")
    member = create_test_member(member_repository, "live.cap", vip_seconds=0, standard_seconds=1 * HOUR)
    started = asyncio.run(service.start_member_session("PC-STD", "live.cap", None, "1234"))
    rewind_session_start(session_repository, started.session.session_id, 5 * HOUR)

    shown = live(member_repository, member.member_id)
    assert shown.live_time_balance_seconds == 0, shown.live_time_balance_seconds


def main() -> None:
    for test in (
        test_standard_pc_shows_what_is_left_while_playing,
        test_member_not_in_a_session_shows_the_stored_balance,
        test_vip_pc_converts_standard_time_two_to_one,
        test_live_balance_never_goes_below_zero,
    ):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")
    print("Member live balance smoke tests passed.")


if __name__ == "__main__":
    main()
