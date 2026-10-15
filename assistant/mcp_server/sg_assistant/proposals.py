"""Action proposals (phase 3).

These tools NEVER change anything. They check the request against live state
(GET only, with the read-only assistant token) and return a proposal: the
exact backend request that WOULD run, plus a plain-English summary. The
agent holds the proposal; only an operator pressing Confirm (or saying
"confirm") in the dashboard runs it, from the dashboard server, with that
operator's own login. The dashboard re-checks every request against its own
hard-coded allow-list before sending it.
"""

import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_FLOOR, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from sg_assistant.backend import BackendClient, BackendError

MESSAGE_MAX_LENGTH = 500
MESSAGE_SEVERITIES = ("info", "warning", "urgent")
MESSAGE_DEFAULT_SECONDS = 20

# Add time (billed later). Matches the dashboard allow-list's own cap.
ADD_TIME_MAX_AMOUNT = Decimal("2000.00")
ADD_TIME_SESSION_STATUSES = ("active", "ending", "grace_period")


def _error(message: str) -> dict[str, str]:
    return {"error": message}


_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
# Speech-to-text often hears the number right after "PC" as a homophone.
_HOMOPHONES_AFTER_PREFIX = {"to": 2, "too": 2, "for": 4, "won": 1, "ate": 8}
_PC_PREFIXES = {"pc", "vip", "station", "computer"}
_FILLER = {"number", "no", "num", "#"}


def _words_to_digits(text: str) -> str:
    """'pc twenty one' -> 'pc 21', 'PC number three' -> 'PC 3', 'pc to' -> 'pc 2'."""
    tokens = re.findall(r"[a-z]+|\d+|[^a-z\d\s]", text.lower())
    out: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        prev = out[-1] if out else ""
        if token in _FILLER and prev in _PC_PREFIXES:
            i += 1
            continue
        if token in _TENS:
            value = _TENS[token]
            nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
            nxt2 = tokens[i + 2] if i + 2 < len(tokens) else ""
            if nxt == "-" and nxt2 in _ONES and 0 < _ONES[nxt2] < 10:
                value += _ONES[nxt2]
                i += 2
            elif nxt in _ONES and 0 < _ONES[nxt] < 10:
                value += _ONES[nxt]
                i += 1
            out.append(str(value))
        elif token in _ONES:
            out.append(str(_ONES[token]))
        elif token in _HOMOPHONES_AFTER_PREFIX and prev in _PC_PREFIXES:
            out.append(str(_HOMOPHONES_AFTER_PREFIX[token]))
        else:
            out.append(token)
        i += 1
    return " ".join(out)


def _normalize_pc(name: str) -> str:
    """'PC 3', 'pc-03', 'PC03', 'pc three' -> 'pc3' (digit groups lose leading zeros)."""
    lowered = re.sub(r"[^a-z0-9]", "", _words_to_digits(name))
    return re.sub(r"\d+", lambda match: str(int(match.group())), lowered)


def _match_pc(pcs: list[dict[str, Any]], wanted: str) -> dict[str, Any] | None:
    key = _normalize_pc(wanted)
    if not key:
        return None
    for pc in pcs:
        names = [pc.get("pc_name") or "", pc.get("display_name") or ""]
        if any(name and _normalize_pc(name) == key for name in names):
            return pc
    return None


def _label(pc: dict[str, Any]) -> str:
    return pc.get("display_name") or pc.get("pc_name") or "?"


def _reachable(pc: dict[str, Any]) -> bool:
    return pc.get("connection_status") == "online" and bool(pc.get("has_command_connection"))


def _clean_message(message: str) -> str | None:
    cleaned = " ".join((message or "").replace("\x00", "").split())
    if not cleaned or len(cleaned) > MESSAGE_MAX_LENGTH:
        return None
    return cleaned


def _clean_severity(severity: str) -> str:
    value = (severity or "info").strip().lower()
    return value if value in MESSAGE_SEVERITIES else "info"


async def propose_message_pc(
    client: BackendClient, pc_name: str, message: str, severity: str = "info"
) -> dict[str, Any]:
    text = _clean_message(message)
    if text is None:
        return _error(f"The message must be 1 to {MESSAGE_MAX_LENGTH} characters.")
    try:
        pcs = await client.get("/pcs")
    except BackendError as error:
        return _error(str(error))

    pc = _match_pc(pcs, pc_name)
    if pc is None:
        known = ", ".join(sorted(_label(p) for p in pcs)) or "none"
        return _error(f"There is no PC called {pc_name!r}. Known PCs: {known}.")
    if not _reachable(pc):
        return _error(f"{_label(pc)} is offline, so it can't receive a message right now.")

    level = _clean_severity(severity)
    return {
        "proposal": {
            "kind": "message_pc",
            "summary": f'Show "{text}" on {_label(pc)}?',
            "targets": [pc["pc_name"]],
            "details": {"message": text, "severity": level},
            "request": {
                "method": "POST",
                "path": f"/pcs/{pc['pc_name']}/message",
                "body": {"message": text, "severity": level, "duration_seconds": MESSAGE_DEFAULT_SECONDS},
            },
        },
        "status": "Waiting for the operator to confirm on screen. Nothing has been sent yet.",
    }


async def propose_broadcast(
    client: BackendClient, message: str, pc_names: list[str] | None = None, severity: str = "info"
) -> dict[str, Any]:
    text = _clean_message(message)
    if text is None:
        return _error(f"The message must be 1 to {MESSAGE_MAX_LENGTH} characters.")
    try:
        pcs = await client.get("/pcs")
    except BackendError as error:
        return _error(str(error))

    if pc_names:
        chosen: list[dict[str, Any]] = []
        for wanted in pc_names:
            pc = _match_pc(pcs, wanted)
            if pc is None:
                return _error(f"There is no PC called {wanted!r}.")
            if pc not in chosen:
                chosen.append(pc)
    else:
        chosen = list(pcs)

    reachable = [pc for pc in chosen if _reachable(pc)]
    unreachable = [pc for pc in chosen if not _reachable(pc)]
    if not reachable:
        return _error("None of those PCs are online right now, so there is nobody to send the message to.")

    level = _clean_severity(severity)
    targets = [pc["pc_name"] for pc in reachable]
    scope = "every online PC" if not pc_names else ", ".join(_label(pc) for pc in reachable)
    summary = f'Show "{text}" on {scope} ({len(reachable)} PC{"s" if len(reachable) != 1 else ""})?'
    return {
        "proposal": {
            "kind": "broadcast",
            "summary": summary,
            "targets": targets,
            "details": {
                "message": text,
                "severity": level,
                "skipped_offline": [_label(pc) for pc in unreachable],
            },
            "request": {
                "method": "POST",
                "path": "/commands/broadcast",
                # Always explicit targets: exactly the PCs the operator saw on the card.
                "body": {
                    "message": text,
                    "severity": level,
                    "duration_seconds": MESSAGE_DEFAULT_SECONDS,
                    "pc_names": targets,
                },
            },
        },
        "status": "Waiting for the operator to confirm on screen. Nothing has been sent yet.",
    }


# --- add time (billed later) -------------------------------------------------
#
# POST /sessions/{id}/add-time adds time to a running PREPAID session and adds
# its price to the session's bill (paid later, e.g. at the end). It is only
# ever proposed with a package (rate_id) or a peso amount: the backend's third
# option, bare seconds, adds the time for FREE, so it is never proposed (and
# the dashboard allow-list refuses it too).


def _money(value: Any) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return Decimal("0.00")


def _peso(value: Decimal) -> str:
    return f"₱{value:,.2f}"


def _duration_words(seconds: int) -> str:
    minutes = max(0, seconds) // 60
    hours, mins = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins or not hours:
        parts.append(f"{mins} minute{'s' if mins != 1 else ''}")
    return " ".join(parts)


def _norm_name(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _scope_ok(rate: dict[str, Any], is_vip: bool) -> bool:
    return rate.get("station_scope", "standard") in ("vip" if is_vip else "standard", "any")


def _packages_for(rates: list[dict[str, Any]], is_vip: bool) -> list[dict[str, Any]]:
    return [
        r for r in rates
        if r.get("rate_type") == "package" and r.get("is_active") and _scope_ok(r, is_vip)
    ]


def _package_list(packages: list[dict[str, Any]]) -> str:
    return ", ".join(f"{p['name']} ({_peso(_money(p['price']))})" for p in packages) or "none"


def _parse_dt(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _clock(value: datetime, tz: ZoneInfo) -> str:
    local = value.astimezone(tz)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


async def propose_add_time(
    client: BackendClient,
    tz: ZoneInfo,
    pc_name: str,
    package: str | None = None,
    minutes: int | None = None,
    amount_pesos: float | str | None = None,
) -> dict[str, Any]:
    chosen = [package not in (None, ""), minutes is not None, amount_pesos not in (None, "")]
    if sum(chosen) != 1:
        return _error(
            "Say how much time to add in exactly one way: a package name, a number of minutes "
            "that matches a package, or a peso amount."
        )

    try:
        pcs = await client.get("/pcs")
        sessions = await client.get("/sessions/active")
    except BackendError as error:
        return _error(str(error))

    pc = _match_pc(pcs, pc_name)
    if pc is None:
        known = ", ".join(sorted(_label(p) for p in pcs)) or "none"
        return _error(f"There is no PC called {pc_name!r}. Known PCs: {known}.")
    label = _label(pc)
    session = next(
        (s for s in sessions if s.get("pc_name") == pc["pc_name"] and s.get("status") in ADD_TIME_SESSION_STATUSES),
        None,
    )
    if session is None:
        return _error(f"{label} has no running session to add time to.")
    mode = session.get("session_mode")
    if mode == "open_time":
        return _error(f"{label} is on open time, which is billed by the minute, so there's no time to add.")
    if mode == "member_balance":
        return _error(f"{label} is a member session; add time from the member's balance in the dashboard instead.")
    if not _reachable(pc):
        return _error(f"{label} is offline, so time can't be added right now.")

    is_vip = bool(pc.get("is_vip"))
    try:
        if amount_pesos not in (None, ""):
            amount = _money(amount_pesos)
            if amount <= 0 or amount > ADD_TIME_MAX_AMOUNT:
                return _error(f"The amount must be between ₱0.01 and {_peso(ADD_TIME_MAX_AMOUNT)}.")
            rate = await client.get("/rates/metered", params={"pc_name": pc["pc_name"]})
            price, duration = _money(rate.get("price")), int(rate.get("duration_seconds") or 0)
            if price <= 0 or duration <= 0:
                return _error("The per-minute (metered) rate isn't set up, so a peso amount can't be converted to time.")
            seconds = int(((amount * duration) / price).to_integral_value(rounding=ROUND_FLOOR))
            if seconds < 60:
                return _error(f"{_peso(amount)} buys less than a minute.")
            body: dict[str, Any] = {"amount": f"{amount:.2f}"}
            choice = f"{_peso(amount)} of time"
        else:
            rates = await client.get("/rates")
            packages = _packages_for(rates, is_vip)
            if package not in (None, ""):
                wanted = _norm_name(str(package))
                matches = [p for p in packages if _norm_name(p["name"]) == wanted] or [
                    p for p in packages if wanted and wanted in _norm_name(p["name"])
                ]
            else:
                if not isinstance(minutes, int) or minutes <= 0:
                    return _error("Minutes must be a positive whole number.")
                matches = [p for p in packages if int(p.get("duration_seconds") or 0) == minutes * 60]
            if len(matches) != 1:
                what = f"package called {package!r}" if package not in (None, "") else f"{minutes}-minute package"
                problem = "There's more than one" if matches else "There's no"
                # Only the real packages and an open question: nothing the model
                # could turn into a suggested amount of its own.
                return _error(
                    f"{problem} {what} for {label}. The packages are: {_package_list(packages)}; "
                    "or tell me a peso amount."
                )
            chosen_rate = matches[0]
            seconds = int(chosen_rate["duration_seconds"])
            amount = _money(chosen_rate["price"])
            body = {"rate_id": chosen_rate["rate_id"]}
            choice = f"the {chosen_rate['name']} package"
    except BackendError as error:
        return _error(str(error))

    end = _parse_dt(session.get("scheduled_end_at"))
    if session.get("status") == "grace_period" or end is None or end < datetime.now(timezone.utc):
        new_end = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    else:
        new_end = end + timedelta(seconds=seconds)
    words = _duration_words(seconds)
    return {
        "proposal": {
            "kind": "add_time",
            "summary": f"Add {words} to {label} ({choice}, {_peso(amount)} added to their bill)?",
            "targets": [pc["pc_name"]],
            "details": {
                "session_id": session["session_id"],
                "added": words,
                "added_seconds": seconds,
                "price": f"{amount:.2f}",
                "new_end_time": _clock(new_end, tz),
                "billed": "later",
            },
            "request": {
                "method": "POST",
                "path": f"/sessions/{session['session_id']}/add-time",
                "body": body,
            },
        },
        "status": "Waiting for the operator to confirm on screen. Nothing has been added yet.",
    }


# --- shut down idle PCs (Admin only, never forced) -------------------------------
#
# POST /pcs/{pc}/power/shutdown is restricted by the backend to the protected
# Admin account. The proposal only ever sends {"force": false}: the backend then
# refuses (409) any PC that has an active session, so a PC that becomes busy
# between proposing and confirming is skipped, never shut down on a customer.

_BUSY_SESSION_STATUSES = {"active", "grace_period", "shutting_down", "ending", "settlement_pending"}


def _idle_reason(pc: dict[str, Any], busy_pcs: set[str]) -> str | None:
    """None if the PC is idle and can take a shutdown; otherwise why not."""
    if not pc.get("is_enabled", True):
        return "disabled"
    if pc.get("connection_status") != "online" or not pc.get("has_command_connection"):
        return "offline"
    if pc["pc_name"] in busy_pcs or pc.get("active_session_id") or pc.get("session_status") in _BUSY_SESSION_STATUSES:
        return "in use"
    if pc.get("startup_status") or pc.get("prepared_session_id"):
        return "starting a session"
    if pc.get("session_status") not in (None, "idle"):
        return str(pc.get("session_status"))
    return None


async def propose_shutdown_idle_pcs(
    client: BackendClient, pc_names: list[str] | None = None, exclude: list[str] | None = None
) -> dict[str, Any]:
    try:
        pcs = await client.get("/pcs")
        sessions = await client.get("/sessions/active")
    except BackendError as error:
        return _error(str(error))

    busy_pcs = {s.get("pc_name") for s in sessions if s.get("status") in _BUSY_SESSION_STATUSES}

    if pc_names:
        candidates: list[dict[str, Any]] = []
        for wanted in pc_names:
            pc = _match_pc(pcs, wanted)
            if pc is None:
                return _error(f"There is no PC called {wanted!r}.")
            if pc not in candidates:
                candidates.append(pc)
    else:
        candidates = list(pcs)

    excluded: set[str] = set()
    for wanted in exclude or []:
        pc = _match_pc(pcs, wanted)
        if pc is None:
            return _error(f"There is no PC called {wanted!r} to leave out.")
        excluded.add(pc["pc_name"])

    idle: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for pc in sorted(candidates, key=lambda p: (p.get("layout_order", 0), str(p.get("pc_name")))):
        if pc["pc_name"] in excluded:
            skipped.append({"pc": _label(pc), "reason": "left out"})
            continue
        reason = _idle_reason(pc, busy_pcs)
        if reason is None:
            idle.append(pc)
        elif pc_names:  # only explain skips for PCs the user named
            skipped.append({"pc": _label(pc), "reason": reason})

    if not idle:
        if skipped:
            listed = ", ".join(f"{s['pc']} ({s['reason']})" for s in skipped)
            return _error(f"None of those PCs can be shut down right now: {listed}.")
        return _error("There are no idle PCs online right now, so there is nothing to shut down.")

    names = [pc["pc_name"] for pc in idle]
    labels = ", ".join(_label(pc) for pc in idle)
    count = f"{len(idle)} idle PC{'s' if len(idle) != 1 else ''}"
    return {
        "proposal": {
            "kind": "shutdown_pcs",
            "summary": f"Shut down {count}: {labels}?",
            "targets": names,
            "details": {
                "power": "shutdown",
                "admin_only": True,
                "skipped": skipped,
                "note": "Only idle PCs are included. A PC that starts a session before you confirm is skipped.",
            },
            "request": {
                "method": "POST",
                # One call per PC in each_pc, run by the dashboard after confirmation.
                "path": "/pcs/{pc}/power/shutdown",
                "body": {"force": False},
                "each_pc": names,
            },
        },
        "status": "Waiting for the Admin to confirm on screen. Nothing has been sent yet.",
    }
