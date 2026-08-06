# Secret Grounds Packaging Readiness

Secret Grounds should be packaged as two separate installer targets. Do not ship one installer that silently enables both roles.

## Target A: Secret Grounds Cashier Setup

Role:
- Runs FastAPI backend on the configured production backend port.
- Runs the Next.js production dashboard on the configured dashboard port.
- Owns the SQLite database, backups, server logs, dashboard logs, deployment config, and operator shortcuts.
- Does not install or autostart the customer desktop client unless the operator explicitly chooses the cashier test-client option.

Included files:
- `server/`
- `dashboard/.next/` production build
- `dashboard/package.json` plus production runtime dependencies, or a bundled Node runtime with a prepared standalone dashboard output
- `scripts/` cashier/server deployment scripts
- `operator-shortcuts/`
- `VERSION`
- `config/.env.example`
- empty `data/`, `backups/`, and `logs/` directories

Excluded development files:
- `.git/`, `.agents/`, `.codex/`
- `dashboard/src/` when shipping a standalone dashboard build
- `dashboard/node_modules/` dev-only packages when using a pruned production runtime
- Python caches, test databases, test logs, source maps if not needed for support, local `config/.env`, local `client/config.json`
- VS Code settings, development dashboards, and dev server launchers

Required runtimes:
- `runtime\python\` — a private Python runtime built from the official embeddable
  distribution by `scripts\prepare-backend-runtime.ps1` (see below), plus a pruned
  dashboard runtime copied by the installer.
- Not required on the cashier PC after install: VS Code, Git, source checkout workflow, or manual npm commands.

`runtime\python\` must never be a raw `.venv`. A `venv`'s `Scripts\python.exe` is a
stub that reads `pyvenv.cfg` to find its "home" interpreter, which hardcodes the
absolute path of whichever machine created it (an AppData\Local\Python path under
the developer's own user profile). Shipping that folder verbatim breaks on any
other PC. The embeddable distribution has no such external reference, so it is
what gets copied into `{app}\runtime\python` by the installer.

Startup behavior:
- Default startup entry: `Secret Grounds Cashier Stack`.
- Starts `scripts/start-secret-grounds.ps1 -NoBrowser`.
- Duplicate starts are rejected by PID identity checks.
- Opens dashboard manually through `operator-shortcuts/Open Secret Grounds Dashboard.url`.
- Customer client autostart is removed by `scripts/install-cashier-role.ps1`.

Uninstall behavior:
- Stop cashier stack with `scripts/stop-secret-grounds.ps1`.
- Remove Task Scheduler entry `Secret Grounds Cashier Stack` or the current-user Startup launcher.
- Preserve `data/`, `backups/`, and `config/.env` by default unless the operator selects data removal.
- Remove firewall rule `Secret Grounds Backend Private` if it was installed.

Log locations:
- `logs/server.log`
- `logs/server-console.log`
- `logs/server-error.log`
- `logs/dashboard.log`
- `logs/dashboard-console.log`
- `logs/dashboard-error.log`
- `logs/deployment.log`

Config locations:
- `config/.env`
- `dashboard/.env.production.local` may be generated locally for production dashboard builds and must not be shared between stores.

Upgrade behavior:
- Stop services.
- Back up `data/secret_grounds.db` and `config/.env`.
- Replace application files.
- Preserve config and database.
- Run database migrations through normal backend startup.
- Rebuild or replace the dashboard production build.
- Restart and verify `/healthz`, dashboard, backups, and status.

## Target B: Secret Grounds Client Setup

Role:
- Runs only the Secret Grounds desktop customer client.
- Owns only station config, local recovery state, and client logs.
- Does not install backend, dashboard, database, backup jobs, Node.js, or cashier shortcuts.

Included files:
- Preferred: PyInstaller one-folder payload containing `SecretGroundsClient.exe`, bundled runtime libraries, `VERSION`, generated `config.json`, and a small startup helper.
- Interim source-based bundle: `client/`, minimal shared modules required by the client, private Python runtime or `.venv`, `scripts/start-client.ps1`, `scripts/configure-client.ps1`, `scripts/install-client-role.ps1`, `VERSION`.

Excluded development files:
- `server/`
- `dashboard/`
- `data/`
- `backups/`
- cashier startup scripts unless needed by a shared support bundle
- tests, `.git/`, VS Code files, Node.js dependencies, source-only development tools

Required runtimes:
- Preferred: no external runtime; ship a standalone executable built from the Python client.
- Acceptable: bundled private Python runtime controlled by the installer.
- Not required on customer PCs: Python installed globally, VS Code, Git, Node.js, npm, source code checkout.

Startup behavior:
- Default startup entry: `Secret Grounds Client`.
- Starts the desktop client hidden/normal depending on final installer UX.
- Uses generated local `config.json`.
- Reconnect settings are per station.
- Does not start backend or dashboard.

Uninstall behavior:
- Stop the client process.
- Remove Task Scheduler entry `Secret Grounds Client` or current-user Startup launcher.
- Preserve or remove local `config.json` and recovery state based on operator choice.

Log locations:
- `logs/client.log`
- `logs/client-console.log`
- `logs/client-error.log`
- `client_session_recovery.json`

Config locations:
- Installed client folder: `config.json` beside `SecretGroundsClient.exe`.
- Source/development fallback: `client/config.json`.

Per-installation values:
- `server_host`
- `server_port`
- `pc_name` station ID
- `display_name`
- `auto_connect`
- `reconnect_interval_seconds`
- `request_timeout_seconds`

Upgrade behavior:
- Stop the client.
- Preserve `config.json` and recovery state.
- Replace executable/runtime files.
- Restart and validate registration, heartbeat, WebSocket, and reconnect.

## Client Executable Assessment

Recommended approach:
- Build the customer client as a standalone Windows executable using PyInstaller first.
- Customer PCs should not need Python, VS Code, Git, Node.js, npm, or source code.
- Keep `config.json` external beside the executable so every station receives a generated identity.

Verification checklist for the executable:
- Starts without source tree.
- Loads external `config.json`.
- Rejects blank or duplicate station IDs clearly.
- Registers with the cashier backend.
- Sends heartbeat.
- Connects command WebSocket.
- Reconnects after backend restart.
- Shows lock screen and customer overlay.
- Keeps shutdown/restart/sleep commands gated by existing command safety checks.
- Writes client logs.

Current build script:
- `scripts/build-client-executable.ps1`
- Requires PyInstaller in `.venv`.

## Cashier Packaging Assessment

Recommended approach:
- Use a professional Windows installer such as WiX Toolset, Advanced Installer, or Inno Setup.
- For cashier/server, prefer WiX or Advanced Installer if Windows services, firewall rules, Start Menu entries, and upgrade preservation need strong control.
- For the client-only installer, Inno Setup is sufficient if the package is a standalone executable plus config wizard.

FastAPI backend:
- Packaged as a private Python runtime (`runtime\python\`, built by
  `scripts\prepare-backend-runtime.ps1` from the official embeddable
  distribution) plus app files. A later milestone may move to a built
  executable if startup and dynamic imports are verified.

Next.js dashboard:
- Ship a production build, not development sources.
- Prefer Next.js standalone output with a bundled private Node runtime.
- Never require `npm run dev` on an installed cashier PC.

SQLite database:
- Installer creates `data/` and `backups/`.
- First startup creates or migrates `data/secret_grounds.db`.
- Upgrades preserve data by default.

PowerShell deployment scripts:
- Keep as support/admin entry points.
- Installer-created startup entries should call the role-specific scripts, not development terminals.

## Remaining Installer Blockers

- Administrator/UAC path is needed to install the Private-profile inbound firewall rule for TCP 18001.
- Task Scheduler registration was denied in the current non-admin context; installer must run elevated or use the current-user Startup fallback.
- PyInstaller availability and executable behavior must be verified before committing to standalone client packaging.
- Next.js standalone packaging should be enabled and verified before final cashier installer build.
