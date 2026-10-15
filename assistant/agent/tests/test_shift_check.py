"""Step (e): read-only close-shift check -- blind count, compare, flag blockers,
never close."""

import json
from datetime import datetime

from fastapi.testclient import TestClient

from conftest import ScriptedModel
from sg_agent.app import create_app
from sg_agent.service import SHOP_TZ, ChatService
from sg_agent.shift_check import CLOSE_HINT, evaluate_shift_close
from test_agent_api import settings

FIXED_NOW = datetime(2026, 10, 16, 21, 0, tzinfo=SHOP_TZ)
SHIFT = {
    "open": True, "cashier_name": "Neil", "opened_at": "2026-10-16T08:00:00+00:00", "opening_cash": "1000.00",
    "expected_cash": "4250.00", "payment_count": 12, "settlement_pending_sessions": 0,
    "review_required_payments": 0, "outstanding_unsettled_balance": "0.00",
}
NEIL = {"display_name": "Neil", "role": "cashier", "is_admin_account": False}


# --- the pure check ---------------------------------------------------------------------


def test_first_step_asks_for_count_without_revealing_expected():
    result = evaluate_shift_close(SHIFT)
    assert result["step"] == "ask_for_count"
    assert "4250" not in json.dumps(result) and "expected_cash" not in result
    assert result["blockers"] == []


def test_opened_at_is_given_in_shop_time():
    # 18:03 UTC on Oct 15 is 2:03 AM on Oct 16 in Manila (the model once said "yesterday at 6:03 PM").
    result = evaluate_shift_close(dict(SHIFT, opened_at="2026-10-15T18:03:33.074430Z"))
    assert result["opened_at"] == "October 16 at 2:03 AM (shop time)"
    assert evaluate_shift_close(dict(SHIFT, opened_at="soon"))["opened_at"] is None


def test_exact_short_over():
    exact = evaluate_shift_close(SHIFT, 4250)
    assert (exact["result"], exact["difference"]) == ("exact", "0.00")
    short = evaluate_shift_close(SHIFT, "4,1OO".replace("O", "0").replace(",", ""))
    assert (short["result"], short["difference"]) == ("short", "-150.00")
    assert short["summary"] == "The drawer is short by ₱150.00: ₱4,100.00 counted, ₱4,250.00 expected."
    over = evaluate_shift_close(SHIFT, 4300.5)
    assert (over["result"], over["difference"]) == ("over", "50.50")
    for r in (exact, short, over):
        assert r["close_hint"] == CLOSE_HINT == "You can close the shift from the Shifts page when ready."
        assert "NOT been closed" in r["note"]


def test_blockers_are_flagged():
    blocked = dict(SHIFT, settlement_pending_sessions=2, review_required_payments=1)
    result = evaluate_shift_close(blocked, 4250)
    assert result["blockers"] == ["2 sessions still waiting to be settled", "1 payment needing review"]
    assert "can't close the shift until these are resolved" in result["blocking_note"]
    assert evaluate_shift_close(blocked)["blockers"]  # also shown on the first step


def test_no_shift_and_no_data():
    assert "don't have a cashier shift open" in evaluate_shift_close({"open": False})["message"]
    assert "dashboard" in evaluate_shift_close(None)["error"]
    assert "peso amount" in evaluate_shift_close(SHIFT, "lots")["error"]
    assert "peso amount" in evaluate_shift_close(SHIFT, -5)["error"]
    assert "expected cash" in evaluate_shift_close(dict(SHIFT, expected_cash=None), 100)["error"]


# --- multi-turn through the agent --------------------------------------------------------------


def make(tool_source, turns):
    model = ScriptedModel(turns)
    service = ChatService(lambda: model, tool_source, now=lambda: FIXED_NOW)
    return TestClient(create_app(service, settings())), model, service


def tool_result_text(request) -> str:
    return json.dumps(request["messages"][-1], ensure_ascii=False)


def test_close_my_shift_multi_turn(tool_source):
    client, model, service = make(tool_source, [
        {"tool": "check_shift_close", "input": {}},
        {"text": "Sure. How much cash is in the drawer?"},
        {"tool": "check_shift_close", "input": {"counted_cash": 4100}},
        {"text": "The drawer is short by 150 pesos. You can close the shift from the Shifts page when ready."},
    ])
    first = client.post("/chat", json={"session_id": "s", "message": "Close my shift", "operator": NEIL, "shift": SHIFT}).json()
    assert first["reply"] == "Sure. How much cash is in the drawer?"
    assert first["tools_used"] == ["check_shift_close"]
    # The blind first step gave the model no expected amount to leak.
    assert "4250" not in tool_result_text(model.requests[1])

    second = client.post("/chat", json={"session_id": "s", "message": "4,100", "operator": NEIL, "shift": SHIFT}).json()
    assert second["reply"].endswith("You can close the shift from the Shifts page when ready.")
    result = tool_result_text(model.requests[3])
    assert "short" in result and "-150.00" in result and "NOT been closed" in result
    # The rules are in the prompt, and nothing in the tool list can close a shift.
    assert "must never say it was closed" in model.requests[0]["system_prompt"]
    assert [n for n in model.requests[0]["tool_names"] if "close" in n] == ["check_shift_close"]


def test_tool_only_when_dashboard_sent_the_shift(tool_source):
    client, model, _ = make(tool_source, [{"text": "a"}, {"text": "b"}])
    client.post("/chat", json={"session_id": "s1", "message": "close my shift"})
    client.post("/chat", json={"session_id": "s2", "message": "close my shift", "shift": {"open": False}})
    assert "check_shift_close" not in model.requests[0]["tool_names"]
    assert "Closing a shift" not in model.requests[0]["system_prompt"]
    assert "check_shift_close" in model.requests[1]["tool_names"]


def test_stream_route_accepts_shift(tool_source):
    client, model, _ = make(tool_source, [{"tool": "check_shift_close", "input": {}}, {"text": "How much cash is in the drawer?"}])
    response = client.post("/chat/stream", json={"session_id": "s", "message": "close my shift", "shift": SHIFT})
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert [e["type"] for e in events] == ["start", "tool", "final"]
    assert events[1]["name"] == "check_shift_close"
