# Shipping a Client Update

How to push a new client build out to every gaming PC without visiting them.
No internet access is required on the Cashier PC or any gaming PC.

## 1. Build and package the release

On a dev machine, from the repo root, on the branch you want to ship:

```
scripts\build-client-executable.ps1
scripts\stage-client-update.ps1
```

`stage-client-update.ps1` reads the current `VERSION`, zips
`dist\SecretGroundsClient\`, hashes it, and writes both files to
`dist\updates\`:

- `SecretGroundsClientUpdate-<version>.zip`
- `SecretGroundsClientUpdate-<version>.json`

Bump `VERSION` first if you haven't -- the server only stages a version
that's newer than its own.

## 2. Copy both files to the Cashier PC

Move both files (USB drive, network share, whatever you already use) into:

```
<install>\updates\incoming\
```

on the Cashier PC. Both files, matching names, same folder. Don't rename
either one.

## 3. Let the server pick it up

The backend scans `updates\incoming\` every 5 minutes on its own. To skip
the wait, sign in as owner/admin and call:

```
POST /system/updates/rescan
```

Either way, check it landed:

```
GET /system/updates
```

`staged_update` should show the new version. If it doesn't, `last_scan_error`
says why (usually a sha256 mismatch -- rebuild rather than hand-editing the
manifest, or a version that isn't actually newer than the server's own).

## 4. Rollout is automatic from here

- Every gaming PC connected right now gets pushed the update over its
  command WebSocket immediately.
- A PC that's offline at that moment just finds the same staged update
  itself the next time it reconnects -- nothing to retry manually.
- Each client downloads and verifies it in the background without
  interrupting whatever it's doing.
- It only applies -- swaps the files and restarts -- once that specific PC
  has no active session and is sitting on the lock screen. A PC mid-session
  updates the moment the customer's time runs out and the screen locks, not
  before.

## 5. Confirm a PC actually updated

Easiest check: open `<install>\VERSION` on that gaming PC and confirm it
matches. Or check `<install>\logs\client.log` on that PC for a line like:

```
Applied client update <version>; previous version backed up to ...
```

## 6. If something goes wrong

The previous build is always kept at `client\_previous-version\` on that PC
(overwritten by the *next* update, nothing else touches it) -- restore it by
hand if a rollout needs to be undone.

If a specific PC never seems to pick it up: check it's actually reaching
the lock screen (an update never applies mid-session by design), and check
that PC's `client.log` for a download or sha256-verification failure.
