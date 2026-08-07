import shutil
import subprocess
import tempfile
import time
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
DECOY_SOURCE = PROJECT_ROOT / "runtime" / "python" / "python.exe"

# Real script under test, plus its one real dependency.
REAL_SCRIPTS = [
    "secret-grounds-common.ps1",
    "start-client-after-install.ps1",
]

# install-client-autostart.ps1/remove-client-autostart.ps1 touch real,
# globally-named Windows Task Scheduler state, which must never be exercised
# against this development machine's actual Task Scheduler (same convention
# as tests/smoke_cashier_install_flow.py). Stubbed with marker-writers so
# start-client-after-install.ps1's own branching logic -- specifically,
# whether it calls them at all -- is still exercised for real.
STUB_TEMPLATE = """Add-Content -LiteralPath (Join-Path $PSScriptRoot "..\\calls.log") -Value "{marker}"
"""


def run_powershell(script_path: Path, args: list[str], cwd: str) -> subprocess.CompletedProcess:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    ] + args
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=30)


def make_scratch_install() -> Path:
    scratch_root = Path(tempfile.mkdtemp(prefix="sg-client-launch-"))
    scratch_scripts = scratch_root / "scripts"
    scratch_scripts.mkdir(parents=True, exist_ok=True)
    (scratch_root / "client").mkdir(parents=True, exist_ok=True)
    (scratch_root / "logs").mkdir(parents=True, exist_ok=True)

    for name in REAL_SCRIPTS:
        shutil.copy(SCRIPTS_DIR / name, scratch_scripts / name)

    for name in ("install-client-autostart.ps1", "remove-client-autostart.ps1"):
        marker = name.replace(".ps1", "")
        (scratch_scripts / name).write_text(STUB_TEMPLATE.format(marker=marker), encoding="utf-8")

    return scratch_root


def read_calls_log(scratch_root: Path) -> list[str]:
    calls_log = scratch_root / "calls.log"
    if not calls_log.exists():
        return []
    return [line.strip() for line in calls_log.read_text(encoding="utf-8").splitlines() if line.strip()]


def start_decoy_client(scratch_root: Path) -> subprocess.Popen:
    if not DECOY_SOURCE.is_file():
        raise AssertionError(f"Bundled runtime is missing, cannot exercise the real check: {DECOY_SOURCE}")
    # python.exe alone will not launch -- it needs its sibling DLLs/zip from
    # the same directory (this is exactly the embeddable-runtime layout
    # verified earlier in this project). Copy the whole runtime, then rename
    # just the executable to match what the script actually checks for.
    client_dir = scratch_root / "client"
    shutil.copytree(DECOY_SOURCE.parent, client_dir, dirs_exist_ok=True)
    decoy_path = client_dir / "SecretGroundsClient.exe"
    (client_dir / "python.exe").rename(decoy_path)
    return subprocess.Popen(
        [str(decoy_path), "-c", "import time; time.sleep(30)"],
        cwd=str(scratch_root),
        creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
    )


def wait_for_decoy_visible(scratch_root: Path, timeout: float = 5.0) -> None:
    decoy_path = str(scratch_root / "client" / "SecretGroundsClient.exe").lower()
    deadline = time.time() + timeout
    while time.time() < deadline:
        check = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_Process -Filter \"Name='SecretGroundsClient.exe'\" | "
                "Where-Object { $_.ExecutablePath -and $_.ExecutablePath.ToLowerInvariant() -eq '"
                + decoy_path + "' }) -ne $null",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if check.stdout.strip().lower() == "true":
            return
        time.sleep(0.25)
    raise AssertionError("Decoy client process never became visible to Get-CimInstance.")


def test_already_running_client_is_not_duplicated_and_autostart_is_removed() -> None:
    scratch_root = make_scratch_install()
    decoy_process = None
    try:
        decoy_process = start_decoy_client(scratch_root)
        wait_for_decoy_visible(scratch_root)

        result = run_powershell(
            scratch_root / "scripts" / "start-client-after-install.ps1",
            [],
            str(scratch_root),
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

        calls = read_calls_log(scratch_root)
        # The critical safety property: an already-running client must short
        # -circuit before ever reaching Start-ScheduledTask/registration --
        # proven here by install-client-autostart never being invoked.
        assert "install-client-autostart" not in calls
        # -KeepAutostart was not passed, so the (already-launched-elsewhere)
        # autostart task must be cleaned up rather than left registered.
        assert "remove-client-autostart" in calls
    finally:
        if decoy_process is not None:
            decoy_process.terminate()
            try:
                decoy_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                decoy_process.kill()
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_already_running_client_with_keep_autostart_does_not_remove_task() -> None:
    scratch_root = make_scratch_install()
    decoy_process = None
    try:
        decoy_process = start_decoy_client(scratch_root)
        wait_for_decoy_visible(scratch_root)

        result = run_powershell(
            scratch_root / "scripts" / "start-client-after-install.ps1",
            ["-KeepAutostart"],
            str(scratch_root),
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

        calls = read_calls_log(scratch_root)
        assert "install-client-autostart" not in calls
        assert "remove-client-autostart" not in calls
    finally:
        if decoy_process is not None:
            decoy_process.terminate()
            try:
                decoy_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                decoy_process.kill()
        shutil.rmtree(scratch_root, ignore_errors=True)


def main() -> None:
    test_already_running_client_is_not_duplicated_and_autostart_is_removed()
    test_already_running_client_with_keep_autostart_does_not_remove_task()
    print("Client post-install launch smoke tests passed.")


if __name__ == "__main__":
    main()
