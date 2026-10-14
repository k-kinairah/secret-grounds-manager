"""Test doubles: a scripted model and an in-memory fake MCP server.

No real model, no network: the real Strands MCPClient talks to a fake MCP
server through mcp's in-memory transport, and the model is a script.
"""

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.server.mcpserver import MCPServer
from strands.models import Model
from strands.tools.mcp import MCPClient

from sg_agent.service import ToolSource

TODAY_OVERVIEW = {
    "date": "2026-10-14",
    "sales": {"total_sales": "1250.00", "payment_count": 9},
    "active_sessions": 3,
    "pcs": {"total": 10, "online": 8, "offline": 2, "busy": 3, "free": 5},
}
SALES_REPORT_WITH_NOTE = {
    "start_date": "2026-10-05",
    "end_date": "2026-10-07",
    "requested_start_date": "2026-10-01",
    "note": "Sales figures were reset on October 5, 2026 at 2:30 PM; earlier sales are not included.",
    "summary": {"total_sales": "800.00"},
}


def build_fake_mcp_server(calls: list[tuple[str, dict[str, Any]]]) -> MCPServer:
    server = MCPServer(name="fake-secret-grounds")

    @server.tool()
    async def get_today_overview() -> dict[str, Any]:
        """Today's sales and PC counts."""
        calls.append(("get_today_overview", {}))
        return TODAY_OVERVIEW

    @server.tool()
    async def get_sales_report(start_date: str, end_date: str) -> dict[str, Any]:
        """Sales for a date range."""
        calls.append(("get_sales_report", {"start_date": start_date, "end_date": end_date}))
        return SALES_REPORT_WITH_NOTE

    @server.tool()
    async def list_pcs() -> dict[str, Any]:
        """All PCs."""
        calls.append(("list_pcs", {}))
        return {"error": "Could not reach the Secret Grounds backend at http://127.0.0.1:8000. Is it running?"}

    return server


class ScriptedModel(Model):
    """Replays a fixed list of model turns. A turn is either
    {"text": "..."} or {"tool": name, "input": {...}}."""

    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = list(turns)
        self.requests: list[dict[str, Any]] = []
        self.config: dict[str, Any] = {"model_id": "scripted"}

    def update_config(self, **model_config: Any) -> None:
        self.config.update(model_config)

    def get_config(self) -> dict[str, Any]:
        return self.config

    async def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):  # pragma: no cover
        raise NotImplementedError
        yield  # noqa

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs) -> AsyncIterator[dict]:
        self.requests.append(
            {
                "messages": json.loads(json.dumps(messages, default=str)),
                "system_prompt": system_prompt,
                "tool_names": sorted(spec["name"] for spec in (tool_specs or [])),
            }
        )
        if not self.turns:
            raise AssertionError("ScriptedModel ran out of turns")
        turn = self.turns.pop(0)
        if "raise" in turn:
            raise turn["raise"]
        yield {"messageStart": {"role": "assistant"}}
        if "tool" in turn:
            tool_use_id = f"tool-{len(self.requests)}"
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tool_use_id, "name": turn["tool"]}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(turn.get("input", {}))}}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockDelta": {"delta": {"text": turn["text"]}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
        yield {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}, "metrics": {"latencyMs": 1}}}


@pytest.fixture
def mcp_calls() -> list[tuple[str, dict[str, Any]]]:
    return []


@pytest.fixture
def tool_source(mcp_calls):
    server = build_fake_mcp_server(mcp_calls)
    source = ToolSource(lambda: MCPClient(lambda: InMemoryTransport(server)))
    yield source
    source.close()
