r"""Build a fresh, fake demo shop for the assistant demo.

Two parts, each run with the venv that has its dependencies:

    # Shop database (main project venv):
    .\.venv\Scripts\python.exe assistant\demo\seed_demo.py --db $env:TEMP\sg-demo\shop.sqlite3

    # One handover note (agent venv, it uses the agent's notes store):
    assistant\agent\.venv\Scripts\python.exe assistant\demo\seed_demo.py --notes-db $env:TEMP\sg-demo\handover.sqlite3

Everything is made up: staff, members, sales. Nothing is read from a real
database. The shop data is created through the backend's own code (schema
setup, repositories and the session service), so it looks exactly like real
use: a fake PC connection acknowledges commands while seeding, and the clock
is moved back for the past week's sales.

Deterministic: a fixed random seed, with times relative to when it runs (so
"today" is always today). Only record ids differ between runs.

Safety: it refuses any path that already exists (or has -wal/-shm files), and
anything inside the repo's data/, backups/ or server/ folders.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import sys
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[2]
SHOP_TZ = ZoneInfo("Asia/Manila")
RANDOM_SEED = 20261023
PROTECTED_DIRS = ("data", "backups", "server")

ADMIN_ID = "operator_admin"
CASHIERS = [("joy", "Joy"), ("mark", "Mark")]
SHIFT_CASHIER = "joy"
OPENING_CASH = Decimal("1000.00")
MEMBER_PIN = "1234"

# (username, display name, top-ups after the membership fee)
MEMBERS = [
    ("andrea.santos", "Andrea Santos", ["member-topup-100", "member-topup-100"]),
    ("paolo.reyes", "Paolo Reyes", ["member-promo"]),
    ("kristine.delacruz", "Kristine Dela Cruz", ["vip-promo-250"]),
    ("jerome.bautista", "Jerome Bautista", []),
    ("mika.villanueva", "Mika Villanueva", ["member-topup-100"]),
    ("carlo.mendoza", "Carlo Mendoza", ["member-promo", "member-promo"]),
    ("bea.garcia", "Bea Garcia", ["vip-promo-250", "member-topup-100"]),
    ("nico.ramos", "Nico Ramos", []),
    ("lyka.aquino", "Lyka Aquino", ["member-topup-100"]),
]
# Members who also play during the past week.
MEMBER_PLAYERS = ["andrea.santos", "carlo.mendoza", "mika.villanueva", "paolo.reyes"]

# PC-01 (the live test client) gets no history: it only has the session about to run out.
STANDARD_PCS = [f"PC-{n:02d}" for n in range(2, 15)]
VIP_PC = "PC-15"
STANDARD_RATES = [("rate-standard-guest-5", 2), ("rate-standard-guest-10", 4), ("rate-standard-guest-15", 6)]
VIP_RATES = [("rate-vip-guest-1hr", 3), ("rate-vip-guest-2hr", 2), ("rate-vip-guest-3hr", 1)]

HANDOVER_NOTE = ("PC 9's headset mic crackles. I swapped in the spare from the drawer; "
                 "the old one needs replacing.", "Mark")


def refuse_unless_new(raw: str) -> Path:
    path = Path(raw).expanduser().resolve()
    for suffix in ("", "-wal", "-shm", "-journal"):
        if Path(f"{path}{suffix}").exists():
            sys.exit(f"Refusing: {path}{suffix} already exists. The demo seed only writes to a new file.")
    for name in PROTECTED_DIRS:
        protected = (REPO_ROOT / name).resolve()
        if path == protected or protected in path.parents:
            sys.exit(f"Refusing: {path} is inside the repo's {name}/ folder.")
    if not path.parent.is_dir():
        sys.exit(f"Refusing: folder {path.parent} doesn't exist.")
    return path


# --- shop database --------------------------------------------------------------------------------


class Clock:
    def __init__(self, start: datetime) -> None:
        self.at = start

    def __call__(self) -> datetime:
        return self.at


class FakePcConnection:
    """Stands in for the PCs while seeding: every command is received and completed."""

    def is_connected(self, pc_name: str) -> bool:
        return True

    async def send_command(self, pc_name, command, on_acknowledgement=None):
        from server.schemas import CommandAcknowledgement

        acks = [CommandAcknowledgement(command_id=command.command_id, status=s) for s in ("received", "completed")]
        for ack in acks:
            if on_acknowledgement is not None:
                on_acknowledgement(ack)
        return acks


def local(day, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), SHOP_TZ).astimezone(timezone.utc)


def cash_for(amount: Decimal) -> Decimal:
    """What the customer hands over: exact, or the next 20/50/100."""
    for note in (Decimal("20"), Decimal("50"), Decimal("100"), Decimal("500")):
        if amount <= note:
            return note if amount != note else amount
    return amount


def seed_shop(db_path: Path) -> None:
    os.environ["SECRET_GROUNDS_DB_PATH"] = str(db_path)
    os.environ.setdefault("SECRET_GROUNDS_BACKUP_DIR", str(db_path.parent / "backups"))
    sys.path.insert(0, str(REPO_ROOT))

    import server.main as main
    from server.schemas import (
        MemberCreateRequest, MemberPurchaseRequest, OperatorCreateRequest, PaymentCreateRequest,
        PCRecord, ShiftOpenRequest, UserCreateRequest,
    )
    from uuid import uuid4

    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    clock = Clock(now - timedelta(days=8))
    for module in list(sys.modules.values()):
        if getattr(module, "__name__", "").startswith("server.") and hasattr(module, "current_utc_time"):
            module.current_utc_time = clock

    main.initialize_database()
    rng = random.Random(RANDOM_SEED)
    service = main.session_service
    service.connection_manager = FakePcConnection()
    for pc in [f"PC-{n:02d}" for n in range(1, 16)]:
        main.pc_registry[pc] = PCRecord(pc_name=pc, ip_address="127.0.0.1", status="online", last_seen=now)

    users = main.user_repository
    operators = main.operator_repository
    members = main.member_repository
    admin = users.get_user(ADMIN_ID)

    # Staff: two demo cashiers next to the default operators the schema setup creates.
    staff = {}
    for username, name in CASHIERS:
        created = operators.create_operator(
            OperatorCreateRequest(username=username, display_name=name, access_method="quick", role="cashier"), admin)
        staff[username] = users.get_user(created.operator_id)
    # The assistant's service account (not an operator, so not on the login picker).
    users.create_user(UserCreateRequest(username="assistant", display_name="Assistant",
                                        password=str(uuid4()), role="cashier"))

    def pay(amount: Decimal, i: int) -> PaymentCreateRequest:
        if rng.random() < 0.2:
            return PaymentCreateRequest(payment_method="gcash", amount=amount, reference_number=f"DEMO{i:06d}")
        return PaymentCreateRequest(payment_method="cash", amount=amount, cash_received=cash_for(amount))

    def cashier_at(moment: datetime):
        return staff["joy"] if moment.astimezone(SHOP_TZ).hour < 16 else staff["mark"]

    rate_price = {r.rate_id: (r.duration_seconds, Decimal(str(r.price))) for r in main.rate_repository.list_rates()}

    # Members join over the past week (each pays the Membership Fee), then top up.
    member_ids = {}
    today = now.astimezone(SHOP_TZ).date()
    for index, (username, name, topups) in enumerate(MEMBERS):
        clock.at = local(today - timedelta(days=7 - index % 7), 10 + index) + timedelta(minutes=rng.randrange(50))
        actor = cashier_at(clock.at)
        member = members.create_member(MemberCreateRequest(
            username=username, display_name=name, pin=MEMBER_PIN, confirm_pin=MEMBER_PIN,
            phone_number=f"0917{rng.randrange(1000000, 9999999)}", payment_method="cash",
            cash_received=Decimal("100.00")), actor)
        member_ids[username] = member.member_id
        for product_id in topups:
            clock.at += timedelta(hours=rng.randrange(2, 30))
            clock.at = min(clock.at, now - timedelta(hours=3))
            members.purchase(member.member_id, MemberPurchaseRequest(
                product_id=product_id, payment_method="cash",
                cash_received=cash_for(Decimal(members.get_product(product_id).price))), cashier_at(clock.at))

    # Walk-in and member sessions, run in time order so PCs never overlap.
    events: list[tuple[datetime, int, str, dict]] = []
    counter = 0

    def plan_day(day, first: datetime, last: datetime, busy: float) -> None:
        nonlocal counter
        pcs = STANDARD_PCS + [VIP_PC]
        for pc in pcs:
            moment = first + timedelta(minutes=rng.randrange(0, 90))
            while True:
                if rng.random() > busy:
                    moment += timedelta(minutes=rng.randrange(30, 120))
                choices = VIP_RATES if pc == VIP_PC else STANDARD_RATES
                rate_id = rng.choices([c[0] for c in choices], weights=[c[1] for c in choices])[0]
                seconds = rate_price[rate_id][0]
                end = moment + timedelta(seconds=seconds)
                if end > last:
                    break
                counter += 1
                events.append((moment, counter, "start", {"pc": pc, "rate_id": rate_id, "end": end, "n": counter}))
                moment = end + timedelta(minutes=rng.randrange(3, 25))

    for days_ago in range(7, 0, -1):
        day = today - timedelta(days=days_ago)
        plan_day(day, local(day, 9), local(day, 23), busy=0.35 + 0.05 * (days_ago % 3))
    # Today: the open shift starts in the morning (or a few hours back when it's early).
    shift_start = local(today, 8)
    if shift_start > now - timedelta(hours=3):
        shift_start = now - timedelta(hours=6)
    # Today's sales stay on today's date (yesterday is already planned in full).
    plan_day(today, max(shift_start, local(today, 0)) + timedelta(minutes=20), now - timedelta(minutes=70), busy=0.3)
    # A few past member sessions (paid from balance).
    for i, username in enumerate(MEMBER_PLAYERS):
        start = local(today - timedelta(days=1 + i), 14 + i, 10)
        counter += 1
        events.append((start, counter, "member", {"pc": STANDARD_PCS[-1 - i], "username": username,
                                                   "end": start + timedelta(minutes=45 + 15 * i)}))

    shift_opened = False
    pending: list[tuple[datetime, int, str, dict]] = sorted(events)
    while pending:
        moment, n, kind, info = pending.pop(0)
        if not shift_opened and moment >= shift_start:
            clock.at = shift_start
            main.shift_repository.open_shift(ShiftOpenRequest(opening_cash=OPENING_CASH), staff[SHIFT_CASHIER])
            shift_opened = True
        clock.at = moment
        if kind == "end":
            asyncio.run(service.end_session(info["session_id"], info["actor"]))
            continue
        if service.repository.get_active_session_for_pc(info["pc"]) is not None:
            continue  # a member session took this PC; skip this walk-in
        actor = staff[SHIFT_CASHIER] if shift_opened and moment >= shift_start else cashier_at(moment)
        if kind == "member":
            response = asyncio.run(service.start_member_session(
                info["pc"], info["username"], int((info["end"] - moment).total_seconds()), MEMBER_PIN, actor.user_id))
        else:
            seconds, price = rate_price[info["rate_id"]]
            response = asyncio.run(service.start_prepaid_session(
                info["pc"], pay(price, info["n"]), rate_id=info["rate_id"], actor_user_id=actor.user_id))
        pending.append((info["end"], n, "end", {"session_id": response.session.session_id, "actor": actor.user_id}))
        pending.sort()

    if not shift_opened:
        clock.at = shift_start
        main.shift_repository.open_shift(ShiftOpenRequest(opening_cash=OPENING_CASH), staff[SHIFT_CASHIER])

    # Live right now: a walk-in and a member in the background, and PC-01 about to run out.
    # PC-01 is the live test client: it picks this session up from the backend when it
    # connects, so "add one hour to PC one" works on camera.
    joy = staff[SHIFT_CASHIER].user_id
    clock.at = now - timedelta(minutes=20)
    asyncio.run(service.start_prepaid_session("PC-03", pay(Decimal("15.00"), 900001),
                                              rate_id="rate-standard-guest-15", actor_user_id=joy))
    clock.at = now - timedelta(minutes=35)
    asyncio.run(service.start_member_session("PC-07", "andrea.santos", 2 * 3600, MEMBER_PIN, joy))
    clock.at = now - timedelta(minutes=45)  # about 15 minutes left at seeding, ~10 when the demo is up
    asyncio.run(service.start_prepaid_session("PC-01", pay(Decimal("15.00"), 900002),
                                              rate_id="rate-standard-guest-15", actor_user_id=joy))

    main.pc_registry.clear()
    clock.at = now
    summarize(main, staff[SHIFT_CASHIER])


def summarize(main, cashier) -> None:
    shift = main.shift_repository.get_open_shift_for_user(cashier.user_id)
    detail = main.shift_repository.get_shift(shift.shift_id, cashier)
    active = main.session_repository.list_active_sessions()
    print("Demo shop ready.")
    print("  Staff on the login picker:", ", ".join(o.display_name for o in main.operator_repository.list_login_operators()))
    print("  Members:", len(main.member_repository.list_members()))
    print(f"  Open shift: {cashier.display_name}, expected cash ₱{detail.totals.expected_cash}")
    if active:
        print("  Active sessions:", ", ".join(s.pc_name for s in active))


# --- handover note (agent's own notes file) --------------------------------------------------------


def seed_notes(notes_path: Path) -> None:
    sys.path.insert(0, str(REPO_ROOT / "assistant" / "agent"))
    from sg_agent.handover import HandoverStore

    yesterday_evening = datetime.combine(datetime.now(SHOP_TZ).date() - timedelta(days=1), time(21, 40), SHOP_TZ)
    store = HandoverStore(notes_path, now=lambda: yesterday_evening.astimezone(timezone.utc))
    try:
        store.add(*HANDOVER_NOTE)
    finally:
        store.close()
    print(f"Handover note from {HANDOVER_NOTE[1]} saved.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", help="new shop database file to create")
    parser.add_argument("--notes-db", help="new handover-notes file to create")
    args = parser.parse_args()
    if not args.db and not args.notes_db:
        parser.error("give --db and/or --notes-db")
    db = refuse_unless_new(args.db) if args.db else None
    notes = refuse_unless_new(args.notes_db) if args.notes_db else None
    if db:
        seed_shop(db)
    if notes:
        seed_notes(notes)


if __name__ == "__main__":
    main()
