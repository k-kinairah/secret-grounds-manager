# Secret Grounds Installer Prototype Manifest

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
- `client\config.example.json`
- client production scripts

Creates:

- `client\config.json`
- optional scheduled task: `Secret Grounds Client`

## Preserved User Data

- `config\`
- `data\`
- `backups\`
- `logs\`
