"""Auto-adopt-identity tests: the "@hostname" diskless variant where a
seat's Windows computer name is turned into a station name.

Covers:
  - server/hostname_identity.py: derive_station_name_from_hostname (pure).
  - client/config.py: the "@hostname" sentinel is accepted and treated as
    automatic-identity (not validated as a literal station id).
  - client/client.py: resolve_runtime_pc_name() sends the hostname only in
    "@hostname" mode and returns the server-resolved name.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from server.hostname_identity import derive_station_name_from_hostname


# --- server-side derivation (pure) ---------------------------------------


def test_derive_folds_common_pc_number_spellings() -> None:
    for raw in ("PC14", "PC-14", "PC_14", "pc-014", "PC-14.shop.local"):
        assert derive_station_name_from_hostname(raw) == "PC-14", raw


def test_derive_passes_through_a_clean_non_pc_name() -> None:
    assert derive_station_name_from_hostname("GAMING-ROW-A") == "GAMING-ROW-A"
    assert derive_station_name_from_hostname("vip07") == "VIP07"


def test_derive_rejects_windows_placeholders_and_junk() -> None:
    for raw in (
        None,
        "",
        "   ",
        "DESKTOP-A1B2C3",
        "WIN-9KJ2L",
        "MININT-ABC",
        "MINWINPC",
        "localhost",
        "PC",
        "PC 14",          # space
        "PC#14",          # punctuation
        "a" * 40,         # too long
    ):
        assert derive_station_name_from_hostname(raw) is None, repr(raw)


# --- client config accepts the sentinel ---------------------------------


def test_client_config_accepts_hostname_sentinel() -> None:
    from client.config import HOSTNAME_ADOPT_SENTINEL, validate_client_config

    config = validate_client_config(
        {
            "server_host": "192.168.1.15",
            "server_port": 18001,
            "pc_name": HOSTNAME_ADOPT_SENTINEL,
            "auto_connect": True,
        },
        Path("config.json"),
    )
    assert config.pc_name == HOSTNAME_ADOPT_SENTINEL
    # No human-chosen display name yet -- resolved at runtime.
    assert config.display_name == ""


def test_client_config_still_rejects_a_malformed_literal_name() -> None:
    from client.config import ClientConfigError, validate_client_config

    try:
        validate_client_config(
            {
                "server_host": "192.168.1.15",
                "server_port": 18001,
                "pc_name": "@bogus",
                "auto_connect": True,
            },
            Path("config.json"),
        )
    except ClientConfigError:
        pass
    else:
        raise AssertionError("'@bogus' is not the sentinel and must be rejected")


# --- client resolve wiring --------------------------------------------------


def test_resolve_runtime_pc_name_sends_hostname_only_in_hostname_mode() -> None:
    import socket

    from client.config import HOSTNAME_ADOPT_SENTINEL
    from client import client as client_module

    captured: dict[str, object] = {}

    def fake_build_http_resolve_fn(server_url, request_timeout, hostname=None):
        captured["hostname"] = hostname

        def resolve_fn(mac_address, ip_address):
            return {"status": "assigned", "pc_name": "PC-14", "display_name": "PC 14"}

        return resolve_fn

    original = client_module.build_http_resolve_fn
    client_module.build_http_resolve_fn = fake_build_http_resolve_fn
    try:
        base = {
            "server_host": "192.168.1.15",
            "server_port": 18001,
            "auto_connect": True,
        }
        from client.config import validate_client_config

        hostname_cfg = validate_client_config(
            {**base, "pc_name": HOSTNAME_ADOPT_SENTINEL}, Path("config.json")
        )
        pc_name, display_name = client_module.resolve_runtime_pc_name(hostname_cfg)
        assert pc_name == "PC-14"
        assert display_name == "PC 14"
        assert captured["hostname"] == socket.gethostname().strip()

        captured.clear()
        blank_cfg = validate_client_config({**base, "pc_name": ""}, Path("config.json"))
        client_module.resolve_runtime_pc_name(blank_cfg)
        assert captured["hostname"] is None
    finally:
        client_module.build_http_resolve_fn = original


def main() -> None:
    tests = [
        test_derive_folds_common_pc_number_spellings,
        test_derive_passes_through_a_clean_non_pc_name,
        test_derive_rejects_windows_placeholders_and_junk,
        test_client_config_accepts_hostname_sentinel,
        test_client_config_still_rejects_a_malformed_literal_name,
        test_resolve_runtime_pc_name_sends_hostname_only_in_hostname_mode,
    ]
    for test in tests:
        test()
        print(f"{test.__name__} passed.")
    print("hostname-identity smoke tests passed.")


if __name__ == "__main__":
    main()
