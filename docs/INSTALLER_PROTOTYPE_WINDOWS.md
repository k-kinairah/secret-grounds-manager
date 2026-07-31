# Secret Grounds Windows Installer Prototype

This prototype uses Inno Setup and builds a single role-selecting installer:

`dist/installer/SecretGroundsSetup-1.0.0-rc.2.exe`

> **RC.1 is obsolete and must not be distributed.** `SecretGroundsSetup-1.0.0-rc.1.exe` was built before the Tcl/Tk packaging fix (bundled a client with a broken Tkinter runtime) and before the Customer Computer upgrade-safe configuration handling described below. Only RC.2 and later should be used for Customer Computer installs or upgrades.

## Build Prerequisites

- Windows x64.
- Inno Setup 6 with `ISCC.exe`.
- Prepared cashier `.venv` with backend dependencies.
- `dashboard\node_modules` and a production `.next` build.
- PyInstaller client bundle at `dist\SecretGroundsClient\SecretGroundsClient.exe`.

Build command:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\build-windows-installer.ps1
```

## Role Selection

The installer asks for one role:

- Cashier Computer: installs backend runtime, dashboard runtime, scripts, config/data/backups/log folders, shortcuts, Private-profile firewall rule, and cashier autostart.
- Customer Computer: installs only the PyInstaller client folder, client config helper, client launcher, optional client autostart, and uninstall support.

The customer client is not installed for cashier role by default.

## Cashier Defaults

- Backend port: `18001`
- Dashboard port: `3001`
- Database: `data\secret_grounds.db`
- Backups: `backups\`
- Logs: `logs\`
- Config: `config\.env`

`scripts\initialize-cashier-config.ps1` generates a cryptographically secure 32-byte deployment secret on first install and preserves an existing valid `config\.env` during upgrades.

## Client Setup

The client setup page asks for:

- Station ID
- Display name
- Cashier server host/IP
- Server port, default `18001`
- Start automatically with Windows
- Offline preparation override

Online validation checks `/healthz`, version compatibility, and station ID availability before saving config. Offline preparation warns the installer operator and saves config without contacting the cashier.

## Data Safety

Persistent cashier data:

- `config\`
- `data\`
- `backups\`
- relevant `logs\`

The uninstaller defaults to retaining those folders. It prompts before permanent deletion.

## Included/Excluded Manifest

Cashier includes:

- `server\*.py`
- `.venv\`
- `dashboard\.next\`
- `dashboard\node_modules\`
- `dashboard\.next\dev` and `dashboard\.next\cache`
- selected production scripts
- `operator-shortcuts\`
- `VERSION`

Client includes:

- `dist\SecretGroundsClient\*`
- selected client scripts
- `client\config.example.json`
- `VERSION`

Excluded:

- `.git`
- tests
- source dashboard `src`
- development caches where practical
- local `config\.env`
- source-tree databases and backups
- log files as installer payload

## Known Prototype Limitations

- The cashier role currently ships `.venv` and `dashboard\node_modules` as runtime folders. A later installer should use slimmer packaged runtimes.
- Inno Setup must be installed on the build machine.
- Firewall and Task Scheduler setup request elevation through the installer.
- Clean two-PC field validation is still required.
