import shutil
import subprocess
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMON_SCRIPT = PROJECT_ROOT / "scripts" / "secret-grounds-common.ps1"
INIT_SCRIPT = PROJECT_ROOT / "scripts" / "initialize-cashier-config.ps1"


def run_powershell(script_path: Path, args: list[str], cwd: str) -> subprocess.CompletedProcess:
    # Windows PowerShell 5.1 (powershell.exe), not PowerShell 7 (pwsh.exe): clean
    # customer/cashier PCs are not guaranteed to have PowerShell 7 installed, and
    # this is exactly the runtime that lacks RandomNumberGenerator.Fill().
    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script_path),
    ] + args
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=60)


def test_harness_is_actually_powershell_5_1() -> None:
    # Guards the rest of this file against silently testing under pwsh.exe
    # instead of powershell.exe, which would make every other assertion here
    # meaningless (pwsh has RandomNumberGenerator.Fill and would never catch
    # this class of bug).
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", "$PSVersionTable.PSEdition"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "Desktop" in result.stdout, (
        "Expected Windows PowerShell 5.1 (PSEdition=Desktop); "
        f"got: {result.stdout!r}"
    )


def test_random_number_generator_fill_is_unavailable_on_this_runtime() -> None:
    # Confirms the reported bug's exact failure mode still reproduces on this
    # runtime, so a pass on the tests below is meaningful evidence the fix
    # works rather than the environment quietly being PS7-compatible already.
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            "[System.Security.Cryptography.RandomNumberGenerator]::Fill([byte[]]::new(32))",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    # PowerShell line-wraps long error messages at the console width, so the
    # exact phrase can be split across a newline (e.g. "...named \n'Fill'.").
    normalized_stderr = " ".join(result.stderr.split())
    assert "does not contain a method named 'Fill'" in normalized_stderr


def test_initialize_cashier_config_creates_env_under_powershell_5_1() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        scratch_root = Path(temp_directory)
        scratch_scripts = scratch_root / "scripts"
        scratch_scripts.mkdir(parents=True, exist_ok=True)
        shutil.copy(COMMON_SCRIPT, scratch_scripts / COMMON_SCRIPT.name)
        shutil.copy(INIT_SCRIPT, scratch_scripts / INIT_SCRIPT.name)

        result = run_powershell(
            scratch_scripts / INIT_SCRIPT.name,
            ["-BackendPort", "18001", "-DashboardPort", "3001"],
            str(scratch_root),
        )
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "does not contain a method named 'Fill'" not in result.stderr

        env_path = scratch_root / "config" / ".env"
        assert env_path.exists(), "config\\.env was not created"
        env_text = env_path.read_text(encoding="utf-8")

        secret_line = next(
            (line for line in env_text.splitlines() if line.startswith("SECRET_GROUNDS_DEPLOYMENT_SECRET=")),
            None,
        )
        assert secret_line is not None, "SECRET_GROUNDS_DEPLOYMENT_SECRET was not written"
        secret_value = secret_line.split("=", 1)[1]
        # 32 random bytes, unchanged from before the fix, base64-encoded to 44 chars.
        assert len(secret_value) == 44
        assert secret_value.endswith("=")

        assert "SECRET_GROUNDS_PORT=18001" in env_text
        assert "SECRET_GROUNDS_DASHBOARD_PORT=3001" in env_text


def test_initialize_cashier_config_preserves_existing_secret_on_rerun() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        scratch_root = Path(temp_directory)
        scratch_scripts = scratch_root / "scripts"
        scratch_scripts.mkdir(parents=True, exist_ok=True)
        shutil.copy(COMMON_SCRIPT, scratch_scripts / COMMON_SCRIPT.name)
        shutil.copy(INIT_SCRIPT, scratch_scripts / INIT_SCRIPT.name)

        args = ["-BackendPort", "18001", "-DashboardPort", "3001"]
        first = run_powershell(scratch_scripts / INIT_SCRIPT.name, args, str(scratch_root))
        assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
        env_path = scratch_root / "config" / ".env"
        first_secret = env_path.read_text(encoding="utf-8")

        second = run_powershell(scratch_scripts / INIT_SCRIPT.name, args, str(scratch_root))
        assert second.returncode == 0, f"stdout={second.stdout!r} stderr={second.stderr!r}"
        second_secret = env_path.read_text(encoding="utf-8")

        assert first_secret == second_secret, "Re-running without -Force must not regenerate the secret"


def main() -> None:
    test_harness_is_actually_powershell_5_1()
    test_random_number_generator_fill_is_unavailable_on_this_runtime()
    test_initialize_cashier_config_creates_env_under_powershell_5_1()
    test_initialize_cashier_config_preserves_existing_secret_on_rerun()
    print("PowerShell 5.1 cashier config smoke tests passed.")


if __name__ == "__main__":
    main()
