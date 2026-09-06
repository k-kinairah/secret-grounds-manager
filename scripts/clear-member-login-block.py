"""Clear a stuck "This member is already using another PC" block.

A member login is refused with that message when the server still sees, for
that member, EITHER:
  * a row in `sessions` with status active/grace_period/shutting_down/ending
    and session_mode = 'member_balance', OR
  * a row in `member_time_reservations` with status = 'active'.

Hiding or deleting a PC slot does NOT touch either of those, so a ghost
session left behind by a crashed client keeps the member locked out. This
script finds and (with --fix) clears those rows for one member.

Balance safety: reservations do NOT debit the member balance up front
(see member_repository.create_reservation), so releasing a stuck
reservation forfeits nothing. Sessions are flipped to 'completed' with
zero billed time.

Run it ON THE CASHIER PC, from the install root, in an elevated PowerShell:

    cd "C:\\Program Files\\Secret Grounds"
    py scripts\\clear-member-login-block.py rogene          # inspect only
    py scripts\\clear-member-login-block.py rogene --fix     # clear it

Stop the stack first if you prefer (not required -- SQLite handles it):
    scripts\\stop-secret-grounds.ps1
"""

from __future__ import annotations

import argparse
import datetime as _dt
import re
import sqlite3
import sys
from pathlib import Path

BLOCKING_SESSION_STATUSES = ("active", "grace_period", "shutting_down", "ending")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def resolve_db_path(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_absolute() else (Path.cwd() / p).resolve()

    # Match how the server resolves it: SECRET_GROUNDS_DB_PATH from config\.env,
    # relative to the install root (this script's parent's parent).
    root = Path(__file__).resolve().parent.parent
    env_file = root / "config" / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*SECRET_GROUNDS_DB_PATH\s*=\s*(.+?)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                raw = m.group(1).strip().strip('"').strip("'")
                p = Path(raw).expanduser()
                return p if p.is_absolute() else (root / p).resolve()

    return (root / "data" / "secret_grounds.db").resolve()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("username", help="member username to unblock (e.g. rogene)")
    parser.add_argument("--fix", action="store_true", help="actually clear the blocking rows (default: inspect only)")
    parser.add_argument("--db", help="path to secret_grounds.db (default: auto-detect from config\\.env)")
    args = parser.parse_args()

    db_path = resolve_db_path(args.db)
    print(f"Database : {db_path}")
    if not db_path.is_file():
        print("ERROR: database file not found. Pass --db with the correct path.")
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    member = conn.execute(
        "SELECT member_id, member_code, username, full_name, status "
        "FROM members WHERE lower(username) = lower(?)",
        (args.username,),
    ).fetchone()
    if member is None:
        print(f"ERROR: no member with username {args.username!r}.")
        return 2

    mid = member["member_id"]
    print(f"Member   : {member['username']}  ({member['member_code']}, {member['full_name']}, status={member['status']})")
    print(f"member_id: {mid}\n")

    placeholders = ",".join("?" for _ in BLOCKING_SESSION_STATUSES)
    sessions = conn.execute(
        f"SELECT session_id, pc_name, status, session_mode, started_at, created_at "
        f"FROM sessions WHERE member_id = ? AND status IN ({placeholders}) "
        f"ORDER BY created_at DESC",
        (mid, *BLOCKING_SESSION_STATUSES),
    ).fetchall()

    reservations = conn.execute(
        "SELECT reservation_id, session_id, status, created_at "
        "FROM member_time_reservations WHERE member_id = ? AND status = 'active'",
        (mid,),
    ).fetchall()

    if not sessions and not reservations:
        print("Nothing is blocking this member. The 409 is coming from somewhere else")
        print("(check that the laptop is pointed at THIS server, and that its PIN is right).")
        return 0

    print(f"Blocking sessions ({len(sessions)}):")
    for s in sessions:
        print(f"  - {s['session_id']}  pc={s['pc_name']}  status={s['status']}  mode={s['session_mode']}  created={s['created_at']}")
    print(f"Active reservations ({len(reservations)}):")
    for r in reservations:
        print(f"  - {r['reservation_id']}  session={r['session_id']}  created={r['created_at']}")

    if not args.fix:
        print("\nInspect only. Re-run with --fix to clear the rows above.")
        return 0

    now = _now_iso()
    with conn:
        for s in sessions:
            conn.execute(
                "UPDATE sessions SET status = 'completed', "
                "ended_at = COALESCE(ended_at, ?), "
                "ended_reason = COALESCE(ended_reason, 'manual_unblock'), "
                "actual_elapsed_seconds = COALESCE(actual_elapsed_seconds, 0), "
                "billed_seconds = COALESCE(billed_seconds, 0), "
                "settlement_pending = 0 "
                "WHERE session_id = ?",
                (now, s["session_id"]),
            )
        for r in reservations:
            conn.execute(
                "UPDATE member_time_reservations SET status = 'released', "
                "finalized_at = COALESCE(finalized_at, ?) WHERE reservation_id = ?",
                (now, r["reservation_id"]),
            )

    print(f"\nCleared {len(sessions)} session(s) and {len(reservations)} reservation(s).")
    print(f"{member['username']} can log in now.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
