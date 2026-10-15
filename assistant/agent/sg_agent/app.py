"""HTTP API for the Secret Grounds agent (phase 2 read-only; phase 3 adds confirmed actions).

Run:  python -m sg_agent.app
Serves http://127.0.0.1:<SG_AGENT_PORT> (default 8781). Loopback only: the
dashboard reaches it through its own server-side proxy route, never from the
browser directly.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from strands.tools.mcp import MCPClient

from sg_agent.config import AGENT_HOST, Settings, get_settings, load_env_file
from sg_agent.models import build_model
from sg_agent.actions import ActionError
from sg_agent.handover import HandoverStore
from sg_agent.service import FAILED_REPLY, ChatService, ToolSource


class Operator(BaseModel):
    """Who is talking, as checked by the dashboard against the backend. Used
    only to phrase replies (e.g. "that needs the Admin account"); the backend
    still enforces every permission itself."""

    display_name: str = Field(..., max_length=80)
    role: str = Field(..., max_length=20)
    is_admin_account: bool = False


class ShiftSnapshot(BaseModel):
    """The signed-in operator's OWN open shift, fetched by the dashboard with
    their login (GET /shifts/current). Used only by the read-only close check."""

    open: bool
    cashier_name: str | None = Field(default=None, max_length=80)
    opened_at: str | None = Field(default=None, max_length=40)
    opening_cash: str | None = Field(default=None, max_length=20)
    expected_cash: str | None = Field(default=None, max_length=20)
    payment_count: int = 0
    settlement_pending_sessions: int = 0
    review_required_payments: int = 0
    outstanding_unsettled_balance: str | None = Field(default=None, max_length=20)


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1, max_length=2000)
    operator: Operator | None = None
    shift: ShiftSnapshot | None = None


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str] = Field(default_factory=list)
    error: str | None = None
    pending_action: dict[str, Any] | None = None


class ResetRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)


class ActionRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    action_id: str = Field(..., min_length=1, max_length=64)


class ResolveNoteRequest(BaseModel):
    note_id: str = Field(..., min_length=1, max_length=16)
    resolved_by: str = Field(..., min_length=1, max_length=80)


class ActionResultRequest(ActionRequest):
    status_code: int
    body: Any = None


def create_app(service: ChatService, settings: Settings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        service.close()

    app = FastAPI(title="Secret Grounds agent", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "provider": settings.provider,
            "model_id": settings.model_id or None,
            "mcp_url": settings.mcp_url,
        }

    @app.post("/chat", response_model=ChatResponse, response_model_exclude_none=True)
    async def chat(request: ChatRequest) -> ChatResponse:
        result = await service.chat(
            request.session_id.strip(),
            request.message.strip(),
            operator=request.operator.model_dump() if request.operator else None,
            shift=request.shift.model_dump() if request.shift else None,
        )
        return ChatResponse(
            reply=result.reply,
            tools_used=result.tools_used,
            error=result.error,
            pending_action=result.pending_action,
        )

    @app.post("/chat/stream")
    async def chat_stream(request: ChatRequest) -> StreamingResponse:
        """Same as /chat, as newline-delimited JSON: {"type":"tool","name":...}
        as soon as each tool call starts, then one {"type":"final",...} line
        with exactly what /chat returns. Lets the voice page show what it's
        doing while the answer is still being worked out."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def on_tool(name: str) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "tool", "name": name})

        async def run() -> None:
            try:
                result = await service.chat(
                    request.session_id.strip(),
                    request.message.strip(),
                    operator=request.operator.model_dump() if request.operator else None,
                    shift=request.shift.model_dump() if request.shift else None,
                    on_tool=on_tool,
                )
                final = ChatResponse(
                    reply=result.reply,
                    tools_used=result.tools_used,
                    error=result.error,
                    pending_action=result.pending_action,
                ).model_dump(exclude_none=True)
            except Exception as error:  # never leave the stream hanging
                final = {"reply": FAILED_REPLY, "tools_used": [], "error": f"{type(error).__name__}: {error}"[:300]}
            loop.call_soon_threadsafe(queue.put_nowait, {"type": "final", **final})

        task = asyncio.create_task(run())

        async def events():
            try:
                yield json.dumps({"type": "start"}) + "\n"
                while True:
                    item = await queue.get()
                    yield json.dumps(item, ensure_ascii=False) + "\n"
                    if item["type"] == "final":
                        break
            finally:
                await task

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/reset")
    def reset(request: ResetRequest) -> dict[str, Any]:
        return {"session_id": request.session_id, "cleared": service.reset(request.session_id.strip())}

    # --- phase 3: the dashboard server runs confirmed actions -----------------
    # Only the dashboard's server-side route calls these, after the operator
    # confirmed on screen. The agent never sends anything to the backend itself.

    @app.post("/actions/take")
    async def take_action(request: ActionRequest) -> JSONResponse:
        try:
            action = await service.take_action(request.session_id.strip(), request.action_id.strip())
        except ActionError as error:
            return JSONResponse({"detail": str(error)}, status_code=409)
        return JSONResponse(
            {
                "action_id": action.action_id,
                "kind": action.kind,
                "summary": action.summary,
                "targets": action.targets,
                "request": action.request,
            }
        )

    # --- shift handover notes (step d) ---------------------------------------------
    # Read by the dashboard's server for the page's Handover panel; resolving
    # from the panel is attributed to the operator the dashboard verified.

    @app.get("/handover")
    def handover_notes() -> dict[str, Any]:
        store = service.handover_store
        if store is None:
            return {"enabled": False, "notes": []}
        return {"enabled": True, "notes": [note.public() for note in store.open_notes()]}

    @app.post("/handover/resolve")
    def resolve_handover(request: ResolveNoteRequest) -> JSONResponse:
        store = service.handover_store
        if store is None:
            return JSONResponse({"detail": "Handover notes are turned off."}, status_code=404)
        try:
            note = store.resolve(request.note_id, request.resolved_by)
        except LookupError as error:
            return JSONResponse({"detail": str(error)}, status_code=409)
        return JSONResponse({"resolved": note.public()})

    @app.post("/actions/cancel")
    async def cancel_action(request: ActionRequest) -> dict[str, Any]:
        return {"reply": await service.cancel_action(request.session_id.strip(), request.action_id.strip())}

    @app.post("/actions/result")
    async def action_result(request: ActionResultRequest) -> JSONResponse:
        try:
            outcome = await service.record_result(
                request.session_id.strip(), request.action_id.strip(), request.status_code, request.body
            )
        except ActionError as error:
            return JSONResponse({"detail": str(error)}, status_code=409)
        return JSONResponse(outcome)

    return app


def build_service(settings: Settings) -> ChatService:
    tool_source = ToolSource(lambda: MCPClient(url=settings.mcp_url, startup_timeout=10))
    return ChatService(
        lambda: build_model(settings),
        tool_source,
        max_turns=settings.max_turns,
        max_sessions=settings.max_sessions,
        handover_store=HandoverStore(settings.notes_db) if settings.notes_db else None,
    )


def main() -> None:
    import uvicorn

    load_env_file()
    settings = get_settings()
    app = create_app(build_service(settings), settings)
    print(
        f"Secret Grounds agent on http://{AGENT_HOST}:{settings.port} "
        f"(provider {settings.provider}, model {settings.model_id or 'NOT SET'}, MCP {settings.mcp_url})"
    )
    uvicorn.run(app, host=AGENT_HOST, port=settings.port)


if __name__ == "__main__":
    main()
