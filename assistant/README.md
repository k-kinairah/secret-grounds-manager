# Secret Grounds AI Assistant

A voice and text shift assistant for Secret Grounds staff (Alexa+ track, Amazon Developer
Hackathon). It answers questions about today's sales, sales over a date range, active
sessions, which PCs are free, member balances, and who is on shift. It can also prepare a
small set of actions, which only run after a staff member confirms them.

- **Phase 1:** `assistant/mcp_server`, an MCP server whose tools wrap existing backend `GET`
  endpoints, plus an opt-in, GET-only bearer token on the backend.
- **Phase 2:** `assistant/agent`, a Strands agent service with a configurable model
  provider, plus a push-to-talk `/assistant` page in the dashboard, behind a feature flag.
- **Phase 3 (in progress):** confirmed actions. So far: a message to one PC, an announcement
  to several or all PCs, adding time to a prepaid session (billed later), and shutting down
  idle PCs (Admin only). Plus shift handover notes that carry over between shifts.

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
| `find_member(query)` | Up to 10 matches by code, username or name: time and VIP balance, status, expiry, **masked** phone. Never returns PINs. If nothing matches exactly (speech-to-text misspelling, e.g. "rojin" for "Rogene"), it returns no member plus up to 3 ranked `did_you_mean` guesses (name, username and code only), by spelling similarity and a Soundex sound-alike key over username, names and member code. The assistant asks "Did you mean Rogene?" and treats a guess as certain only after the user confirms. | `/members?q=`, then `/members` for guesses |
| `get_current_shift` | On-duty operator, when they came on duty, when the shift opened, running totals per payment method | `/operators/current`, `/dashboard/operator-sale`, `/shifts` |

Backend problems (backend down, bad token, missing permission) come back as `{"error": "..."}`
with a readable message instead of failing the call.

`/pcs` only lists PCs whose client is connected to *that* backend process. A dev backend on
copied data therefore shows 0 PCs unless a test client is connected to it (see
"Demo PC" below).

## Actions (phase 3): propose, confirm, run

Off unless the MCP server runs with `SG_ASSISTANT_ACTIONS=true`. When it's off, the propose
tools don't exist and the agent keeps its read-only prompt.

```
 "Tell PC 3 their friend is here"
   │
   ▼  agent ── propose_message_pc ──▶ MCP server: checks PC 3 exists and is online (GET only),
   │                                  builds the exact request, sends NOTHING
   │◀── proposal ────────────────────
   │  agent parks it as a pending action: single use, 2-minute expiry, this conversation only
   ▼
 /assistant page shows a confirm card (summary, target PCs, Confirm / Cancel)
   │  operator clicks Confirm, or says/types exactly "confirm" / "yes" / "go ahead"
   │  (matched in the browser by exact phrase; the model is never asked)
   ▼
 Dashboard server /api/assistant/confirm
   1. checks the dashboard login (same as /chat)
   2. takes the action from the agent (single use, scoped to this user's conversation)
   3. checks it against a hard-coded allow-list and rebuilds the request from validated fields
   4. sends it to the backend with the OPERATOR'S OWN sg_session cookie (not the token)
   5. reports the backend's real response to the agent, which builds the spoken result
   ▼
 Result card (✓ / ○ offline / ✕ per PC) + spoken outcome
```

- **Identity:** an action runs as the operator who confirmed it. The backend's normal role
  checks apply, and the audit trail (`remote_commands.requested_by`) shows that operator.
  The bearer token stays GET-only; the backend is unchanged for actions.
- **What the model can't do:** it can't run anything, and it can't create an action by just
  saying so. Only `propose_*` tool results become pending actions, and "done" is only ever
  spoken from the backend's real response.

| Action | Tool | Backend call (allow-listed) | Role |
|---|---|---|---|
| Message one PC | `propose_message_pc(pc_name, message, severity?)` | `POST /pcs/{pc}/message` | cashier+ |
| Announcement to several or all online PCs | `propose_broadcast(message, pc_names?, severity?)` | `POST /commands/broadcast` with an explicit `pc_names` list | cashier+ |
| Add time to a running **prepaid** session, billed later | `propose_add_time(pc_name, package \| minutes \| amount_pesos)` | `POST /sessions/{session_id}/add-time` with exactly one of `rate_id` or `amount` (≤ ₱2,000) | cashier+ |

| Shut down **idle** PCs (online, no session, nothing starting) | `propose_shutdown_idle_pcs(pc_names?, exclude?)` | one `POST /pcs/{pc}/power/shutdown` per PC, body exactly `{"force": false}` | **protected Admin account only** (backend-enforced) |

About shutting down idle PCs:
- **Never forced.** The backend refuses (409) any PC that has a session. A PC that starts a
  session between proposing and confirming is skipped, not powered off.
- **Honest results.** A PC *receiving* the command is not proof it powered off, so the card
  and the spoken reply say "received the shutdown command". A real PC adds "should power off
  in a few seconds". A test-mode client (the demo PC) adds "(test mode: not powered off)":
  in test mode the client acknowledges with `accepted (test mode: not powered off)` and never
  runs the real shutdown.
- **Admin only.** Anyone else's confirmation is refused by the backend, and the assistant says
  "only the Admin account can shut down PCs". The dashboard also tells the agent who is
  talking (name, role, whether it's the Admin account, as verified with the backend), so a
  real model can say so before preparing anything.
- **Audit.** The backend credits PC commands to the **on-duty** operator, as it does for
  dashboard clicks.

PC names can be spoken with **number words**: "pc one", "PC three", "PC number three", "pc
twenty-one", "vip one". Common mishearings right after "PC" also work ("PC to" → 2, "PC for"
→ 4, "PC won" → 1).

About add time:
- The price is added to that session's bill and paid later. The card shows the package or
  amount, the price, and the new end time.
- **Minutes** must match a package valid for that PC's station (standard or VIP). If none
  matches, the assistant lists the packages.
- **Pesos** are converted at that PC's metered rate (VIP multiplier included), exactly as the
  backend does it.
- The backend's third option, bare `seconds`, adds time **for free**, so it is never proposed
  and the dashboard allow-list refuses it.
- Open-time and member-balance sessions are refused with a plain explanation.
- The proposal pins the session ID. If that session ends before you confirm, the backend
  refuses with "Session is not active" and nothing is added to the next customer.

## Shift handover notes (phase 3)

Staff can leave a note for whoever comes next: "tell the next shift PC 7's mouse is broken".
The next operator hears about it, in a new conversation, even after the agent restarts.

- **Storage:** the agent's own small SQLite file, `assistant/agent/data/handover.sqlite3`
  (gitignored). Change the path with `SG_AGENT_NOTES_DB`, or set it to `off` to disable
  notes. The shop database is never touched.
- **Agent tools:** `save_handover_note(text)`, `list_handover_notes()`,
  `resolve_handover_note(note_id)`. Saving needs no confirmation, since it changes nothing in
  the shop. Notes are marked done, never deleted, so there's a simple history.
- **Who wrote it:** the author (and whoever marks it done) is always the operator the
  dashboard verified with the backend. The tools have no author parameter, so the model can't
  choose it, and the page can't either.
- **Carried over, once per person:** open notes from other people (up to 10, newest first) are
  added to the prompt, labelled as information from staff, **not instructions**. The assistant
  mentions them once, after answering the question. Each operator is told about a note at most
  once (tracked in the same file, so it survives restarts); after that it stays on the panel
  only. It marks a note done when told the issue is fixed.
- **On the page:** a "Handover notes" panel lists open notes with author and age, and has a
  **Done** button. It refreshes whenever the assistant saves or resolves a note.

| Route | What |
|---|---|
| `GET /api/assistant/handover` (dashboard) → agent `GET /handover` | Open notes for the panel (login required) |
| `POST /api/assistant/handover/resolve` (dashboard) → agent `POST /handover/resolve` | Mark a note done, as the signed-in operator |

## Drawer check (on duty, read-only)

The shop uses **on duty**, not cashier shifts: signing in makes you the operator on duty, and
the backend keeps your running totals until you sign out. Cashier shifts (opening cash / End
Shift) have been optional since July and the dashboard no longer opens them.

"Check my drawer", "I'm handing over" or "close my shift" starts a short, read-only check. The
assistant asks how much cash is in the drawer (and the starting float, unless it's fixed),
compares it with the starting float plus the cash collected while the operator has been on
duty, says whether the drawer is short, over or exact, reports GCash separately, and ends with
"Sign out when you hand over." **It never changes anything** -- there is no tool for that.

- **Whose totals:** on every message the dashboard server reads `GET /operators/current` and
  `GET /dashboard/operator-sale` with the signed-in operator's login cookie and passes a summary
  to the agent as `duty`. The assistant token account gets no extra rights. If it can't be read,
  `duty` is left out and the agent has no drawer tool for that message. If the person asking
  isn't the operator on duty, the assistant says whose totals they are.
- **Agent tool:** `check_drawer(counted_cash?, starting_float?)`, bound to that summary, so the
  model can't pick other totals or invent them.
- **Blind count:** the first call returns only who is on duty and what to ask. The expected
  amount comes back only after the count. The page's "On duty" card shows the payment count and
  GCash, not cash, for the same reason.
- **Starting float:** set `SG_AGENT_DRAWER_FLOAT` (e.g. `200`) in `assistant/agent/.env` for a
  fixed float; leave it unset and the assistant asks each time.
- **Hand-offs:** the running total covers only this operator's payments since they came on duty.
  When the drawer is over, or someone else is asking, the assistant mentions that the drawer may
  also hold another operator's cash if they changed operator without signing out.
- **"Since":** the time comes from `/operators/current` (the last on-duty selection). After a
  Change Operator round trip it can be later than when the running total started.

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
| `SG_ASSISTANT_USERNAME` | backend | *(none)* | Existing, active staff username the token acts as. `cashier` works for every tool. With `admin`, `get_current_shift` can also see when another operator's shift opened. See "Creating the assistant account" below. |
| `SG_ASSISTANT_ACTIONS` | MCP server | `false` | Phase 3: also offer the `propose_*` action tools. |
| `SG_BACKEND_URL` | MCP server | `http://127.0.0.1:8000` | Backend base URL. |
| `SG_MCP_PORT` | MCP server | `8765` | MCP port. Always bound to `127.0.0.1`. |
| `SG_SHOP_TIMEZONE` | MCP server | `Asia/Manila` | Timezone used to display times. |
| `SG_BACKEND_TIMEOUT_SECONDS` | MCP server | `10` | Backend request timeout. |

### Creating the assistant account

Create the account the token maps to with **`UserRepository.create_user`**, not the
`/admin/operators` route or the Staff page. Operators (`is_operator = 1`) appear on the
"Who's in charge right now?" login picker. A plain user doesn't, which is what you want for
a service account. To remove an account that was already created as an operator, set
`is_operator = 0` on it. The family-operator seeding at startup only touches its own fixed
list, so the change sticks. To switch the assistant off, unset `SG_ASSISTANT_TOKEN` on the
backend.

### Agent variables

| Variable | Default | Meaning |
|---|---|---|
| `SG_AGENT_PROVIDER` | `bedrock` | `bedrock`, `anthropic`, `openai` or `ollama`. |
| `SG_AGENT_MODEL_ID` | *(required)* | Model ID for that provider. |
| `SG_AGENT_PORT` | `8781` | Agent API port. Always bound to `127.0.0.1`. |
| `SG_MCP_URL` | `http://127.0.0.1:8765/mcp` | The MCP server. |
| `SG_AGENT_CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Comma-separated browser origins (direct browser calls only). |
| `SG_AGENT_MAX_TURNS` / `SG_AGENT_MAX_SESSIONS` | `20` / `200` | Conversation memory per session, and the number of sessions kept (least recently used are dropped first). |
| `SG_AGENT_MAX_TOKENS` / `SG_AGENT_TEMPERATURE` | `300` / provider default | Reply length and sampling. Spoken replies are 1–3 sentences, so 300 is plenty. |
| `SG_AGENT_NOTES_DB` | `assistant/agent/data/handover.sqlite3` | Shift handover notes file; `off` disables notes. |
| `SG_AGENT_DRAWER_FLOAT` | (unset) | Fixed starting float for the drawer check, e.g. `200`; unset = ask each time. |

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

## Run the demo in one command

For a demo you don't need the snapshot or five windows. Once the [Setup](#setup) is done
(three venvs, `npm install` in `dashboard`, and both `.env` files: the MCP server's
`SG_ASSISTANT_TOKEN` and the agent's model settings), run this from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File assistant\demo\start-demo.ps1
```

It takes a minute or two (`next dev` compiles on first load), then opens
`http://127.0.0.1:3000/assistant`. **Sign in as Tyrone** (password; Tyrone has the open shift).
To stop everything it started:

```powershell
powershell -ExecutionPolicy Bypass -File assistant\demo\stop-demo.ps1
```

What it does:

- **Fresh fake data every time.** `assistant\demo\seed_demo.py` builds a new database under
  `%TEMP%\sg-demo\run-<time>\` through the backend's own code: schema setup, repositories, and
  the session service, with a fake PC connection and a shifted clock for the past week.
  It refuses any path that already exists, and anything in the repo's `data\`, `backups\` or
  `server\` folders. It never reads the snapshot or the live database. A fixed random seed
  means the same data each run, relative to the current time.
- **What's in it:**
  - **Staff:** the default family operators the backend always creates. Tyrone has the open
    shift and uses the same password as Admin; Rogene and the others use quick access.
  - **Members:** 9 fake members with different Standard and VIP balances.
  - **Sales:** a week of walk-in sales and top-ups (roughly ₱1,400–2,000 a day), plus today's.
  - **Shift:** Tyrone's shift is open with ₱1,000 opening cash.
  - **Sessions running now:** PC 1 about to run out (about 10 minutes left once the demo is
    up), plus a walk-in on PC 3 and a member (Andrea Santos) on PC 7 in the background.
  - **Handover note:** one note from Rogene, in the agent's notes file.

  PC 1 is the live test client: it picks up its seeded session from the backend when it
  connects. Seeding in the early morning gives "today" only a few sales, so seed during the
  day for a fuller picture.
- **Starts, all on 127.0.0.1:** backend `:8000` on the demo database, MCP server `:8765` with
  actions on, agent `:8781` (model settings from `assistant\agent\.env`), the test client
  as **PC-01** (TEST_MODE: power commands are refused, no lock screen; messages pop up on this
  laptop), and the dashboard `:3000` with the assistant page on. It refuses to start if any
  of those ports is busy, and it never uses 18001/3001.
- **Stops only what it started.** Process ids and start times are kept in
  `%TEMP%\sg-demo\demo-state.json`. `stop-demo.ps1` stops those processes (with their child
  processes) only if the id still has the same start time. Logs and data stay in the run
  folder; delete `%TEMP%\sg-demo` when you no longer need them.

Things to try: "How are we doing today compared to yesterday?", "Which sessions are running
out?", "Any notes from the last shift?", "Send PC 1 a message: your food is ready", and
"Close my shift" (the expected cash is shown in the seed output). "Add one hour to PC one"
works live on PC 1's session. Only PC 1 has a real client: the sessions on PCs 3 and 7 can be
read, but actions on those PCs are refused because the PC is offline. If PC 1's session runs out
before you get to it, start a new one on PC 1 from the dashboard.

Notes:
- **Admin password:** Admin uses the project's default seeding, so its password is
  `SECRET_GROUNDS_ADMIN_PASSWORD` if that was set when seeding, otherwise the backend's
  built-in default.
- **`next dev` rewrites `dashboard\next-env.d.ts`.** Undo it with
  `git checkout -- dashboard/next-env.d.ts` before committing.

### Filming with two PCs (`-Lan`)

To speak on one PC (the "cashier") while the customer PC visibly reacts, keep the whole demo
on this laptop and open the assistant page from the other PC. `-Lan` is off by default.

```powershell
powershell -ExecutionPolicy Bypass -File assistant\demo\start-demo.ps1 -Lan
```

With `-Lan`, the backend (`:8000`) and dashboard (`:3000`) listen on the laptop's home-LAN
address (found automatically; pass `-LanIp 192.168.0.109` if there is more than one). The MCP
server and the agent stay on 127.0.0.1. Only private addresses are accepted, and
`stop-demo.ps1` closes everything again. The demo PC-01 client on the laptop is the PC
that reacts. The mic doesn't work on the laptop's own page in this mode, because the page
isn't served from `localhost`.

**On the other PC** (it only runs a browser; nothing is installed and the live system is not
touched). In PowerShell:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --user-data-dir="$env:TEMP\sg-demo-chrome" --unsafely-treat-insecure-origin-as-secure=http://192.168.0.109:3000 http://192.168.0.109:3000/assistant
```

- Use the address that `start-demo.ps1 -Lan` prints if it differs. If Chrome is installed
  per user, the path is `$env:LOCALAPPDATA\Google\Chrome\Application\chrome.exe`.
- This opens a separate, throwaway Chrome profile. Your normal Chrome profile and the live
  dashboard are untouched. The flag lets that profile treat this one address as secure, which
  the browser needs before it allows the microphone.
- Chrome shows an "unsupported command-line flag" bar; close it before filming.
- Sign in as Tyrone, allow the microphone, and hold the mic button. If the mic still isn't
  offered, type in the text box instead.
- **Afterwards:** close that Chrome window, then
  `Remove-Item -Recurse -Force "$env:TEMP\sg-demo-chrome"`.

**Firewall on the laptop (optional, admin PowerShell).** Windows treats this home network as
**Public**, and existing "Allow" rules already let any device on it reach Python 3.14 and
Node.js. So in `-Lan` mode the demo is reachable from the whole home network, not just the
other PC. To allow only the other PC while filming:

```powershell
# Before filming: switch off the broad python/node rules (remembered in a file), allow only the other PC.
$apps = "C:\Users\Kin\AppData\Local\Python\pythoncore-3.14-64\python.exe", "C:\Program Files\nodejs\node.exe"
$rules = Get-NetFirewallApplicationFilter | Where-Object { $apps -contains $_.Program } | Get-NetFirewallRule |
    Where-Object { $_.Direction -eq "Inbound" -and $_.Action -eq "Allow" -and $_.Enabled -eq "True" }
$rules.Name | Set-Content "$env:TEMP\sg-demo-firewall-rules.txt"
$rules | Disable-NetFirewallRule
New-NetFirewallRule -DisplayName "SG demo (temporary)" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3000,8000 -RemoteAddress 192.168.0.112 -Profile Any

# After filming: remove the temporary rule and switch the remembered rules back on.
Remove-NetFirewallRule -DisplayName "SG demo (temporary)"
Get-Content "$env:TEMP\sg-demo-firewall-rules.txt" | ForEach-Object { Enable-NetFirewallRule -Name $_ }
Remove-Item "$env:TEMP\sg-demo-firewall-rules.txt"
```

If Windows shows an "allow access" prompt for Python or Node while the demo starts, press
**Cancel**. Pressing Cancel can add a *Block* rule, which also blocks the other PC. If the other
PC can't connect, or once you've finished, look for new block rules and delete them:
`Get-NetFirewallRule -Direction Inbound -Action Block | Where-Object DisplayName -match "python|node"`
(then `| Remove-NetFirewallRule`).

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

**2. MCP server (port 8765).** It reads `assistant\mcp_server\.env`. Add
`$env:SG_ASSISTANT_ACTIONS = "true"` first (or put it in `.env`) to enable actions:

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

### Demo PC (a test client on the dev backend)

To see a PC react on the dev backend, run a second client from the repo next to the installed
one. In `TEST_MODE`, power commands are refused on the client, and the lock screen is
switched off so the laptop stays usable. It runs as `PC-01` from `client\config.json`
(read-only), pointed at the dev backend. Its recovery file and log go to `%TEMP%`.

```powershell
$env:SECRET_GROUNDS_SERVER_URL = "http://127.0.0.1:8000"
$env:SECRET_GROUNDS_TEST_MODE = "true"
$env:ENABLE_LOCK_SCREEN = "false"
$env:SECRET_GROUNDS_RECOVERY_PATH = "$env:TEMP\sg-demo-client-recovery.json"
$env:SECRET_GROUNDS_CLIENT_LOG = "$env:TEMP\sg-demo-client.log"
$env:SECRET_GROUNDS_REMINDER_VOICE_ENABLED = "false"
.\.venv\Scripts\python.exe -m client.client
```

Messages and announcements pop up on this laptop. Close the window or stop the process to
remove it; nothing installed is changed. It shows a second tray icon while it runs. Tested
alongside the installed client: no conflict.

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
cd dashboard; npx tsx src\tests\assistant.test.ts; npx tsx src\tests\assistantActions.test.ts; npx tsx src\tests\assistantShutdown.test.ts; npx tsx src\tests\assistantHandover.test.ts; npx tsx src\tests\assistantStream.test.ts; npx tsx src\tests\assistantDuty.test.ts; npm run typecheck; npm test
```

## Latency and model choice

- **Streaming progress.** The page uses `POST /api/assistant/chat/stream`, which proxies the
  agent's `POST /chat/stream`. That route sends newline-delimited JSON: a `{"type":"tool"}`
  line the moment each tool call starts, then one `{"type":"final"}` line with exactly what
  `/chat` returns. The page shows "Thinking… · Checking: today's sales" while it waits, and
  falls back to `/chat` if streaming isn't available.
- **Short replies:** `SG_AGENT_MAX_TOKENS` defaults to 300.
- **Prompt caching: tried, not kept (2026-10-16).** Bedrock prompt caching works for Haiku 4.5
  in `ap-southeast-2` (each answer read about 9,600 tokens from cache), but on the same 5
  questions × 3, interleaved off/on twice, first-chip time was 5.09 s off vs 5.03 s on, and
  total time 10.33 s vs 10.94 s. The wait is Bedrock's own latency, which swung between 1.7 s
  and 5.6 s within minutes, not prompt size.
- **MCP server restarts:** the agent notices a dead MCP connection (tool results saying "client
  session is not running"), reconnects, and answers that message again once. It's safe to
  repeat because MCP tools only read or prepare.
- **Model (tested 2026-10-16, same 23-message round from an identical fresh DB copy):**

  | Model (Bedrock, `ap-southeast-2`) | Avg / max total | Avg time to first tool chip | Correctness |
  |---|---|---|---|
  | `au.anthropic.claude-haiku-4-5-20251001-v1:0` (**default**) | 9.1 s / 21.1 s | 5.2 s | No made-up answers, numbers right |
  | `global.amazon.nova-2-lite-v1:0` | 3.3 s / 7.0 s | 1.8 s | **Not recommended:** answered two questions without tools and made things up (a wrong session list; claimed a note was marked done when it wasn't) |
  | `au.anthropic.claude-haiku-5-5` | — | — | Not enabled on the account (AccessDenied); enable model access in the Bedrock console to test |

## Versions

- MCP server: `mcp==2.3.0`. In the 2.x SDK, `FastMCP` was renamed to `MCPServer`. It
  negotiates `2024-11-05` through `2025-11-25` via `initialize`, plus `2026-07-28`.
- Agent: `strands-agents==1.60.0`, which brings its own `mcp` client (2.1.1) inside the
  agent's venv. FastAPI `0.143.1`.
