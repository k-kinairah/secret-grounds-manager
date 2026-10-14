"""Tool tests with the backend mocked by httpx.MockTransport (no network,
no Secret Grounds server, no database)."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from sg_assistant import tools
from sg_assistant.backend import BackendClient
from sg_assistant.server import build_server

TZ = ZoneInfo("Asia/Manila")
TOKEN = "test-token"


def iso(dt: datetime) -> str:
    return dt.isoformat()


NOW = datetime.now(timezone.utc)

SALES_REPORT = {
    "period": "today",
    "start_date": "2026-10-13",
    "end_date": "2026-10-13",
    "summary": {
        "total_sales": "1500.00",
        "gross_sales": "1550.00",
        "refund_total": "50.00",
        "net_sales": "1500.00",
        "cash_total": "1000.00",
        "gcash_total": "400.00",
        "maya_total": "100.00",
        "other_total": "0.00",
        "payment_count": 12,
        "outstanding_amount": "30.00",
        "session_revenue": "1200.00",
        "member_purchase_revenue": "300.00",
        "other_revenue": "0.00",
        "average_transaction_value": "125.00",
    },
    "operators": [{"operator_id": "u1", "operator_name": "Ana", "total_amount": "1500.00", "payment_count": 12}],
    "pcs": [{"pc_name": "PC-01", "total_amount": "200.00", "payment_count": 2, "paid_session_count": 2}],
}

ACTIVE_SESSIONS = [
    {
        "session_id": "s1",
        "pc_name": "PC-02",
        "status": "active",
        "session_mode": "prepaid",
        "started_at": iso(NOW - timedelta(minutes=30)),
        "scheduled_end_at": iso(NOW + timedelta(minutes=90, seconds=30)),
        "balance_due": "0.00",
        "payment_status": "paid",
        "member_code_snapshot": None,
    },
    {
        "session_id": "s2",
        "pc_name": "PC-01",
        "status": "active",
        "session_mode": "open_time",
        "started_at": iso(NOW - timedelta(hours=2, minutes=5)),
        "scheduled_end_at": iso(NOW + timedelta(days=1)),
        "balance_due": "0.00",
        "estimated_balance_due": "70.00",
        "payment_status": "unpaid",
    },
]

PCS = [
    {"pc_name": "PC-01", "display_name": "PC 1", "layout_order": 1, "connection_status": "online",
     "session_status": "active", "active_session_id": "s2", "remaining_seconds": None, "is_vip": False},
    {"pc_name": "PC-02", "display_name": "PC 2", "layout_order": 2, "connection_status": "online",
     "session_status": "active", "active_session_id": "s1", "remaining_seconds": 5430, "is_vip": False},
    {"pc_name": "PC-03", "display_name": "PC 3", "layout_order": 3, "connection_status": "online",
     "session_status": "idle", "active_session_id": None, "is_vip": True},
    {"pc_name": "PC-04", "display_name": None, "layout_order": 4, "connection_status": "offline",
     "session_status": "idle", "active_session_id": None, "is_vip": False},
]

MEMBERS = [
    {
        "member_id": "m1",
        "member_code": "SG-0012",
        "username": "juan",
        "display_name": None,
        "full_name": "Juan Dela Cruz",
        "phone_number": "09171234567",
        "pin_hash": "$argon2id$should-never-leak",
        "has_pin": True,
        "status": "active",
        "time_balance_seconds": 7200,
        "vip_balance_seconds": 1800,
        "live_time_balance_seconds": 5400,
        "live_vip_balance_seconds": None,
        "in_session": True,
        "expires_at": "2026-12-31T16:00:00+00:00",
    }
]


def make_client(routes: dict[str, object], seen: list[httpx.Request] | None = None) -> BackendClient:
    """routes maps a path to a JSON body, or to an int status code."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "Authentication required"})
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        if isinstance(body, int):
            return httpx.Response(body, json={"detail": "boom"})
        return httpx.Response(200, json=body)

    return BackendClient("http://backend.test", TOKEN, transport=httpx.MockTransport(handler))


def run(coro):
    return asyncio.run(coro)


# --- get_today_overview ------------------------------------------------------


def test_today_overview_combines_sales_sessions_and_pcs():
    seen: list[httpx.Request] = []
    client = make_client(
        {"/sales/report": SALES_REPORT, "/sessions/active": ACTIVE_SESSIONS, "/pcs": PCS}, seen
    )
    result = run(tools.get_today_overview(client, TZ))
    assert result["sales"]["total_sales"] == "1500.00"
    assert result["sales"]["by_payment_method"] == {"cash": "1000.00", "gcash": "400.00", "maya": "100.00", "other": "0.00"}
    assert result["active_sessions"] == 2
    assert result["pcs"] == {"total": 4, "online": 3, "offline": 1, "busy": 2, "free": 1}
    # Only GET, always with the bearer token.
    assert all(r.method == "GET" for r in seen)
    assert seen[0].url.params["period"] == "today"


# --- get_sales_report --------------------------------------------------------


def test_sales_report_passes_custom_range():
    seen: list[httpx.Request] = []
    client = make_client({"/sales/report": SALES_REPORT}, seen)
    result = run(tools.get_sales_report(client, TZ, "2026-10-01", "2026-10-07"))
    params = seen[0].url.params
    assert params["period"] == "custom"
    assert params["start_date"] == "2026-10-01"
    assert params["end_date"] == "2026-10-07"
    assert result["summary"]["net_sales"] == "1500.00"
    assert result["by_operator"] == [{"operator": "Ana", "total": "1500.00", "payments": 12}]
    assert result["by_pc"] == [{"pc": "PC-01", "total": "200.00", "paid_sessions": 2}]


def test_sales_report_without_reset_has_no_note():
    # start_at is exactly local midnight of the requested start (Manila is UTC+8).
    report = dict(SALES_REPORT, start_date="2026-10-01", start_at="2026-09-30T16:00:00+00:00")
    client = make_client({"/sales/report": report})
    result = run(tools.get_sales_report(client, TZ, "2026-10-01", "2026-10-07"))
    assert "note" not in result
    assert "requested_start_date" not in result


def test_sales_report_explains_sales_epoch_clamp():
    # Owner reset sales at 2026-10-05 14:30 Manila (06:30 UTC); backend moved the start there.
    report = dict(SALES_REPORT, start_date="2026-10-05", start_at="2026-10-05T06:30:00+00:00")
    client = make_client({"/sales/report": report})
    result = run(tools.get_sales_report(client, TZ, "2026-10-01", "2026-10-07"))
    assert result["start_date"] == "2026-10-05"
    assert result["requested_start_date"] == "2026-10-01"
    assert result["note"] == (
        "Sales figures were reset on October 5, 2026 at 2:30 PM; earlier sales are not included."
    )


def test_sales_report_detects_reset_within_the_first_day():
    # start_date did not move, but the reset was mid-day, so the morning is excluded.
    report = dict(SALES_REPORT, start_date="2026-10-05", start_at="2026-10-05T01:05:00+00:00")
    client = make_client({"/sales/report": report})
    result = run(tools.get_sales_report(client, TZ, "2026-10-05", "2026-10-05"))
    assert result["requested_start_date"] == "2026-10-05"
    assert "October 5, 2026 at 9:05 AM" in result["note"]


def test_sales_report_rejects_bad_dates_without_calling_backend():
    seen: list[httpx.Request] = []
    client = make_client({"/sales/report": SALES_REPORT}, seen)
    assert "error" in run(tools.get_sales_report(client, TZ, "10/01/2026", "2026-10-07"))
    assert "error" in run(tools.get_sales_report(client, TZ, "2026-02-30", "2026-03-01"))
    assert "error" in run(tools.get_sales_report(client, TZ, "2026-10-07", "2026-10-01"))
    assert seen == []


# --- list_active_sessions ----------------------------------------------------


def test_active_sessions_remaining_and_amount_due():
    client = make_client({"/sessions/active": ACTIVE_SESSIONS})
    result = run(tools.list_active_sessions(client, TZ))
    assert result["count"] == 2
    by_pc = {s["pc"]: s for s in result["sessions"]}
    prepaid = by_pc["PC-02"]
    assert prepaid["mode"] == "prepaid"
    assert prepaid["time_remaining"] in {"1h 30m", "1h 29m"}
    assert prepaid["amount_due"] == "0.00"
    assert prepaid["ends_at"].endswith("+08:00")  # shop-local
    open_time = by_pc["PC-01"]
    assert open_time["time_remaining"] is None
    assert open_time["elapsed"] in {"2h 05m", "2h 04m"}
    assert open_time["amount_due"] == "70.00"


# --- list_pcs ----------------------------------------------------------------


def test_list_pcs_states():
    client = make_client({"/pcs": PCS})
    result = run(tools.list_pcs(client, TZ))
    states = {pc["pc_name"]: pc["state"] for pc in result["pcs"]}
    assert states == {"PC-01": "in_use", "PC-02": "in_use", "PC-03": "free", "PC-04": "offline"}
    assert result["free"] == 1 and result["in_use"] == 2 and result["online"] == 3
    pc4 = next(pc for pc in result["pcs"] if pc["pc_name"] == "PC-04")
    assert pc4["name"] == "PC-04" and pc4["online"] is False


# --- find_member -------------------------------------------------------------


def test_find_member_masks_phone_and_never_returns_pin():
    seen: list[httpx.Request] = []
    client = make_client({"/members": MEMBERS}, seen)
    result = run(tools.find_member(client, TZ, " juan "))
    assert seen[0].url.params["q"] == "juan"
    member = result["members"][0]
    assert member["member_code"] == "SG-0012"
    assert member["phone"] == "********567"
    assert member["time_balance_seconds"] == 5400  # live balance preferred
    assert member["time_balance"] == "1h 30m"
    assert member["vip_balance_seconds"] == 1800  # falls back to stored
    text = json.dumps(result)
    assert "pin" not in text.lower()
    assert "09171234567" not in text
    assert "argon2" not in text


def test_find_member_requires_a_real_query():
    seen: list[httpx.Request] = []
    client = make_client({"/members": MEMBERS}, seen)
    assert "error" in run(tools.find_member(client, TZ, " a "))
    assert seen == []


def test_mask_phone():
    assert tools.mask_phone(None) is None
    assert tools.mask_phone("") is None
    assert tools.mask_phone("12") == "**"
    assert tools.mask_phone("+63 917 123 4567") == "*********567"


# --- get_current_shift -------------------------------------------------------


def test_current_shift_with_operator_and_totals():
    client = make_client(
        {
            "/operators/current": {
                "operator": {"user_id": "u1", "username": "ana", "display_name": "Ana", "role": "cashier"},
                "selected_at": "2026-10-13T00:00:00+00:00",
            },
            "/dashboard/operator-sale": {
                "has_open_shift": True,
                "shift_id": "sh1",
                "operator_sale": "900.00",
                "cash_payments": "600.00",
                "gcash_payments": "300.00",
                "maya_payments": "0.00",
                "other_payments": "0.00",
                "voided_payments": "0.00",
                "payment_count": 7,
            },
            "/shifts": [{"shift_id": "sh1", "opened_at": "2026-10-12T23:55:00+00:00"}],
        }
    )
    result = run(tools.get_current_shift(client, TZ))
    assert result["on_duty"] == "Ana"
    assert result["on_duty_since"] == "2026-10-13T08:00+08:00"
    assert result["shift_opened_at"] == "2026-10-13T07:55+08:00"
    assert result["running_totals"]["cash"] == "600.00"
    assert result["running_totals"]["payment_count"] == 7


def test_current_shift_nobody_on_duty():
    client = make_client({"/operators/current": {"operator": None}, "/dashboard/operator-sale": {"has_open_shift": False}})
    result = run(tools.get_current_shift(client, TZ))
    assert result["on_duty"] is None
    assert "Nobody" in result["message"]


def test_current_shift_survives_shift_list_failure():
    client = make_client(
        {
            "/operators/current": {"operator": {"display_name": "Ana", "role": "cashier"}},
            "/dashboard/operator-sale": {"has_open_shift": True, "shift_id": "sh1", "operator_sale": "1.00"},
            "/shifts": 500,
        }
    )
    result = run(tools.get_current_shift(client, TZ))
    assert result["on_duty"] == "Ana"
    assert result["shift_opened_at"] is None


# --- error handling ----------------------------------------------------------


def test_backend_down_returns_readable_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = BackendClient("http://backend.test", TOKEN, transport=httpx.MockTransport(handler))
    for coro in (
        tools.get_today_overview(client, TZ),
        tools.list_pcs(client, TZ),
        tools.list_active_sessions(client, TZ),
        tools.find_member(client, TZ, "juan"),
        tools.get_current_shift(client, TZ),
        tools.get_sales_report(client, TZ, "2026-10-01", "2026-10-01"),
    ):
        result = run(coro)
        assert "Could not reach" in result["error"]


def test_wrong_token_returns_401_message():
    client = make_client({"/pcs": PCS})
    bad = BackendClient("http://backend.test", "wrong", transport=client._transport)
    result = run(tools.list_pcs(bad, TZ))
    assert "401" in result["error"]


def test_missing_token_is_reported_without_calling_backend():
    seen: list[httpx.Request] = []
    client = make_client({"/pcs": PCS}, seen)
    unconfigured = BackendClient("http://backend.test", "", transport=client._transport)
    result = run(tools.list_pcs(unconfigured, TZ))
    assert "SG_ASSISTANT_TOKEN" in result["error"]
    assert seen == []


def test_backend_500_and_403_are_readable():
    client = make_client({"/pcs": 500, "/members": 403})
    assert "500" in run(tools.list_pcs(client, TZ))["error"]
    assert "403" in run(tools.find_member(client, TZ, "juan"))["error"]


# --- MCP wiring ----------------------------------------------------------------


def test_server_registers_six_read_only_tools_and_calls_through():
    client = make_client({"/pcs": PCS})
    server = build_server(client, TZ)
    listed = run(server.list_tools())
    names = sorted(t.name for t in listed)
    assert names == sorted(
        ["get_today_overview", "get_sales_report", "list_active_sessions", "list_pcs", "find_member", "get_current_shift"]
    )
    for tool in listed:
        dumped = tool.model_dump(by_alias=True, exclude_none=True)
        assert dumped["annotations"]["readOnlyHint"] is True
        assert dumped["description"]
    result = run(server.call_tool("list_pcs", {}))
    assert result.structured_content["count"] == 4
