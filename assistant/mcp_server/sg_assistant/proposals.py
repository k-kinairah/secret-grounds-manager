"""Action proposals (phase 3).

These tools NEVER change anything. They check the request against live state
(GET only, with the read-only assistant token) and return a proposal: the
exact backend request that WOULD run, plus a plain-English summary. The
agent holds the proposal; only an operator pressing Confirm (or saying
"confirm") in the dashboard runs it, from the dashboard server, with that
operator's own login. The dashboard re-checks every request against its own
hard-coded allow-list before sending it.
"""

import re
from typing import Any

from sg_assistant.backend import BackendClient, BackendError

MESSAGE_MAX_LENGTH = 500
MESSAGE_SEVERITIES = ("info", "warning", "urgent")
MESSAGE_DEFAULT_SECONDS = 20


def _error(message: str) -> dict[str, str]:
    return {"error": message}


def _normalize_pc(name: str) -> str:
    """'PC 3', 'pc-03', 'PC03' -> 'pc3' (digit groups lose leading zeros)."""
    lowered = re.sub(r"[^a-z0-9]", "", name.lower())
    return re.sub(r"\d+", lambda match: str(int(match.group())), lowered)


def _match_pc(pcs: list[dict[str, Any]], wanted: str) -> dict[str, Any] | None:
    key = _normalize_pc(wanted)
    if not key:
        return None
    for pc in pcs:
        names = [pc.get("pc_name") or "", pc.get("display_name") or ""]
        if any(name and _normalize_pc(name) == key for name in names):
            return pc
    return None


def _label(pc: dict[str, Any]) -> str:
    return pc.get("display_name") or pc.get("pc_name") or "?"


def _reachable(pc: dict[str, Any]) -> bool:
    return pc.get("connection_status") == "online" and bool(pc.get("has_command_connection"))


def _clean_message(message: str) -> str | None:
    cleaned = " ".join((message or "").replace("\x00", "").split())
    if not cleaned or len(cleaned) > MESSAGE_MAX_LENGTH:
        return None
    return cleaned


def _clean_severity(severity: str) -> str:
    value = (severity or "info").strip().lower()
    return value if value in MESSAGE_SEVERITIES else "info"


async def propose_message_pc(
    client: BackendClient, pc_name: str, message: str, severity: str = "info"
) -> dict[str, Any]:
    text = _clean_message(message)
    if text is None:
        return _error(f"The message must be 1 to {MESSAGE_MAX_LENGTH} characters.")
    try:
        pcs = await client.get("/pcs")
    except BackendError as error:
        return _error(str(error))

    pc = _match_pc(pcs, pc_name)
    if pc is None:
        known = ", ".join(sorted(_label(p) for p in pcs)) or "none"
        return _error(f"There is no PC called {pc_name!r}. Known PCs: {known}.")
    if not _reachable(pc):
        return _error(f"{_label(pc)} is offline, so it can't receive a message right now.")

    level = _clean_severity(severity)
    return {
        "proposal": {
            "kind": "message_pc",
            "summary": f'Show "{text}" on {_label(pc)}?',
            "targets": [pc["pc_name"]],
            "details": {"message": text, "severity": level},
            "request": {
                "method": "POST",
                "path": f"/pcs/{pc['pc_name']}/message",
                "body": {"message": text, "severity": level, "duration_seconds": MESSAGE_DEFAULT_SECONDS},
            },
        },
        "status": "Waiting for the operator to confirm on screen. Nothing has been sent yet.",
    }


async def propose_broadcast(
    client: BackendClient, message: str, pc_names: list[str] | None = None, severity: str = "info"
) -> dict[str, Any]:
    text = _clean_message(message)
    if text is None:
        return _error(f"The message must be 1 to {MESSAGE_MAX_LENGTH} characters.")
    try:
        pcs = await client.get("/pcs")
    except BackendError as error:
        return _error(str(error))

    if pc_names:
        chosen: list[dict[str, Any]] = []
        for wanted in pc_names:
            pc = _match_pc(pcs, wanted)
            if pc is None:
                return _error(f"There is no PC called {wanted!r}.")
            if pc not in chosen:
                chosen.append(pc)
    else:
        chosen = list(pcs)

    reachable = [pc for pc in chosen if _reachable(pc)]
    unreachable = [pc for pc in chosen if not _reachable(pc)]
    if not reachable:
        return _error("None of those PCs are online right now, so there is nobody to send the message to.")

    level = _clean_severity(severity)
    targets = [pc["pc_name"] for pc in reachable]
    scope = "every online PC" if not pc_names else ", ".join(_label(pc) for pc in reachable)
    summary = f'Show "{text}" on {scope} ({len(reachable)} PC{"s" if len(reachable) != 1 else ""})?'
    return {
        "proposal": {
            "kind": "broadcast",
            "summary": summary,
            "targets": targets,
            "details": {
                "message": text,
                "severity": level,
                "skipped_offline": [_label(pc) for pc in unreachable],
            },
            "request": {
                "method": "POST",
                "path": "/commands/broadcast",
                # Always explicit targets: exactly the PCs the operator saw on the card.
                "body": {
                    "message": text,
                    "severity": level,
                    "duration_seconds": MESSAGE_DEFAULT_SECONDS,
                    "pc_names": targets,
                },
            },
        },
        "status": "Waiting for the operator to confirm on screen. Nothing has been sent yet.",
    }
