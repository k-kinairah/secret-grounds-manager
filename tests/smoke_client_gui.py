import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from client.customer_overlay import run_customer_overlay_gui_smoke
from client.lock_screen import run_lock_screen_gui_smoke
from client.tk_runtime import verify_tk_runtime


def main() -> None:
    info = verify_tk_runtime(create_root=False)
    print(
        "Tk runtime verified: "
        f"python={info.python_executable}; "
        f"tcl={info.tcl_version}; tk={info.tk_version}; "
        f"tcl_library={info.tcl_library}; tk_library={info.tk_library}; "
        f"frozen={info.frozen}"
    )
    run_lock_screen_gui_smoke()
    run_customer_overlay_gui_smoke()
    print("Client GUI smoke tests passed.")


if __name__ == "__main__":
    main()
    # Two sequential Tk() interpreters, each created on its own background
    # thread (one per smoke check above), leave a Tcl async handler that
    # normal Python interpreter finalization tries to clean up from the main
    # thread -- a different thread than created it. Tcl treats that mismatch
    # as fatal ("async handler deleted by the wrong thread") and aborts with
    # a nonzero exit code even though both checks already passed. Exiting
    # immediately, before that finalization runs, sidesteps it.
    sys.stdout.flush()
    os._exit(0)
