"""Read-only drawer check (replaces the cashier-shift close check).

The shop no longer uses cashier shifts; it uses "on duty". On every message
the dashboard's server reads who is on duty and their running totals
(GET /operators/current and GET /dashboard/operator-sale, with the signed-in
operator's own login) and passes that summary here. The model gets one tool
bound to it: it asks for the counted cash, compares it with the starting float
plus the cash collected while the operator has been on duty, and reports GCash
separately. It can never change anything -- there is no tool for that.

Blind count: the expected amount is only revealed after the count. The cash
collected is not shown on the page either.

Starting float: SG_AGENT_DRAWER_FLOAT (e.g. 200) when the shop uses a fixed
float; otherwise the operator is asked each time.
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from strands import tool

HANDOVER_HINT = "Sign out when you hand over."
MAX_AMOUNT = Decimal("1000000")
HANDOFF_NOTE = (
    "If someone else used this drawer before this operator came on duty and changed operator "
    "without signing out, their cash is in the drawer too, so it can look over."
)


def _money(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").replace("₱", "").strip()).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _peso(value: Decimal) -> str:
    return f"₱{value:,.2f}"


def _amount(value: Any) -> Decimal | None:
    amount = _money(value)
    if amount is None or amount < 0 or amount > MAX_AMOUNT:
        return None
    return amount


def evaluate_drawer(
    duty: dict[str, Any] | None,
    counted_cash: Any = None,
    starting_float: Any = None,
    configured_float: Decimal | None = None,
) -> dict[str, Any]:
    """Pure function behind the tool (easy to test)."""
    if duty is None:
        return {"error": "I can only check the drawer from the dashboard's assistant page."}
    operator = duty.get("operator_name")
    if not duty.get("on_duty") or not operator:
        return {"on_duty": False, "message": "Nobody is on duty right now, so there's nothing to check against."}

    base: dict[str, Any] = {"on_duty": True, "operator": operator, "on_duty_since": duty.get("since")}
    if duty.get("asked_by_on_duty_operator") is False:
        base["whose_totals"] = (
            f"These are {operator}'s totals (the operator on duty), not the totals of the person asking."
        )

    if counted_cash in (None, ""):
        needs_float = configured_float is None
        return {
            **base,
            "step": "ask_for_count",
            "instruction": (
                "Ask how much cash is in the drawer now"
                + (" and how much was in it when they came on duty (the starting float)" if needs_float else "")
                + ". Don't say any expected or collected amount yet."
            ),
            "next_step": (
                "When they answer, call check_drawer again with counted_cash"
                + (" and starting_float" if needs_float else "")
                + ". You don't know the expected cash; only that call does, so never work out a difference yourself."
            ),
        }

    counted = _amount(counted_cash)
    if counted is None:
        return {"error": "Please say the counted cash as a peso amount, for example 1,250."}

    if configured_float is not None:
        float_amount, float_source = configured_float, "the shop's fixed starting float"
    else:
        if starting_float in (None, ""):
            return {
                **base,
                "step": "ask_for_float",
                "instruction": (
                    "Ask how much cash was in the drawer when they came on duty (the starting float), then "
                    "call check_drawer again with the same counted_cash and starting_float."
                ),
            }
        float_amount = _amount(starting_float)
        if float_amount is None:
            return {"error": "Please say the starting float as a peso amount, for example 200."}
        float_source = "the starting float you gave"

    cash_collected = _money(duty.get("cash_collected"))
    if cash_collected is None:
        return {"error": "The shop system didn't give the cash collected while on duty."}
    expected = float_amount + cash_collected
    difference = counted - expected
    if difference == 0:
        result, summary = "exact", f"The drawer matches: {_peso(counted)} counted, {_peso(expected)} expected."
    elif difference < 0:
        result = "short"
        summary = f"The drawer is short by {_peso(-difference)}: {_peso(counted)} counted, {_peso(expected)} expected."
    else:
        result = "over"
        summary = f"The drawer is over by {_peso(difference)}: {_peso(counted)} counted, {_peso(expected)} expected."

    gcash = _money(duty.get("gcash_collected")) or Decimal("0.00")
    response = {
        **base,
        "step": "result",
        "counted_cash": f"{counted:.2f}",
        "starting_float": f"{float_amount:.2f}",
        "float_source": float_source,
        "cash_collected_on_duty": f"{cash_collected:.2f}",
        "expected_cash": f"{expected:.2f}",
        "difference": f"{difference:.2f}",
        "result": result,
        "summary": summary,
        "gcash_note": (
            f"{_peso(gcash)} came in by GCash while on duty; that isn't in the drawer, check it against the GCash app."
            if gcash > 0
            else None
        ),
        "handover_hint": HANDOVER_HINT,
        "note": "This is a check only. Nothing was changed.",
    }
    if result == "over" or duty.get("asked_by_on_duty_operator") is False:
        response["handoff_note"] = HANDOFF_NOTE
    return response


def make_drawer_tools(duty: dict[str, Any] | None, configured_float: Decimal | None = None) -> list[Any]:
    @tool
    def check_drawer(counted_cash: float | None = None, starting_float: float | None = None) -> dict[str, Any]:
        """Read-only drawer check for the operator on duty. Use for "check my drawer", "I'm handing
        over", "close my shift", "is my drawer right?". This tool cannot change anything.

        First call it WITHOUT counted_cash: it says who is on duty and what to ask. Ask for the cash
        in the drawer (and the starting float if it says so) without saying any expected amount.
        When they answer, call it again WITH counted_cash (and starting_float if asked). It returns
        the expected cash, the difference (short / over / exact), a GCash note and sometimes a
        hand-off note. Report the summary and those notes in one to three sentences, then end with
        exactly the handover_hint. Never say anything was closed or signed out.

        Args:
            counted_cash: The cash counted in the drawer now, in pesos (e.g. 1250).
            starting_float: The cash in the drawer when they came on duty, in pesos (e.g. 200).
        """
        return evaluate_drawer(duty, counted_cash, starting_float, configured_float)

    return [check_drawer]
