# Secret Grounds Client Windows Startup

The production client PC should start `client/client.py` automatically when Windows boots.

## Recommended first setup

1. Install Python on the client PC and make sure it can run the project virtual environment.
2. Put the Secret Grounds project in a stable local folder, for example `C:\SecretGrounds\secret-grounds-system`.
3. Run the client once manually so `client/config.json` contains the correct server URL, PC name, and local settings.
4. Confirm the client registers, opens the command WebSocket, and stays locked when no session exists.

## Startup shortcut

Create a shortcut in:

`shell:startup`

Use this target, adjusted for the actual install path:

```text
C:\SecretGrounds\secret-grounds-system\.venv\Scripts\python.exe C:\SecretGrounds\secret-grounds-system\client\client.py
```

Set the shortcut's **Start in** folder to:

```text
C:\SecretGrounds\secret-grounds-system
```

After rebooting the PC, the client should:

- load `client/config.json`
- register with the server
- open the command WebSocket
- remain locked if no prepared or active session exists
- automatically activate a prepared session after Wake-on-LAN boot

## Customer overlay

The active-session customer overlay uses Tkinter. Install a Python build that includes Tcl/Tk support on each client PC.

Optional overlay settings:

```text
ENABLE_CUSTOMER_OVERLAY=true
CUSTOMER_OVERLAY_POSITION=top-right
```

Supported positions are `top-left`, `top-right`, `bottom-left`, and `bottom-right`.

Reminder banners fire once each at 15/10/5/4/3/2/1 minutes remaining. Alongside the on-screen banner, the client speaks the same reminder aloud (Windows' built-in text-to-speech, no internet required). Turn the spoken part off independently if it's too chatty over game audio without losing the plain 30-second expiration beep:

```text
SECRET_GROUNDS_REMINDER_VOICE_ENABLED=false
SECRET_GROUNDS_EXPIRATION_SOUND_ENABLED=false
```

## Deployment warning

Before production deployment, set:

```text
ENABLE_DEV_ESCAPE=false
```

The development lock-screen escape is intentionally enabled during development and must be disabled for shop use.
