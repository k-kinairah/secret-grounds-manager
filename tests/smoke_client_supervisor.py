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
import time
import uuid
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = PROJECT_ROOT / "scripts" / "run-client-supervised.ps1"


def supervisor_command(
    *, client_command: str, stop_sentinel: Path, max_relaunches: int, extra_args: list[str] | None = None
) -> list[str]:
    return [
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
            *(extra_args or []),
        ]


def run_supervisor(
    *, client_command: str, stop_sentinel: Path, max_relaunches: int, extra_args: list[str] | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        supervisor_command(
            client_command=client_command,
            stop_sentinel=stop_sentinel,
            max_relaunches=max_relaunches,
            extra_args=extra_args,
        ),
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


def test_a_second_supervisor_exits_instead_of_running_alongside_the_first() -> None:
    """Regression: Task Scheduler lost track of the wscript wrapper while the
    supervisor kept running, then started a second supervisor -- two clients,
    two tray icons. The supervisor itself must refuse to be a duplicate."""
    lock = ["-InstanceMutexName", f"Local\\SecretGroundsSupervisorTest-{uuid.uuid4().hex}"]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp:
        first_runs = Path(temp) / "first.txt"
        second_runs = Path(temp) / "second.txt"
        third_runs = Path(temp) / "third.txt"
        first = subprocess.Popen(
            supervisor_command(
                client_command=f"Add-Content -LiteralPath '{first_runs}' -Value run; Start-Sleep -Seconds 8",
                stop_sentinel=Path(temp) / "first.stop",
                max_relaunches=1,
                extra_args=lock,
            ),
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.monotonic() + 30
            while not first_runs.exists() and time.monotonic() < deadline:
                time.sleep(0.2)
            assert first_runs.exists(), "first supervisor never started its client"

            second = run_supervisor(
                client_command=f"Add-Content -LiteralPath '{second_runs}' -Value run",
                stop_sentinel=Path(temp) / "second.stop",
                max_relaunches=3,
                extra_args=lock,
            )
            assert second.returncode == 0, second.stderr
            assert not second_runs.exists(), "a duplicate supervisor must not start a second client"
        finally:
            first.communicate(timeout=60)

        # Once the first supervisor has exited its lock must be free again,
        # or a legitimate restart would be refused forever.
        third = run_supervisor(
            client_command=f"Add-Content -LiteralPath '{third_runs}' -Value run",
            stop_sentinel=Path(temp) / "third.stop",
            max_relaunches=1,
            extra_args=lock,
        )
        assert third.returncode == 0, third.stderr
        assert third_runs.exists(), f"lock was not released when the first supervisor exited\n{third.stdout}"


def main() -> None:
    for test in (
        test_supervisor_relaunches_the_client_each_time_it_exits,
        test_supervisor_stops_without_relaunch_when_the_sentinel_appears,
        test_a_second_supervisor_exits_instead_of_running_alongside_the_first,
    ):
        test()
        print(f"{test.__name__} passed.")
    print("client supervisor smoke tests passed.")


if __name__ == "__main__":
    main()
