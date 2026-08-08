import os
import subprocess
import sys

from client.client import main
from client.customer_overlay import run_customer_overlay_gui_smoke
from client.lock_screen import run_lock_screen_gui_smoke
from client.tk_runtime import verify_tk_runtime


if __name__ == "__main__":
    if os.getenv("SECRET_GROUNDS_CLIENT_GUI_SMOKE", "").strip().lower() in {"1", "true", "yes", "on"}:
        smoke_target = os.getenv("SECRET_GROUNDS_CLIENT_GUI_SMOKE_TARGET", "").strip().lower()
        if smoke_target == "":
            for target in ("lock", "overlay"):
                child_env = os.environ.copy()
                child_env["SECRET_GROUNDS_CLIENT_GUI_SMOKE_TARGET"] = target
                result = subprocess.run(
                    [sys.executable],
                    env=child_env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if result.stdout:
                    print(result.stdout, end="")
                if result.stderr:
                    print(result.stderr, end="", file=sys.stderr)
                if result.returncode != 0:
                    raise SystemExit(result.returncode)
            print("Packaged lock-screen and customer-overlay GUI smoke passed.")
        elif smoke_target == "lock":
            info = verify_tk_runtime(create_root=False)
            print(
                "Tcl/Tk runtime ready: "
                f"python={info.python_executable}; "
                f"tcl={info.tcl_version}; tk={info.tk_version}; "
                f"tcl_library={info.tcl_library}; tk_library={info.tk_library}; "
                f"frozen={info.frozen}; packaged_root={info.packaged_root}"
            )
            run_lock_screen_gui_smoke()
            print("Packaged lock-screen GUI smoke passed.")
        elif smoke_target == "overlay":
            run_customer_overlay_gui_smoke()
            print("Packaged customer-overlay GUI smoke passed.")
        else:
            raise SystemExit(f"Unknown SECRET_GROUNDS_CLIENT_GUI_SMOKE_TARGET: {smoke_target}")
        # Two separate Tk() interpreters, each created on its own background
        # thread (one per smoke check), cannot safely share one frozen
        # process. The parent smoke process above fans out into target
        # processes, then exits immediately before Tcl finalization can run
        # from the wrong thread. This diagnostic-only branch is gated behind
        # SECRET_GROUNDS_CLIENT_GUI_SMOKE and is never reached by the real
        # client.
        sys.stdout.flush()
        os._exit(0)
    else:
        main()
