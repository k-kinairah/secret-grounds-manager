"""Functional tests for scripts/run-client-supervised.ps1 -- the loop that
keeps the customer client alive so ending it in Task Manager just re-locks
the PC (PanCafe's pguard behaviour).

Drives the real script against a fake "client" (a one-line PowerShell
command) via -ClientCommandOverride, so no packaged exe / config / server
is involved.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = PROJECT_ROOT / "scripts" / "run-client-supervised.ps1"


def run_supervisor(*, client_command: str, stop_sentinel: Path, max_relaunches: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SUPERVISOR),
            "-ClientCommandOverride",
            client_command,
            "-StopSentinelPath",
            str(stop_sentinel),
            "-RelaunchDelaySeconds",
            "0",
            "-MaxRelaunches",
            str(max_relaunches),
        ],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        timeout=90,
    )


def test_supervisor_relaunches_the_client_each_time_it_exits() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        runs = Path(temp) / "runs.txt"
        sentinel = Path(temp) / "stop.sentinel"
        result = run_supervisor(
            client_command=f"Add-Content -LiteralPath '{runs}' -Value run",
            stop_sentinel=sentinel,
            max_relaunches=3,
        )
        assert result.returncode == 0, result.stderr
        launched = runs.read_text(encoding="utf-8").split()
        assert len(launched) == 3, f"expected 3 relaunches, got {launched!r}\n{result.stdout}"


def test_supervisor_stops_without_relaunch_when_the_sentinel_appears() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        runs = Path(temp) / "runs.txt"
        sentinel = Path(temp) / "stop.sentinel"
        # The fake client writes the stop sentinel on its 2nd run; the
        # supervisor must then exit instead of starting a 3rd.
        client_command = (
            f"$n = (Get-Content -LiteralPath '{runs}' -ErrorAction SilentlyContinue).Count; "
            f"Add-Content -LiteralPath '{runs}' -Value run; "
            f"if ($n -ge 1) {{ Set-Content -LiteralPath '{sentinel}' -Value stop }}"
        )
        result = run_supervisor(
            client_command=client_command,
            stop_sentinel=sentinel,
            max_relaunches=10,
        )
        assert result.returncode == 0, result.stderr
        launched = runs.read_text(encoding="utf-8").split()
        assert len(launched) == 2, f"supervisor should have stopped after 2 runs, got {launched!r}\n{result.stdout}"
        assert not sentinel.exists(), "supervisor must consume (delete) the stop sentinel"


def main() -> None:
    for test in (
        test_supervisor_relaunches_the_client_each_time_it_exits,
        test_supervisor_stops_without_relaunch_when_the_sentinel_appears,
    ):
        test()
        print(f"{test.__name__} passed.")
    print("client supervisor smoke tests passed.")


if __name__ == "__main__":
    main()
