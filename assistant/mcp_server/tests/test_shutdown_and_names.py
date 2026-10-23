"""Phase 3 step (c): number words in PC names, and propose_shutdown_idle_pcs."""

import asyncio
from zoneinfo import ZoneInfo

import httpx
import pytest

from sg_assistant import proposals
from sg_assistant.backend import BackendClient
from sg_assistant.server import build_server

TOKEN = "test-token"


def pc(name, display=None, online=True, cmd=True, session_status="idle", order=0, **extra):
    return {
        "pc_name": name, "display_name": display, "connection_status": "online" if online else "offline",
        "has_command_connection": cmd, "session_status": session_status, "layout_order": order,
        "is_enabled": True, "active_session_id": None, "startup_status": None, "prepared_session_id": None,
        **extra,
    }


PCS = [
    pc("PC-01", "PC 1", order=1),
    pc("PC-02", "PC 2", order=2, session_status="active", active_session_id="s2"),
    pc("PC-03", "PC 3", order=3),
    pc("PC-04", "PC 4", order=4, online=False, cmd=False),
    pc("PC-05", "PC 5", order=5, startup_status="waking", prepared_session_id="p5"),
    pc("PC-06", "PC 6", order=6, is_enabled=False),
    pc("PC-07", "PC 7", order=7),  # idle by /pcs but has a session in /sessions/active (race)
    pc("PC-21", "PC 21", order=21),
    pc("VIP-1", "VIP 1", order=30),
]
SESSIONS = [{"pc_name": "PC-02", "status": "active"}, {"pc_name": "PC-07", "status": "grace_period"}]


def make_client(pcs=PCS, sessions=SESSIONS, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.url.path == "/pcs":
            return httpx.Response(200, json=pcs)
        if request.url.path == "/sessions/active":
            return httpx.Response(200, json=sessions)
        return httpx.Response(404, json={"detail": "Not Found"})

    return BackendClient("http://backend.test", TOKEN, transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


# --- number words -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spoken, expected",
    [
        ("pc one", "PC-01"), ("PC One", "PC-01"), ("PC 1", "PC-01"), ("pc-01", "PC-01"),
        ("pc three", "PC-03"), ("PC number three", "PC-03"), ("pc #3", "PC-03"),
        ("pc to", "PC-02"), ("PC too", "PC-02"), ("pc for", "PC-04"), ("pc won", "PC-01"),
        ("pc twenty one", "PC-21"), ("PC twenty-one", "PC-21"), ("pc twenty one.", "PC-21"),
        ("vip one", "VIP-1"), ("VIP won", "VIP-1"),
    ],
)
def test_pc_names_accept_number_words(spoken, expected):
    assert proposals._match_pc(PCS, spoken)["pc_name"] == expected


def test_every_number_word_one_to_twenty():
    words = ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
             "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]
    pcs = [pc(f"PC-{n:02d}") for n in range(1, 21)]
    for n, word in enumerate(words, start=1):
        assert proposals._match_pc(pcs, f"pc {word}")["pc_name"] == f"PC-{n:02d}", word


def test_number_words_do_not_overmatch():
    # "to"/"for" only count as numbers right after "PC"; other words are left alone.
    assert proposals._normalize_pc("message to pc") == "messagetopc"
    assert proposals._match_pc(PCS, "pc") is None
    assert proposals._match_pc(PCS, "pc fifty") is None


def test_number_words_work_in_other_proposals():
    result = run(proposals.propose_message_pc(make_client(), "pc three", "hi"))
    assert result["proposal"]["targets"] == ["PC-03"]


# --- shutdown idle PCs ------------------------------------------------------------------------


def test_shutdown_idle_only_and_never_forced():
    seen: list[httpx.Request] = []
    result = run(proposals.propose_shutdown_idle_pcs(make_client(seen=seen)))
    proposal = result["proposal"]
    assert proposal["kind"] == "shutdown_pcs"
    # In use, waking, offline, disabled and the /sessions/active race (PC-07) are all excluded.
    assert proposal["targets"] == ["PC-01", "PC-03", "PC-21", "VIP-1"]
    assert proposal["request"] == {
        "method": "POST",
        "path": "/pcs/{pc}/power/shutdown",
        "body": {"force": False},
        "each_pc": ["PC-01", "PC-03", "PC-21", "VIP-1"],
    }
    assert proposal["summary"] == "Shut down 4 idle PCs: PC 1, PC 3, PC 21, VIP 1?"
    assert proposal["details"]["admin_only"] is True
    assert [r.method for r in seen] == ["GET", "GET"]


def test_shutdown_named_pcs_explains_skips():
    result = run(proposals.propose_shutdown_idle_pcs(make_client(), pc_names=["pc one", "pc two", "PC 4", "pc seven"]))
    proposal = result["proposal"]
    assert proposal["targets"] == ["PC-01"]
    assert proposal["details"]["skipped"] == [
        {"pc": "PC 2", "reason": "in use"},
        {"pc": "PC 4", "reason": "offline"},
        {"pc": "PC 7", "reason": "in use"},
    ]
    assert proposal["summary"] == "Shut down 1 idle PC: PC 1?"


def test_shutdown_exclude():
    result = run(proposals.propose_shutdown_idle_pcs(make_client(), exclude=["pc one", "VIP one"]))
    assert result["proposal"]["targets"] == ["PC-03", "PC-21"]
    assert {"pc": "PC 1", "reason": "left out"} in result["proposal"]["details"]["skipped"]


def test_shutdown_nothing_idle():
    busy = [pc("PC-01", session_status="active", active_session_id="x"), pc("PC-02", online=False, cmd=False)]
    assert "no idle PCs" in run(proposals.propose_shutdown_idle_pcs(make_client(pcs=busy, sessions=[])))["error"]
    named = run(proposals.propose_shutdown_idle_pcs(make_client(pcs=busy, sessions=[]), pc_names=["pc one"]))
    assert named["error"] == "None of those PCs can be shut down right now: PC-01 (in use)."
    assert "no PC called" in run(proposals.propose_shutdown_idle_pcs(make_client(), pc_names=["pc ninety"]))["error"]


def test_shutdown_tool_registered_only_with_actions():
    tz = ZoneInfo("Asia/Manila")
    assert "propose_shutdown_idle_pcs" not in {t.name for t in run(build_server(make_client(), tz).list_tools())}
    tools = {t.name: t for t in run(build_server(make_client(), tz, enable_actions=True).list_tools())}
    description = tools["propose_shutdown_idle_pcs"].model_dump(by_alias=True)["description"]
    assert "ADMIN ONLY" in description and "never forces" in description
