# Secret Grounds AI Assistant (phase 1)

A **read-only** [Model Context Protocol](https://modelcontextprotocol.io) server that lets
an AI voice assistant (Alexa+ track, Amazon Developer Hackathon) answer staff questions
about the shop: today's sales, sales over a date range, active sessions, which PCs are
free, member balances, and who is on shift.

```
LLM / voice assistant ──MCP (Streamable HTTP)──▶ assistant/mcp_server  ──HTTP GET + Bearer token──▶ backend (server/)
                                                  127.0.0.1:8765/mcp                                    127.0.0.1:8000 (dev)
```

- The MCP server is a separate process with its **own venv**. It never imports backend
  code and never opens the SQLite file; it only calls existing backend `GET` endpoints.
- The backend accepts `Authorization: Bearer <SG_ASSISTANT_TOKEN>` as an alternative to the
  login cookie, mapped to one staff user (`SG_ASSISTANT_USERNAME`). That user's normal role
  checks still apply. **Only `GET` is allowed** with the token; anything else gets `403`.
- If `SG_ASSISTANT_TOKEN` is not set on the backend, the token path is completely off and
  the backend behaves exactly as before.

## Tools

| Tool | What it returns | Backend endpoints |
|---|---|---|
| `get_today_overview` | Today's sales total + breakdown by payment method/type, active session count, PCs online/busy/free | `/sales/report?period=today`, `/sessions/active`, `/pcs` |
| `get_sales_report(start_date, end_date)` | Sales for shop-local days `YYYY-MM-DD`..`YYYY-MM-DD` (inclusive), by method/type/operator/PC | `/sales/report?period=custom` |
| `list_active_sessions` | PC, mode, time remaining (or elapsed for open time), amount due | `/sessions/active` |
| `list_pcs` | Name, online/offline, in use/free, time remaining | `/pcs` |
| `find_member(query)` | Up to 10 matches by code/username/name: time + VIP balance, status, expiry, **masked** phone. Never returns PINs | `/members?q=` |
| `get_current_shift` | On-duty operator, since when, shift opened at, running totals per payment method | `/operators/current`, `/dashboard/operator-sale`, `/shifts` |

Backend problems (backend down, bad token, missing permission) come back as
`{"error": "..."}` with a readable message instead of failing the call.

## Setup

Requires Python 3.12+ (developed on 3.14). From the repo root, in PowerShell:

```powershell
cd assistant\mcp_server
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # or requirements.txt without tests
Copy-Item .env.example .env    # then edit .env and set SG_ASSISTANT_TOKEN
```

Generate a token:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Environment variables

| Variable | Where | Default | Meaning |
|---|---|---|---|
| `SG_ASSISTANT_TOKEN` | backend **and** MCP server | *(unset = feature off)* | Shared secret. Must match on both sides. |
| `SG_ASSISTANT_USERNAME` | backend | *(none)* | Existing, active staff username the token acts as. `admin` role recommended so `get_current_shift` can see every shift; `cashier` works for everything else. |
| `SG_BACKEND_URL` | MCP server | `http://127.0.0.1:8000` | Backend base URL. |
| `SG_MCP_PORT` | MCP server | `8765` | MCP port. The server always binds to `127.0.0.1`. |
| `SG_SHOP_TIMEZONE` | MCP server | `Asia/Manila` | Timezone used to display times. |
| `SG_BACKEND_TIMEOUT_SECONDS` | MCP server | `10` | Backend request timeout. |

The MCP server reads `assistant/mcp_server/.env` if present (gitignored); real environment
variables take precedence. Never commit real tokens.

## Run the backend in dev with the token set

Use a **copy** of the database, a dev port (not 18001), and never the live `data/` DB.
From the repo root, in PowerShell:

```powershell
$env:SECRET_GROUNDS_DB_PATH = "$env:TEMP\sg-assistant-dev.sqlite3"
# Optional: snapshot real data into the copy (read-only SQLite backup, safe with WAL).
# Skip this line to start from an empty dev DB.
.\.venv\Scripts\python.exe -c "import sqlite3,os; s=sqlite3.connect('file:data/secret_grounds.db?mode=ro', uri=True); d=sqlite3.connect(os.environ['SECRET_GROUNDS_DB_PATH']); s.backup(d); d.close(); s.close()"
$env:SG_ASSISTANT_TOKEN = "<same token as in assistant/mcp_server/.env>"
$env:SG_ASSISTANT_USERNAME = "<an existing active staff username>"
.\.venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Check it (should print your mapped user):

```powershell
curl.exe -H "Authorization: Bearer $env:SG_ASSISTANT_TOKEN" http://127.0.0.1:8000/auth/me
```

## Run the MCP server

```powershell
cd assistant\mcp_server
.\.venv\Scripts\python.exe -m sg_assistant.server
```

It serves Streamable HTTP at `http://127.0.0.1:8765/mcp`.

> Note: the backend test `tests/smoke_websocket_commands.py` starts a temporary backend on
> port 8765. Stop the MCP server (or set `SG_MCP_PORT`) before running the backend smoke tests.

## Test with MCP Inspector

With the backend and MCP server running:

```powershell
npx @modelcontextprotocol/inspector
```

In the Inspector UI: **Transport Type** = `Streamable HTTP`, **URL** = `http://127.0.0.1:8765/mcp`,
then **Connect** → **Tools** → **List Tools**, pick a tool, and **Run Tool**.

## Tests

```powershell
cd assistant\mcp_server
.\.venv\Scripts\python.exe -m pytest
```

The tests mock the backend with `httpx.MockTransport`; no backend or database is needed.
The backend's token-auth tests are in `tests/smoke_assistant_token_auth.py` (run with the
main project venv).

## MCP SDK / protocol

Pinned to `mcp==2.3.0`. In the 2.x SDK, `FastMCP` was renamed to `MCPServer`
(`from mcp.server.mcpserver import MCPServer`). This version negotiates protocol versions
`2024-11-05`, `2025-03-26`, `2025-06-18` and `2025-11-25` through the `initialize`
handshake, and `2026-07-28` for clients that use the newer per-request envelope.
