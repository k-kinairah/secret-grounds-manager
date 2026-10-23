"""Fix 6: /chat/stream sends each tool start as it happens, then the final answer."""

import json
from datetime import datetime

from fastapi.testclient import TestClient

from conftest import ScriptedModel
from sg_agent.app import create_app
from sg_agent.config import DEFAULT_MAX_TOKENS
from sg_agent.service import SHOP_TZ, ChatService
from test_agent_api import settings

FIXED_NOW = datetime(2026, 10, 16, 21, 0, tzinfo=SHOP_TZ)


def stream(client, body):
    response = client.post("/chat/stream", json=body)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def test_tool_event_comes_before_final(tool_source):
    model = ScriptedModel([{"tool": "get_today_overview"}, {"text": "We've made ₱1,250 today."}])
    client = TestClient(create_app(ChatService(lambda: model, tool_source, now=lambda: FIXED_NOW), settings()))
    events = stream(client, {"session_id": "s1", "message": "How are we doing?"})
    assert [e["type"] for e in events] == ["start", "tool", "final"]
    assert events[1]["name"] == "get_today_overview"
    final = events[-1]
    assert final["reply"] == "We've made ₱1,250 today."
    assert final["tools_used"] == ["get_today_overview"]
    assert "error" not in final


def test_each_tool_call_is_reported(tool_source):
    model = ScriptedModel([
        {"tool": "get_today_overview"},
        {"tool": "list_pcs"},
        {"text": "Done looking."},
    ])
    client = TestClient(create_app(ChatService(lambda: model, tool_source, now=lambda: FIXED_NOW), settings()))
    events = stream(client, {"session_id": "s1", "message": "overview and pcs"})
    assert [e.get("name") for e in events if e["type"] == "tool"] == ["get_today_overview", "list_pcs"]


def test_stream_without_tools_and_memory_shared_with_chat(tool_source):
    model = ScriptedModel([{"text": "Hello."}, {"text": "Again."}])
    service = ChatService(lambda: model, tool_source, now=lambda: FIXED_NOW)
    client = TestClient(create_app(service, settings()))
    events = stream(client, {"session_id": "s1", "message": "hi"})
    assert [e["type"] for e in events] == ["start", "final"]
    client.post("/chat", json={"session_id": "s1", "message": "again"})
    assert service.history("s1") == [("hi", "Hello."), ("again", "Again.")]


def test_stream_reports_model_failure_as_final(tool_source):
    model = ScriptedModel([{"raise": RuntimeError("throttled")}])
    client = TestClient(create_app(ChatService(lambda: model, tool_source, now=lambda: FIXED_NOW), settings()))
    events = stream(client, {"session_id": "s1", "message": "hi"})
    assert events[-1]["type"] == "final" and "throttled" in events[-1]["error"]


def test_default_max_tokens_is_short():
    assert DEFAULT_MAX_TOKENS == 300
