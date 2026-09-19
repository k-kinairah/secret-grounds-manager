# Secret Grounds Architecture

## Decision #1

The Admin PC acts as the server.

Reason:
A single source of truth for sessions, billing, and connected PCs.

---

## Decision #2

Each gaming PC runs a persistent client.

Reason:
The client can continue running even if the server temporarily disconnects.

---

## Decision #3

Clients register themselves when they start.

Reason:
The server automatically discovers available gaming PCs.

---

## Decision #4

Clients send a heartbeat every 5 seconds.

Reason:
The server can detect disconnected PCs within about 15 seconds without excessive network traffic.

---

## Decision #5

The server stores only the latest state of each PC.

Reason:
Fast lookup using the PC name as the unique identifier.

## Decision #6: WebSockets for live commands

The server and each gaming PC maintain a persistent WebSocket connection.

Reason:
Commands such as start session, add time, lock, and shutdown must be delivered immediately without waiting for client polling.

---

## Decision #7: Every command has a unique command ID

The server assigns a unique ID to every command.

Reason:
The server must track whether a command is pending, received, completed, or failed.

---

## Decision #8: Clients acknowledge command receipt and execution

The client first confirms that it received the command, then reports whether execution completed or failed.

Reason:
Receiving a command does not guarantee that the requested action succeeded.

---

## Decision #9: Commands are not queued for offline clients

If a client is offline or does not have an active WebSocket connection, the command is rejected.

Reason:
Old commands such as shutdown, restart, unlock, or add time could execute unexpectedly after reconnection.

---

## Decision #10: Clients lock locally when idle

Each client owns a local lock-screen controller. The client starts locked, unlocks only after a session timer is initialized, and locks again before notifying the server that a timer expired.

Reason:
The PC should be unavailable whenever there is no active local session, even if the server is temporarily unreachable.

---

## Decision #11: Local recovery is the next milestone

This milestone includes a recovery abstraction but does not persist local session state yet. The next milestone should store the active session_id and scheduled_end_at locally.

Reason:
Crash recovery needs a small durable client state file, but adding it separately keeps the first lock-screen milestone safer.

---

## Development warning

The lock screen currently supports ENABLE_DEV_ESCAPE for development. Disable it before deployment.

---

## Decision #12: Clients reconcile local recovery with the server

The client stores one small JSON recovery record for active or pending-completion sessions. On startup and WebSocket reconnect, it compares the local record with the server's active session state before unlocking.

Reason:
A paid session can survive a client restart, while mismatched or server-only sessions stay locked until an operator investigates.

---

## Decision #13: Session completion is confirmed over WebSocket

When a local timer expires, the client locks first and sends session_finished. If delivery or confirmation is unavailable, it keeps a pending-completion recovery record and resends after reconnect.

Reason:
The PC should lock immediately at expiration, and server completion recording must remain idempotent across reconnects and duplicate sends.

## Decision #10: Prove commands with a harmless ping first

The first WebSocket command is a ping command that returns pong.

Reason:
It verifies connection, delivery, acknowledgement, execution, and failure handling before implementing paid sessions or operating-system controls.

## Decision #11

The server owns every session.

Reason:
The cashier controls purchased time and billing.

---

## Decision #12

The client owns the countdown timer.

Reason:
The timer continues even if the server temporarily disconnects.

---

## Decision #13

Only one active session may exist per PC.

Reason:
Prevents accidental duplicate sessions.

---

## Decision #14

The server sends time additions as additional seconds.

Reason:
Adding seconds avoids overwriting the remaining time incorrectly.

---

## Decision #15

The client reports only significant session events.

Reason:
Avoid unnecessary network traffic while keeping the server informed.

## Decision #18: Client PCs are locked by default

A client without a confirmed active session displays the lock screen.

Reason:
Restarting the client or PC must not provide free computer access.

---

## Decision #19: Active sessions continue during temporary network loss

A client with a valid local active session continues its countdown when disconnected from the server.

Reason:
Customers must not lose paid time because of a temporary LAN interruption.

---

## Decision #20: Session expiration locks locally before server confirmation

When the timer reaches zero, the client immediately displays the lock screen and then reports completion to the server.

Reason:
A server or network failure must not leave an expired PC usable.

---

## Decision #21: Client recovery uses scheduled end time

The client stores the session ID and scheduled end timestamp for crash recovery.

Reason:
Remaining time can be reconstructed accurately after a client restart.

---

## Decision #22: Uncertain session state fails closed

If the client cannot confirm that its recovered session is valid, it remains locked.

Reason:
The system should prefer preventing unpaid access over incorrectly unlocking a PC.

---

## Decision #23: Emergency maintenance access is protected

The lock screen includes an administrator-only recovery mechanism protected by a hashed PIN.

Reason:
Authorized staff must always be able to regain control of a client PC.

---

## Decision #24: Client updates are staged on the Cashier PC, not fetched from the internet

An operator builds a release, then drops its zip and a manifest (version, sha256, size) into `updates\incoming\` on the Cashier PC (`scripts\stage-client-update.ps1` produces both). The backend validates and promotes that drop into `updates\staged\` on its own; it never reaches out to the internet to look for one.

Reason:
The shop's own LAN, not internet access, is the one thing every deployment can rely on. Requiring the operator to physically place the release also doubles as the authorization step -- nothing auto-installs anything nobody chose to hand the server.

---

## Decision #25: The server pushes updates over the same command WebSocket; clients also poll on reconnect

Once an update is staged, the server sends every currently-connected customer PC an `update_available` command (payload: version, sha256, size). A PC that is offline at that moment, or whose push doesn't get delivered, still discovers the same staged update itself the next time its command WebSocket (re)connects, via `GET /updates/client/manifest`.

Reason:
Decision #9 already established that commands are not queued for offline clients. Update delivery cannot depend on every PC being online at the exact moment the operator stages a release, so the push is best-effort and reconnect-time polling is the real guarantee.

---

## Decision #26: A client update is downloaded and verified in the background, but only ever applied while the PC is idle

`UpdateManager` (client\update_manager.py) downloads the staged package over HTTP, verifies its sha256, and extracts it into `updates\staged\<version>\` -- all while the client keeps handling sessions normally. Only once there is no active local session (Decision #18: that already means the lock screen is showing) does the client write `updates\pending-apply.json` and exit the process.

Reason:
Windows keeps a running exe's own files open, so this process can never safely replace itself anyway. Gating the exit on "idle" the same way Decisions #18-#22 already gate unlocking means an update can never interrupt a paying customer's session, and a session that starts in the split second before the exit is no different from the customer killing the client in Task Manager -- already a case the client recovers from cleanly.

---

## Decision #27: The process supervisor performs the actual file swap

`scripts\run-client-supervised.ps1` already restarts the client unconditionally whenever it exits, so it is the one place guaranteed to run with the old client's files completely unlocked. On seeing `updates\pending-apply.json` after an exit, it moves the current `SecretGroundsClient.exe`/`_internal\` into `client\_previous-version\` and moves the staged build into their place before relaunching.

Reason:
Building a separate updater process to do this would duplicate a restart-and-recover mechanism the supervisor already provides for a different reason (a customer ending the client in Task Manager). Reusing it means the update path is exercised by the same code as ordinary crash recovery, not a rarely-run parallel one.
