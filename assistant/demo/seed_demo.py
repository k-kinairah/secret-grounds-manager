r"""Build a fresh, fake demo shop for the assistant demo.

Two parts, each run with the venv that has its dependencies:

    # Shop database (main project venv):
    .\.venv\Scripts\python.exe assistant\demo\seed_demo.py --db $env:TEMP\sg-demo\shop.sqlite3

    # One handover note (agent venv, it uses the agent's notes store):
    assistant\agent\.venv\Scripts\python.exe assistant\demo\seed_demo.py --notes-db $env:TEMP\sg-demo\handover.sqlite3

Members and sales are made up; the staff are the default family operators the
backend always creates (Tyrone is on duty since the morning, Rogene left the
handover note). No cashier shift is opened; the shop uses on duty.
Nothing is read from a real database. The shop data is created through the
backend's own code (schema setup, repositories and the session service), so
it looks exactly like real use: a fake PC connection acknowledges commands while seeding, and the clock
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

# Staff are the default family operators the backend always creates.
DUTY_OPERATOR = "operator_tyrone"   # on duty since the morning
NOTE_AUTHOR_OPERATOR = "operator_rogene"   # wrote the handover note, works the evenings
DEMO_FLOAT = Decimal("200.00")   # only for the hint printed at the end
MEMBER_PIN = "1234"

# (username, display name, top-ups after the membership fee). The first nine keep their
# fixed top-ups; the rest get a deterministic mix (see MORE_MEMBERS).
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
MORE_MEMBERS = [
    ("joshua.navarro", "Joshua Navarro"), ("angelica.torres", "Angelica Torres"),
    ("renz.castillo", "Renz Castillo"), ("kyla.fernandez", "Kyla Fernandez"),
    ("ivan.morales", "Ivan Morales"), ("trisha.domingo", "Trisha Domingo"),
    ("gab.salazar", "Gab Salazar"), ("janelle.cruz", "Janelle Cruz"),
    ("ken.aguilar", "Ken Aguilar"), ("rhea.pascual", "Rhea Pascual"),
    ("marvin.ocampo", "Marvin Ocampo"), ("cheska.valdez", "Cheska Valdez"),
    ("jm.rivera", "JM Rivera"), ("patricia.soriano", "Patricia Soriano"),
    ("enzo.manalo", "Enzo Manalo"),
]
TOPUP_MIXES = [[], ["member-promo"], ["member-topup-100"], ["member-topup-100", "member-topup-100"],
               ["vip-promo-250"], ["member-promo", "member-promo"], ["member-topup-100", "member-promo"]]
# Members who also play during the past week.
MEMBER_PLAYERS = ["andrea.santos", "carlo.mendoza", "mika.villanueva", "paolo.reyes",
                  "joshua.navarro", "kyla.fernandez", "ken.aguilar", "cheska.valdez"]

# Running right now: (pc, kind, package or member, minutes ago started, minutes long).
# About 10 of the 15 PCs busy, a few nearly done, one VIP. PC-01 is the live test client
# (the add-time scene) and PC-07 is Andrea Santos.
LIVE_SESSIONS = [
    ("PC-01", "walkin", "rate-standard-guest-15", 45, 60),
    ("PC-02", "member", "carlo.mendoza", 50, 120),
    ("PC-03", "walkin", "rate-standard-guest-15", 20, 60),
    ("PC-04", "walkin", "rate-standard-guest-10", 22, 40),
    ("PC-05", "walkin", "rate-standard-guest-15", 48, 60),
    ("PC-07", "member", "andrea.santos", 35, 120),
    ("PC-09", "walkin", "rate-standard-guest-15", 5, 60),
    ("PC-11", "member", "mika.villanueva", 30, 90),
    ("PC-13", "walkin", "rate-standard-guest-15", 10, 60),
    ("PC-15", "walkin", "rate-vip-guest-2hr", 40, 120),
]

# PC-01 (the live test client) gets no history: it only has the session about to run out.
STANDARD_PCS = [f"PC-{n:02d}" for n in range(2, 15)]
VIP_PC = "PC-15"
STANDARD_RATES = [("rate-standard-guest-5", 2), ("rate-standard-guest-10", 4), ("rate-standard-guest-15", 6)]
VIP_RATES = [("rate-vip-guest-1hr", 3), ("rate-vip-guest-2hr", 2), ("rate-vip-guest-3hr", 1)]

HANDOVER_NOTE = ("PC 9's headset mic crackles. I swapped in the spare from the drawer; "
                 "the old one needs replacing.", "Rogene")


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
        MemberCreateRequest, MemberPurchaseRequest, PaymentCreateRequest,
        PCRecord, UserCreateRequest,
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
    members = main.member_repository

    staff = {"duty": users.get_user(DUTY_OPERATOR), "evening": users.get_user(NOTE_AUTHOR_OPERATOR)}
    # The assistant's service account (not an operator, so not on the login picker).
    users.create_user(UserCreateRequest(username="assistant", display_name="Assistant",
                                        password=str(uuid4()), role="cashier"))

    def pay(amount: Decimal, i: int) -> PaymentCreateRequest:
        if rng.random() < 0.2:
            return PaymentCreateRequest(payment_method="gcash", amount=amount, reference_number=f"DEMO{i:06d}")
        return PaymentCreateRequest(payment_method="cash", amount=amount, cash_received=cash_for(amount))

    def cashier_at(moment: datetime):
        return staff["duty"] if moment.astimezone(SHOP_TZ).hour < 16 else staff["evening"]

    rate_price = {r.rate_id: (r.duration_seconds, Decimal(str(r.price))) for r in main.rate_repository.list_rates()}

    # Members join over the past week (each pays the Membership Fee), then top up.
    member_ids = {}
    today = now.astimezone(SHOP_TZ).date()
    all_members = MEMBERS + [(u, n, rng.choice(TOPUP_MIXES)) for u, n in MORE_MEMBERS]
    for index, (username, name, topups) in enumerate(all_members):
        clock.at = local(today - timedelta(days=7 - index % 7), 10 + index % 12) + timedelta(minutes=rng.randrange(50))
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
        plan_day(day, local(day, 9), local(day, 23), busy=0.6 + 0.05 * (days_ago % 3))
    # Today: the shop opens at 9 AM; when the demo starts before 10 AM, "today" runs from
    # midnight (a late-night crowd) so today's figures are never empty. Sales spread from
    # opening up to shortly before now; the live sessions below fill the last hour.
    opening = local(today, 9) if now >= local(today, 10) else local(today, 0)
    duty_start = opening - timedelta(minutes=30)  # Tyrone comes on duty just before opening
    plan_day(today, opening + timedelta(minutes=5), now - timedelta(minutes=55), busy=0.6)
    # A few past member sessions (paid from balance).
    for i, username in enumerate(MEMBER_PLAYERS):
        start = local(today - timedelta(days=1 + i % 6), 13 + i, 10)
        counter += 1
        events.append((start, counter, "member", {"pc": STANDARD_PCS[-1 - i], "username": username,
                                                   "end": start + timedelta(minutes=45 + 15 * i)}))

    on_duty = False

    def come_on_duty() -> None:
        # What signing in does: Tyrone is the operator on duty and his running total starts
        # now. Signing in again at demo time keeps this start (the window stays open).
        clock.at = duty_start
        main.operator_state_repository.set_current(staff["duty"].user_id, staff["duty"].user_id)
        main.operator_state_repository.begin_sale_window(staff["duty"].user_id)
    pending: list[tuple[datetime, int, str, dict]] = sorted(events)
    while pending:
        moment, n, kind, info = pending.pop(0)
        if not on_duty and moment >= duty_start:
            come_on_duty()
            on_duty = True
        clock.at = moment
        if kind == "end":
            asyncio.run(service.end_session(info["session_id"], info["actor"]))
            continue
        if service.repository.get_active_session_for_pc(info["pc"]) is not None:
            continue  # a member session took this PC; skip this walk-in
        actor = staff["duty"] if on_duty and moment >= duty_start else cashier_at(moment)
        if kind == "member":
            response = asyncio.run(service.start_member_session(
                info["pc"], info["username"], int((info["end"] - moment).total_seconds()), MEMBER_PIN, actor.user_id))
        else:
            seconds, price = rate_price[info["rate_id"]]
            response = asyncio.run(service.start_prepaid_session(
                info["pc"], pay(price, info["n"]), rate_id=info["rate_id"], actor_user_id=actor.user_id))
        pending.append((info["end"], n, "end", {"session_id": response.session.session_id, "actor": actor.user_id}))
        pending.sort()

    if not on_duty:
        come_on_duty()

    # Live right now (see LIVE_SESSIONS). PC-01 is the live test client: it picks its session
    # up from the backend when it connects, so "add one hour to PC one" works on camera; the
    # others are shown by the simulated PCs (fake_pcs.py).
    on_duty_id = staff["duty"].user_id
    for index, (pc, kind, what, minutes_ago, minutes_long) in enumerate(sorted(LIVE_SESSIONS, key=lambda s: -s[3])):
        clock.at = now - timedelta(minutes=minutes_ago)
        if service.repository.get_active_session_for_pc(pc) is not None:
            continue
        if kind == "member":
            asyncio.run(service.start_member_session(pc, what, minutes_long * 60, MEMBER_PIN, on_duty_id))
        else:
            price = rate_price[what][1]
            asyncio.run(service.start_prepaid_session(pc, pay(price, 900001 + index), rate_id=what, actor_user_id=on_duty_id))

    main.pc_registry.clear()
    clock.at = now
    summarize(main, staff["duty"])


def summarize(main, operator) -> None:
    since = main.operator_state_repository.get_sale_window_start(operator.user_id)
    totals = main.shift_repository.calculate_operator_sale(operator.user_id, since)
    active = main.session_repository.list_active_sessions()
    print("Demo shop ready.")
    print("  Staff on the login picker:", ", ".join(o.display_name for o in main.operator_repository.list_login_operators()))
    print("  Members:", len(main.member_repository.list_members()))
    print(f"  On duty: {operator.display_name} since {since.astimezone(SHOP_TZ):%I:%M %p}, "
          f"cash collected ₱{totals['cash']} (with a ₱{DEMO_FLOAT} float the drawer should hold "
          f"₱{totals['cash'] + DEMO_FLOAT}), GCash ₱{totals['gcash']}")
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
