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
BUILTIN_LIBRARY = "(built into the Tcl/Tk 9 DLL)"
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


def configure_tk_runtime() -> tuple[Path | None, Path | None]:
    """Point Tkinter at bundled Tcl/Tk data before importing tkinter.

    Tcl/Tk 8.6 (the packaged/installed client, and older Pythons) keeps its
    script library in tcl8.6/_tcl_data folders, which we find and point at.
    Tcl/Tk 9 (e.g. Python 3.14.8 from the Python install manager) keeps it
    inside its own DLL instead; when there is no 8.6-style folder but a
    Tcl 9 DLL is present, both paths are None and Tk finds its own files --
    any leftover TCL_LIBRARY/TK_LIBRARY would only point it somewhere wrong.
    """
    tcl_library = _find_tcl_library()
    tk_library = _find_tk_library()

    _initialize_tcl_executable()
    if tcl_library is not None and tk_library is not None:
        os.environ[TCL_LIBRARY_ENV] = str(tcl_library)
        os.environ[TK_LIBRARY_ENV] = str(tk_library)
    else:
        os.environ.pop(TCL_LIBRARY_ENV, None)
        os.environ.pop(TK_LIBRARY_ENV, None)
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
        tcl_library=str(tcl_library) if tcl_library is not None else BUILTIN_LIBRARY,
        tk_library=str(tk_library) if tk_library is not None else BUILTIN_LIBRARY,
        tcl_init=str(tcl_library / "init.tcl") if tcl_library is not None else BUILTIN_LIBRARY,
        tk_init=str(tk_library / "tk.tcl") if tk_library is not None else BUILTIN_LIBRARY,
        packaged_root=str(_packaged_root()) if _packaged_root() else None,
        frozen=bool(getattr(sys, "frozen", False)),
    )


def _apply_secret_grounds_icon(tk, root) -> None:
    """Give the Tk root (and, via default=/True, every later Toplevel) the
    Secret Grounds icon so the taskbar / Alt-Tab / title bar stop showing the
    stock Tk feather. Best-effort: any failure just leaves the default icon."""
    from client.utils import get_logo_ico_path, get_logo_path

    ico_path = get_logo_ico_path()
    if ico_path is not None:
        try:
            root.iconbitmap(default=str(ico_path))
            return
        except Exception:
            pass
    png_path = get_logo_path()
    if png_path is None:
        return
    try:
        image = tk.PhotoImage(file=str(png_path))
        root.iconphoto(True, image)
        # PhotoImage is dropped by Tk once garbage-collected -- pin it to the
        # root so the icon survives for the window's lifetime.
        root._secret_grounds_icon = image  # noqa: SLF001
    except Exception:
        pass


def create_tk_root():
    with _TK_STARTUP_LOCK:
        configure_tk_runtime()
        import tkinter as tk

        root = tk.Tk()
        _apply_secret_grounds_icon(tk, root)
        return tk, root


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


def _find_tcl_library() -> Path | None:
    return _first_existing_library(
        env_name=TCL_LIBRARY_ENV,
        init_file="init.tcl",
        names=("_tcl_data", "tcl8.6"),
        nested_names=(("tcl", "tcl8.6"),),
    )


def _find_tk_library() -> Path | None:
    return _first_existing_library(
        env_name=TK_LIBRARY_ENV,
        init_file="tk.tcl",
        names=("_tk_data", "tk8.6"),
        nested_names=(("tcl", "tk8.6"),),
    )


def _find_builtin_tcl9_dll() -> Path | None:
    """A Tcl 9 DLL (tcl90.dll), which carries its own script library inside."""
    for root in _runtime_roots():
        for folder in (root, root / "DLLs", root / "_internal"):
            for candidate in sorted(folder.glob("tcl9*.dll")) if folder.is_dir() else ():
                if candidate.is_file() and "tk" not in candidate.name.lower():
                    return candidate
    return None


def _first_existing_library(
    *,
    env_name: str,
    init_file: str,
    names: tuple[str, ...],
    nested_names: tuple[tuple[str, ...], ...],
) -> Path | None:
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

    # No Tcl/Tk 8.6 folder anywhere: fine for Tcl/Tk 9, whose library is inside its DLL.
    if _find_builtin_tcl9_dll() is not None:
        return None

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
