from __future__ import annotations

import ctypes
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Iterable


TCL_LIBRARY_ENV = "TCL_LIBRARY"
TK_LIBRARY_ENV = "TK_LIBRARY"
CLIENT_LOG_ENV = "SECRET_GROUNDS_CLIENT_LOG"
_TCL_DLL_HANDLE: ctypes.WinDLL | None = None
_TK_STARTUP_LOCK = Lock()


class TkRuntimeError(RuntimeError):
    """Raised when the bundled Tcl/Tk runtime cannot initialize."""


@dataclass(frozen=True)
class TkRuntimeInfo:
    python_executable: str
    python_version: str
    tcl_version: str | None
    tk_version: str | None
    tcl_library: str
    tk_library: str
    tcl_init: str
    tk_init: str
    packaged_root: str | None
    frozen: bool


def configure_tk_runtime() -> tuple[Path, Path]:
    """Point Tkinter at bundled Tcl/Tk data before importing tkinter."""
    tcl_library = _find_tcl_library()
    tk_library = _find_tk_library()

    _initialize_tcl_executable()
    os.environ[TCL_LIBRARY_ENV] = str(tcl_library)
    os.environ[TK_LIBRARY_ENV] = str(tk_library)
    _add_packaged_tkinter_to_path()
    return tcl_library, tk_library


def verify_tk_runtime(*, create_root: bool = True) -> TkRuntimeInfo:
    try:
        with _TK_STARTUP_LOCK:
            tcl_library, tk_library = configure_tk_runtime()
            import _tkinter
            import tkinter as tk
            root = tk.Tk() if create_root else tk.Tcl()
        try:
            tcl_patchlevel = root.tk.call("info", "patchlevel")
            tk_patchlevel = root.tk.call("package", "require", "Tk") if create_root else None
        finally:
            if create_root:
                root.withdraw()
                root.update_idletasks()
                root.destroy()
            else:
                # A bare tk.Tcl() (useTk=False) has no destroy() -- that's a
                # Tk widget command, unavailable without Tk loaded. Force
                # its refcount to zero and collect it here, synchronously,
                # on this same thread, rather than leaving it for whatever
                # later GC pass happens to run -- possibly on a different
                # thread while other code is creating/destroying real Tk()
                # roots on background threads, which is exactly the
                # mismatch that trips Tcl's "async handler deleted by the
                # wrong thread" panic at process exit.
                del root
                import gc

                gc.collect()
    except Exception as error:
        raise TkRuntimeError(_build_failure_message(error)) from error

    return TkRuntimeInfo(
        python_executable=sys.executable,
        python_version=sys.version.replace("\n", " "),
        tcl_version=tcl_patchlevel or getattr(_tkinter, "TCL_VERSION", None),
        tk_version=tk_patchlevel or getattr(_tkinter, "TK_VERSION", None),
        tcl_library=str(tcl_library),
        tk_library=str(tk_library),
        tcl_init=str(tcl_library / "init.tcl"),
        tk_init=str(tk_library / "tk.tcl"),
        packaged_root=str(_packaged_root()) if _packaged_root() else None,
        frozen=bool(getattr(sys, "frozen", False)),
    )


def create_tk_root():
    with _TK_STARTUP_LOCK:
        configure_tk_runtime()
        import tkinter as tk

        return tk, tk.Tk()


def log_tk_startup_error(context: str, error: BaseException) -> None:
    message = f"{context}: {error}"
    print(message)
    try:
        log_path = _client_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as log_file:
            log_file.write("Secret Grounds Tcl/Tk startup failure\n")
            log_file.write(message + "\n")
            log_file.write(_diagnostics_text())
            log_file.write(traceback.format_exc())
            log_file.write("\n")
    except OSError as log_error:
        print(f"Could not write Tcl/Tk startup log: {log_error}")


def show_native_startup_error(context: str, error: BaseException) -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"{context}\n\n{error}",
            "Secret Grounds Client UI Startup Error",
            0x00000010,
        )
    except Exception:
        pass


def _initialize_tcl_executable() -> None:
    if sys.platform != "win32":
        return

    dll_path = _find_tcl_dll()
    if dll_path is None:
        return

    dll_directory = str(dll_path.parent)
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(dll_directory)

    global _TCL_DLL_HANDLE
    _TCL_DLL_HANDLE = ctypes.WinDLL(str(dll_path))
    _TCL_DLL_HANDLE.Tcl_FindExecutable.argtypes = [ctypes.c_wchar_p]
    previous_tcl_library = os.environ.pop(TCL_LIBRARY_ENV, None)
    previous_tk_library = os.environ.pop(TK_LIBRARY_ENV, None)
    try:
        _TCL_DLL_HANDLE.Tcl_FindExecutable(str(Path(sys.executable).resolve()))
    finally:
        if previous_tcl_library is not None:
            os.environ[TCL_LIBRARY_ENV] = previous_tcl_library
        if previous_tk_library is not None:
            os.environ[TK_LIBRARY_ENV] = previous_tk_library


def _find_tcl_library() -> Path:
    return _first_existing_library(
        env_name=TCL_LIBRARY_ENV,
        init_file="init.tcl",
        names=("_tcl_data", "tcl8.6"),
        nested_names=(("tcl", "tcl8.6"),),
    )


def _find_tk_library() -> Path:
    return _first_existing_library(
        env_name=TK_LIBRARY_ENV,
        init_file="tk.tcl",
        names=("_tk_data", "tk8.6"),
        nested_names=(("tcl", "tk8.6"),),
    )


def _first_existing_library(
    *,
    env_name: str,
    init_file: str,
    names: tuple[str, ...],
    nested_names: tuple[tuple[str, ...], ...],
) -> Path:
    candidates: list[Path] = []
    env_value = os.getenv(env_name)
    if env_value:
        candidates.append(Path(env_value))

    for root in _runtime_roots():
        candidates.extend(root / name for name in names)
        candidates.extend(root.joinpath(*parts) for parts in nested_names)

    for candidate in candidates:
        if (candidate / init_file).is_file():
            return candidate

    expected = "\n".join(f" - {candidate / init_file}" for candidate in candidates)
    raise TkRuntimeError(
        f"Could not find Tcl/Tk startup file {init_file}. Checked:\n{expected}"
    )


def _find_tcl_dll() -> Path | None:
    for root in _runtime_roots():
        for candidate in (
            root / "tcl86t.dll",
            root / "DLLs" / "tcl86t.dll",
            root / "_internal" / "tcl86t.dll",
        ):
            if candidate.is_file():
                return candidate
    return None


def _add_packaged_tkinter_to_path() -> None:
    packaged_root = _packaged_root()
    if packaged_root is None:
        return
    if not (packaged_root / "tkinter" / "__init__.py").is_file():
        return
    packaged_root_text = str(packaged_root)
    if packaged_root_text not in sys.path:
        sys.path.insert(0, packaged_root_text)


def _runtime_roots() -> Iterable[Path]:
    seen: set[Path] = set()
    roots: list[Path] = []

    meipass = _packaged_root()
    if meipass is not None:
        roots.extend([meipass, meipass / "_internal"])

    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        roots.extend([exe_dir, exe_dir / "_internal"])

    roots.extend(
        [
            Path(sys.base_prefix).resolve(),
            Path(sys.prefix).resolve(),
            Path(__file__).resolve().parents[1],
        ]
    )

    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        yield root


def _packaged_root() -> Path | None:
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    return Path(meipass).resolve()


def _client_log_path() -> Path:
    configured = os.getenv(CLIENT_LOG_ENV)
    if configured:
        return Path(configured)
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().with_name("client-tk-startup.log")
    return Path(__file__).resolve().parents[1] / "logs" / "client.log"


def _build_failure_message(error: BaseException) -> str:
    return (
        "Tcl/Tk runtime self-check failed before the lock screen could open. "
        f"{error}\n{_diagnostics_text()}"
    )


def _diagnostics_text() -> str:
    lines = [
        f"python_executable={sys.executable}",
        f"python_version={sys.version.replace(chr(10), ' ')}",
        f"frozen={bool(getattr(sys, 'frozen', False))}",
        f"packaged_root={_packaged_root()}",
        f"{TCL_LIBRARY_ENV}={os.getenv(TCL_LIBRARY_ENV)}",
        f"{TK_LIBRARY_ENV}={os.getenv(TK_LIBRARY_ENV)}",
    ]
    for root in _runtime_roots():
        lines.append(f"runtime_root={root}")
    return "\n".join(lines) + "\n"
