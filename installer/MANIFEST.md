# Secret Grounds Installer Prototype Manifest

> **`SecretGroundsSetup-1.0.0-rc.1.exe` is obsolete and must not be distributed.** It predates the Tcl/Tk packaging fix (broken Tkinter in the packaged client) and predates upgrade-safe Customer Computer configuration handling. Use `1.0.0-rc.2` or later.

## Cashier Computer

Installs:

- `VERSION`
- `ARCHITECTURE.md`
- `config\.env.example`
- `server\*.py`
- `.venv\`
- `dashboard\package.json`
- `dashboard\package-lock.json`
- `dashboard\next.config.ts`
- `dashboard\.next\`
- `dashboard\node_modules\`
- `operator-shortcuts\`
- cashier production scripts

Creates at install or first run:

- `config\.env`
- `data\`
- `backups\`
- `logs\`
- Private-profile-only firewall rule: `Secret Grounds Backend Private`
- Scheduled task: `Secret Grounds Cashier Stack`

## Customer Computer

Installs:

- `VERSION`
- `client\SecretGroundsClient.exe`
- `client\_internal\`
- `client\_internal\_tcl_data\`
- `client\_internal\_tk_data\`
- `client\_internal\tkinter\`
- `client\_internal\_tkinter.pyd`
- `client\_internal\tcl86t.dll`
- `client\_internal\tk86t.dll`
- `client\config.example.json`
- client production scripts, including `scripts\client-config-tool.ps1`

Creates:

- `client\config.json`
- `client\config-backups\` (timestamped backups made before any explicit configuration change or when an existing config is found invalid)
- optional scheduled task: `Secret Grounds Client`

Upgrade behavior:

- If a valid `client\config.json` already exists, Setup detects it before installing, prefills the wizard from it, defaults to preserving it unchanged, and never overwrites it with wizard defaults. Unattended/silent re-runs preserve it automatically.
- If the installer user explicitly edits the prefilled fields, the previous config is backed up before the new values are written.
- If the existing `client\config.json` is invalid, Setup backs it up and exits without making changes rather than overwriting it silently.

## Preserved User Data

- `config\`
- `data\`
- `backups\`
- `logs\`
