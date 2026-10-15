"""Read-only close-shift check (phase 3 step e).

The dashboard's server fetches the signed-in operator's OWN open shift
(GET /shifts/current with their login) on every message and passes the
summary here. The model gets one tool bound to that verified data: it can
ask for the counted cash, compare it with the expected cash and point out
anything that would block closing. It can never close the shift -- there is
no tool for that -- and it always ends by pointing to the Shifts page.

Blind count: the expected amount is only revealed after the operator has
said what they counted, so the count isn't anchored to the number.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from strands import tool

CLOSE_HINT = "You can close the shift from the Shifts page when ready."
MAX_COUNTED = Decimal("1000000")
SHOP_TZ = ZoneInfo("Asia/Manila")


def _opened_local(value: Any) -> str | None:
    """The backend sends UTC; give the model shop time so it never converts it itself."""
    try:
        opened = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if opened.tzinfo is None:
        return None
    local = opened.astimezone(SHOP_TZ)
    hour = local.hour % 12 or 12
    return f"{local:%B} {local.day} at {hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'} (shop time)"


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _peso(value: Decimal) -> str:
    return f"₱{value:,.2f}"


def _blockers(shift: dict[str, Any]) -> list[str]:
    items: list[str] = []
    pending = int(shift.get("settlement_pending_sessions") or 0)
    review = int(shift.get("review_required_payments") or 0)
    if pending:
        items.append(f"{pending} session{'s' if pending != 1 else ''} still waiting to be settled")
    if review:
        items.append(f"{review} payment{'s' if review != 1 else ''} needing review")
    return items


def evaluate_shift_close(shift: dict[str, Any] | None, counted_cash: Any = None) -> dict[str, Any]:
    """Pure function behind the tool (easy to test)."""
    if shift is None:
        return {"error": "I can only check your shift from the dashboard's assistant page."}
    if not shift.get("open"):
        return {"open_shift": False, "message": "You don't have a cashier shift open, so there's nothing to close."}

    blockers = _blockers(shift)
    base = {
        "open_shift": True,
        "cashier": shift.get("cashier_name"),
        "opened_at": _opened_local(shift.get("opened_at")),
        "blockers": blockers,
        "blocking_note": (
            "A cashier can't close the shift until these are resolved; an admin or owner can still close it."
            if blockers else None
        ),
    }

    if counted_cash in (None, ""):
        return {
            **base,
            "step": "ask_for_count",
            "instruction": (
                "Ask how much cash is in the drawer. Don't say the expected amount yet, and don't say "
                "the shift is ready to close; it hasn't been checked."
            ),
        }

    counted = _money(counted_cash)
    if counted is None or counted < 0 or counted > MAX_COUNTED:
        return {"error": "Please say the counted cash as a peso amount, for example 4,250."}
    expected = _money(shift.get("expected_cash"))
    if expected is None:
        return {"error": "The shop system didn't give an expected cash amount for this shift."}

    difference = counted - expected
    if difference == 0:
        result, summary = "exact", f"The drawer matches: {_peso(counted)} counted, {_peso(expected)} expected."
    elif difference < 0:
        result = "short"
        summary = f"The drawer is short by {_peso(-difference)}: {_peso(counted)} counted, {_peso(expected)} expected."
    else:
        result = "over"
        summary = f"The drawer is over by {_peso(difference)}: {_peso(counted)} counted, {_peso(expected)} expected."
    return {
        **base,
        "step": "result",
        "counted_cash": f"{counted:.2f}",
        "expected_cash": f"{expected:.2f}",
        "difference": f"{difference:.2f}",
        "result": result,
        "summary": summary,
        "close_hint": CLOSE_HINT,
        "note": "This is a check only. The shift has NOT been closed.",
    }


def make_shift_tools(shift: dict[str, Any] | None) -> list[Any]:
    @tool
    def check_shift_close(counted_cash: float | None = None) -> dict[str, Any]:
        """Check the signed-in operator's OWN open cashier shift before they close it. Use for "close
        my shift", "end my shift", "is my drawer right?". This tool cannot close the shift.

        First call it WITHOUT counted_cash: it says whether a shift is open and whether anything
        would block closing; then ask how much cash is in the drawer (don't say the expected amount
        first). When they answer, call it again WITH counted_cash: it returns the expected cash,
        the difference (short / over / exact) and any blockers. Report the summary and blockers in
        one or two sentences, then end with exactly the close_hint. Never say the shift was closed.

        Args:
            counted_cash: The cash the operator counted in the drawer, in pesos (e.g. 4250).
        """
        return evaluate_shift_close(shift, counted_cash)

    return [check_shift_close]
