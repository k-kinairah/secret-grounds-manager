"""Secret Grounds MCP server (phase 1, read-only).

Run:  python -m sg_assistant.server
Serves MCP over Streamable HTTP at http://127.0.0.1:<SG_MCP_PORT>/mcp.
"""

from typing import Annotated, Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from sg_assistant import tools
from sg_assistant.backend import BackendClient
from sg_assistant.config import MCP_HOST, get_settings, load_env_file

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

INSTRUCTIONS = (
    "Read-only assistant for Secret Grounds, a PC gaming cafe. Use these tools to answer staff "
    "questions about today's sales, sales over a date range, active PC sessions, which PCs are "
    "free or in use, member balances, and the current on-duty operator. Money is in Philippine "
    "pesos. Times are shop-local. These tools cannot change anything: you cannot start or end "
    "sessions, add time, or take payments."
)


def build_server(client: BackendClient, tz: ZoneInfo) -> MCPServer:
    mcp = MCPServer(name="secret-grounds", instructions=INSTRUCTIONS)

    @mcp.tool(annotations=READ_ONLY)
    async def get_today_overview() -> dict[str, Any]:
        """Snapshot of the shop right now. Use for "how are we doing today?", "how much have we made
        today?", "how many PCs are free?".

        Returns today's (shop-local) sales total, net sales and payment count, a breakdown by payment
        method (cash/gcash/maya/other) and by type (sessions/member purchases/other), the number of
        active sessions, and PC counts (total/online/offline/busy/free). Money values are peso strings.
        """
        return await tools.get_today_overview(client, tz)

    @mcp.tool(annotations=READ_ONLY)
    async def get_sales_report(
        start_date: Annotated[str, Field(description="First day to include, YYYY-MM-DD, shop-local.")],
        end_date: Annotated[
            str, Field(description="Last day to include (inclusive), YYYY-MM-DD, shop-local. Same as start_date for one day.")
        ],
    ) -> dict[str, Any]:
        """Sales for a date range of shop-local calendar days (inclusive). Use for "how much did we
        make yesterday / last week / in March?". For today only, prefer get_today_overview.

        Returns total, gross, refunds, net, payment count, average transaction, and breakdowns by
        payment method, by type, by operator, and by PC. Money values are peso strings. If the shop's
        sales figures were reset after the requested start, the range starts at the reset instead and
        the result includes `requested_start_date` and a `note` explaining it; tell the user the note.
        """
        return await tools.get_sales_report(client, tz, start_date, end_date)

    @mcp.tool(annotations=READ_ONLY)
    async def list_active_sessions() -> dict[str, Any]:
        """Every PC session currently running. Use for "who's playing?", "how much time is left on
        PC 5?", "how much does PC 3 owe?".

        For each session: PC, mode (prepaid / open_time / member_balance), status, member code if any,
        start time, time remaining and end time (prepaid/member) or elapsed time (open_time), amount
        still due, and payment status.
        """
        return await tools.list_active_sessions(client, tz)

    @mcp.tool(annotations=READ_ONLY)
    async def list_pcs() -> dict[str, Any]:
        """All registered PCs and their state. Use for "which PCs are free?", "is PC 7 online?",
        "are any PCs offline?".

        For each PC: name, VIP or not, online/offline, state (in_use / free / offline), session status,
        and time remaining if in use. Also returns online/in-use/free counts.
        """
        return await tools.list_pcs(client, tz)

    @mcp.tool(annotations=READ_ONLY)
    async def find_member(
        query: Annotated[
            str, Field(description="Part of the member's code, username or name (at least 2 characters).")
        ],
    ) -> dict[str, Any]:
        """Look up members by member code, username or name. Use for "how much time does Juan have
        left?", "is member SG-0012 still active?", "when does Maria's membership expire?".

        Returns up to 10 matches with: member code, username, name, status (active/inactive), standard
        time balance, VIP time balance, whether they are playing now, membership expiry, and a masked
        phone number. PINs are never returned.
        """
        return await tools.find_member(client, tz, query)

    @mcp.tool(annotations=READ_ONLY)
    async def get_current_shift() -> dict[str, Any]:
        """Who is on duty right now and what they have collected. Use for "who's on shift?", "how much
        cash should be in the drawer?".

        Returns the on-duty operator's name and role, when they came on duty, whether a cashier shift is
        open (and when it opened, if visible to the assistant account), and running totals since they
        came on duty: total, cash, gcash, maya, other, voided, payment count.
        """
        return await tools.get_current_shift(client, tz)

    return mcp


def main() -> None:
    load_env_file()
    settings = get_settings()
    if not settings.token:
        print("WARNING: SG_ASSISTANT_TOKEN is not set; every tool will return a configuration error.")
    client = BackendClient(settings.backend_url, settings.token, settings.timeout_seconds)
    server = build_server(client, ZoneInfo(settings.shop_timezone))
    print(
        f"Secret Grounds MCP server on http://{MCP_HOST}:{settings.mcp_port}/mcp "
        f"(backend {settings.backend_url}, timezone {settings.shop_timezone})"
    )
    server.run(transport="streamable-http", host=MCP_HOST, port=settings.mcp_port)


if __name__ == "__main__":
    main()
