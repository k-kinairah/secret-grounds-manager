"""HTTP API for the Secret Grounds agent (phase 2, read-only).

Run:  python -m sg_agent.app
Serves http://127.0.0.1:<SG_AGENT_PORT> (default 8781). Loopback only: the
dashboard reaches it through its own server-side proxy route, never from the
browser directly.
"""

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from strands.tools.mcp import MCPClient

from sg_agent.config import AGENT_HOST, Settings, get_settings, load_env_file
from sg_agent.models import build_model
from sg_agent.service import ChatService, ToolSource


class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)
    message: str = Field(..., min_length=1, max_length=2000)


class ChatResponse(BaseModel):
    reply: str
    tools_used: list[str] = Field(default_factory=list)
    error: str | None = None


class ResetRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=100)


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
        result = await service.chat(request.session_id.strip(), request.message.strip())
        return ChatResponse(reply=result.reply, tools_used=result.tools_used, error=result.error)

    @app.post("/reset")
    def reset(request: ResetRequest) -> dict[str, Any]:
        return {"session_id": request.session_id, "cleared": service.reset(request.session_id.strip())}

    return app


def build_service(settings: Settings) -> ChatService:
    tool_source = ToolSource(lambda: MCPClient(url=settings.mcp_url, startup_timeout=10))
    return ChatService(
        lambda: build_model(settings),
        tool_source,
        max_turns=settings.max_turns,
        max_sessions=settings.max_sessions,
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
