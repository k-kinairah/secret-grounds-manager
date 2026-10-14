"""Pending actions (phase 3): propose -> confirm -> run.

The model can only call propose_* MCP tools, which return a proposal (the
exact backend request plus a summary) and change nothing. The agent takes
proposals ONLY from those tools' results -- never from the model's own text --
and parks one per session as a pending action: single-use, expiring.

Running it is not the agent's job. The dashboard server takes the pending
action (after the operator confirms on screen), re-checks it against its own
allow-list, sends it with the operator's own login, and reports the backend's
response back here so the conversation can continue. The spoken outcome is
built from that real response, not by the model, so "done" is never invented.
"""

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

SHOP_TZ = ZoneInfo("Asia/Manila")
ACTION_TTL_SECONDS = 120
PROPOSE_TOOL_PREFIX = "propose_"
_FAILED_STATUSES = ("failed", "timed_out", "rejected")


@dataclass
class PendingAction:
    action_id: str
    kind: str
    summary: str
    targets: list[str]
    details: dict[str, Any]
    request: dict[str, Any]
    created_at: float = field(default_factory=time.monotonic)

    def expired(self, now: float | None = None) -> bool:
        return (now if now is not None else time.monotonic()) - self.created_at > ACTION_TTL_SECONDS

    def public(self) -> dict[str, Any]:
        """What the browser sees: enough to render the confirm card, not the raw request."""
        remaining = max(0, int(ACTION_TTL_SECONDS - (time.monotonic() - self.created_at)))
        return {
            "id": self.action_id,
            "kind": self.kind,
            "summary": self.summary,
            "targets": list(self.targets),
            "details": dict(self.details),
            "expires_in_seconds": remaining,
        }


class ActionError(Exception):
    """Unknown, expired or already-used action. Safe to show."""


def new_pending_action(proposal: dict[str, Any]) -> PendingAction:
    return PendingAction(
        action_id=uuid.uuid4().hex,
        kind=str(proposal["kind"]),
        summary=str(proposal["summary"]),
        targets=[str(t) for t in proposal.get("targets") or []],
        details=dict(proposal.get("details") or {}),
        request=dict(proposal["request"]),
    )


def extract_proposals(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Proposals returned by propose_* tool calls in these messages, in order."""
    propose_ids: set[str] = set()
    for message in messages:
        for block in message.get("content", []):
            tool_use = block.get("toolUse") if isinstance(block, dict) else None
            if tool_use and str(tool_use.get("name", "")).startswith(PROPOSE_TOOL_PREFIX):
                propose_ids.add(tool_use.get("toolUseId"))

    found: list[dict[str, Any]] = []
    for message in messages:
        for block in message.get("content", []):
            result = block.get("toolResult") if isinstance(block, dict) else None
            if not result or result.get("toolUseId") not in propose_ids or result.get("status") == "error":
                continue
            payload = _result_payload(result)
            proposal = payload.get("proposal") if isinstance(payload, dict) else None
            if _valid_proposal(proposal):
                found.append(proposal)  # type: ignore[arg-type]
    return found


def _result_payload(result: dict[str, Any]) -> Any:
    for item in result.get("content", []):
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("json"), dict):
            return item["json"]
        if isinstance(item.get("text"), str):
            try:
                return json.loads(item["text"])
            except ValueError:
                continue
    return None


def _valid_proposal(proposal: Any) -> bool:
    if not isinstance(proposal, dict):
        return False
    request = proposal.get("request")
    return (
        isinstance(proposal.get("kind"), str)
        and isinstance(proposal.get("summary"), str)
        and isinstance(request, dict)
        and isinstance(request.get("method"), str)
        and isinstance(request.get("path"), str)
        and isinstance(request.get("body"), dict)
    )


# --- outcomes -------------------------------------------------------------


def describe_outcome(action: PendingAction, status_code: int, body: Any) -> dict[str, Any]:
    """Turn the backend's real response into a spoken reply + per-PC results."""
    if not 200 <= status_code < 300:
        detail = body.get("detail") if isinstance(body, dict) else None
        reason = detail if isinstance(detail, str) and detail else f"the shop system returned an error ({status_code})"
        if status_code in (401, 403):
            reason = detail or "you don't have permission to do that"
        targets = [{"pc": pc, "status": "failed", "detail": reason} for pc in action.targets]
        return {"ok": False, "reply": f"That didn't go through: {_sentence(reason)}", "targets": targets}

    if action.kind == "message_pc":
        command = body.get("command", {}) if isinstance(body, dict) else {}
        pc = action.targets[0] if action.targets else "the PC"
        if command.get("status") in _FAILED_STATUSES:
            reason = command.get("failure_reason") or command.get("status")
            return {
                "ok": False,
                "reply": f"The message didn't reach {pc}: {_sentence(str(reason))}",
                "targets": [{"pc": pc, "status": "failed", "detail": str(reason)}],
            }
        return {
            "ok": True,
            "reply": f"Done. The message is showing on {pc}.",
            "targets": [{"pc": pc, "status": "done", "detail": None}],
        }

    if action.kind == "broadcast":
        data = body if isinstance(body, dict) else {}
        commands = [c for c in data.get("commands", []) if isinstance(c, dict)]
        sent = [str(c.get("pc_name")) for c in commands if c.get("status") not in _FAILED_STATUSES]
        offline = [str(pc) for pc in data.get("offline_targets", [])]
        failed = {str(pc): str(reason) for pc, reason in (data.get("failed_targets") or {}).items()}
        for command in commands:
            if command.get("status") in _FAILED_STATUSES:
                failed[str(command.get("pc_name"))] = str(command.get("failure_reason") or command.get("status"))
        targets = (
            [{"pc": pc, "status": "done", "detail": None} for pc in sent]
            + [{"pc": pc, "status": "offline", "detail": "went offline"} for pc in offline]
            + [{"pc": pc, "status": "failed", "detail": reason} for pc, reason in failed.items()]
        )
        if not sent:
            return {"ok": False, "reply": "The announcement didn't reach any PC.", "targets": targets}
        reply = f"Done. The announcement went out to {len(sent)} PC{'s' if len(sent) != 1 else ''}."
        missed = offline + list(failed)
        if missed:
            reply += f" It didn't reach {_join(missed)}."
        return {"ok": True, "reply": reply, "targets": targets}

    if action.kind == "add_time":
        session = body.get("session", {}) if isinstance(body, dict) else {}
        pc = action.targets[0] if action.targets else "the PC"
        added = action.details.get("added") or "the time"
        price = action.details.get("price")
        reply = f"Done. Added {added} to {pc}."
        ends = _clock(session.get("scheduled_end_at"))
        if ends:
            reply += f" It now ends at {ends}."
        if price:
            reply += f" ₱{price} was added to their bill."
        return {"ok": True, "reply": reply, "targets": [{"pc": pc, "status": "done", "detail": ends}]}

    return {
        "ok": True,
        "reply": "Done.",
        "targets": [{"pc": pc, "status": "done", "detail": None} for pc in action.targets],
    }


def _clock(value: Any) -> str | None:
    """'2026-10-15T13:45:00+00:00' -> '9:45 PM' in shop time."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    local = parsed.astimezone(SHOP_TZ)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


def _sentence(text: str) -> str:
    text = text.strip().rstrip(".")
    return f"{text[:1].lower()}{text[1:]}." if text else "unknown error."


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return f"{', '.join(items[:-1])} and {items[-1]}"
