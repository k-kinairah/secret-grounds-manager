# Secret Grounds Windows Deployment

This milestone uses a portable deployment layout. Do not install Secret Grounds under `Program Files` unless a future installer also provisions write permissions.

## Current Manual Startup

Development startup is separate from production:

- Backend development: run Uvicorn against `server.main:app`, often with reload during development.
- Dashboard development: `npm.cmd run dev` in `dashboard/`.
- Dashboard production: `npm.cmd run build`, then `npm.cmd run start` in `dashboard/`.
- Client development: run `python -m client.client` with `client/config.json`.

The cashier/server PC owns the backend, production dashboard, SQLite database, backups, and server logs. Customer PCs run only the Secret Grounds client.

## Portable Layout

Recommended runtime layout:

```text
secret-grounds/
  server/
  dashboard/
  client/
  data/secret_grounds.db
  backups/
  logs/server.log
  logs/dashboard.log
  logs/client.log
  config/.env
  scripts/
```

Runtime files belong in `data/`, `backups/`, `logs/`, and private `config/.env`.

## Cashier/Server Installation

1. Install Python and Node.js, or copy a prepared portable folder that already includes `.venv` and dashboard dependencies.
2. Copy `config/.env.example` to `config/.env`.
3. Edit `config/.env`; set ports, `SECRET_GROUNDS_DB_PATH`, `SECRET_GROUNDS_BACKUP_DIR`, `NEXT_PUBLIC_FASTAPI_BASE_URL`, CORS origins, and a real `SECRET_GROUNDS_DEPLOYMENT_SECRET`.
4. Keep `SECRET_GROUNDS_TEST_MODE=false` for shop use.
5. Run `powershell -ExecutionPolicy Bypass -File scripts\install-dependencies.ps1`.
6. Run `powershell -ExecutionPolicy Bypass -File scripts\build-dashboard.ps1`.
7. Run `powershell -ExecutionPolicy Bypass -File scripts\check-deployment.ps1`.
8. Start production: `powershell -ExecutionPolicy Bypass -File scripts\start-secret-grounds.ps1`.
9. Check status: `powershell -ExecutionPolicy Bypass -File scripts\status-secret-grounds.ps1`.
10. Stop or restart with `scripts\stop-secret-grounds.ps1` and `scripts\restart-secret-grounds.ps1`.

The production dashboard can be opened on the cashier PC at `http://127.0.0.1:3001`. For LAN access, set `NEXT_PUBLIC_FASTAPI_BASE_URL` and `SECRET_GROUNDS_CORS_ORIGINS` to the cashier PC LAN address, rebuild the dashboard, and open the configured LAN dashboard URL.

## Cashier Auto-Start

Supported scheduled task name: `Secret Grounds Cashier Stack`.

The GUI installer's "Start Secret Grounds automatically when this computer starts" option registers this task with the **at-startup** trigger, running as **SYSTEM** (RunLevel Highest), so the backend and dashboard come up on boot without anyone signing in. The at-sign-in variant instead runs as the interactive user, who has a session by then.

- Install at system startup (installer default): `powershell -ExecutionPolicy Bypass -File scripts\install-cashier-autostart.ps1 -AtStartup`
- Install after sign-in instead: `powershell -ExecutionPolicy Bypass -File scripts\install-cashier-autostart.ps1`
- Remove: `powershell -ExecutionPolicy Bypass -File scripts\remove-cashier-autostart.ps1`

The task starts in the Secret Grounds folder, uses the production startup script, and ignores duplicate task instances.

## Customer PC Installation

The installer's **Customer Computer** role has three modes (second wizard step, "Customer Computer Setup"):

- **Fixed Station** - a normal, individually installed gaming PC. The wizard asks for Station ID, display name, cashier IP, and port, validates the server (`/healthz` version + `/client-registration/check`), and writes `config.json` with that `pc_name`.
- **Diskless / Automatic** - preparing a shared/master image. The wizard asks only for cashier IP and port, does **not** ask for a Station ID, and writes `config.json` with `"pc_name": ""`. Each PC cloned from the image resolves its own identity from its wired NIC MAC via `/stations/resolve` on first boot and is **assigned once from the dashboard** (Setup/Layout -> Station Assignment). The installer configures client auto-start on the image but does not launch the client; capture/clone the image afterward. Offline preparation is still available when the cashier is unreachable.
- **Diskless / Adopt PC name** - same shared/master image flow, but `config.json` is written with `"pc_name": "@hostname"`. On first boot each seat reports its Windows computer name (the per-seat name a diskless system such as iCafe8/CCBoot already assigns) and the cashier **auto-binds** it to a station with a matching name, with **no dashboard step per PC**. Requirements: (a) the diskless system must give each seat a real, distinct computer name (`PC14`, `PC-14`, ... - Windows placeholders like `DESKTOP-xxxx` are ignored); (b) a station with that name must already exist under Setup/Layout (`PC-01`..`PC-15` are seeded by default; `PC14`/`PC-014` fold to `PC-14`). If the name has no matching station, or is already held by another PC, or the PC was explicitly unassigned, the seat falls back to the "waiting for station assignment" screen and its computer name is shown in **Newly Detected Computers**. Auto-bound stations show a "Bound by: Auto" line and can still be reassigned or unassigned.

None of the modes require hand-editing `config.json`.

### Manual (portable folder, no installer)

1. Copy the client folder and shared deployment scripts, or copy the full portable folder if that is how the shop is staged.
2. Copy `client/config.example.json` to `client/config.json`.
3. Set `server_host` to the cashier/server LAN IPv4 address.
4. Set a unique `pc_name` for that PC (fixed station); leave `pc_name` blank / omit it for a diskless image assigned from the dashboard; or set `pc_name` to `"@hostname"` for a diskless image that adopts each seat's Windows computer name.
5. Set `server_port` to the configured backend port.
6. Test in a safe single-PC setup with `SECRET_GROUNDS_TEST_MODE=true` only when needed and start with `scripts\start-client.ps1 -AllowTestMode`.
7. For production, keep test mode off and run `scripts\start-client.ps1`.
8. Verify the lock screen and overlay behavior.

Supported scheduled task name: `Secret Grounds Client`.

- Install: `powershell -ExecutionPolicy Bypass -File scripts\install-client-autostart.ps1`
- Remove: `powershell -ExecutionPolicy Bypass -File scripts\remove-client-autostart.ps1`

### Customer client self-heals

The `Secret Grounds Client` task runs a supervisor loop (`run-client-supervised.ps1`), not a one-shot launcher. If a customer ends `SecretGroundsClient.exe` in Task Manager, the supervisor relaunches it within ~2 seconds and the lock screen comes back; if they also end the supervisor process, the task's 1-minute repetition trigger brings it back (the client keeps running, still locked, in that gap). The cashier server stays authoritative for session and lock state, so a relaunched client re-locks an idle PC and never adds time to an active session.

To actually stop it (maintenance, editing a diskless image):

```
powershell -ExecutionPolicy Bypass -File scripts\stop-client.ps1
```

That drops a stop sentinel, disables the task, and stops the supervisor and client. Re-enable with `scripts\install-client-autostart.ps1` (or `Enable-ScheduledTask "Secret Grounds Client"; Start-ScheduledTask "Secret Grounds Client"`). Safe Mode also bypasses the supervisor. Uninstall disables the task and drops the sentinel before removing files, so nothing respawns mid-uninstall.

Not covered yet: a user with local **admin** rights can unregister the task and kill it for good — closing that needs running the customer session as a standard (non-admin) account.

## Logs And Diagnostics

- Backend log: `logs/server.log`
- Backend console output: `logs/server-console.log`
- Backend stderr: `logs/server-error.log`
- Dashboard log: `logs/dashboard.log`
- Dashboard stderr: `logs/dashboard-error.log`
- Client log: `logs/client.log`
- Client stderr: `logs/client-error.log`
- PID/status file: `logs/secret-grounds-pids.json`

Server logging rotates according to `SECRET_GROUNDS_LOG_MAX_BYTES` and `SECRET_GROUNDS_LOG_BACKUP_COUNT`.

## Troubleshooting

- Port already in use: run `scripts\status-secret-grounds.ps1`; if the PID file does not own the process, choose different ports or stop the conflicting app.
- Backend unavailable: inspect `logs/server.log` and `logs/server-error.log`, then run `scripts\check-deployment.ps1`.
- Dashboard unavailable: confirm `dashboard\.next\BUILD_ID` exists and inspect `logs/dashboard.log`.
- Client cannot connect: confirm the cashier LAN IP, backend port, Windows Firewall inbound rule for the configured backend port, and matching `client/config.json`.
- Database not writable: move `SECRET_GROUNDS_DB_PATH` to a writable folder such as the portable `data/` directory.
- Invalid secret: edit `config/.env`; scripts do not print secret values.
- Duplicate instance: use `scripts\status-secret-grounds.ps1`; stale PID files are ignored when process identity no longer matches.
- Test mode accidentally enabled: set `SECRET_GROUNDS_TEST_MODE=false`; production startup rejects test mode by default.
- Windows Firewall: allow inbound traffic only for the configured backend and dashboard ports on the shop LAN profile. Do not disable the firewall.

## Safe Manual Retest

1. Stop development servers.
2. Run `scripts\check-deployment.ps1`.
3. Start with `scripts\start-secret-grounds.ps1 -NoBrowser`.
4. Confirm `http://127.0.0.1:18001/healthz`.
5. Confirm the production dashboard loads at `http://127.0.0.1:3001`.
6. Confirm logs are created.
7. Run `scripts\status-secret-grounds.ps1`.
8. Run startup again and confirm duplicate startup is blocked.
9. Run `scripts\restart-secret-grounds.ps1 -NoBrowser`.
10. Run `scripts\stop-secret-grounds.ps1` and confirm unrelated Python/Node processes are untouched.
11. Start `scripts\start-client.ps1 -AllowTestMode` only on the same PC for connectivity verification.
12. Confirm version values match `VERSION`.
13. Temporarily rename `config/.env` and confirm validation fails clearly.
14. Do not execute real power commands during deployment tests.
