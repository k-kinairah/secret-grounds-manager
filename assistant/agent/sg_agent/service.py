"""Chat service: one Strands agent call per message, with capped per-session memory.

Memory is kept as plain user/assistant text turns (last N per session). Each
request builds a fresh Agent from that history, so old tool results are never
re-sent to the model, the cap is exact, and concurrent requests never share
an Agent instance.
"""

import asyncio
import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from strands import Agent
from strands.agent.conversation_manager import NullConversationManager
from strands.models import Model
from strands.tools.mcp import MCPClient

from sg_agent.prompt import SYSTEM_PROMPT

SHOP_TZ = ZoneInfo("Asia/Manila")

MCP_DOWN_REPLY = "Sorry, I can't reach the shop system right now. Please try again in a moment."
MODEL_DOWN_REPLY = "Sorry, I can't reach the AI model right now, so I can't answer that."
FAILED_REPLY = "Sorry, something went wrong while I was answering that."


@dataclass
class ChatResult:
    reply: str
    tools_used: list[str] = field(default_factory=list)
    error: str | None = None


class ToolSource:
    """Owns the MCP client connection and the tool list taken from it.

    Connects lazily on first use and reconnects after a failure, so the agent
    service can start before the MCP server and survive it restarting."""

    def __init__(self, client_factory: Callable[[], MCPClient]) -> None:
        self._client_factory = client_factory
        self._client: MCPClient | None = None
        self._tools: list[Any] | None = None
        self._lock = threading.Lock()

    def get_tools(self) -> list[Any]:
        with self._lock:
            if self._tools is None:
                client = self._client_factory()
                try:
                    client.start()
                    self._tools = list(client.list_tools_sync())
                except BaseException:
                    _stop_quietly(client)
                    raise
                self._client = client
            return self._tools

    def reset(self) -> None:
        with self._lock:
            if self._client is not None:
                _stop_quietly(self._client)
            self._client = None
            self._tools = None

    def close(self) -> None:
        self.reset()


def _stop_quietly(client: MCPClient) -> None:
    try:
        client.stop(None, None, None)
    except Exception:
        pass


@dataclass
class _Session:
    turns: list[tuple[str, str]] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class ChatService:
    def __init__(
        self,
        model_factory: Callable[[], Model],
        tool_source: ToolSource,
        *,
        max_turns: int = 20,
        max_sessions: int = 200,
        now: Callable[[], datetime] = lambda: datetime.now(SHOP_TZ),
    ) -> None:
        self._model_factory = model_factory
        self._model: Model | None = None
        self._tool_source = tool_source
        self.max_turns = max_turns
        self.max_sessions = max_sessions
        self._now = now
        self._sessions: OrderedDict[str, _Session] = OrderedDict()

    # --- sessions ---------------------------------------------------------

    def _session(self, session_id: str) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            session = _Session()
            self._sessions[session_id] = session
            while len(self._sessions) > self.max_sessions:
                self._sessions.popitem(last=False)  # forget the least recently used
        self._sessions.move_to_end(session_id)
        return session

    def reset(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def close(self) -> None:
        self._tool_source.close()

    def history(self, session_id: str) -> list[tuple[str, str]]:
        session = self._sessions.get(session_id)
        return list(session.turns) if session else []

    # --- chat -------------------------------------------------------------

    async def chat(self, session_id: str, message: str) -> ChatResult:
        session = self._session(session_id)
        async with session.lock:
            try:
                tools = await asyncio.to_thread(self._tool_source.get_tools)
            except Exception as error:
                self._tool_source.reset()
                return ChatResult(MCP_DOWN_REPLY, error=f"Could not connect to the MCP server: {_describe(error)}")

            try:
                if self._model is None:
                    self._model = self._model_factory()
            except Exception as error:
                return ChatResult(MODEL_DOWN_REPLY, error=f"Model is not configured: {_describe(error)}")

            history = _history_messages(session.turns)
            history_length = len(history)  # the Agent appends to this same list
            agent = Agent(
                model=self._model,
                tools=tools,
                system_prompt=self._system_prompt(),
                messages=history,
                conversation_manager=NullConversationManager(),
                callback_handler=None,
            )
            try:
                result = await agent.invoke_async(message)
            except Exception as error:
                if _looks_like_mcp_failure(error):
                    self._tool_source.reset()
                    return ChatResult(MCP_DOWN_REPLY, error=f"MCP tool call failed: {_describe(error)}")
                return ChatResult(MODEL_DOWN_REPLY, error=f"Model call failed: {_describe(error)}")

            new_messages = agent.messages[history_length:]
            tools_used = _tool_names(new_messages)
            reply = _spoken(_final_text(result.message)) or FAILED_REPLY

            session.turns.append((message, reply))
            del session.turns[: max(0, len(session.turns) - self.max_turns)]
            return ChatResult(reply, tools_used)

    def _system_prompt(self) -> str:
        now = self._now().astimezone(SHOP_TZ)
        return f"{SYSTEM_PROMPT}\nRight now it is {now:%A, %B %d, %Y, %I:%M %p} in Asia/Manila ({now:%Y-%m-%d})."


# --- helpers -------------------------------------------------------------


def _history_messages(turns: list[tuple[str, str]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for user_text, assistant_text in turns:
        messages.append({"role": "user", "content": [{"text": user_text}]})
        messages.append({"role": "assistant", "content": [{"text": assistant_text}]})
    return messages


def _tool_names(messages: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for message in messages:
        for block in message.get("content", []):
            tool_use = block.get("toolUse") if isinstance(block, dict) else None
            if tool_use and tool_use.get("name") and tool_use["name"] not in names:
                names.append(tool_use["name"])
    return names


def _final_text(message: dict[str, Any] | None) -> str:
    if not message:
        return ""
    return " ".join(
        block["text"].strip() for block in message.get("content", []) if isinstance(block, dict) and block.get("text")
    ).strip()


_MARKDOWN = re.compile(r"[*`#]+|^\s*[-•]\s+|^\s*\d+\.\s+", re.MULTILINE)


def _spoken(text: str) -> str:
    """Safety net for speech: drop markdown symbols the model may still emit."""
    cleaned = _MARKDOWN.sub("", text).replace("_", " ")
    return re.sub(r"\s+", " ", cleaned).strip()


def _looks_like_mcp_failure(error: BaseException) -> bool:
    text = f"{type(error).__module__}.{type(error).__name__}: {error}".lower()
    return "mcp" in text and ("connect" in text or "closed" in text or "session" in text)


def _describe(error: BaseException) -> str:
    detail = str(error).strip().splitlines()[0] if str(error).strip() else ""
    return f"{type(error).__name__}: {detail}"[:300] if detail else type(error).__name__
