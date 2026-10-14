# Secret Grounds AI Assistant

A **read-only** voice and text shift assistant for Secret Grounds staff (Alexa+ track,
Amazon Developer Hackathon). It answers questions about today's sales, sales over a date
range, active sessions, which PCs are free, member balances, and who is on shift.

- **Phase 1:** `assistant/mcp_server`, an MCP server whose tools wrap existing backend `GET`
  endpoints, plus an opt-in, GET-only bearer token on the backend.
- **Phase 2:** `assistant/agent`, a Strands agent service with a configurable model
  provider, plus a push-to-talk `/assistant` page in the dashboard, behind a feature flag.

## Architecture

```
 Browser (staff, signed in to the dashboard)
   │  /assistant page: Web Speech API speech-to-text + speechSynthesis, text fallback
   │  POST /api/assistant/chat        (same origin as the dashboard)
   ▼
 Dashboard Next.js server (dashboard/, :3000 dev)
   │  1. checks the caller's dashboard login: GET backend /auth/me with their sg_session cookie
   │  2. scopes the conversation to that user:  session_id = "<user_id>:<conversation_id>"
   │  POST http://127.0.0.1:8781/chat          (server-to-server, loopback only)
   ▼
 Agent service (assistant/agent, FastAPI + Strands, 127.0.0.1:8781)
   │  model: Bedrock | Anthropic | OpenAI | Ollama   (SG_AGENT_PROVIDER)
   │  MCP client, Streamable HTTP
   ▼
 MCP server (assistant/mcp_server, 127.0.0.1:8765/mcp)
   │  HTTP GET + Authorization: Bearer SG_ASSISTANT_TOKEN
   ▼
 Backend (server/, dev :8000)  →  SQLite (a COPY in dev, never data/)
```

Every hop is read-only:
- Every tool is a `GET`.
- The backend rejects any non-`GET` request made with the assistant token (`403`).
- The agent's prompt tells it to say write actions are "coming soon".

### Why the dashboard proxies to the agent

The dashboard's production server listens on `0.0.0.0` so other PCs in the shop can use it.
Exposing the agent the same way would give anyone on the LAN an unauthenticated way to query
shop data. So the browser **never talks to the agent**. Instead it calls the dashboard's own
route, `/api/assistant/chat`, and that route, running in the dashboard's Node server:

1. returns `404` unless `NEXT_PUBLIC_ASSISTANT_ENABLED=true`;
2. rejects cross-origin requests;
3. verifies the dashboard login by calling the backend's `/auth/me` with the caller's
   `sg_session` cookie (only that cookie is forwarded);
4. prefixes the session with the signed-in user's id, so one operator can never read another
   operator's conversation;
5. forwards the request to the agent on `127.0.0.1`.

The agent binds to `127.0.0.1` (not configurable) and the MCP server does too. Neither is
reachable from another machine. The agent's CORS is limited to the dashboard dev origin, but
that only matters if you call it directly from a browser.

## Tools (MCP server)

| Tool | What it returns | Backend endpoints |
|---|---|---|
| `get_today_overview` | Today's sales total and breakdown by payment method and type, active session count, PCs online/busy/free | `/sales/report?period=today`, `/sessions/active`, `/pcs` |
| `get_sales_report(start_date, end_date)` | Sales for shop-local days `YYYY-MM-DD` to `YYYY-MM-DD` (inclusive), broken down by method, type, operator and PC. If the owner reset the sales figures after `start_date`, the range starts at the reset and the result adds `requested_start_date` and a `note` such as *"Sales figures were reset on October 5, 2026 at 2:30 PM; earlier sales are not included."* | `/sales/report?period=custom` |
| `list_active_sessions` | PC, mode, time remaining (or elapsed time for open-time sessions), amount due | `/sessions/active` |
| `list_pcs` | Name, online/offline, in use/free, time remaining | `/pcs` |
| `find_member(query)` | Up to 10 matches by code, username or name: time and VIP balance, status, expiry, **masked** phone. Never returns PINs | `/members?q=` |
| `get_current_shift` | On-duty operator, when they came on duty, when the shift opened, running totals per payment method | `/operators/current`, `/dashboard/operator-sale`, `/shifts` |

Backend problems (backend down, bad token, missing permission) come back as `{"error": "..."}`
with a readable message instead of failing the call.

`/pcs` only lists PCs whose client is connected to *that* backend process. A dev backend on
copied data therefore always shows 0 PCs.

## Setup

Requires Python 3.12+ (developed on 3.14) and Node.js for the dashboard. Each Python piece
has its **own venv**. Nothing is added to `server/requirements.txt`. From the repo root, in
PowerShell:

```powershell
# MCP server
cd assistant\mcp_server
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env        # then set SG_ASSISTANT_TOKEN
cd ..\..

# Agent service
cd assistant\agent
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env        # then set SG_AGENT_PROVIDER / SG_AGENT_MODEL_ID / keys
cd ..\..
```

Generate a token:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Both `.env` files are gitignored. Real environment variables take precedence over them.
**Never commit tokens or keys.**

### Backend and MCP server variables

| Variable | Where | Default | Meaning |
|---|---|---|---|
| `SG_ASSISTANT_TOKEN` | backend **and** MCP server | *(unset = feature off)* | Shared secret. Must match on both sides. |
| `SG_ASSISTANT_USERNAME` | backend | *(none)* | Existing, active staff username the token acts as. `cashier` works for every tool. With `admin`, `get_current_shift` can also see when another operator's shift opened. |
| `SG_BACKEND_URL` | MCP server | `http://127.0.0.1:8000` | Backend base URL. |
| `SG_MCP_PORT` | MCP server | `8765` | MCP port. Always bound to `127.0.0.1`. |
| `SG_SHOP_TIMEZONE` | MCP server | `Asia/Manila` | Timezone used to display times. |
| `SG_BACKEND_TIMEOUT_SECONDS` | MCP server | `10` | Backend request timeout. |

### Agent variables

| Variable | Default | Meaning |
|---|---|---|
| `SG_AGENT_PROVIDER` | `bedrock` | `bedrock`, `anthropic`, `openai` or `ollama`. |
| `SG_AGENT_MODEL_ID` | *(required)* | Model ID for that provider. |
| `SG_AGENT_PORT` | `8781` | Agent API port. Always bound to `127.0.0.1`. |
| `SG_MCP_URL` | `http://127.0.0.1:8765/mcp` | The MCP server. |
| `SG_AGENT_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Comma-separated browser origins (direct browser calls only). |
| `SG_AGENT_MAX_TURNS` / `SG_AGENT_MAX_SESSIONS` | `20` / `200` | Conversation memory per session, and the number of sessions kept (least recently used are dropped first). |
| `SG_AGENT_MAX_TOKENS` / `SG_AGENT_TEMPERATURE` | `600` / provider default | Reply length and sampling. |

### Model providers

The agent uses Strands' built-in provider classes. Pick one with `SG_AGENT_PROVIDER` and set
the variables for it:

| Provider | You need | Variables |
|---|---|---|
| `bedrock` (default) | An AWS identity allowed to call `bedrock:InvokeModelWithResponseStream`, with model access enabled for the model in the region | `SG_AGENT_MODEL_ID` (model or inference-profile ID from the Bedrock console), `SG_AGENT_BEDROCK_REGION` (default `ap-southeast-2`), and AWS credentials from the standard chain: `AWS_PROFILE`, or `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` (+ `AWS_SESSION_TOKEN`) |
| `anthropic` | An Anthropic API key | `ANTHROPIC_API_KEY`, `SG_AGENT_MODEL_ID` (e.g. `claude-sonnet-5-5`) |
| `openai` | An OpenAI API key, or any OpenAI-compatible endpoint | `OPENAI_API_KEY`, `SG_AGENT_MODEL_ID`, optional `SG_AGENT_OPENAI_BASE_URL` |
| `ollama` | Ollama running locally with a model that supports tool calling | `SG_AGENT_MODEL_ID` (the pulled tag), `SG_AGENT_OLLAMA_HOST` (default `http://127.0.0.1:11434`) |

The model must support tool calling. With no model configured, the agent still starts:
`/health` works, and `/chat` returns a readable "can't reach the AI model" reply with the
reason in `error`.

### Dashboard variables

| Variable | Where | Default | Meaning |
|---|---|---|---|
| `NEXT_PUBLIC_ASSISTANT_ENABLED` | dashboard (dev env or build env) | *(off)* | Must be `true` for the `/assistant` page, its nav link and the `/api/assistant/*` routes to exist. Inlined at build time. The production build is unaffected unless it's built with this set. |
| `ASSISTANT_AGENT_URL` | dashboard server | `http://127.0.0.1:8781` | Where the proxy route reaches the agent. |
| `ASSISTANT_BACKEND_URL` | dashboard server | `NEXT_PUBLIC_FASTAPI_BASE_URL` | Backend used to check the dashboard login. `localhost` is rewritten to `127.0.0.1`. |

## Run everything locally (dev)

Use a **copy** of the database, dev ports (never 18001 or 3001), and never the live `data/` DB.
Open five PowerShell windows at the repo root.

**1. Backend (port 8000) on a DB copy, with the assistant token:**

```powershell
$env:SECRET_GROUNDS_DB_PATH = "$env:TEMP\sg-assistant-dev.sqlite3"
# Optional, once: snapshot real data into the copy (read-only SQLite backup, safe with WAL).
.\.venv\Scripts\python.exe -c "import sqlite3,os; s=sqlite3.connect('file:data/secret_grounds.db?mode=ro', uri=True); d=sqlite3.connect(os.environ['SECRET_GROUNDS_DB_PATH']); s.backup(d); d.close(); s.close()"
# Keep the 02:00 auto-backup out of the repo's backups/ folder:
$env:SECRET_GROUNDS_BACKUP_DIR = "$env:TEMP\sg-assistant-dev-backups"
# Let the dev dashboard call this backend:
$env:SECRET_GROUNDS_CORS_ORIGINS = "http://127.0.0.1:3000"
$env:SG_ASSISTANT_TOKEN = (Select-String '^SG_ASSISTANT_TOKEN=(.+)$' assistant\mcp_server\.env).Matches[0].Groups[1].Value
$env:SG_ASSISTANT_USERNAME = "assistant"   # an existing, active staff user in that DB
.\.venv\Scripts\python.exe -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Check it. This should print your mapped user:

```powershell
$t = (Select-String '^SG_ASSISTANT_TOKEN=(.+)$' assistant\mcp_server\.env).Matches[0].Groups[1].Value
curl.exe -H "Authorization: Bearer $t" http://127.0.0.1:8000/auth/me
```

**2. MCP server (port 8765).** It reads `assistant\mcp_server\.env`:

```powershell
cd assistant\mcp_server
.\.venv\Scripts\python.exe -m sg_assistant.server
```

**3. Agent (port 8781).** It reads `assistant\agent\.env`:

```powershell
cd assistant\agent
.\.venv\Scripts\python.exe -m sg_agent.app
```

```powershell
curl.exe http://127.0.0.1:8781/health
curl.exe -X POST http://127.0.0.1:8781/chat -H "Content-Type: application/json" -d '{\"session_id\":\"test\",\"message\":\"How are we doing today?\"}'
```

**4. Dashboard dev server (port 3000) with the assistant on.** Use `127.0.0.1` for both the
dashboard and the backend: the login cookie is per hostname, so mixing `localhost` and
`127.0.0.1` makes the proxy see you as signed out.

```powershell
cd dashboard
$env:NEXT_PUBLIC_ASSISTANT_ENABLED = "true"
$env:NEXT_PUBLIC_FASTAPI_BASE_URL = "http://127.0.0.1:8000"
npx next dev -H 127.0.0.1 -p 3000
```

> Run `next dev` (it writes to `dashboard\.next\dev`), not `npm run build`. The live cashier
> dashboard runs from `dashboard\.next\standalone`, and a rebuild would replace it.
> `npm run dev` also works but binds to `0.0.0.0`, so it's visible on the LAN.

**5. Open** `http://127.0.0.1:3000/assistant` in Chrome or Edge, and sign in as usual.
- Hold the mic button (or hold Space or Enter on it) to talk, and release to send.
- Or type in the text box.
- Replies are spoken unless you untick "Speak replies".
- Under each reply, chips show which tools were used (e.g. "Checked: today's sales").

Voice notes:
- The mic only works on `https://` or `localhost` / `127.0.0.1`. Over plain `http://` with a
  LAN IP, the page falls back to the text box and explains why.
- Speech recognition uses the browser's built-in service. In Chrome and Edge, that means audio
  is sent to the browser vendor's speech servers and needs internet access. Text input and
  spoken replies work offline.
- Firefox has no `SpeechRecognition`; the page detects this and offers the text box.

### MCP Inspector

With the backend and MCP server running:

```powershell
npx @modelcontextprotocol/inspector
```

In the Inspector UI:
1. Set **Transport Type** to `Streamable HTTP` and **URL** to `http://127.0.0.1:8765/mcp`.
2. Click **Connect**, open **Tools**, and click **List Tools**.
3. Pick a tool and click **Run Tool**.

> Port note: the backend test `tests/smoke_websocket_commands.py` starts a temporary backend
> on port 8765. Stop the MCP server (or change `SG_MCP_PORT` and `SG_MCP_URL`) before running
> the backend smoke tests.

## Tests

```powershell
# MCP server tools (backend mocked with httpx.MockTransport)
cd assistant\mcp_server; .\.venv\Scripts\python.exe -m pytest; cd ..\..

# Agent API and wiring: scripted fake model + fake MCP server over mcp's in-memory transport,
# driving the real Strands agent loop and MCP client. No real model, no network.
cd assistant\agent; .\.venv\Scripts\python.exe -m pytest; cd ..\..

# Backend token auth (main project venv)
.\.venv\Scripts\python.exe tests\smoke_assistant_token_auth.py

# Dashboard proxy route + helpers (fetch stubbed)
cd dashboard; npx tsx src\tests\assistant.test.ts; npm run typecheck; npm test
```

## Versions

- MCP server: `mcp==2.3.0`. In the 2.x SDK, `FastMCP` was renamed to `MCPServer`. It
  negotiates `2024-11-05` through `2025-11-25` via `initialize`, plus `2026-07-28`.
- Agent: `strands-agents==1.60.0`, which brings its own `mcp` client (2.1.1) inside the
  agent's venv. FastAPI `0.143.1`.
