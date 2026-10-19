"""client.tk_runtime finds Tcl/Tk on both layouts:

- Tcl/Tk 8.6 (the packaged/installed client): tcl8.6/_tcl_data folders with
  init.tcl -- found and pointed at via TCL_LIBRARY/TK_LIBRARY, as before.
- Tcl/Tk 9 (e.g. Python 3.14.8 from the Python install manager): no such
  folders, the library is inside tcl90.dll -- Tk must be left to find it.
- Neither: still a clear TkRuntimeError.
"""

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

import client.tk_runtime as tk_runtime  # noqa: E402


def with_roots(roots, check) -> None:
    original = tk_runtime._runtime_roots
    saved = {name: os.environ.get(name) for name in (tk_runtime.TCL_LIBRARY_ENV, tk_runtime.TK_LIBRARY_ENV)}
    tk_runtime._runtime_roots = lambda: iter(roots)  # type: ignore[assignment]
    try:
        for name in saved:
            os.environ.pop(name, None)
        check()
    finally:
        tk_runtime._runtime_roots = original  # type: ignore[assignment]
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_tcl86_layout_is_found_and_used(tmp: Path) -> None:
    root = tmp / "tcl86"
    (root / "tcl" / "tcl8.6").mkdir(parents=True)
    (root / "tcl" / "tk8.6").mkdir(parents=True)
    (root / "tcl" / "tcl8.6" / "init.tcl").write_text("# test", encoding="utf-8")
    (root / "tcl" / "tk8.6" / "tk.tcl").write_text("# test", encoding="utf-8")

    def check() -> None:
        tcl_library, tk_library = tk_runtime.configure_tk_runtime()
        assert tcl_library == root / "tcl" / "tcl8.6", tcl_library
        assert tk_library == root / "tcl" / "tk8.6", tk_library
        assert os.environ[tk_runtime.TCL_LIBRARY_ENV] == str(tcl_library)
        assert os.environ[tk_runtime.TK_LIBRARY_ENV] == str(tk_library)

    with_roots([root], check)


def test_tcl9_dll_without_folders_uses_the_builtin_library(tmp: Path) -> None:
    root = tmp / "tcl9"
    (root / "DLLs").mkdir(parents=True)
    (root / "DLLs" / "tcl90.dll").write_bytes(b"")
    (root / "DLLs" / "tcl9tk90.dll").write_bytes(b"")

    def check() -> None:
        os.environ[tk_runtime.TCL_LIBRARY_ENV] = str(tmp / "stale")  # a leftover must not survive
        assert tk_runtime.configure_tk_runtime() == (None, None)
        assert tk_runtime.TCL_LIBRARY_ENV not in os.environ
        assert tk_runtime.TK_LIBRARY_ENV not in os.environ

    with_roots([root], check)


def test_neither_layout_is_a_clear_error(tmp: Path) -> None:
    root = tmp / "empty"
    root.mkdir()

    def check() -> None:
        try:
            tk_runtime.configure_tk_runtime()
        except tk_runtime.TkRuntimeError as error:
            assert "init.tcl" in str(error)
        else:
            raise AssertionError("expected TkRuntimeError when no Tcl/Tk is present")

    with_roots([root], check)


def test_this_python_really_starts_tcl() -> None:
    # Whatever this machine's Python ships (8.6 folders or Tcl 9 built in), the real check passes.
    info = tk_runtime.verify_tk_runtime(create_root=False)
    assert info.tcl_version, info
    print(f"Tcl {info.tcl_version} via {info.tcl_library}")


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        tmp = Path(directory)
        test_tcl86_layout_is_found_and_used(tmp)
        test_tcl9_dll_without_folders_uses_the_builtin_library(tmp)
        test_neither_layout_is_a_clear_error(tmp)
    test_this_python_really_starts_tcl()
    print("Client Tk runtime smoke tests passed.")


if __name__ == "__main__":
    main()
    sys.stdout.flush()
    os._exit(0)  # same Tcl finalization caveat as smoke_client_gui.py
