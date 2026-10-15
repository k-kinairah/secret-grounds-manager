"""If the MCP server restarted, the cached MCP session is dead and Strands turns
every tool call into an error result (no exception). The service must notice,
reconnect, and answer the same message again once."""

from datetime import datetime

from fastapi.testclient import TestClient
from strands import tool
from strands.types.exceptions import MCPClientInitializationError

from conftest import ScriptedModel
from sg_agent.app import create_app
from sg_agent.service import SHOP_TZ, ChatService, ToolSource
from test_agent_api import settings

FIXED_NOW = datetime(2026, 10, 16, 21, 0, tzinfo=SHOP_TZ)


@tool
def dead_get_today_overview() -> dict:
    """Today's overview (dead MCP session)."""
    raise MCPClientInitializationError("the client session is not running. Ensure the agent is used within the MCP client context manager.")


@tool
def live_get_today_overview() -> dict:
    """Today's overview (fresh MCP session)."""
    return {"sales": {"total_sales": "1250.00"}}


class FlakyToolSource(ToolSource):
    """First connection is dead (server restarted); after reset() it's live."""

    def __init__(self) -> None:
        super().__init__(lambda: None)  # type: ignore[arg-type]
        self.resets = 0
        self.connects = 0

    def get_tools(self):
        self.connects += 1
        return [dead_get_today_overview] if self.resets == 0 else [live_get_today_overview]

    def reset(self) -> None:
        self.resets += 1


def make(turns):
    source = FlakyToolSource()
    model = ScriptedModel(turns)
    service = ChatService(lambda: model, source, now=lambda: FIXED_NOW)
    return TestClient(create_app(service, settings())), service, model, source


def test_dead_mcp_session_reconnects_and_answers_once():
    client, service, model, source = make([
        {"tool": "dead_get_today_overview"}, {"text": "I can't reach the shop system."},
        {"tool": "live_get_today_overview"}, {"text": "We've made ₱1,250 today."},
    ])
    body = client.post("/chat", json={"session_id": "s", "message": "How are we doing?"}).json()
    assert body["reply"] == "We've made ₱1,250 today."
    assert body["tools_used"] == ["live_get_today_overview"]
    assert source.resets == 1 and source.connects == 2
    # Only the successful answer is remembered, once.
    assert service.history("s") == [("How are we doing?", "We've made ₱1,250 today.")]
    # The retry got the same system prompt as the first attempt.
    assert model.requests[0]["system_prompt"] == model.requests[2]["system_prompt"]


def test_retries_at_most_once():
    client, service, model, source = make([
        {"tool": "dead_get_today_overview"}, {"text": "first"},
        {"tool": "live_get_today_overview"}, {"text": "second"},
    ])
    # Make the "live" tool dead too: the second failure is reported, not retried again.
    source.get_tools = lambda: [dead_get_today_overview]  # type: ignore[method-assign]
    model.turns[2] = {"tool": "dead_get_today_overview"}
    body = client.post("/chat", json={"session_id": "s", "message": "hi"}).json()
    assert body["reply"] == "second"
    assert source.resets == 1
    assert len(model.requests) == 4


def test_ordinary_tool_errors_do_not_trigger_reconnect():
    @tool
    def flaky_list_pcs() -> dict:
        """List PCs."""
        raise RuntimeError("backend returned 500")

    source = FlakyToolSource()
    source.get_tools = lambda: [flaky_list_pcs]  # type: ignore[method-assign]
    model = ScriptedModel([{"tool": "flaky_list_pcs"}, {"text": "The shop system had an error."}])
    client = TestClient(create_app(ChatService(lambda: model, source, now=lambda: FIXED_NOW), settings()))
    body = client.post("/chat", json={"session_id": "s", "message": "pcs?"}).json()
    assert body["reply"] == "The shop system had an error."
    assert source.resets == 0
