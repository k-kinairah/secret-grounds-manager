"""Guard against "ready to confirm" replies with nothing prepared.

In a real-model run, late in a long conversation the model copied its earlier reply
("I've got that message ready for PC 9. Confirm to send it.") without calling a propose_
tool, so no card appeared. The service now answers once more with a reminder, and if the
reply still claims something is ready with nothing prepared, says so honestly instead."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.client._memory import InMemoryTransport
from strands.tools.mcp import MCPClient

from conftest import ScriptedModel
from sg_agent.app import create_app
from sg_agent.service import NOT_PREPARED_REPLY, READY_WITHOUT_TOOL_REMINDER, ChatService, ToolSource, _claims_ready
from test_actions import FIXED_NOW, fake_action_server
from test_agent_api import settings

COPIED_REPLY = "I've got that message ready for PC 9. Confirm to send it."
PROPOSE = {"tool": "propose_message_pc", "input": {"pc_name": "PC 3", "message": "Your friend is here"}}
READY = {"text": "I've got that message ready for PC 3. Confirm to send it."}


@pytest.fixture
def make():
    calls: list[str] = []
    source = ToolSource(lambda: MCPClient(lambda: InMemoryTransport(fake_action_server(calls))))

    def build(turns: list[dict[str, Any]]):
        model = ScriptedModel(turns)
        service = ChatService(lambda: model, source, now=lambda: FIXED_NOW)
        return TestClient(create_app(service, settings())), service, model, calls

    yield build
    source.close()


def chat(client, message, session="s1"):
    response = client.post("/chat", json={"session_id": session, "message": message})
    assert response.status_code == 200
    return response.json()


def test_claim_without_tool_is_retried_and_the_retry_prepares_it(make):
    client, _, model, calls = make([{"text": COPIED_REPLY}, PROPOSE, READY])
    body = chat(client, "Send PC 3 a message: your friend is here")
    assert calls == ["propose_message_pc"]
    assert body["pending_action"]["kind"] == "message_pc"
    assert body["tools_used"] == ["propose_message_pc"]
    assert body["reply"] == READY["text"]
    # The retry carried the reminder; the first attempt didn't.
    assert READY_WITHOUT_TOOL_REMINDER not in model.requests[0]["system_prompt"]
    assert READY_WITHOUT_TOOL_REMINDER in model.requests[1]["system_prompt"]


def test_retry_runs_without_the_earlier_turns(make):
    # The copying comes from earlier "ready ... Confirm" replies in the history, so the
    # retry answers the message on its own.
    client, service, model, _ = make([{"text": "We made 1,035 pesos today."}, {"text": COPIED_REPLY}, PROPOSE, READY])
    chat(client, "How are we doing?")
    body = chat(client, "Send PC 3 a message: your friend is here")
    assert body["pending_action"]["kind"] == "message_pc"
    first_try, retry = model.requests[1], model.requests[2]
    assert len(first_try["messages"]) == 3  # earlier question + answer + this message
    assert len(retry["messages"]) == 1  # only this message
    assert "Send PC 3" in str(retry["messages"][0])
    # The conversation itself is kept: both turns are remembered.
    assert [user for user, _ in service.history("s1")] == ["How are we doing?", "Send PC 3 a message: your friend is here"]


def test_claim_again_after_the_retry_becomes_an_honest_reply(make):
    client, service, _, calls = make([{"text": COPIED_REPLY}, {"text": COPIED_REPLY}])
    body = chat(client, "Send PC 9 a message: no food near the keyboard")
    assert calls == []
    assert body["reply"] == NOT_PREPARED_REPLY == "I couldn't prepare that, please ask again."
    assert "pending_action" not in body
    # The honest reply is what the conversation remembers, not the copied claim.
    assert service.history("s1")[-1][1] == NOT_PREPARED_REPLY


def test_retry_that_answers_without_claiming_is_kept(make):
    client, _, _, _ = make([{"text": COPIED_REPLY}, {"text": "Which PC should I message?"}])
    body = chat(client, "message someone")
    assert body["reply"] == "Which PC should I message?"
    assert "pending_action" not in body


def test_model_error_on_retry_gives_the_honest_reply(make):
    client, _, _, _ = make([{"text": COPIED_REPLY}, {"raise": RuntimeError("bedrock down")}])
    body = chat(client, "Send PC 9 a message: hi")
    assert body["reply"] == NOT_PREPARED_REPLY
    assert "pending_action" not in body


def test_no_retry_for_a_normal_proposal_or_an_ordinary_answer(make):
    client, _, model, _ = make([PROPOSE, READY, {"text": "We made 1,035 pesos today."}])
    assert chat(client, "Send PC 3 a message: your friend is here")["pending_action"]
    assert chat(client, "How are we doing?", session="s2")["reply"] == "We made 1,035 pesos today."
    assert len(model.requests) == 3  # propose + ready + one answer; no extra attempt


def test_no_retry_while_an_earlier_card_is_still_open(make):
    still_ready = "That message for PC 3 is still ready. Confirm on screen to send it."
    client, _, model, _ = make([PROPOSE, READY, {"text": still_ready}])
    assert chat(client, "Send PC 3 a message: your friend is here")["pending_action"]
    body = chat(client, "Is it ready?")
    assert body["reply"] == still_ready
    assert len(model.requests) == 3


def test_read_only_mode_has_no_guard(tool_source):
    # Without propose tools there are no cards to promise, so nothing to check.
    model = ScriptedModel([{"text": COPIED_REPLY}])
    service = ChatService(lambda: model, tool_source, now=lambda: FIXED_NOW)
    body = TestClient(create_app(service, settings())).post("/chat", json={"session_id": "s", "message": "hi"}).json()
    assert body["reply"] == COPIED_REPLY
    assert len(model.requests) == 1


@pytest.mark.parametrize(
    ("reply", "claims"),
    [
        ("I've got that message ready for PC 8. Confirm to send it.", True),
        ("Five idle PCs are ready to shut down. Confirm on screen?", True),
        ("I've prepared the announcement; please confirm it.", True),
        ("We made 1,035 pesos today.", False),
        ("Okay, cancelled. Nothing was sent.", False),
        ("Say confirm or cancel, or use the buttons.", False),
        ("The drawer matches. Sign out when you hand over.", False),
    ],
)
def test_claims_ready(reply, claims):
    assert _claims_ready(reply) is claims
