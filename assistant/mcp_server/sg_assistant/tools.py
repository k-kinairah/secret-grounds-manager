"""Read-only tool implementations.

Each function takes a BackendClient, calls existing GET endpoints of the
Secret Grounds backend, and returns a small JSON-able dict. Backend failures
never raise out of a tool: they come back as {"error": "..."} so the LLM can
read the problem out loud instead of the call crashing.

Times are returned in shop-local time (SG_SHOP_TIMEZONE, default
Asia/Manila -- the same default the backend uses); money is returned as
strings exactly as the backend formats it (e.g. "150.00", in pesos).
"""

import re
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sg_assistant.backend import BackendClient, BackendError

MAX_MEMBER_RESULTS = 10
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Session statuses that mean a PC is in use (anything but idle/unknown).
_IN_USE_SESSION_STATUSES = {"active", "grace_period", "shutting_down", "ending", "settlement_pending"}


# --- helpers ---------------------------------------------------------------


def _error(error: Exception) -> dict[str, str]:
    return {"error": str(error)}


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    # The backend stores UTC; a naive value is treated as UTC.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _local(value: Any, tz: ZoneInfo) -> str | None:
    parsed = _parse_dt(value)
    return parsed.astimezone(tz).isoformat(timespec="minutes") if parsed else None


def _hms(seconds: Any) -> str | None:
    if seconds is None:
        return None
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes = remainder // 60
    return f"{hours}h {minutes:02d}m"


def mask_phone(phone: Any) -> str | None:
    """Keep only the last 3 digits: '09171234567' -> '********567'."""
    if not phone or not isinstance(phone, str):
        return None
    digits = [c for c in phone if c.isdigit()]
    if len(digits) <= 3:
        return "*" * len(digits)
    return "*" * (len(digits) - 3) + "".join(digits[-3:])


def _pc_in_use(pc: dict[str, Any]) -> bool:
    return bool(pc.get("active_session_id")) or pc.get("session_status") in _IN_USE_SESSION_STATUSES


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- tools -----------------------------------------------------------------


async def get_today_overview(client: BackendClient, tz: ZoneInfo) -> dict[str, Any]:
    try:
        report = await client.get("/sales/report", params={"period": "today"})
        sessions = await client.get("/sessions/active")
        pcs = await client.get("/pcs")
    except BackendError as error:
        return _error(error)

    summary = report.get("summary", {})
    online = [pc for pc in pcs if pc.get("connection_status") == "online"]
    busy = [pc for pc in pcs if _pc_in_use(pc)]
    return {
        "date": report.get("start_date"),
        "sales": {
            "total_sales": summary.get("total_sales"),
            "net_sales": summary.get("net_sales"),
            "payment_count": summary.get("payment_count"),
            "by_payment_method": {
                "cash": summary.get("cash_total"),
                "gcash": summary.get("gcash_total"),
                "maya": summary.get("maya_total"),
                "other": summary.get("other_total"),
            },
            "by_type": {
                "sessions": summary.get("session_revenue"),
                "member_purchases": summary.get("member_purchase_revenue"),
                "other": summary.get("other_revenue"),
            },
            "refunds": summary.get("refund_total"),
            "outstanding_unpaid": summary.get("outstanding_amount"),
        },
        "active_sessions": len(sessions),
        "pcs": {
            "total": len(pcs),
            "online": len(online),
            "offline": len(pcs) - len(online),
            "busy": len(busy),
            "free": len([pc for pc in online if not _pc_in_use(pc)]),
        },
        "as_of": _now().astimezone(tz).isoformat(timespec="minutes"),
    }


async def get_sales_report(
    client: BackendClient, tz: ZoneInfo, start_date: str, end_date: str
) -> dict[str, Any]:
    for label, value in (("start_date", start_date), ("end_date", end_date)):
        if not isinstance(value, str) or not _DATE_RE.match(value):
            return {"error": f"{label} must be a date in YYYY-MM-DD format, got {value!r}."}
        try:
            date.fromisoformat(value)
        except ValueError:
            return {"error": f"{label} is not a real calendar date: {value!r}."}
    if end_date < start_date:
        return {"error": "end_date cannot be before start_date."}

    try:
        report = await client.get(
            "/sales/report",
            params={"period": "custom", "start_date": start_date, "end_date": end_date},
        )
    except BackendError as error:
        return _error(error)

    summary = report.get("summary", {})
    result: dict[str, Any] = {
        "start_date": report.get("start_date"),
        "end_date": report.get("end_date"),
        "summary": {
            "total_sales": summary.get("total_sales"),
            "gross_sales": summary.get("gross_sales"),
            "refunds": summary.get("refund_total"),
            "net_sales": summary.get("net_sales"),
            "payment_count": summary.get("payment_count"),
            "average_transaction": summary.get("average_transaction_value"),
            "outstanding_unpaid": summary.get("outstanding_amount"),
        },
        "by_payment_method": {
            "cash": summary.get("cash_total"),
            "gcash": summary.get("gcash_total"),
            "maya": summary.get("maya_total"),
            "other": summary.get("other_total"),
        },
        "by_type": {
            "sessions": summary.get("session_revenue"),
            "member_purchases": summary.get("member_purchase_revenue"),
            "other": summary.get("other_revenue"),
        },
        "by_operator": [
            {"operator": op.get("operator_name"), "total": op.get("total_amount"), "payments": op.get("payment_count")}
            for op in report.get("operators", [])
        ],
        "by_pc": [
            {"pc": pc.get("pc_name"), "total": pc.get("total_amount"), "paid_sessions": pc.get("paid_session_count")}
            for pc in report.get("pcs", [])
        ],
        "timezone": str(tz),
    }

    # The backend silently moves the start forward to the sales epoch when the
    # owner has reset the sales figures. Its start_at is then later than local
    # midnight of the requested start date (possibly within that same day, so
    # comparing start_date alone is not enough).
    reset_at = _sales_reset_at(report.get("start_at"), start_date, tz)
    if reset_at is not None:
        result["requested_start_date"] = start_date
        result["note"] = (
            f"Sales figures were reset on {_spoken_datetime(reset_at)}; "
            "earlier sales are not included."
        )
    return result


def _sales_reset_at(start_at: Any, requested_start_date: str, tz: ZoneInfo) -> datetime | None:
    actual_start = _parse_dt(start_at)
    if actual_start is None:
        return None
    requested_start = datetime.combine(date.fromisoformat(requested_start_date), time.min, tzinfo=tz)
    if actual_start <= requested_start:
        return None
    return actual_start.astimezone(tz)


def _spoken_datetime(value: datetime) -> str:
    """'October 5, 2026 at 2:30 PM' -- reads naturally when spoken."""
    hour = value.hour % 12 or 12
    meridiem = "AM" if value.hour < 12 else "PM"
    return f"{value.strftime('%B')} {value.day}, {value.year} at {hour}:{value.minute:02d} {meridiem}"


async def list_active_sessions(client: BackendClient, tz: ZoneInfo) -> dict[str, Any]:
    try:
        sessions = await client.get("/sessions/active")
    except BackendError as error:
        return _error(error)

    now = _now()
    rows = []
    for session in sessions:
        mode = session.get("session_mode")
        status = session.get("status")
        started = _parse_dt(session.get("started_at"))
        row: dict[str, Any] = {
            "pc": session.get("pc_name"),
            "mode": mode,
            "status": status,
            "member": session.get("member_code_snapshot") or None,
            "started_at": _local(session.get("started_at"), tz),
        }
        if mode == "open_time":
            # Open time has no fixed end: report elapsed time and the running estimate.
            elapsed = int((now - started).total_seconds()) if started else None
            row["elapsed"] = _hms(elapsed)
            row["time_remaining"] = None
            due = session.get("estimated_balance_due")
            row["amount_due"] = due if due is not None else session.get("balance_due")
        else:
            target_key = (
                "shutdown_at"
                if status in {"grace_period", "shutting_down"} and session.get("shutdown_at")
                else "scheduled_end_at"
            )
            target = _parse_dt(session.get(target_key))
            remaining = int((target - now).total_seconds()) if target else None
            row["time_remaining"] = _hms(remaining)
            row["ends_at"] = _local(session.get(target_key), tz)
            row["amount_due"] = session.get("balance_due")
        row["payment_status"] = session.get("payment_status")
        rows.append(row)

    rows.sort(key=lambda r: str(r.get("pc") or ""))
    return {"count": len(rows), "sessions": rows}


async def list_pcs(client: BackendClient, tz: ZoneInfo) -> dict[str, Any]:
    try:
        pcs = await client.get("/pcs")
    except BackendError as error:
        return _error(error)

    rows = []
    for pc in sorted(pcs, key=lambda p: (p.get("layout_order", 0), str(p.get("pc_name")))):
        in_use = _pc_in_use(pc)
        online = pc.get("connection_status") == "online"
        rows.append(
            {
                "name": pc.get("display_name") or pc.get("pc_name"),
                "pc_name": pc.get("pc_name"),
                "vip": bool(pc.get("is_vip")),
                "online": online,
                "state": "in_use" if in_use else ("free" if online else "offline"),
                "session_status": pc.get("session_status"),
                "time_remaining": _hms(pc.get("remaining_seconds")) if in_use else None,
                "enabled": pc.get("is_enabled", True),
            }
        )
    return {
        "count": len(rows),
        "online": sum(1 for r in rows if r["online"]),
        "in_use": sum(1 for r in rows if r["state"] == "in_use"),
        "free": sum(1 for r in rows if r["state"] == "free"),
        "pcs": rows,
    }


async def find_member(client: BackendClient, tz: ZoneInfo, query: str) -> dict[str, Any]:
    cleaned = (query or "").strip()
    if len(cleaned) < 2:
        return {"error": "Please give at least 2 characters of a member code, username or name."}

    try:
        members = await client.get("/members", params={"q": cleaned})
    except BackendError as error:
        return _error(error)

    results = []
    for member in members[:MAX_MEMBER_RESULTS]:
        standard = member.get("live_time_balance_seconds")
        if standard is None:
            standard = member.get("time_balance_seconds")
        vip = member.get("live_vip_balance_seconds")
        if vip is None:
            vip = member.get("vip_balance_seconds")
        # Explicit allowlist: secrets (pin_hash etc.) can never leak even if
        # the backend response grows new fields.
        results.append(
            {
                "member_code": member.get("member_code"),
                "username": member.get("username"),
                "name": member.get("display_name") or member.get("full_name"),
                "status": member.get("status"),
                "time_balance": _hms(standard),
                "time_balance_seconds": standard,
                "vip_balance": _hms(vip),
                "vip_balance_seconds": vip,
                "in_session": bool(member.get("in_session")),
                "expires_at": _local(member.get("expires_at"), tz),
                "phone": mask_phone(member.get("phone_number")),
            }
        )
    result: dict[str, Any] = {
        "query": cleaned,
        "match_count": len(members),
        "showing": len(results),
        "members": results,
    }
    if not members:
        # Speech-to-text often misspells names ("rojin" for "Rogene"). Offer the
        # closest members as guesses -- names and codes only, never balances --
        # so the assistant can ask "Did you mean ...?" instead of "not found".
        try:
            everyone = await client.get("/members")
        except BackendError:
            everyone = []
        suggestions = closest_members(cleaned, everyone)
        if suggestions:
            result["did_you_mean"] = suggestions
            result["note"] = (
                "No exact match. These are guesses from a similar spelling or sound; ask the user "
                "to confirm the name before using any of them."
            )
    return result


# --- fuzzy / sound-alike member matching ----------------------------------------------

FUZZY_THRESHOLD = 0.6
MAX_SUGGESTIONS = 3
_SOUNDEX_CODES = {
    **dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"), **dict.fromkeys("dt", "3"),
    "l": "4", **dict.fromkeys("mn", "5"), "r": "6",
}


def soundex(word: str) -> str:
    """American Soundex: 'Rogene', 'rojin', 'raw jin' -> 'R250'."""
    letters = [c for c in word.lower() if c.isalpha()]
    if not letters:
        return ""
    first = letters[0]
    digits: list[str] = []
    previous = _SOUNDEX_CODES.get(first, "")
    for char in letters[1:]:
        code = _SOUNDEX_CODES.get(char, "")
        if code and code != previous:
            digits.append(code)
        if char not in "hw":  # h/w don't separate equal codes; vowels do
            previous = code
    return (first.upper() + "".join(digits) + "000")[:4]


def _fold(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _candidate_strings(member: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("username", "display_name", "full_name", "member_code"):
        value = member.get(key)
        if not value:
            continue
        values.append(str(value))
        values.extend(str(value).split())  # each word of a name on its own
    code = str(member.get("member_code") or "")
    if "-" in code:
        values.append(code.split("-", 1)[1])
    return [v for v in values if _fold(v)]


def _similarity(query: str, candidate: str) -> float:
    from difflib import SequenceMatcher

    q, c = _fold(query), _fold(candidate)
    if not q or not c:
        return 0.0
    score = SequenceMatcher(None, q, c).ratio()
    if q.isalpha() and c.isalpha() and soundex(q) == soundex(c):
        score = max(score, 0.75) + 0.15  # sounds the same: strong hint
    return min(score, 1.0)


def closest_members(query: str, members: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ranked guesses: best of edit-distance similarity and Soundex, over
    username, names (whole and per word) and member code."""
    scored: list[tuple[float, dict[str, Any]]] = []
    for member in members:
        best = max((_similarity(query, value) for value in _candidate_strings(member)), default=0.0)
        if best >= FUZZY_THRESHOLD:
            scored.append((best, member))
    scored.sort(key=lambda pair: (-pair[0], str(pair[1].get("member_code"))))
    return [
        {
            "name": m.get("display_name") or m.get("full_name") or m.get("username"),
            "username": m.get("username"),
            "member_code": m.get("member_code"),
            "match": round(score, 2),
        }
        for score, m in scored[:MAX_SUGGESTIONS]
    ]


async def get_current_shift(client: BackendClient, tz: ZoneInfo) -> dict[str, Any]:
    try:
        on_duty = await client.get("/operators/current")
        sale = await client.get("/dashboard/operator-sale")
    except BackendError as error:
        return _error(error)

    operator = (on_duty or {}).get("operator") or None
    if operator is None:
        return {"on_duty": None, "message": "Nobody is signed in as the on-duty operator right now."}

    result: dict[str, Any] = {
        "on_duty": operator.get("display_name") or operator.get("username"),
        "role": operator.get("role"),
        "on_duty_since": _local(on_duty.get("selected_at"), tz),
        "has_open_shift": bool(sale.get("has_open_shift")),
        "shift_opened_at": None,
        "running_totals": {
            "total": sale.get("operator_sale"),
            "cash": sale.get("cash_payments"),
            "gcash": sale.get("gcash_payments"),
            "maya": sale.get("maya_payments"),
            "other": sale.get("other_payments"),
            "voided": sale.get("voided_payments"),
            "payment_count": sale.get("payment_count"),
        },
    }

    # Best effort: the shift's opened_at. /shifts lists only the caller's own
    # shifts for a cashier-role token user, so this may be unavailable.
    shift_id = sale.get("shift_id")
    if shift_id:
        try:
            shifts = await client.get("/shifts")
        except BackendError:
            shifts = []
        for shift in shifts or []:
            if shift.get("shift_id") == shift_id:
                result["shift_opened_at"] = _local(shift.get("opened_at"), tz)
                break
    return result
