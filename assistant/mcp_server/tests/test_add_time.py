"""propose_add_time (phase 3 step b): billed-later add time, never free time."""

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from sg_assistant import proposals
from sg_assistant.backend import BackendClient
from sg_assistant.server import build_server

TOKEN = "test-token"
TZ = ZoneInfo("Asia/Manila")
NOW = datetime.now(timezone.utc)

PCS = [
    {"pc_name": "PC-01", "display_name": "PC 1", "connection_status": "online", "has_command_connection": True, "is_vip": False},
    {"pc_name": "PC-02", "display_name": "PC 2", "connection_status": "online", "has_command_connection": True, "is_vip": False},
    {"pc_name": "VIP-1", "display_name": "VIP 1", "connection_status": "online", "has_command_connection": True, "is_vip": True},
    {"pc_name": "PC-04", "display_name": "PC 4", "connection_status": "offline", "has_command_connection": False, "is_vip": False},
    {"pc_name": "PC-05", "display_name": "PC 5", "connection_status": "online", "has_command_connection": True, "is_vip": False},
    {"pc_name": "PC-06", "display_name": "PC 6", "connection_status": "online", "has_command_connection": True, "is_vip": False},
]
SESSIONS = [
    {"session_id": "sess-1", "pc_name": "PC-01", "status": "active", "session_mode": "prepaid",
     "scheduled_end_at": (NOW + timedelta(minutes=10)).isoformat()},
    {"session_id": "sess-vip", "pc_name": "VIP-1", "status": "active", "session_mode": "prepaid",
     "scheduled_end_at": (NOW + timedelta(minutes=10)).isoformat()},
    {"session_id": "sess-4", "pc_name": "PC-04", "status": "active", "session_mode": "prepaid",
     "scheduled_end_at": (NOW + timedelta(minutes=10)).isoformat()},
    {"session_id": "sess-5", "pc_name": "PC-05", "status": "active", "session_mode": "open_time",
     "scheduled_end_at": (NOW + timedelta(days=1)).isoformat()},
    {"session_id": "sess-6", "pc_name": "PC-06", "status": "active", "session_mode": "member_balance",
     "scheduled_end_at": (NOW + timedelta(minutes=10)).isoformat()},
]
RATES = [
    {"rate_id": "r-1h", "name": "1 Hour", "duration_seconds": 3600, "price": "20.00", "rate_type": "package",
     "is_active": True, "station_scope": "standard"},
    {"rate_id": "r-3h", "name": "3 Hours", "duration_seconds": 10800, "price": "50.00", "rate_type": "package",
     "is_active": True, "station_scope": "any"},
    {"rate_id": "r-vip1h", "name": "VIP 1 Hour", "duration_seconds": 3600, "price": "35.00", "rate_type": "package",
     "is_active": True, "station_scope": "vip"},
    {"rate_id": "r-old", "name": "30 Minutes", "duration_seconds": 1800, "price": "10.00", "rate_type": "package",
     "is_active": False, "station_scope": "standard"},
    {"rate_id": "r-meter", "name": "Metered", "duration_seconds": 3600, "price": "20.00", "rate_type": "metered",
     "is_active": True, "station_scope": "any"},
]


def make_client(seen=None, metered=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "Authentication required"})
        path = request.url.path
        if path == "/pcs":
            return httpx.Response(200, json=PCS)
        if path == "/sessions/active":
            return httpx.Response(200, json=SESSIONS)
        if path == "/rates":
            return httpx.Response(200, json=RATES)
        if path == "/rates/metered":
            vip = request.url.params.get("pc_name") == "VIP-1"
            rate = metered or {"name": "Metered", "duration_seconds": 3600, "price": "30.00" if vip else "20.00"}
            return httpx.Response(200, json=rate)
        return httpx.Response(404, json={"detail": "Not Found"})

    return BackendClient("http://backend.test", TOKEN, transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


def propose(client=None, **kwargs):
    return run(proposals.propose_add_time(client or make_client(), TZ, **kwargs))


def test_package_by_minutes():
    seen: list[httpx.Request] = []
    result = propose(make_client(seen), pc_name="pc 1", minutes=60)
    proposal = result["proposal"]
    assert proposal["kind"] == "add_time"
    assert proposal["request"] == {"method": "POST", "path": "/sessions/sess-1/add-time", "body": {"rate_id": "r-1h"}}
    assert proposal["summary"] == "Add 1 hour to PC 1 (the 1 Hour package, ₱20.00 added to their bill)?"
    assert proposal["targets"] == ["PC-01"]
    assert proposal["details"]["price"] == "20.00" and proposal["details"]["billed"] == "later"
    assert proposal["details"]["session_id"] == "sess-1"
    assert all(r.method == "GET" for r in seen)


def test_package_by_name_and_scope():
    assert propose(pc_name="PC 1", package="3 hours")["proposal"]["request"]["body"] == {"rate_id": "r-3h"}
    # A VIP PC only gets VIP/any packages: the 60-minute match is the VIP one.
    vip = propose(pc_name="VIP 1", minutes=60)["proposal"]
    assert vip["request"]["body"] == {"rate_id": "r-vip1h"}
    assert "₱35.00" in vip["summary"]
    # Inactive and wrong-scope packages are never offered.
    error = propose(pc_name="PC 1", minutes=30)["error"]
    assert error.startswith("There's no 30-minute package for PC 1.")
    assert "1 Hour (₱20.00)" in error and "30 Minutes" not in error and "VIP 1 Hour" not in error


def test_amount_uses_metered_rate_for_that_pc():
    standard = propose(pc_name="PC 1", amount_pesos=50)["proposal"]
    assert standard["request"]["body"] == {"amount": "50.00"}
    assert standard["details"]["added_seconds"] == 9000  # 50 / 20 * 3600
    assert standard["summary"] == "Add 2 hours 30 minutes to PC 1 (₱50.00 of time, ₱50.00 added to their bill)?"
    vip = propose(pc_name="VIP 1", amount_pesos="30")["proposal"]
    assert vip["details"]["added_seconds"] == 3600  # VIP metered price 30/hour


def test_never_proposes_free_seconds():
    for kwargs in ({"pc_name": "PC 1"}, {"pc_name": "PC 1", "minutes": 60, "amount_pesos": 20}):
        assert "exactly one way" in propose(**kwargs)["error"]
    for result in (
        propose(pc_name="PC 1", minutes=60),
        propose(pc_name="PC 1", amount_pesos=20),
        propose(pc_name="PC 1", package="1 Hour"),
    ):
        body = result["proposal"]["request"]["body"]
        assert "seconds" not in body and len(body) == 1


def test_session_and_pc_checks():
    assert "has no running session" in propose(pc_name="PC 2", minutes=60)["error"]
    assert "open time" in propose(pc_name="PC 5", minutes=60)["error"]
    assert "member session" in propose(pc_name="PC 6", minutes=60)["error"]
    assert "offline" in propose(pc_name="PC 4", minutes=60)["error"]
    assert "no PC called" in propose(pc_name="PC 99", minutes=60)["error"]


def test_amount_limits():
    assert "between" in propose(pc_name="PC 1", amount_pesos=0)["error"]
    assert "between" in propose(pc_name="PC 1", amount_pesos=2500)["error"]
    assert "less than a minute" in propose(pc_name="PC 1", amount_pesos=0.1)["error"]
    assert "metered" in propose(
        make_client(metered={"name": "x", "duration_seconds": 0, "price": "0"}), pc_name="PC 1", amount_pesos=20
    )["error"]


def test_backend_down():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = BackendClient("http://backend.test", TOKEN, transport=httpx.MockTransport(refuse))
    assert "Could not reach" in propose(client, pc_name="PC 1", minutes=60)["error"]


def test_no_matching_package_lists_only_real_packages():
    error = propose(pc_name="PC 1", minutes=30)["error"]
    assert error == (
        "There's no 30-minute package for PC 1. The packages are: 1 Hour (₱20.00), 3 Hours (₱50.00); "
        "or tell me a peso amount."
    )
    # The only peso figures in the message are real package prices -- nothing
    # derived from the request (e.g. "30 pesos") the model could repeat.
    import re

    assert re.findall(r"₱[\d.,]+", error) == ["₱20.00", "₱50.00"]
    assert "30 pesos" not in error and "₱30" not in error


def test_add_time_tool_description_forbids_suggesting_amounts():
    tools = {t.name: t for t in run(build_server(make_client(), TZ, enable_actions=True).list_tools())}
    description = " ".join(tools["propose_add_time"].model_dump(by_alias=True)["description"].split())
    assert "Never suggest a peso amount or a package yourself" in description
