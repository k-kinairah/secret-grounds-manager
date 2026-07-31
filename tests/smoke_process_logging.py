import subprocess
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMON_SCRIPT = PROJECT_ROOT / "scripts" / "secret-grounds-common.ps1"
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"

HARNESS_TEMPLATE = """
param(
    [string]$Role,
    [string]$PythonExe,
    [string]$ChildScript,
    [string]$LogPath,
    [string]$ErrorLogPath,
    [string]$WorkingDirectory
)
. "{common_script}"
$process = Start-SgLoggedProcess -Role $Role -FilePath $PythonExe -Arguments @($ChildScript) -WorkingDirectory $WorkingDirectory -LogPath $LogPath -ErrorLogPath $ErrorLogPath
Write-Output "LAUNCHER_PID=$($process.Id)"
Write-Output "IDENTITY=$($Script:LastSgLaunchIdentity)"
"""


def write_child_script(temp_directory: str, lines: int, sleep_seconds: float, stderr_every: int) -> Path:
    child_path = Path(temp_directory) / "child.py"
    child_path.write_text(
        "import sys, time\n"
        f"for i in range({lines}):\n"
        "    print(f'out-line-{i}', flush=True)\n"
        f"    if i % {stderr_every} == 0:\n"
        "        print(f'err-line-{i}', file=sys.stderr, flush=True)\n"
        f"    time.sleep({sleep_seconds})\n"
        "print('CHILD-DONE', flush=True)\n",
        encoding="utf-8",
    )
    return child_path


def run_harness(temp_directory: str, role: str, child_script: Path, log_path: Path, error_log_path: Path) -> tuple[subprocess.CompletedProcess, float]:
    harness_path = Path(temp_directory) / "harness.ps1"
    harness_path.write_text(HARNESS_TEMPLATE.format(common_script=str(COMMON_SCRIPT)), encoding="utf-8")

    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(harness_path),
        "-Role",
        role,
        "-PythonExe",
        str(PYTHON_EXE),
        "-ChildScript",
        str(child_script),
        "-LogPath",
        str(log_path),
        "-ErrorLogPath",
        str(error_log_path),
        "-WorkingDirectory",
        temp_directory,
    ]
    start = time.monotonic()
    result = subprocess.run(command, cwd=temp_directory, capture_output=True, text=True, timeout=30)
    elapsed = time.monotonic() - start
    return result, elapsed


def parse_launcher_pid(stdout: str) -> int:
    for line in stdout.splitlines():
        if line.startswith("LAUNCHER_PID="):
            return int(line.split("=", 1)[1].strip())
    raise AssertionError(f"LAUNCHER_PID not found in harness output: {stdout!r}")


def wait_for_file_containing(path: Path, needle: str, timeout_seconds: float = 15) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if path.exists() and needle in path.read_text(encoding="utf-8", errors="ignore"):
            return True
        time.sleep(0.2)
    return False


def is_pid_alive(pid: int) -> bool:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", f"(Get-Process -Id {pid} -ErrorAction SilentlyContinue) -ne $null"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.stdout.strip() == "True"


def test_launcher_returns_promptly_without_waiting_for_child(temp_directory: str) -> None:
    child_script = write_child_script(temp_directory, lines=40, sleep_seconds=0.1, stderr_every=10)
    log_path = Path(temp_directory) / "out.log"
    error_log_path = Path(temp_directory) / "err.log"

    result, elapsed = run_harness(temp_directory, "test-prompt", child_script, log_path, error_log_path)
    assert result.returncode == 0, result.stderr

    # The child sleeps for ~4s total (40 * 0.1s); the launcher must not block on that.
    assert elapsed < 3.0, f"harness took {elapsed:.2f}s, looks like it waited for the child"


def test_stdout_is_written_to_intended_log(temp_directory: str) -> None:
    child_script = write_child_script(temp_directory, lines=20, sleep_seconds=0.05, stderr_every=1000)
    log_path = Path(temp_directory) / "out.log"
    error_log_path = Path(temp_directory) / "err.log"

    result, _ = run_harness(temp_directory, "test-stdout", child_script, log_path, error_log_path)
    assert result.returncode == 0, result.stderr

    assert wait_for_file_containing(log_path, "CHILD-DONE"), "stdout log never received expected child output"
    content = log_path.read_text(encoding="utf-8")
    assert "out-line-0" in content
    assert "out-line-19" in content


def test_stderr_is_written_to_intended_log(temp_directory: str) -> None:
    child_script = write_child_script(temp_directory, lines=20, sleep_seconds=0.05, stderr_every=5)
    log_path = Path(temp_directory) / "out.log"
    error_log_path = Path(temp_directory) / "err.log"

    result, _ = run_harness(temp_directory, "test-stderr", child_script, log_path, error_log_path)
    assert result.returncode == 0, result.stderr

    assert wait_for_file_containing(log_path, "CHILD-DONE")
    error_content = error_log_path.read_text(encoding="utf-8", errors="ignore")
    assert "err-line-0" in error_content
    assert "err-line-15" in error_content


def test_long_running_child_stays_detached_after_launcher_exits(temp_directory: str) -> None:
    # The launcher subprocess (powershell -File harness.ps1) fully exits well before the
    # child finishes; the child must keep running and complete on its own afterward.
    child_script = write_child_script(temp_directory, lines=30, sleep_seconds=0.1, stderr_every=1000)
    log_path = Path(temp_directory) / "out.log"
    error_log_path = Path(temp_directory) / "err.log"

    result, elapsed = run_harness(temp_directory, "test-detached", child_script, log_path, error_log_path)
    assert result.returncode == 0, result.stderr
    assert elapsed < 3.0, "harness should return long before the ~3s child finishes"

    # Harness process (and, transitively, the powershell.exe that ran it) has already exited.
    assert wait_for_file_containing(log_path, "CHILD-DONE", timeout_seconds=15), \
        "child did not keep running/complete after its launcher process exited"


def test_readiness_style_polling_detects_wrapper_exit(temp_directory: str) -> None:
    child_script = write_child_script(temp_directory, lines=10, sleep_seconds=0.05, stderr_every=1000)
    log_path = Path(temp_directory) / "out.log"
    error_log_path = Path(temp_directory) / "err.log"

    result, _ = run_harness(temp_directory, "test-readiness", child_script, log_path, error_log_path)
    assert result.returncode == 0, result.stderr
    pid = parse_launcher_pid(result.stdout)

    # Mirrors Wait-SgHttpReady's own Process.Refresh()/HasExited polling loop: the tracked
    # PID (the cmd.exe wrapper) must reflect the real child's lifetime, not exit instantly.
    assert wait_for_file_containing(log_path, "CHILD-DONE", timeout_seconds=15)
    deadline = time.monotonic() + 5
    exited = False
    while time.monotonic() < deadline:
        if not is_pid_alive(pid):
            exited = True
            break
        time.sleep(0.2)
    assert exited, "tracked wrapper PID never reported as exited after the child finished"


def main() -> None:
    tests = [
        test_launcher_returns_promptly_without_waiting_for_child,
        test_stdout_is_written_to_intended_log,
        test_stderr_is_written_to_intended_log,
        test_long_running_child_stays_detached_after_launcher_exits,
        test_readiness_style_polling_detects_wrapper_exit,
    ]
    for test in tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
    print("Process logging smoke tests passed.")


if __name__ == "__main__":
    main()
