import subprocess
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STOP_SCRIPT = PROJECT_ROOT / "scripts" / "stop-all-for-uninstall.ps1"
DECOY_PYTHON = PROJECT_ROOT / "runtime" / "python" / "python.exe"


def run_powershell(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass"] + args,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def find_decoy_pid() -> int | None:
    result = run_powershell(
        [
            "-Command",
            "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
            "Where-Object { $_.ExecutablePath -eq '" + str(DECOY_PYTHON) + "' } | "
            "Select-Object -First 1 -ExpandProperty ProcessId)",
        ]
    )
    output = result.stdout.strip()
    return int(output) if output.isdigit() else None


def is_pid_alive(pid: int) -> bool:
    result = run_powershell(["-Command", f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue) -ne $null"])
    return result.stdout.strip().lower() == "true"


def test_stop_script_kills_an_untracked_decoy_process() -> None:
    # This is the actual bug: an uninstall test found SecretGroundsClient.exe
    # and node.exe still running afterward because nothing had ever stopped
    # them. A decoy process launched here is deliberately NOT started through
    # Start-SgLoggedProcess, so it has no PID-tracking record at all -- the
    # only way stop-all-for-uninstall.ps1 can catch it is the executable-path
    # fallback scan, which is exactly the gap that let the client and
    # dashboard survive the real uninstall.
    if not DECOY_PYTHON.is_file():
        raise AssertionError(f"Bundled runtime is missing, cannot exercise the real fallback path: {DECOY_PYTHON}")

    subprocess.Popen(
        [str(DECOY_PYTHON), "-c", "import time; time.sleep(30)"],
        cwd=str(PROJECT_ROOT),
        creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
    )

    decoy_pid = None
    for _ in range(20):
        decoy_pid = find_decoy_pid()
        if decoy_pid is not None:
            break
        time.sleep(0.25)
    if decoy_pid is None:
        raise AssertionError("Decoy process never appeared in the process list; cannot exercise the test.")

    try:
        result = run_powershell(["-File", str(STOP_SCRIPT)], timeout=30)
        if result.returncode != 0:
            raise AssertionError(
                f"stop-all-for-uninstall.ps1 failed unexpectedly: "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        if is_pid_alive(decoy_pid):
            raise AssertionError(
                f"Untracked decoy process (PID {decoy_pid}) was still running after "
                "stop-all-for-uninstall.ps1 reported success."
            )
    finally:
        # Best-effort cleanup in case an assertion fired before the script
        # got a chance to stop it.
        if decoy_pid is not None and is_pid_alive(decoy_pid):
            run_powershell(["-Command", f"Stop-Process -Id {decoy_pid} -Force -ErrorAction SilentlyContinue"])


def test_stop_script_is_idempotent_with_nothing_running() -> None:
    result = run_powershell(["-File", str(STOP_SCRIPT)], timeout=30)
    assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    assert "confirmed stopped" in result.stdout.lower()


def main() -> None:
    test_stop_script_kills_an_untracked_decoy_process()
    test_stop_script_is_idempotent_with_nothing_running()
    print("Uninstall cleanup smoke tests passed.")


if __name__ == "__main__":
    main()
