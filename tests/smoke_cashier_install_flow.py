import shutil
import subprocess
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

# Scripts copied verbatim into the scratch install for a realistic flow.
REAL_SCRIPTS = [
    "secret-grounds-common.ps1",
    "initialize-cashier-config.ps1",
    "install-cashier-role.ps1",
]

# These touch real, globally-named Windows state (Task Scheduler, the
# Startup folder) that must never be exercised against this development
# machine's actual Task Scheduler. Stubbed with marker-writers instead so
# install-cashier-role.ps1's own branching logic is still exercised for
# real, just without a live side effect.
STUBBED_SCRIPTS = [
    "install-cashier-autostart.ps1",
    "remove-cashier-autostart.ps1",
    "remove-client-autostart.ps1",
    "remove-client-startup-folder-shortcut.ps1",
]

STUB_TEMPLATE = """param([switch]$AtStartup, [switch]$UseStartupFolderFallback)
Add-Content -LiteralPath (Join-Path $PSScriptRoot "..\\calls.log") -Value "{marker}"
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
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=60)


def make_scratch_install() -> Path:
    scratch_root = Path(tempfile.mkdtemp(prefix="sg-cashier-install-"))
    scratch_scripts = scratch_root / "scripts"
    scratch_scripts.mkdir(parents=True, exist_ok=True)

    for name in REAL_SCRIPTS:
        shutil.copy(SCRIPTS_DIR / name, scratch_scripts / name)

    for name in STUBBED_SCRIPTS:
        marker = name.replace(".ps1", "")
        (scratch_scripts / name).write_text(STUB_TEMPLATE.format(marker=marker), encoding="utf-8")

    return scratch_root


def read_calls_log(scratch_root: Path) -> list[str]:
    calls_log = scratch_root / "calls.log"
    if not calls_log.exists():
        return []
    return [line.strip() for line in calls_log.read_text(encoding="utf-8").splitlines() if line.strip()]


def init_config(scratch_root: Path) -> subprocess.CompletedProcess:
    return run_powershell(
        scratch_root / "scripts" / "initialize-cashier-config.ps1",
        ["-BackendPort", "18001", "-DashboardPort", "3001"],
        str(scratch_root),
    )


def test_enable_autostart_true_installs_autostart() -> None:
    scratch_root = make_scratch_install()
    try:
        init_result = init_config(scratch_root)
        assert init_result.returncode == 0, init_result.stderr

        role_result = run_powershell(
            scratch_root / "scripts" / "install-cashier-role.ps1",
            [],
            str(scratch_root),
        )
        assert role_result.returncode == 0, f"stdout={role_result.stdout!r} stderr={role_result.stderr!r}"

        calls = read_calls_log(scratch_root)
        assert "install-cashier-autostart" in calls
        assert "remove-cashier-autostart" not in calls
        # Cleaning up any stray Customer Computer autostart must happen
        # regardless of the cashier autostart choice.
        assert "remove-client-autostart" in calls
        assert "remove-client-startup-folder-shortcut" in calls
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_enable_autostart_false_removes_autostart() -> None:
    scratch_root = make_scratch_install()
    try:
        init_result = init_config(scratch_root)
        assert init_result.returncode == 0, init_result.stderr

        role_result = run_powershell(
            scratch_root / "scripts" / "install-cashier-role.ps1",
            ["-DisableAutostart"],
            str(scratch_root),
        )
        assert role_result.returncode == 0, f"stdout={role_result.stdout!r} stderr={role_result.stderr!r}"

        calls = read_calls_log(scratch_root)
        assert "remove-cashier-autostart" in calls
        assert "install-cashier-autostart" not in calls
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_autostart_choice_is_idempotent_on_rerun() -> None:
    # A second run with the same choice must not add a second marker beyond
    # what a real re-registration would do (install-cashier-autostart.ps1
    # itself unregisters-then-registers, so re-running is expected to call
    # it again, not accumulate duplicate *tasks*). This test only proves
    # install-cashier-role.ps1 keeps calling the same single script on
    # rerun rather than drifting into some other path.
    scratch_root = make_scratch_install()
    try:
        assert init_config(scratch_root).returncode == 0

        args = []
        first = run_powershell(scratch_root / "scripts" / "install-cashier-role.ps1", args, str(scratch_root))
        second = run_powershell(scratch_root / "scripts" / "install-cashier-role.ps1", args, str(scratch_root))
        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr

        calls = read_calls_log(scratch_root)
        assert calls.count("install-cashier-autostart") == 2
        assert calls.count("remove-cashier-autostart") == 0
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def test_install_cashier_role_requires_existing_config() -> None:
    # Proves the install ordering the installer relies on: cashier role
    # setup cannot silently proceed without a config\.env already in place.
    scratch_root = make_scratch_install()
    try:
        role_result = run_powershell(
            scratch_root / "scripts" / "install-cashier-role.ps1",
            [],
            str(scratch_root),
        )
        assert role_result.returncode != 0
        calls = read_calls_log(scratch_root)
        assert calls == []
    finally:
        shutil.rmtree(scratch_root, ignore_errors=True)


def main() -> None:
    test_enable_autostart_true_installs_autostart()
    test_enable_autostart_false_removes_autostart()
    test_autostart_choice_is_idempotent_on_rerun()
    test_install_cashier_role_requires_existing_config()
    print("Cashier install flow smoke tests passed.")


if __name__ == "__main__":
    main()
