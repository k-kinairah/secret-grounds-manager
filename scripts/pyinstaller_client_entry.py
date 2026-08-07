import os
import sys

from client.client import main
from client.customer_overlay import run_customer_overlay_gui_smoke
from client.lock_screen import run_lock_screen_gui_smoke
from client.tk_runtime import verify_tk_runtime


if __name__ == "__main__":
    if os.getenv("SECRET_GROUNDS_CLIENT_GUI_SMOKE", "").strip().lower() in {"1", "true", "yes", "on"}:
        info = verify_tk_runtime(create_root=False)
        print(
            "Tcl/Tk runtime ready: "
            f"python={info.python_executable}; "
            f"tcl={info.tcl_version}; tk={info.tk_version}; "
            f"tcl_library={info.tcl_library}; tk_library={info.tk_library}; "
            f"frozen={info.frozen}; packaged_root={info.packaged_root}"
        )
        run_lock_screen_gui_smoke()
        run_customer_overlay_gui_smoke()
        print("Packaged lock-screen and customer-overlay GUI smoke passed.")
        # Two separate Tk() interpreters, each created on its own background
        # thread (one per smoke check), leave a Tcl async handler that a
        # normal Python interpreter finalization tries to clean up from the
        # main thread -- a different thread than created it. Tcl treats that
        # mismatch as fatal ("async handler deleted by the wrong thread") and
        # aborts the process with a nonzero exit code, even though both
        # checks above already completed successfully. Exiting immediately,
        # before that finalization runs, sidesteps it; this branch is a
        # diagnostic-only entry point (gated behind SECRET_GROUNDS_CLIENT_GUI_SMOKE),
        # never reached by the real client, so skipping normal shutdown here
        # has no production effect.
        sys.stdout.flush()
        os._exit(0)
    else:
        main()
