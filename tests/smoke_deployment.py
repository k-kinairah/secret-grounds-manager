import json
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from client.config import APP_VERSION as CLIENT_VERSION
from server.database import get_database_path
from server.version import APP_VERSION


SCRIPT_NAMES = {
    "secret-grounds-common.ps1",
    "install-dependencies.ps1",
    "build-dashboard.ps1",
    "start-backend.ps1",
    "start-dashboard.ps1",
    "start-secret-grounds.ps1",
    "stop-secret-grounds.ps1",
    "restart-secret-grounds.ps1",
    "status-secret-grounds.ps1",
    "install-cashier-autostart.ps1",
    "remove-cashier-autostart.ps1",
    "start-client.ps1",
    "install-client-autostart.ps1",
    "remove-client-autostart.ps1",
    "check-deployment.ps1",
    "build-portable-release.ps1",
}


def read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_version_consistency() -> None:
    version = read("VERSION").strip()
    package = json.loads(read("dashboard/package.json"))
    assert version == "1.0.0-rc.2"
    assert APP_VERSION == version
    assert CLIENT_VERSION == version
    assert package["version"] == version


def test_required_deployment_scripts_exist() -> None:
    scripts = {path.name for path in (PROJECT_ROOT / "scripts").glob("*.ps1")}
    assert SCRIPT_NAMES <= scripts


def test_cashier_scripts_use_production_startup_and_pid_identity() -> None:
    backend = read("scripts/start-backend.ps1")
    dashboard = read("scripts/start-dashboard.ps1")
    common = read("scripts/secret-grounds-common.ps1")
    stop = read("scripts/stop-secret-grounds.ps1")

    assert "--reload" not in backend
    assert "server.main:app" in backend
    assert "uvicorn" in backend
    assert "Wait-SgHttpReady" in backend
    assert "healthz" in backend
    assert "Resolve-SgNodePath" in dashboard
    assert "next\\dist\\bin\\next" in dashboard
    assert '"start"' in dashboard
    assert "next dev" not in dashboard.lower()
    assert "secret-grounds-pids.json" in common
    assert "CommandLine" in common
    assert "Start-Process" in common
    assert "function Start-SgLoggedProcess" in common
    assert "launch-wrappers" in common
    assert "HasExited" in common
    assert "Show-SgLogTail" in common
    assert "dashboard-console.log" in dashboard
    assert "server-console.log" in backend
    assert "Stop-SgStartedProcess" in stop
    assert "Stop-Process -Name" not in stop


def test_autostart_task_names_are_stable() -> None:
    cashier = read("scripts/install-cashier-autostart.ps1")
    client = read("scripts/install-client-autostart.ps1")
    assert "Secret Grounds Cashier Stack" in cashier
    assert "Secret Grounds Client" in client
    assert "MultipleInstances IgnoreNew" in cashier
    assert "MultipleInstances IgnoreNew" in client


def test_config_examples_are_secret_safe() -> None:
    env_example = read("config/.env.example")
    client_example = json.loads(read("client/config.example.json"))

    assert "SECRET_GROUNDS_DEPLOYMENT_SECRET=replace-with-a-long-random-secret" in env_example
    assert not re.search(r"(password|token)\s*=\s*(?!replace|example|false|true)[^#\r\n]+", env_example, re.IGNORECASE)
    assert "SECRET_GROUNDS_AUTH_SECURE_COOKIE=false" in env_example
    assert "SECRET_GROUNDS_PORT=18001" in env_example
    assert "SECRET_GROUNDS_DASHBOARD_PORT=3001" in env_example
    assert "NEXT_PUBLIC_FASTAPI_BASE_URL=http://127.0.0.1:18001" in env_example
    assert "SECRET_GROUNDS_CORS_ORIGINS=http://127.0.0.1:3001" in env_example
    assert "localhost" not in env_example
    assert client_example["server_host"] == "cashier-server"
    assert client_example["server_port"] == 18001
    assert client_example["pc_name"] == "CHANGE-ME"
    assert client_example["pc_name"] == "CHANGE-ME"


def test_development_and_production_urls_are_separate() -> None:
    dashboard_env = read("dashboard/.env.example")
    cashier_config = read("config/.env.example")
    common = read("scripts/secret-grounds-common.ps1")
    initializer = read("scripts/initialize-cashier-config.ps1")
    ports_doc = read("docs/PORTS.md")

    assert "NEXT_PUBLIC_FASTAPI_BASE_URL=http://localhost:8000" in dashboard_env
    assert "127.0.0.1:18001" not in dashboard_env
    assert "localhost:8000" not in cashier_config
    assert "$Script:DefaultBackendPort = 18001" in common
    assert '"http://127.0.0.1:$DashboardPort"' in initializer
    assert "http://localhost:3000" in ports_doc
    assert "http://localhost:8000" in ports_doc
    assert "http://127.0.0.1:3001" in ports_doc
    assert "http://127.0.0.1:18001" in ports_doc


def test_deployment_paths_are_outside_source_defaults(monkeypatch) -> None:
    monkeypatch.setenv("SECRET_GROUNDS_DB_PATH", r".\data\secret_grounds.db")
    resolved = get_database_path()
    assert resolved.is_absolute()
    assert resolved.name == "secret_grounds.db"
    assert "data" in resolved.parts


def test_check_deployment_covers_requested_safety_checks() -> None:
    check = read("scripts/check-deployment.ps1")
    for expected in [
        "SECRET_GROUNDS_DEPLOYMENT_SECRET",
        "SECRET_GROUNDS_TEST_MODE",
        "dashboard\\.next\\BUILD_ID",
        "PRAGMA integrity_check",
        "legacy database",
        "Test-SgPortAvailable",
    ]:
        assert expected in check


def test_client_start_rejects_shared_default_pc_name() -> None:
    start_client = read("scripts/start-client.ps1")
    assert "Confirm this is the assigned customer PC" in start_client
    assert "SECRET_GROUNDS_TEST_MODE=true is blocked" in start_client
    assert "client-error.log" in start_client


def test_docs_cover_manual_retest_and_firewall_guidance() -> None:
    docs = read("docs/DEPLOYMENT_WINDOWS.md")
    assert "Safe Manual Retest" in docs
    assert "Do not disable the firewall" in docs
    assert "Secret Grounds Cashier Stack" in docs
    assert "Secret Grounds Client" in docs


def main() -> None:
    tests = [
        test_version_consistency,
        test_required_deployment_scripts_exist,
        test_cashier_scripts_use_production_startup_and_pid_identity,
        test_autostart_task_names_are_stable,
        test_config_examples_are_secret_safe,
        test_development_and_production_urls_are_separate,
        lambda: test_deployment_paths_are_outside_source_defaults(
            type(
                "MonkeyPatch",
                (),
                {"setenv": staticmethod(lambda name, value: __import__("os").environ.__setitem__(name, value))},
            )
        ),
        test_check_deployment_covers_requested_safety_checks,
        test_client_start_rejects_shared_default_pc_name,
        test_docs_cover_manual_retest_and_firewall_guidance,
    ]
    for test in tests:
        test()
    print("Deployment smoke tests passed.")


if __name__ == "__main__":
    main()
