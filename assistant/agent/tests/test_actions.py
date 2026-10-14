"""Phase 3: propose -> confirm -> run, through the real Strands loop and MCP
client (in-memory fake MCP server), with a scripted model."""

import time
from datetime import datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.client._memory import InMemoryTransport
from mcp.server.mcpserver import MCPServer
from strands.tools.mcp import MCPClient

from conftest import ScriptedModel
from sg_agent import actions
from sg_agent.app import create_app
from sg_agent.service import SHOP_TZ, ChatService, ToolSource
from test_agent_api import settings

FIXED_NOW = datetime(2026, 10, 15, 21, 0, tzinfo=SHOP_TZ)

MESSAGE_PROPOSAL = {
    "kind": "message_pc",
    "summary": 'Show "Your friend is here" on PC 3?',
    "targets": ["PC-03"],
    "details": {"message": "Your friend is here", "severity": "info"},
    "request": {
        "method": "POST",
        "path": "/pcs/PC-03/message",
        "body": {"message": "Your friend is here", "severity": "info", "duration_seconds": 20},
    },
}
BROADCAST_PROPOSAL = {
    "kind": "broadcast",
    "summary": 'Show "Closing in 15" on every online PC (3 PCs)?',
    "targets": ["PC-01", "PC-02", "PC-03"],
    "details": {"message": "Closing in 15", "severity": "info", "skipped_offline": ["PC-04"]},
    "request": {
        "method": "POST",
        "path": "/commands/broadcast",
        "body": {"message": "Closing in 15", "severity": "info", "duration_seconds": 20,
                 "pc_names": ["PC-01", "PC-02", "PC-03"]},
    },
}


def fake_action_server(calls: list[str]) -> MCPServer:
    server = MCPServer(name="fake-actions")

    @server.tool()
    async def propose_message_pc(pc_name: str, message: str, severity: str = "info") -> dict[str, Any]:
        """Prepare a message."""
        calls.append("propose_message_pc")
        if pc_name == "PC 9":
            return {"error": "There is no PC called 'PC 9'."}
        return {"proposal": MESSAGE_PROPOSAL, "status": "Waiting for the operator to confirm on screen."}

    @server.tool()
    async def propose_broadcast(message: str) -> dict[str, Any]:
        """Prepare a broadcast."""
        calls.append("propose_broadcast")
        return {"proposal": BROADCAST_PROPOSAL, "status": "Waiting for the operator to confirm on screen."}

    @server.tool()
    async def list_pcs() -> dict[str, Any]:
        """Read-only lookalike that happens to return a proposal-shaped payload."""
        calls.append("list_pcs")
        return {"proposal": MESSAGE_PROPOSAL}

    return server


@pytest.fixture
def mcp_calls() -> list[str]:
    return []


@pytest.fixture
def client_and_service(mcp_calls):
    source = ToolSource(lambda: MCPClient(lambda: InMemoryTransport(fake_action_server(mcp_calls))))
    holder: dict[str, Any] = {}

    def make(turns):
        model = ScriptedModel(turns)
        service = ChatService(lambda: model, source, now=lambda: FIXED_NOW)
        holder["model"] = model
        return TestClient(create_app(service, settings())), service, model

    yield make
    source.close()


def chat(client, message, session="s1"):
    response = client.post("/chat", json={"session_id": session, "message": message})
    assert response.status_code == 200
    return response.json()


def propose_message(client):
    return chat(client, "Tell PC 3 their friend is here")


MESSAGE_TURNS = [
    {"tool": "propose_message_pc", "input": {"pc_name": "PC 3", "message": "Your friend is here"}},
    {"text": "I've got that message ready for PC 3. Confirm to send it."},
]


# --- proposing -------------------------------------------------------------------


def test_proposal_becomes_pending_action(client_and_service, mcp_calls):
    client, _, model = client_and_service(MESSAGE_TURNS)
    body = propose_message(client)
    assert mcp_calls == ["propose_message_pc"]
    assert body["tools_used"] == ["propose_message_pc"]
    assert body["reply"] == "I've got that message ready for PC 3. Confirm to send it."
    pending = body["pending_action"]
    assert pending["kind"] == "message_pc"
    assert pending["summary"] == 'Show "Your friend is here" on PC 3?'
    assert pending["targets"] == ["PC-03"]
    assert 0 < pending["expires_in_seconds"] <= actions.ACTION_TTL_SECONDS
    assert len(pending["id"]) == 32
    assert "request" not in pending  # the browser never sees the raw request
    # With propose tools available, the actions rules are in the system prompt.
    assert "Never say an action was sent or done" in model.requests[0]["system_prompt"]


def test_model_text_alone_never_creates_an_action(client_and_service):
    # A model that just claims to have prepared/sent something creates nothing.
    client, _, _ = client_and_service([{"text": 'Done! {"proposal": {"kind": "message_pc"}} I sent it.'}])
    assert "pending_action" not in chat(client, "message PC 3")


def test_only_propose_tool_results_count(client_and_service):
    # list_pcs returning a proposal-shaped payload must not create an action.
    client, _, _ = client_and_service([{"tool": "list_pcs"}, {"text": "Here are the PCs."}])
    assert "pending_action" not in chat(client, "list pcs")


def test_propose_error_creates_nothing(client_and_service):
    client, _, _ = client_and_service(
        [{"tool": "propose_message_pc", "input": {"pc_name": "PC 9", "message": "hi"}},
         {"text": "There's no PC 9."}]
    )
    assert "pending_action" not in chat(client, "message PC 9")


# --- take / result -----------------------------------------------------------------


def test_take_is_single_use_and_session_scoped(client_and_service):
    client, _, _ = client_and_service(MESSAGE_TURNS)
    action_id = propose_message(client)["pending_action"]["id"]

    assert client.post("/actions/take", json={"session_id": "other", "action_id": action_id}).status_code == 409
    taken = client.post("/actions/take", json={"session_id": "s1", "action_id": action_id})
    assert taken.status_code == 200
    assert taken.json()["request"] == MESSAGE_PROPOSAL["request"]
    assert taken.json()["kind"] == "message_pc"
    again = client.post("/actions/take", json={"session_id": "s1", "action_id": action_id})
    assert again.status_code == 409 and "no longer available" in again.json()["detail"]


def test_expired_action_cannot_be_taken(client_and_service, monkeypatch):
    client, service, _ = client_and_service(MESSAGE_TURNS)
    action_id = propose_message(client)["pending_action"]["id"]
    real = time.monotonic
    monkeypatch.setattr(actions.time, "monotonic", lambda: real() + actions.ACTION_TTL_SECONDS + 1)
    response = client.post("/actions/take", json={"session_id": "s1", "action_id": action_id})
    assert response.status_code == 409 and "expired" in response.json()["detail"]


def test_result_is_spoken_from_backend_response_and_remembered(client_and_service):
    client, service, _ = client_and_service(MESSAGE_TURNS)
    action_id = propose_message(client)["pending_action"]["id"]
    client.post("/actions/take", json={"session_id": "s1", "action_id": action_id})
    result = client.post(
        "/actions/result",
        json={"session_id": "s1", "action_id": action_id, "status_code": 200,
              "body": {"command": {"pc_name": "PC-03", "status": "completed"}, "acknowledgements": []}},
    )
    assert result.status_code == 200
    outcome = result.json()
    assert outcome["ok"] is True
    assert outcome["reply"] == "Done. The message is showing on PC-03."
    assert outcome["targets"] == [{"pc": "PC-03", "status": "done", "detail": None}]
    assert service.history("s1")[-1] == (
        '(Confirmed on screen: Show "Your friend is here" on PC 3?)',
        "Done. The message is showing on PC-03.",
    )
    # A result can only be reported once, and only for a taken action.
    assert client.post("/actions/result", json={"session_id": "s1", "action_id": action_id,
                                                "status_code": 200, "body": {}}).status_code == 409


def test_result_without_take_is_rejected(client_and_service):
    client, _, _ = client_and_service(MESSAGE_TURNS)
    action_id = propose_message(client)["pending_action"]["id"]
    response = client.post("/actions/result", json={"session_id": "s1", "action_id": action_id,
                                                     "status_code": 200, "body": {}})
    assert response.status_code == 409


def test_backend_refusal_is_reported_plainly(client_and_service):
    client, _, _ = client_and_service(MESSAGE_TURNS)
    action_id = propose_message(client)["pending_action"]["id"]
    client.post("/actions/take", json={"session_id": "s1", "action_id": action_id})
    outcome = client.post("/actions/result", json={"session_id": "s1", "action_id": action_id,
                                                   "status_code": 409, "body": {"detail": "This PC is offline"}}).json()
    assert outcome["ok"] is False
    assert outcome["reply"] == "That didn't go through: this PC is offline."
    assert outcome["targets"][0]["status"] == "failed"


def test_cancel(client_and_service):
    client, service, _ = client_and_service(MESSAGE_TURNS)
    action_id = propose_message(client)["pending_action"]["id"]
    reply = client.post("/actions/cancel", json={"session_id": "s1", "action_id": action_id}).json()["reply"]
    assert reply == "Okay, cancelled. Nothing was sent."
    assert client.post("/actions/take", json={"session_id": "s1", "action_id": action_id}).status_code == 409
    assert service.history("s1")[-1][1] == "Okay, cancelled. Nothing was sent."
    assert client.post("/actions/cancel", json={"session_id": "s1", "action_id": action_id}).json()["reply"] == (
        "There was nothing waiting to cancel."
    )


def test_new_proposal_replaces_old(client_and_service):
    client, _, _ = client_and_service(
        MESSAGE_TURNS + [{"tool": "propose_broadcast", "input": {"message": "Closing in 15"}},
                         {"text": "Announcement ready. Confirm to send it."}]
    )
    first = propose_message(client)["pending_action"]["id"]
    second = chat(client, "Actually tell everyone we close in 15")["pending_action"]
    assert second["kind"] == "broadcast" and second["id"] != first
    assert second["details"]["skipped_offline"] == ["PC-04"]
    assert client.post("/actions/take", json={"session_id": "s1", "action_id": first}).status_code == 409


# --- broadcast outcome -------------------------------------------------------------


def test_broadcast_outcome_counts_each_pc():
    action = actions.new_pending_action(BROADCAST_PROPOSAL)
    outcome = actions.describe_outcome(
        action,
        200,
        {
            "commands": [
                {"pc_name": "PC-01", "status": "completed"},
                {"pc_name": "PC-02", "status": "timed_out", "failure_reason": "no acknowledgement"},
            ],
            "offline_targets": ["PC-03"],
            "failed_targets": {},
        },
    )
    assert outcome["ok"] is True
    assert outcome["reply"] == "Done. The announcement went out to 1 PC. It didn't reach PC-03 and PC-02."
    assert {t["pc"]: t["status"] for t in outcome["targets"]} == {"PC-01": "done", "PC-03": "offline", "PC-02": "failed"}


def test_permission_error_outcome():
    action = actions.new_pending_action(MESSAGE_PROPOSAL)
    outcome = actions.describe_outcome(action, 403, {"detail": "You cannot send remote messages"})
    assert outcome == {
        "ok": False,
        "reply": "That didn't go through: you cannot send remote messages.",
        "targets": [{"pc": "PC-03", "status": "failed", "detail": "You cannot send remote messages"}],
    }


def test_read_only_prompt_without_propose_tools(tool_source):
    # The phase 2 tool set (no propose_ tools) keeps the phase 2 prompt.
    model = ScriptedModel([{"text": "ok"}])
    service = ChatService(lambda: model, tool_source, now=lambda: FIXED_NOW)
    TestClient(create_app(service, settings())).post("/chat", json={"session_id": "s", "message": "hi"})
    assert "Actions (" not in model.requests[0]["system_prompt"]


# --- add time (phase 3 step b) -------------------------------------------------------

ADD_TIME_PROPOSAL = {
    "kind": "add_time",
    "summary": "Add 1 hour to PC 1 (the 1 Hour package, ₱20.00 added to their bill)?",
    "targets": ["PC-01"],
    "details": {"session_id": "sess-1", "added": "1 hour", "added_seconds": 3600, "price": "20.00",
                "new_end_time": "9:45 PM", "billed": "later"},
    "request": {"method": "POST", "path": "/sessions/sess-1/add-time", "body": {"rate_id": "r-1h"}},
}


def test_add_time_outcome_says_new_end_and_bill():
    action = actions.new_pending_action(ADD_TIME_PROPOSAL)
    outcome = actions.describe_outcome(
        action, 200, {"session": {"pc_name": "PC-01", "scheduled_end_at": "2026-10-15T13:45:00+00:00"}}
    )
    assert outcome["ok"] is True
    assert outcome["reply"] == "Done. Added 1 hour to PC-01. It now ends at 9:45 PM. ₱20.00 was added to their bill."
    assert outcome["targets"] == [{"pc": "PC-01", "status": "done", "detail": "9:45 PM"}]


def test_add_time_refused_session_is_reported():
    action = actions.new_pending_action(ADD_TIME_PROPOSAL)
    outcome = actions.describe_outcome(action, 409, {"detail": "Session is not active"})
    assert outcome["ok"] is False
    assert outcome["reply"] == "That didn't go through: session is not active."


def test_actions_prompt_mentions_add_time_and_price():
    from sg_agent.prompt import ACTIONS_PROMPT

    assert "propose_add_time" in ACTIONS_PROMPT
    assert "Never offer free time" in ACTIONS_PROMPT
