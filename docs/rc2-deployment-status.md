# Secret Grounds RC.2 Deployment Status

## Verified Build

Commit: `5941765`

RC.2 has been physically verified across the cashier and customer PC setup.

## Verified Features

- Customer WebView2 lock screen
- Member login
- Member-only Lock PC / Reserve Seat
- Lock confirmation flow
- SESSION LOCKED screen
- PIN unlock to the same active session
- Timer continues while locked
- Cashier Customer Locked status
- Cashier Clear Customer Lock override
- Non-topmost collapsed desktop control
- Heartbeat and WebSocket connectivity
- Existing member/session data preserved during cashier upgrade

## Deployment Notes

The customer PC and cashier backend/dashboard must run compatible RC.2 builds.

The Member Lock feature requires these backend routes:

- `/client/session-lock`
- `/client/session-unlock`
- `/sessions/{session_id}/clear-customer-lock`

A customer-client upgrade without updating the cashier backend can result in `404 Not Found` when using Lock PC.

## Current Status

RC.2 Member Lock acceptance test: PASS

Next planned work:
Standard/VIP pricing and member balance model redesign based on confirmed shop-owner rules.