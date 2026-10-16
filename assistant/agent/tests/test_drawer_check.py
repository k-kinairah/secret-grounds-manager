"""Read-only drawer check (on duty, replaces the cashier-shift close check): blind count,
starting float (fixed or asked), compare, GCash separately, hand-off note, never changes anything."""

import json
from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from conftest import ScriptedModel
from sg_agent.app import create_app
from sg_agent.config import get_settings
from sg_agent.drawer_check import HANDOVER_HINT, evaluate_drawer
from sg_agent.service import SHOP_TZ, ChatService
from test_agent_api import settings

FIXED_NOW = datetime(2026, 10, 17, 21, 0, tzinfo=SHOP_TZ)
DUTY = {
    "on_duty": True, "operator_name": "Tyrone", "since": "2026-10-17T00:05:00+00:00",
    "cash_collected": "1050.00", "gcash_collected": "220.00", "payment_count": 31,
    "asked_by_on_duty_operator": True,
}
TYRONE = {"display_name": "Tyrone", "role": "owner", "is_admin_account": False}


# --- the pure check ---------------------------------------------------------------------


def test_first_step_asks_for_count_and_float_without_revealing_amounts():
    result = evaluate_drawer(DUTY)
    assert result["step"] == "ask_for_count"
    text = json.dumps(result)
    assert "1050" not in text and "220" not in text and "expected" not in result
    assert "starting float" in result["instruction"] and "starting_float" in result["next_step"]


def test_fixed_float_is_not_asked_for():
    result = evaluate_drawer(DUTY, configured_float=Decimal("200.00"))
    assert "starting float" not in result["instruction"]
    done = evaluate_drawer(DUTY, 1250, configured_float=Decimal("200.00"))
    assert (done["result"], done["expected_cash"], done["float_source"]) == ("exact", "1250.00", "the shop's fixed starting float")


def test_float_is_asked_when_missing():
    assert evaluate_drawer(DUTY, 1200)["step"] == "ask_for_float"


def test_short_over_exact_with_gcash_and_closing_line():
    short = evaluate_drawer(DUTY, "1,100", 200)
    assert (short["result"], short["difference"]) == ("short", "-150.00")
    assert short["summary"] == "The drawer is short by ₱150.00: ₱1,100.00 counted, ₱1,250.00 expected."
    assert "₱220.00 came in by GCash" in short["gcash_note"]
    assert "handoff_note" not in short
    over = evaluate_drawer(DUTY, 1300, 200)
    assert (over["result"], over["difference"]) == ("over", "50.00")
    assert "changed operator without signing out" in over["handoff_note"]
    exact = evaluate_drawer(DUTY, 1250, "₱200")
    assert exact["result"] == "exact"
    for r in (short, over, exact):
        assert r["handover_hint"] == HANDOVER_HINT == "Sign out when you hand over."
        assert "Nothing was changed" in r["note"]


def test_someone_else_asking_gets_whose_totals_and_handoff_notes():
    result = evaluate_drawer(dict(DUTY, asked_by_on_duty_operator=False), 1250, 200)
    assert "Tyrone's totals" in result["whose_totals"]
    assert "handoff_note" in result


def test_nobody_on_duty_and_bad_input():
    assert "Nobody is on duty" in evaluate_drawer({"on_duty": False})["message"]
    assert "dashboard" in evaluate_drawer(None)["error"]
    assert "peso amount" in evaluate_drawer(DUTY, "lots")["error"]
    assert "peso amount" in evaluate_drawer(DUTY, -5)["error"]
    assert "starting float" in evaluate_drawer(DUTY, 1000, "some")["error"]
    assert "cash collected" in evaluate_drawer(dict(DUTY, cash_collected=None), 1000, 200)["error"]
    assert evaluate_drawer(dict(DUTY, gcash_collected="0.00"), 1250, 200)["gcash_note"] is None


def test_drawer_float_setting(monkeypatch):
    monkeypatch.setenv("SG_AGENT_DRAWER_FLOAT", "200")
    assert get_settings().drawer_float == Decimal("200.00")
    monkeypatch.setenv("SG_AGENT_DRAWER_FLOAT", "")
    assert get_settings().drawer_float is None


# --- multi-turn through the agent --------------------------------------------------------------


def make(tool_source, turns, drawer_float=None):
    model = ScriptedModel(turns)
    service = ChatService(lambda: model, tool_source, now=lambda: FIXED_NOW, drawer_float=drawer_float)
    return TestClient(create_app(service, settings())), model, service


def tool_result_text(request) -> str:
    return json.dumps(request["messages"][-1], ensure_ascii=False)


def test_check_my_drawer_multi_turn(tool_source):
    client, model, _ = make(tool_source, [
        {"tool": "check_drawer", "input": {}},
        {"text": "How much cash is in the drawer, and how much did you start with?"},
        {"tool": "check_drawer", "input": {"counted_cash": 1100, "starting_float": 200}},
        {"text": "The drawer is short by 150 pesos. Sign out when you hand over."},
    ])
    first = client.post("/chat", json={"session_id": "s", "message": "Check my drawer", "operator": TYRONE, "duty": DUTY}).json()
    assert first["tools_used"] == ["check_drawer"]
    assert "1050" not in tool_result_text(model.requests[1])  # blind: nothing to leak

    second = client.post("/chat", json={"session_id": "s", "message": "1,100 and 200", "operator": TYRONE, "duty": DUTY}).json()
    assert second["reply"].endswith("Sign out when you hand over.")
    result = tool_result_text(model.requests[3])
    assert "short" in result and "-150.00" in result and "Nothing was changed" in result and "GCash" in result
    assert "must never say you did" in model.requests[0]["system_prompt"]
    assert [n for n in model.requests[0]["tool_names"] if "drawer" in n or "close" in n] == ["check_drawer"]


def test_tool_only_when_dashboard_sent_duty(tool_source):
    client, model, _ = make(tool_source, [{"text": "a"}, {"text": "b"}])
    client.post("/chat", json={"session_id": "s1", "message": "check my drawer"})
    client.post("/chat", json={"session_id": "s2", "message": "check my drawer", "duty": {"on_duty": False}})
    assert "check_drawer" not in model.requests[0]["tool_names"]
    assert "Drawer check" not in model.requests[0]["system_prompt"]
    assert "check_drawer" in model.requests[1]["tool_names"]


def test_fixed_float_reaches_the_tool(tool_source):
    client, model, _ = make(tool_source, [
        {"tool": "check_drawer", "input": {"counted_cash": 1250}},
        {"text": "Exact. Sign out when you hand over."},
    ], drawer_float=Decimal("200.00"))
    client.post("/chat", json={"session_id": "s", "message": "I counted 1250", "duty": DUTY})
    assert '"result": "exact"' in tool_result_text(model.requests[1]).replace('\\"', '"')


def test_stream_route_accepts_duty(tool_source):
    client, _, _ = make(tool_source, [{"tool": "check_drawer", "input": {}}, {"text": "How much is in the drawer?"}])
    response = client.post("/chat/stream", json={"session_id": "s", "message": "I'm handing over", "duty": DUTY})
    events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
    assert [e["type"] for e in events] == ["start", "tool", "final"]
    assert events[1]["name"] == "check_drawer"
