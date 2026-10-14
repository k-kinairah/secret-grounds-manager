"""Proposal tools (phase 3): validate against live state, return the exact
request, and never send anything but GETs."""

import asyncio
import json
from zoneinfo import ZoneInfo

import httpx

from sg_assistant import proposals
from sg_assistant.backend import BackendClient
from sg_assistant.server import build_server

TOKEN = "test-token"
PCS = [
    {"pc_name": "PC-01", "display_name": "PC 1", "connection_status": "online", "has_command_connection": True},
    {"pc_name": "PC-02", "display_name": "PC 2", "connection_status": "online", "has_command_connection": True},
    {"pc_name": "PC-03", "display_name": None, "connection_status": "offline", "has_command_connection": False},
    # Online by heartbeat but its command socket dropped: can't receive commands.
    {"pc_name": "VIP-1", "display_name": "VIP 1", "connection_status": "online", "has_command_connection": False},
]


def make_client(pcs=PCS, seen=None, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "Authentication required"})
        if status != 200:
            return httpx.Response(status, json={"detail": "boom"})
        if request.url.path == "/pcs":
            return httpx.Response(200, json=pcs)
        return httpx.Response(404, json={"detail": "Not Found"})

    return BackendClient("http://backend.test", TOKEN, transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def test_message_pc_builds_exact_request_and_only_reads():
    seen: list[httpx.Request] = []
    result = run(proposals.propose_message_pc(make_client(seen=seen), "pc 1", "  Your friend   is here  "))
    proposal = result["proposal"]
    assert proposal["kind"] == "message_pc"
    assert proposal["targets"] == ["PC-01"]
    assert proposal["request"] == {
        "method": "POST",
        "path": "/pcs/PC-01/message",
        "body": {"message": "Your friend is here", "severity": "info", "duration_seconds": 20},
    }
    assert proposal["summary"] == 'Show "Your friend is here" on PC 1?'
    assert "Nothing has been sent" in result["status"]
    assert [r.method for r in seen] == ["GET"]


def test_pc_name_matching_is_forgiving():
    client = make_client()
    for spoken in ("PC 1", "pc-01", "PC01", "pc1", "PC-01"):
        assert run(proposals.propose_message_pc(client, spoken, "hi"))["proposal"]["targets"] == ["PC-01"]
    assert run(proposals.propose_message_pc(client, "vip 1", "hi"))["error"].startswith("VIP 1 is offline")


def test_message_pc_errors():
    client = make_client()
    unknown = run(proposals.propose_message_pc(client, "PC 9", "hi"))
    assert "There is no PC called 'PC 9'" in unknown["error"] and "PC 1" in unknown["error"]
    assert "offline" in run(proposals.propose_message_pc(client, "PC-03", "hi"))["error"]
    assert "1 to 500" in run(proposals.propose_message_pc(client, "PC 1", "   "))["error"]
    assert "1 to 500" in run(proposals.propose_message_pc(client, "PC 1", "x" * 501))["error"]
    assert "Could not reach" in run(
        proposals.propose_message_pc(
            BackendClient("http://backend.test", TOKEN, transport=httpx.MockTransport(_refuse)), "PC 1", "hi"
        )
    )["error"]


def _refuse(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


def test_severity_is_limited():
    client = make_client()
    assert run(proposals.propose_message_pc(client, "PC 1", "hi", "urgent"))["proposal"]["details"]["severity"] == "urgent"
    assert run(proposals.propose_message_pc(client, "PC 1", "hi", "SHOUT"))["proposal"]["details"]["severity"] == "info"


def test_broadcast_to_all_online_lists_skipped_and_pins_targets():
    seen: list[httpx.Request] = []
    result = run(proposals.propose_broadcast(make_client(seen=seen), "We close in 15 minutes"))
    proposal = result["proposal"]
    assert proposal["kind"] == "broadcast"
    assert proposal["targets"] == ["PC-01", "PC-02"]
    assert proposal["details"]["skipped_offline"] == ["PC-03", "VIP 1"]
    assert proposal["request"]["path"] == "/commands/broadcast"
    # Explicit targets, never "everyone online at execution time".
    assert proposal["request"]["body"]["pc_names"] == ["PC-01", "PC-02"]
    assert proposal["summary"] == 'Show "We close in 15 minutes" on every online PC (2 PCs)?'
    assert [r.method for r in seen] == ["GET"]


def test_broadcast_to_named_pcs():
    result = run(proposals.propose_broadcast(make_client(), "Promo!", ["pc 2", "PC-02", "pc 3"]))
    assert result["proposal"]["targets"] == ["PC-02"]
    assert result["proposal"]["details"]["skipped_offline"] == ["PC-03"]
    assert "PC 2 (1 PC)" in result["proposal"]["summary"]
    assert "no PC called" in run(proposals.propose_broadcast(make_client(), "x", ["PC 77"]))["error"]


def test_broadcast_with_nobody_online():
    offline = [dict(pc, connection_status="offline") for pc in PCS]
    assert "None of those PCs are online" in run(proposals.propose_broadcast(make_client(pcs=offline), "hi"))["error"]


def test_propose_tools_are_opt_in():
    tz = ZoneInfo("Asia/Manila")
    default_names = {t.name for t in run(build_server(make_client(), tz).list_tools())}
    assert not any(name.startswith("propose_") for name in default_names)

    server = build_server(make_client(), tz, enable_actions=True)
    tools = {t.name: t for t in run(server.list_tools())}
    assert {"propose_message_pc", "propose_broadcast"} <= set(tools)
    for name in ("propose_message_pc", "propose_broadcast"):
        dumped = tools[name].model_dump(by_alias=True, exclude_none=True)
        assert dumped["annotations"]["readOnlyHint"] is True
        assert "does NOT send anything" in dumped["description"]

    result = run(server.call_tool("propose_message_pc", {"pc_name": "PC 2", "message": "hello"}))
    assert result.structured_content["proposal"]["targets"] == ["PC-02"]
    assert json.loads(result.content[0].text)["proposal"]["kind"] == "message_pc"
