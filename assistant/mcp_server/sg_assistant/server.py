"""Secret Grounds MCP server (phase 1, read-only).

Run:  python -m sg_assistant.server
Serves MCP over Streamable HTTP at http://127.0.0.1:<SG_MCP_PORT>/mcp.
"""

from typing import Annotated, Any
from zoneinfo import ZoneInfo

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from sg_assistant import proposals, tools
from sg_assistant.backend import BackendClient
from sg_assistant.config import MCP_HOST, get_settings, load_env_file

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

INSTRUCTIONS = (
    "Read-only assistant for Secret Grounds, a PC gaming cafe. Use these tools to answer staff "
    "questions about today's sales, sales over a date range, active PC sessions, which PCs are "
    "free or in use, member balances, and the current on-duty operator. Money is in Philippine "
    "pesos. Times are shop-local. These tools cannot change anything by themselves. The propose_* "
    "tools only PREPARE an action (such as a message to a PC); a staff member must confirm it on "
    "screen before anything happens. You cannot start or end sessions or take payments."
)

PROPOSE = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def build_server(client: BackendClient, tz: ZoneInfo, enable_actions: bool = False) -> MCPServer:
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
        When answering out loud: give the count, then the few sessions that matter (ending soon or
        owing money). Sessions stuck in "settlement_pending" for days are leftovers waiting to be
        settled; mention them as one count ("plus four old unsettled sessions"), don't read each.
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
        If nothing matches exactly (speech-to-text often misspells names), `members` is empty and
        `did_you_mean` lists up to 3 close names by spelling and sound. Then ask "Did you mean
        <name>?" and only look the member up again after the user confirms; a guess is never the
        member.
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

    if not enable_actions:
        return mcp

    # Phase 3, opt-in (SG_ASSISTANT_ACTIONS=true): proposals only, never execution.
    @mcp.tool(annotations=PROPOSE)
    async def propose_message_pc(
        pc_name: Annotated[str, Field(description="The PC to message, as the user said it, e.g. 'PC 3' or 'PC-03'.")],
        message: Annotated[str, Field(description="The text to show on that PC's screen (max 500 characters).")],
        severity: Annotated[
            str, Field(description="'info' (default), 'warning' or 'urgent'. Only use urgent if the user asks.")
        ] = "info",
    ) -> dict[str, Any]:
        """PREPARE a pop-up message on ONE customer PC's screen. Use for "tell PC 3 their friend is
        here", "message PC 5 to keep it down".

        This does NOT send anything. It checks the PC exists and is online, then returns a
        `proposal` that the staff member must confirm on screen. Afterwards, tell the user in one
        short sentence what you prepared and ask them to confirm; never say it was sent.
        Returns {"error": ...} if the PC is unknown or offline.
        """
        return await proposals.propose_message_pc(client, pc_name, message, severity)

    @mcp.tool(annotations=PROPOSE)
    async def propose_broadcast(
        message: Annotated[str, Field(description="The text to show on the PCs' screens (max 500 characters).")],
        pc_names: Annotated[
            list[str] | None,
            Field(description="Only these PCs (as the user said them). Leave empty for every online PC."),
        ] = None,
        severity: Annotated[
            str, Field(description="'info' (default), 'warning' or 'urgent'. Only use urgent if the user asks.")
        ] = "info",
    ) -> dict[str, Any]:
        """PREPARE an announcement on several or all customer PCs. Use for "tell everyone we close
        in 15 minutes", "announce the promo to all PCs".

        This does NOT send anything. It works out which PCs are online (offline ones are listed as
        skipped) and returns a `proposal` that the staff member must confirm on screen. Afterwards,
        tell the user in one short sentence what you prepared and ask them to confirm; never say it
        was sent. Returns {"error": ...} if no target PC is online.
        """
        return await proposals.propose_broadcast(client, message, pc_names, severity)

    @mcp.tool(annotations=PROPOSE)
    async def propose_add_time(
        pc_name: Annotated[str, Field(description="The PC whose session gets more time, e.g. 'PC 3'.")],
        package: Annotated[
            str | None, Field(description="A package name the user said, e.g. '1 Hour'. Use only one of package/minutes/amount_pesos.")
        ] = None,
        minutes: Annotated[
            int | None, Field(description="Minutes to add; must match a package's length exactly (e.g. 60).")
        ] = None,
        amount_pesos: Annotated[
            float | None, Field(description="Pesos' worth of time to add (converted at the per-minute rate), e.g. 50.")
        ] = None,
    ) -> dict[str, Any]:
        """PREPARE adding time to a running PREPAID session, billed later: the price is added to
        that session's bill and paid afterwards. Use for "add an hour to PC 3", "give PC 5 fifty
        pesos more".

        Give exactly ONE of: package (name), minutes (must match a package length), or amount_pesos.
        This does NOT add anything. It checks the PC has a running prepaid session, works out the
        time and price, and returns a `proposal` the staff member must confirm on screen. Then tell
        the user in one short sentence what you prepared, including the price, and ask them to
        confirm. Never say the time was added. Returns {"error": ...} (for example listing the
        available packages) if it can't prepare it; read that error to the user as it is. Never
        suggest a peso amount or a package yourself; the user picks.
        Open-time and member-balance sessions can't get time this way.
        """
        return await proposals.propose_add_time(client, tz, pc_name, package, minutes, amount_pesos)

    @mcp.tool(annotations=PROPOSE)
    async def propose_shutdown_idle_pcs(
        pc_names: Annotated[
            list[str] | None,
            Field(description="Only consider these PCs (as the user said them). Leave empty for every idle PC."),
        ] = None,
        exclude: Annotated[
            list[str] | None, Field(description="PCs to leave on, e.g. ['PC 1'] for 'all except PC 1'.")
        ] = None,
    ) -> dict[str, Any]:
        """PREPARE shutting down IDLE PCs: online PCs with no session and nothing starting. Use for
        "shut down the idle PCs", "turn off the free computers except PC 2". ADMIN ONLY: only the
        protected Admin account can run it; anyone else's confirmation is refused by the shop system.

        This does NOT shut anything down. It lists which PCs are idle right now and returns a
        `proposal` the Admin must confirm on screen. It never forces: a PC that starts a session
        before confirmation is skipped. Afterwards say in one short sentence how many PCs (and
        which) are ready to shut down and ask them to confirm. Never say they were shut down.
        Returns {"error": ...} if no PC is idle.
        """
        return await proposals.propose_shutdown_idle_pcs(client, pc_names, exclude)

    return mcp


def main() -> None:
    load_env_file()
    settings = get_settings()
    if not settings.token:
        print("WARNING: SG_ASSISTANT_TOKEN is not set; every tool will return a configuration error.")
    client = BackendClient(settings.backend_url, settings.token, settings.timeout_seconds)
    server = build_server(client, ZoneInfo(settings.shop_timezone), enable_actions=settings.enable_actions)
    print(
        f"Secret Grounds MCP server on http://{MCP_HOST}:{settings.mcp_port}/mcp "
        f"(backend {settings.backend_url}, timezone {settings.shop_timezone})"
    )
    server.run(transport="streamable-http", host=MCP_HOST, port=settings.mcp_port)


if __name__ == "__main__":
    main()
