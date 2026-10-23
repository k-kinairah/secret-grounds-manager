# Secret Grounds

**Secret Grounds** is the management system for a real internet and gaming café in the
Philippines: 15 customer PCs, walk-in and member time, and a cashier at the counter. It has
three parts:

- **Backend** (`server/`): FastAPI + SQLite. Sessions, payments, members, sales and remote PC
  commands.
- **Cashier dashboard** (`dashboard/`): Next.js. The PC grid, sessions, members, sales and the
  operator on duty.
- **PC client** (`client/`): runs on every customer PC. It shows the lock screen, the session
  timer and member login, and connects to the backend over a WebSocket for commands.

## Shift Assistant (Alexa+ / Amazon hackathon project)

The **Shift Assistant** lets the person at the counter run the shop by voice. They hold a
button (or the Spacebar), ask a question, and hear the answer. Some examples:

- "How are we doing today compared to yesterday?" or "Which sessions are running out soon?"
- "Who's on PC 7?" or "Any notes from the last shift?"
- "Send PC 1 a message: your food is ready", "Add one hour to PC one", or "Tell everyone we
  close in fifteen minutes"
- "Check my drawer" (a read-only cash check against what was collected while on duty)

**Actions never happen on the model's word.** The model can only *prepare* an action. A card
appears on screen, and only the staff member's **Confirm** runs it, with their own login. The
reply then reports what each PC actually acknowledged (for example "Shown on 13 of 15 PCs. PC-01
got it but couldn't show it: …").

What it adds (all under `assistant/`):

| Part | What it is |
|---|---|
| **MCP server** (`assistant/mcp_server`) | Model Context Protocol server with read-only shop tools (sales, sessions, PCs, members, on duty) and `propose_*` tools that only *prepare* actions. It talks to the backend with a read-only (GET-only) token. |
| **Agent** (`assistant/agent`) | [Strands Agents](https://strandsagents.com) service using **Claude Haiku 4.5 on Amazon Bedrock** (AU cross-region inference profile). It holds the conversation, handover notes and the drawer check. |
| **Voice page** (`dashboard`, `/assistant`) | Push-to-talk page using the browser's speech recognition and speech output, with confirm/cancel cards, live "Checking: …" chips, handover notes and an "On duty" card. |

### Architecture

```
Browser /assistant (voice + text)
   → dashboard server (Next.js, checks the login)
   → agent (Strands, :8781) ──→ Claude Haiku 4.5 on Amazon Bedrock
                         └──→ MCP server (:8765) ──→ backend (FastAPI, :8000) ──→ SQLite
Confirmed actions: dashboard server → backend (with the operator's own login) → PCs over WebSocket
```

Full details (tools, safety rules, variables, tests, filming with two PCs) are in
**[assistant/README.md](assistant/README.md)**.

## Run the demo (Windows)

The demo runs entirely on your PC with **fresh, fake shop data**. It never touches a real shop
database, and the live ports (18001/3001) are never used.

**You need:** Windows, [Python 3.14](https://www.python.org/downloads/) (`py -3.14`),
[Node.js 24](https://nodejs.org/), Git, Chrome or Edge, and an **Amazon Bedrock API key** with
access to Claude Haiku 4.5.

1. **Set up once** (from the repo root, in PowerShell). This makes three venvs and installs
   the dashboard:
   ```powershell
   py -3.14 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r server\requirements.txt
   cd assistant\mcp_server; py -3.14 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt; Copy-Item .env.example .env; cd ..\..
   cd assistant\agent; py -3.14 -m venv .venv; .\.venv\Scripts\python.exe -m pip install -r requirements.txt; Copy-Item .env.example .env; cd ..\..
   cd dashboard; npm ci; cd ..
   ```
2. **Fill in the two `.env` files.** Both are gitignored; never commit them.
   - `assistant\mcp_server\.env`: set `SG_ASSISTANT_TOKEN` to a long random value, e.g. the
     output of `py -3.14 -c "import secrets; print(secrets.token_urlsafe(32))"`.
   - `assistant\agent\.env`: set `SG_AGENT_PROVIDER=bedrock`,
     `SG_AGENT_BEDROCK_REGION=ap-southeast-2`,
     `SG_AGENT_MODEL_ID=au.anthropic.claude-haiku-4-5-20251001-v1:0`, and add the line
     `AWS_BEARER_TOKEN_BEDROCK=<your Bedrock API key>`.
3. **Start everything** with one command:
   ```powershell
   powershell -ExecutionPolicy Bypass -File assistant\demo\start-demo.ps1
   ```
   It takes 2–4 minutes (seeding a busy week of data, then the dashboard compiles), then
   opens `http://127.0.0.1:3000/assistant`.
4. **Sign in** on the "Who's in charge right now?" screen as **Tyrone**, with the demo password
   **`sg-demo-2026`**. Tyrone is the operator on duty. This password exists only in the
   throwaway demo database. Allow the microphone, then hold the mic button (or the Spacebar)
   and talk.
5. **Stop everything:**
   ```powershell
   powershell -ExecutionPolicy Bypass -File assistant\demo\stop-demo.ps1
   ```

Optional switches for `start-demo.ps1`:
- `-Lan`: film with a second PC.
- `-NoFakePcs`
- `-NoBrowser`
- `-ForcePopupFailure`: shows an honest failure.

See [assistant/README.md](assistant/README.md#run-the-demo-in-one-command) for these.

## What's real and what's simulated in the demo

| Real | Simulated |
|---|---|
| The backend, MCP server, agent and dashboard: the same code as the shop | The shop data: 24 fake members, a week of fake sales, and fake sessions (made through the backend's own code into a new database) |
| Claude Haiku 4.5 on Amazon Bedrock: every answer is a live model call | PC-02 to PC-15: `assistant/demo/fake_pcs.py` connects them like real clients and acknowledges honestly ("displayed (simulated PC)", "received (simulated PC, test mode: nothing powered off)") |
| **PC-01**: the real Secret Grounds client on this laptop, in test mode. Messages and announcements really pop up; added time really changes its timer | Power commands: in test mode nothing is ever shut down or restarted |
| The confirm-before-acting flow, and the per-PC delivery results | |

## Tests

Backend and client smoke tests are in `tests/` (`.\.venv\Scripts\python.exe tests\smoke_*.py`).
The MCP server and agent have pytest suites, and the dashboard has `npx tsx` tests. See
[assistant/README.md](assistant/README.md#tests).
