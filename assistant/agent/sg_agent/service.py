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
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from strands import Agent
from strands.agent.conversation_manager import NullConversationManager
from strands.hooks import BeforeToolCallEvent
from strands.models import Model
from strands.tools.mcp import MCPClient

from sg_agent.actions import ActionError, PendingAction, describe_outcome, extract_proposals, new_pending_action
from sg_agent.handover import HandoverStore, make_handover_tools
from sg_agent.prompt import ACTIONS_PROMPT, DRAWER_CHECK_PROMPT, HANDOVER_PROMPT, SYSTEM_PROMPT
from sg_agent.drawer_check import make_drawer_tools

SHOP_TZ = ZoneInfo("Asia/Manila")

MCP_DOWN_REPLY = "Sorry, I can't reach the shop system right now. Please try again in a moment."
MODEL_DOWN_REPLY = "Sorry, I can't reach the AI model right now, so I can't answer that."
FAILED_REPLY = "Sorry, something went wrong while I was answering that."


@dataclass
class ChatResult:
    reply: str
    tools_used: list[str] = field(default_factory=list)
    error: str | None = None
    pending_action: dict[str, Any] | None = None


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
    # Phase 3: at most one action waiting for the operator, plus actions the
    # dashboard has taken to run and not yet reported back.
    pending: PendingAction | None = None
    in_flight: dict[str, PendingAction] = field(default_factory=dict)


class ChatService:
    def __init__(
        self,
        model_factory: Callable[[], Model],
        tool_source: ToolSource,
        *,
        max_turns: int = 20,
        max_sessions: int = 200,
        now: Callable[[], datetime] = lambda: datetime.now(SHOP_TZ),
        handover_store: HandoverStore | None = None,
        drawer_float: Decimal | None = None,
    ) -> None:
        self.handover_store = handover_store
        # Fixed starting float for the drawer check (SG_AGENT_DRAWER_FLOAT); None = ask each time.
        self.drawer_float = drawer_float
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

    def history(self, session_id: str) -> list[tuple[str, str]]:
        session = self._sessions.get(session_id)
        return list(session.turns) if session else []

    # --- chat -------------------------------------------------------------

    async def chat(
        self,
        session_id: str,
        message: str,
        operator: dict[str, Any] | None = None,
        on_tool: Callable[[str], None] | None = None,
        duty: dict[str, Any] | None = None,
    ) -> ChatResult:
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

            author = str(operator.get("display_name")) if operator and operator.get("display_name") else None
            # Built once per message: it also records which handover notes this
            # operator has now been given, so a retry must not rebuild it.
            system_prompt = self._system_prompt(actions_enabled=_has_propose_tools(tools), operator=operator)
            if duty is not None:
                system_prompt += "\n" + DRAWER_CHECK_PROMPT

            async def run_turn(prompt: str, with_history: bool = True) -> tuple[Any, list[dict[str, Any]]] | ChatResult:
                nonlocal tools
                for attempt in (1, 2):
                    history = _history_messages(session.turns) if with_history else []
                    history_length = len(history)  # the Agent appends to this same list
                    agent_tools = list(tools)
                    if self.handover_store is not None:
                        # Bound to the dashboard-verified operator; the model can't choose the author.
                        agent_tools += make_handover_tools(self.handover_store, author)
                    if duty is not None:
                        # Bound to the on-duty summary the dashboard fetched with the operator's login.
                        agent_tools += make_drawer_tools(duty, self.drawer_float)
                    agent = Agent(
                        model=self._model,
                        tools=agent_tools,
                        system_prompt=prompt,
                        messages=history,
                        conversation_manager=NullConversationManager(),
                        callback_handler=None,
                    )
                    if on_tool is not None:
                        # Lets the page show "Thinking… · Checking: today's sales" the
                        # moment a tool starts, instead of after the whole answer.
                        agent.hooks.add_callback(
                            BeforeToolCallEvent,
                            lambda event: on_tool(str((event.tool_use or {}).get("name") or "")),
                        )
                    try:
                        result = await agent.invoke_async(message)
                    except Exception as error:
                        if _looks_like_mcp_failure(error):
                            self._tool_source.reset()
                            return ChatResult(MCP_DOWN_REPLY, error=f"MCP tool call failed: {_describe(error)}")
                        return ChatResult(MODEL_DOWN_REPLY, error=f"Model call failed: {_describe(error)}")

                    new_messages = agent.messages[history_length:]
                    # If the MCP server restarted, the cached connection is dead and
                    # every tool call comes back as an error instead of raising.
                    # Reconnect and answer again once (all MCP tools only read or
                    # prepare, so repeating the turn changes nothing).
                    if attempt == 1 and _mcp_session_lost(new_messages):
                        self._tool_source.reset()
                        try:
                            tools = await asyncio.to_thread(self._tool_source.get_tools)
                        except Exception as error:
                            self._tool_source.reset()
                            return ChatResult(MCP_DOWN_REPLY, error=f"Could not reconnect to the MCP server: {_describe(error)}")
                        continue
                    return result, new_messages
                return ChatResult(FAILED_REPLY)  # not reached: the loop always returns

            outcome = await run_turn(system_prompt)
            if isinstance(outcome, ChatResult):
                return outcome
            result, new_messages = outcome
            tools_used = _tool_names(new_messages)
            reply = _spoken(_final_text(result.message)) or FAILED_REPLY
            proposals = extract_proposals(new_messages)

            # In long conversations the model sometimes copies an earlier reply ("I've got
            # that message ready ... Confirm to send it.") without calling a propose_ tool, so
            # no card appears. Answer again once with a reminder and without the earlier turns
            # (the copying comes from them; in a fresh conversation the model calls the tool).
            # If it still claims something is ready with nothing prepared, say so honestly
            # instead. Not while an earlier card is still open: then "it's ready, confirm on
            # screen" can be true.
            card_still_open = session.pending is not None and not session.pending.expired()
            if not proposals and not card_still_open and _has_propose_tools(tools) and _claims_ready(reply):
                retry = await run_turn(system_prompt + "\n" + READY_WITHOUT_TOOL_REMINDER, with_history=False)
                if isinstance(retry, ChatResult):
                    reply = NOT_PREPARED_REPLY
                else:
                    result, new_messages = retry
                    tools_used = _tool_names(new_messages)
                    reply = _spoken(_final_text(result.message)) or FAILED_REPLY
                    proposals = extract_proposals(new_messages)
                    if not proposals and _claims_ready(reply):
                        reply = NOT_PREPARED_REPLY

            # Only proposals that came back from propose_* tool results count;
            # if the model prepared several, the last one is the one shown.
            pending = None
            if proposals:
                session.pending = new_pending_action(proposals[-1])
                pending = session.pending.public()

            self._remember(session, message, reply)
            return ChatResult(reply, tools_used, pending_action=pending)

    # --- actions (phase 3) -----------------------------------------------------

    async def take_action(self, session_id: str, action_id: str) -> PendingAction:
        """Hand the pending action to the dashboard to run. Single use."""
        session = self._sessions.get(session_id)
        if session is None:
            raise ActionError("That action is no longer available. Please ask again.")
        async with session.lock:
            action = session.pending
            if action is None or action.action_id != action_id:
                raise ActionError("That action is no longer available. Please ask again.")
            session.pending = None
            if action.expired():
                raise ActionError("That action expired. Please ask again.")
            session.in_flight[action.action_id] = action
            return action

    async def cancel_action(self, session_id: str, action_id: str) -> str:
        session = self._sessions.get(session_id)
        if session is None:
            return "There was nothing waiting to cancel."
        async with session.lock:
            action = session.pending
            if action is None or action.action_id != action_id:
                return "There was nothing waiting to cancel."
            session.pending = None
            reply = "Okay, cancelled. Nothing was sent."
            self._remember(session, f"(Cancelled on screen: {action.summary})", reply)
            return reply

    async def record_result(self, session_id: str, action_id: str, status_code: int, body: Any) -> dict[str, Any]:
        session = self._sessions.get(session_id)
        if session is None:
            raise ActionError("Unknown action.")
        async with session.lock:
            action = session.in_flight.pop(action_id, None)
            if action is None:
                raise ActionError("Unknown action.")
            outcome = describe_outcome(action, status_code, body)
            self._remember(session, f"(Confirmed on screen: {action.summary})", outcome["reply"])
            return {"kind": action.kind, "summary": action.summary, **outcome}

    def _remember(self, session: _Session, user_text: str, reply: str) -> None:
        session.turns.append((user_text, reply))
        del session.turns[: max(0, len(session.turns) - self.max_turns)]

    def _system_prompt(self, actions_enabled: bool = False, operator: dict[str, Any] | None = None) -> str:
        now = self._now().astimezone(SHOP_TZ)
        prompt = f"{SYSTEM_PROMPT}\n{ACTIONS_PROMPT}" if actions_enabled else SYSTEM_PROMPT
        prompt += f"\nRight now it is {now:%A, %B %d, %Y, %I:%M %p} in Asia/Manila ({now:%Y-%m-%d})."
        if operator:
            name = str(operator.get("display_name") or "the operator")[:80]
            role = str(operator.get("role") or "staff")[:20]
            admin = bool(operator.get("is_admin_account"))
            prompt += (
                f"\nYou are talking to {name} ({role}"
                f"{', signed in as the Admin account' if admin else ''})."
            )
            if actions_enabled and not admin:
                prompt += (
                    " They are not the Admin account, so shutting down PCs will be refused; tell them "
                    "to sign in as Admin instead of preparing it."
                )
        if self.handover_store is not None:
            prompt += f"\n{HANDOVER_PROMPT}"
            author = str(operator.get("display_name")) if operator and operator.get("display_name") else None
            section = self.handover_store.prompt_section(author)
            if section:
                prompt += f"\n{section}"
        return prompt

    def close(self) -> None:
        self._tool_source.close()
        if self.handover_store is not None:
            self.handover_store.close()


# --- helpers -------------------------------------------------------------


NOT_PREPARED_REPLY = "I couldn't prepare that, please ask again."
READY_WITHOUT_TOOL_REMINDER = (
    "Important: your first answer to this message said an action was ready to confirm, but you "
    "did not call a propose_ tool, so nothing was prepared and no confirmation card appeared. If "
    "the user is asking for an action, call the matching propose_ tool now and only then say it is "
    "ready. If they are not asking for an action, answer without saying anything is ready to confirm."
)
_READY_WORDS = re.compile(r"\b(ready|prepared|set up|lined up)\b", re.IGNORECASE)
_CONFIRM_WORD = re.compile(r"\bconfirm", re.IGNORECASE)


def _claims_ready(reply: str) -> bool:
    """True for replies like "I've got that message ready for PC 8. Confirm to send it." """
    return bool(_READY_WORDS.search(reply) and _CONFIRM_WORD.search(reply))


def _history_messages(turns: list[tuple[str, str]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for user_text, assistant_text in turns:
        messages.append({"role": "user", "content": [{"text": user_text}]})
        messages.append({"role": "assistant", "content": [{"text": assistant_text}]})
    return messages


_SESSION_LOST_MARKERS = ("client session is not running", "mcpclientinitializationerror")


def _mcp_session_lost(messages: list[dict[str, Any]]) -> bool:
    """True if a tool result says the MCP client's connection is gone."""
    for message in messages:
        for block in message.get("content", []):
            result = block.get("toolResult") if isinstance(block, dict) else None
            if not result or result.get("status") != "error":
                continue
            text = " ".join(
                str(item.get("text", "")) for item in result.get("content", []) if isinstance(item, dict)
            ).lower()
            if any(marker in text for marker in _SESSION_LOST_MARKERS):
                return True
    return False


def _has_propose_tools(tools: list[Any]) -> bool:
    return any(str(getattr(tool, "tool_name", "")).startswith("propose_") for tool in tools)


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
