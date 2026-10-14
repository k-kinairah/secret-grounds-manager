from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedModel
from sg_agent.app import create_app
from sg_agent.config import Settings, get_settings
from sg_agent.models import ModelConfigError, build_model
from sg_agent.service import MCP_DOWN_REPLY, MODEL_DOWN_REPLY, SHOP_TZ, ChatService, ToolSource

FIXED_NOW = datetime(2026, 10, 14, 15, 0, tzinfo=SHOP_TZ)


def settings(**overrides) -> Settings:
    base = dict(
        port=8781,
        mcp_url="http://127.0.0.1:8765/mcp",
        provider="bedrock",
        model_id="test-model",
        max_tokens=600,
        temperature=None,
        bedrock_region="ap-southeast-2",
        anthropic_api_key="",
        openai_api_key="",
        openai_base_url="",
        ollama_host="http://127.0.0.1:11434",
        cors_origins=("http://localhost:3000",),
        max_turns=20,
        max_sessions=200,
    )
    base.update(overrides)
    return Settings(**base)


def make_client(model, tool_source, **service_kwargs):
    service = ChatService(lambda: model, tool_source, now=lambda: FIXED_NOW, **service_kwargs)
    return TestClient(create_app(service, settings())), service


# --- /chat ------------------------------------------------------------------------


def test_chat_uses_mcp_tool_and_reports_it(tool_source, mcp_calls):
    model = ScriptedModel(
        [
            {"tool": "get_today_overview"},
            {"text": "We've made **₱1,250** today from 9 payments, and 5 PCs are free."},
        ]
    )
    client, _ = make_client(model, tool_source)
    response = client.post("/chat", json={"session_id": "s1", "message": "How are we doing today?"})
    assert response.status_code == 200
    body = response.json()
    assert body["tools_used"] == ["get_today_overview"]
    assert body["reply"] == "We've made ₱1,250 today from 9 payments, and 5 PCs are free."  # markdown stripped
    assert "error" not in body
    assert mcp_calls == [("get_today_overview", {})]

    # The model saw every MCP tool and got the real tool result back.
    first, second = model.requests
    assert first["tool_names"] == ["get_sales_report", "get_today_overview", "list_pcs"]
    tool_result = second["messages"][-1]["content"][0]["toolResult"]
    assert "1250.00" in str(tool_result)

    # The system prompt carries the spoken-reply rules and today's shop-local date.
    system_prompt = first["system_prompt"]
    assert "read out loud" in system_prompt and "No markdown" in system_prompt
    assert "Asia/Manila" in system_prompt and "2026-10-14" in system_prompt
    assert "coming soon" in system_prompt


def test_chat_passes_tool_arguments_and_note(tool_source, mcp_calls):
    model = ScriptedModel(
        [
            {"tool": "get_sales_report", "input": {"start_date": "2026-10-01", "end_date": "2026-10-07"}},
            {"text": "Sales were reset on October 5, so from then you made ₱800."},
        ]
    )
    client, _ = make_client(model, tool_source)
    body = client.post("/chat", json={"session_id": "s1", "message": "Sales Oct 1 to 7?"}).json()
    assert mcp_calls == [("get_sales_report", {"start_date": "2026-10-01", "end_date": "2026-10-07"})]
    assert body["tools_used"] == ["get_sales_report"]
    assert "Sales figures were reset" in str(model.requests[1]["messages"][-1])


def test_tool_error_reaches_the_model_and_is_not_a_crash(tool_source):
    model = ScriptedModel([{"tool": "list_pcs"}, {"text": "I couldn't reach the shop system to check the PCs."}])
    client, _ = make_client(model, tool_source)
    body = client.post("/chat", json={"session_id": "s1", "message": "Which PCs are free?"}).json()
    assert body["tools_used"] == ["list_pcs"]
    assert "couldn't reach" in body["reply"]
    assert "Could not reach the Secret Grounds backend" in str(model.requests[1]["messages"][-1])


def test_reply_without_tools(tool_source):
    model = ScriptedModel([{"text": "Starting sessions from here is coming soon."}])
    client, _ = make_client(model, tool_source)
    body = client.post("/chat", json={"session_id": "s1", "message": "Start PC 3 for an hour"}).json()
    assert body == {"reply": "Starting sessions from here is coming soon.", "tools_used": []}


# --- memory ---------------------------------------------------------------------


def test_memory_is_per_session_and_text_only(tool_source):
    model = ScriptedModel(
        [
            {"tool": "get_today_overview"},
            {"text": "Five PCs are free."},
            {"text": "Yes, five."},
            {"text": "Hello."},
        ]
    )
    client, service = make_client(model, tool_source)
    client.post("/chat", json={"session_id": "a", "message": "Free PCs?"})
    client.post("/chat", json={"session_id": "a", "message": "Sure?"})
    client.post("/chat", json={"session_id": "b", "message": "Hi"})

    # Session a's second call carried its first turn as plain text, with no stale tool results.
    second_a = model.requests[2]["messages"]
    assert [m["role"] for m in second_a] == ["user", "assistant", "user"]
    assert second_a[0]["content"] == [{"text": "Free PCs?"}]
    assert second_a[1]["content"] == [{"text": "Five PCs are free."}]
    assert "toolResult" not in str(second_a)
    # Session b started fresh.
    assert [m["role"] for m in model.requests[3]["messages"]] == ["user"]
    assert service.history("a") == [("Free PCs?", "Five PCs are free."), ("Sure?", "Yes, five.")]


def test_memory_is_capped(tool_source):
    model = ScriptedModel([{"text": f"Reply {i}"} for i in range(5)])
    client, service = make_client(model, tool_source, max_turns=2)
    for i in range(5):
        client.post("/chat", json={"session_id": "s", "message": f"Q{i}"})
    assert service.history("s") == [("Q3", "Reply 3"), ("Q4", "Reply 4")]
    assert len(model.requests[-1]["messages"]) == 2 * 2 + 1


def test_session_count_is_capped(tool_source):
    model = ScriptedModel([{"text": "ok"} for _ in range(3)])
    client, service = make_client(model, tool_source, max_sessions=2)
    for sid in ("one", "two", "three"):
        client.post("/chat", json={"session_id": sid, "message": "hi"})
    assert service.history("one") == []  # least recently used was dropped
    assert service.history("three") == [("hi", "ok")]


def test_reset_clears_one_session(tool_source):
    model = ScriptedModel([{"text": "ok"}, {"text": "ok"}, {"text": "fresh"}])
    client, service = make_client(model, tool_source)
    client.post("/chat", json={"session_id": "a", "message": "hi"})
    client.post("/chat", json={"session_id": "b", "message": "hi"})
    assert client.post("/reset", json={"session_id": "a"}).json() == {"session_id": "a", "cleared": True}
    assert client.post("/reset", json={"session_id": "a"}).json()["cleared"] is False
    assert service.history("a") == [] and service.history("b") == [("hi", "ok")]
    client.post("/chat", json={"session_id": "a", "message": "again"})
    assert [m["role"] for m in model.requests[-1]["messages"]] == ["user"]


# --- failures ---------------------------------------------------------------------


class BrokenToolSource(ToolSource):
    def __init__(self) -> None:
        super().__init__(lambda: None)  # type: ignore[arg-type]
        self.resets = 0

    def get_tools(self):
        raise ConnectionError("connection refused")

    def reset(self) -> None:
        self.resets += 1


def test_mcp_server_down_gives_readable_error():
    source = BrokenToolSource()
    model = ScriptedModel([])
    client, _ = make_client(model, source)
    body = client.post("/chat", json={"session_id": "s", "message": "Sales today?"}).json()
    assert body["reply"] == MCP_DOWN_REPLY
    assert "MCP server" in body["error"] and "connection refused" in body["error"]
    assert body["tools_used"] == []
    assert model.requests == []  # never asked the model to guess
    assert source.resets == 1


def test_model_failure_gives_readable_error(tool_source):
    model = ScriptedModel([{"raise": RuntimeError("AccessDeniedException: not authorized to invoke model")}])
    client, service = make_client(model, tool_source)
    body = client.post("/chat", json={"session_id": "s", "message": "Sales today?"}).json()
    assert body["reply"] == MODEL_DOWN_REPLY
    assert "not authorized" in body["error"]
    assert service.history("s") == []  # failed turns are not remembered


def test_model_not_configured_gives_readable_error(tool_source):
    def factory():
        raise ModelConfigError("No model configured: set SG_AGENT_MODEL_ID for provider 'bedrock'.")

    service = ChatService(factory, tool_source, now=lambda: FIXED_NOW)
    client = TestClient(create_app(service, settings()))
    body = client.post("/chat", json={"session_id": "s", "message": "hi"}).json()
    assert body["reply"] == MODEL_DOWN_REPLY
    assert "SG_AGENT_MODEL_ID" in body["error"]


def test_request_validation(tool_source):
    client, _ = make_client(ScriptedModel([]), tool_source)
    assert client.post("/chat", json={"session_id": "", "message": "hi"}).status_code == 422
    assert client.post("/chat", json={"session_id": "s", "message": ""}).status_code == 422
    assert client.post("/chat", json={"message": "hi"}).status_code == 422


# --- health, CORS -------------------------------------------------------------------


def test_health(tool_source):
    client, _ = make_client(ScriptedModel([]), tool_source)
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["provider"] == "bedrock" and body["model_id"] == "test-model"


def test_cors_only_allows_dashboard_dev_origin(tool_source):
    client, _ = make_client(ScriptedModel([]), tool_source)
    allowed = client.options(
        "/chat", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"}
    )
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    denied = client.options(
        "/chat", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"}
    )
    assert "access-control-allow-origin" not in denied.headers


# --- configuration -------------------------------------------------------------------


def test_settings_defaults(monkeypatch):
    for name in ("SG_AGENT_PROVIDER", "SG_AGENT_PORT", "SG_MCP_URL", "SG_AGENT_BEDROCK_REGION", "AWS_REGION",
                 "SG_AGENT_CORS_ORIGINS", "SG_AGENT_MODEL_ID"):
        monkeypatch.delenv(name, raising=False)
    s = get_settings()
    assert (s.provider, s.port, s.mcp_url, s.bedrock_region) == (
        "bedrock", 8781, "http://127.0.0.1:8765/mcp", "ap-southeast-2"
    )
    assert s.cors_origins == ("http://localhost:3000", "http://127.0.0.1:3000")


def test_settings_rejects_unknown_provider(monkeypatch):
    monkeypatch.setenv("SG_AGENT_PROVIDER", "gpt-local")
    with pytest.raises(ValueError):
        get_settings()


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"model_id": ""}, "SG_AGENT_MODEL_ID"),
        ({"provider": "anthropic"}, "ANTHROPIC_API_KEY"),
        ({"provider": "openai"}, "OPENAI_API_KEY"),
    ],
)
def test_build_model_explains_missing_config(overrides, message):
    with pytest.raises(ModelConfigError, match=message):
        build_model(settings(**overrides))


@pytest.mark.parametrize(
    "overrides, class_name",
    [
        ({"provider": "bedrock"}, "BedrockModel"),
        ({"provider": "anthropic", "anthropic_api_key": "sk-test"}, "AnthropicModel"),
        ({"provider": "openai", "openai_api_key": "sk-test"}, "OpenAIModel"),
        ({"provider": "ollama"}, "OllamaModel"),
    ],
)
def test_build_model_selects_provider_class(overrides, class_name):
    # Constructing a provider client does not call the network.
    model = build_model(settings(**overrides))
    assert type(model).__name__ == class_name
    assert model.get_config()["model_id"] == "test-model"


def test_spoken_cleanup_keeps_words_apart():
    from sg_agent.service import _spoken

    assert _spoken("**Total_sales** is `₱1,250`.\n- 5 PCs free") == "Total sales is ₱1,250. 5 PCs free"
