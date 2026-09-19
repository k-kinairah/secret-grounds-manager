import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def test_dev_escape_defaults_to_disabled_when_unset() -> None:
    # Audited before go-live: ENABLE_DEV_ESCAPE used to default to True with
    # no install script ever setting it False, so every shipped customer PC
    # had its lock-screen bypass (Ctrl+Shift+F12) active out of the box --
    # any customer who knew the shortcut got free unpaid access. Must be
    # off unless a developer explicitly opts in (as the packaged-client GUI
    # smoke test does by setting the env var itself).
    import os

    assert "ENABLE_DEV_ESCAPE" not in os.environ, (
        "This test must run in a clean process with ENABLE_DEV_ESCAPE unset "
        "to actually prove the default -- run this file on its own."
    )
    import client.config as config_module

    assert config_module.ENABLE_DEV_ESCAPE is False


def main() -> None:
    test_dev_escape_defaults_to_disabled_when_unset()
    print("Config defaults smoke test passed.")


if __name__ == "__main__":
    main()
