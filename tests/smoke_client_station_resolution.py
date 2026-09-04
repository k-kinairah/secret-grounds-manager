"""Stage 3 (generic diskless client identity resolution) tests.

Covers:
  - client/network_identity.py: wired-adapter selection (pure, injected
    fixtures -- no real ctypes/OS enumeration involved).
  - client/station_resolver.py: the resolve/poll loop (pure, injected
    fake resolve_fn/mac_provider/sleep_fn -- no real HTTP or real sleeping).
  - client/config.py: pc_name-optional loading.
  - client/client.py: resolve_runtime_pc_name() wiring, and one true
    end-to-end scenario against a real live server proving the resolved
    pc_name actually reaches /register, /heartbeat, and /ws/{pc_name}.

No dashboard changes, no pricing/income-group changes, no wired-NIC
detection is exercised against real hardware beyond what Stage 3's own
opportunistic sanity check already covered manually.
"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from client.config import ClientConfig, ClientConfigError, load_client_config
from client.network_identity import (
    NetworkAdapter,
    WiredMacDetectionError,
    get_wired_mac_address,
    normalize_mac_address,
    select_wired_adapter,
)
from client.station_resolver import (
    ResolvedStation,
    StationResolutionError,
    WAITING_MESSAGE,
    resolve_station_identity,
)


# =====================================================================
# Section A: wired-adapter selection (pure, no OS calls)
# =====================================================================


def wired(mac: str = "08:BF:B8:C2:CE:CD", description: str = "Realtek PCIe GbE Family Controller", up: bool = True) -> NetworkAdapter:
    return NetworkAdapter(name="wired0", description=description, mac_address=mac, if_type=6, is_up=up)


def wifi(mac: str = "10:68:38:F0:FC:08", description: str = "Realtek 8852BE Wireless LAN WiFi 6 PCI-E NIC", up: bool = True) -> NetworkAdapter:
    return NetworkAdapter(name="wifi0", description=description, mac_address=mac, if_type=71, is_up=up)


def loopback() -> NetworkAdapter:
    return NetworkAdapter(name="loop0", description="Software Loopback Interface 1", mac_address=None, if_type=24, is_up=True)


def virtual_ethernet(mac: str = "00:15:5D:AA:BB:CC") -> NetworkAdapter:
    # Hyper-V/VMware/VPN virtual adapters commonly present as ifType=6
    # (Ethernet) -- only the description marks them as virtual.
    return NetworkAdapter(name="veth0", description="Hyper-V Virtual Ethernet Adapter", mac_address=mac, if_type=6, is_up=True)


def test_wired_adapter_preferred_over_wifi() -> None:
    adapters = [wifi(), wired(), loopback()]
    selected = select_wired_adapter(adapters)
    assert selected is not None
    assert selected.description == "Realtek PCIe GbE Family Controller"


def test_wifi_never_selected_even_alone() -> None:
    assert select_wired_adapter([wifi(), loopback()]) is None


def test_virtual_ethernet_adapter_rejected_when_physical_wired_exists() -> None:
    selected = select_wired_adapter([virtual_ethernet(), wired()])
    assert selected is not None
    assert "Realtek" in selected.description


def test_virtual_ethernet_adapter_rejected_even_when_it_is_the_only_ethernet_typed_one() -> None:
    assert select_wired_adapter([virtual_ethernet(), wifi(), loopback()]) is None


def test_down_wired_adapter_not_selected() -> None:
    assert select_wired_adapter([wired(up=False)]) is None


def test_no_suitable_wired_adapter_fails_safely() -> None:
    def empty_source() -> list[NetworkAdapter]:
        return []

    try:
        get_wired_mac_address(adapter_source=empty_source)
        raise AssertionError("expected WiredMacDetectionError")
    except WiredMacDetectionError as error:
        assert "No active physical wired Ethernet adapter" in str(error)


def test_adapter_enumeration_failure_fails_safely_not_with_a_guess() -> None:
    def broken_source() -> list[NetworkAdapter]:
        raise OSError("simulated enumeration failure")

    try:
        get_wired_mac_address(adapter_source=broken_source)
        raise AssertionError("expected WiredMacDetectionError")
    except WiredMacDetectionError as error:
        assert "Could not enumerate network adapters" in str(error)


def test_get_wired_mac_address_normalizes_and_selects_correctly() -> None:
    def source() -> list[NetworkAdapter]:
        return [wifi(), wired(mac="aa-bb-cc-dd-ee-ff"), loopback()]

    assert get_wired_mac_address(adapter_source=source) == "AA:BB:CC:DD:EE:FF"


def test_client_mac_normalization_matches_server_convention() -> None:
    for raw in ("aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF", "aabb.ccdd.eeff", "AABBCCDDEEFF"):
        assert normalize_mac_address(raw) == "AA:BB:CC:DD:EE:FF"
    assert normalize_mac_address("not-a-mac") is None
    assert normalize_mac_address("") is None
    assert normalize_mac_address(None) is None


# =====================================================================
# Section B: resolution/polling loop (pure, no HTTP, no real sleeping)
# =====================================================================


class FakeResolveSequence:
    """Injectable resolve_fn stand-in: returns a scripted sequence of
    responses (or raises a scripted exception) on successive calls, and
    records every (mac_address, ip_address) it was called with."""

    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def __call__(self, mac_address: str, ip_address: str) -> dict[str, Any]:
        self.calls.append((mac_address, ip_address))
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        outcome = self.responses[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def no_sleep(_seconds: float) -> None:
    return None


def test_automatic_mode_calls_stations_resolve_with_mac_and_ip() -> None:
    sequence = FakeResolveSequence([{"status": "assigned", "pc_name": "PC-11", "display_name": "Front 11"}])
    resolved = resolve_station_identity(
        resolve_fn=sequence,
        mac_provider=lambda: "AA:BB:CC:DD:EE:01",
        ip_provider=lambda: "192.168.0.55",
        sleep_fn=no_sleep,
        log=lambda _message: None,
    )
    assert sequence.calls == [("AA:BB:CC:DD:EE:01", "192.168.0.55")]
    assert resolved == ResolvedStation(pc_name="PC-11", display_name="Front 11")


def test_unclaimed_response_does_not_produce_an_identity_and_keeps_retrying() -> None:
    sequence = FakeResolveSequence(
        [
            {"status": "unclaimed", "pc_name": None},
            {"status": "unclaimed", "pc_name": None},
            {"status": "assigned", "pc_name": "PC-12", "display_name": None},
        ]
    )
    messages: list[str] = []
    resolved = resolve_station_identity(
        resolve_fn=sequence,
        mac_provider=lambda: "AA:BB:CC:DD:EE:02",
        sleep_fn=no_sleep,
        log=messages.append,
    )
    assert resolved.pc_name == "PC-12"
    assert len(sequence.calls) == 3
    assert messages.count(WAITING_MESSAGE) == 2


def test_malformed_or_failed_resolve_never_invents_an_identity() -> None:
    sequence = FakeResolveSequence(
        [
            requests.ConnectionError("server unreachable"),
            {"not": "a valid shape"},
            {"status": "assigned", "pc_name": ""},  # blank pc_name must not be accepted
            {"status": "assigned", "pc_name": "PC-13"},
        ]
    )
    resolved = resolve_station_identity(
        resolve_fn=sequence,
        mac_provider=lambda: "AA:BB:CC:DD:EE:03",
        sleep_fn=no_sleep,
        log=lambda _message: None,
    )
    assert resolved.pc_name == "PC-13"
    assert len(sequence.calls) == 4


def test_mac_detection_failure_keeps_waiting_without_calling_resolve() -> None:
    attempts = {"count": 0}

    def flaky_mac_provider() -> str:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise WiredMacDetectionError("no adapter yet")
        return "AA:BB:CC:DD:EE:04"

    sequence = FakeResolveSequence([{"status": "assigned", "pc_name": "PC-14"}])
    resolved = resolve_station_identity(
        resolve_fn=sequence,
        mac_provider=flaky_mac_provider,
        sleep_fn=no_sleep,
        log=lambda _message: None,
    )
    assert resolved.pc_name == "PC-14"
    assert attempts["count"] == 3
    assert len(sequence.calls) == 1  # resolve_fn was never called during the two failed MAC attempts


def test_resolution_stops_cleanly_when_bounded_and_never_assigned() -> None:
    sequence = FakeResolveSequence([{"status": "unclaimed", "pc_name": None}])
    try:
        resolve_station_identity(
            resolve_fn=sequence,
            mac_provider=lambda: "AA:BB:CC:DD:EE:05",
            sleep_fn=no_sleep,
            log=lambda _message: None,
            max_attempts=3,
        )
        raise AssertionError("expected StationResolutionError")
    except StationResolutionError:
        pass
    assert len(sequence.calls) == 3


# =====================================================================
# Section C: config loading + explicit-mode short-circuit (no server)
# =====================================================================


def test_generic_config_with_no_pc_name_key_is_accepted(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_host": "192.168.0.109",
                "server_port": 18001,
                "auto_connect": True,
            }
        ),
        encoding="utf-8",
    )
    config = load_client_config(config_path)
    assert config.pc_name == ""


def test_generic_config_with_blank_pc_name_is_accepted(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_host": "192.168.0.109",
                "server_port": 18001,
                "pc_name": "   ",
                "auto_connect": True,
            }
        ),
        encoding="utf-8",
    )
    config = load_client_config(config_path)
    assert config.pc_name == ""


def test_generic_config_with_blank_display_name_is_accepted(temp_directory: str) -> None:
    # The diskless installer writes the full 7-key schema with both pc_name
    # and display_name blank; loading must accept a present-but-empty
    # display_name the same as an absent one (fall back to the blank pc_name).
    config_path = Path(temp_directory) / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_host": "192.168.0.109",
                "server_port": 18001,
                "pc_name": "",
                "display_name": "",
                "auto_connect": True,
                "reconnect_interval_seconds": 5,
                "request_timeout_seconds": 5,
            }
        ),
        encoding="utf-8",
    )
    config = load_client_config(config_path)
    assert config.pc_name == ""
    assert config.display_name == ""


def test_explicit_pc_name_config_still_loads_and_validates_format(temp_directory: str) -> None:
    config_path = Path(temp_directory) / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "server_host": "192.168.0.109",
                "server_port": 18001,
                "pc_name": "PC-07",
                "display_name": "Front Row 07",
                "auto_connect": True,
            }
        ),
        encoding="utf-8",
    )
    config = load_client_config(config_path)
    assert config.pc_name == "PC-07"
    assert config.display_name == "Front Row 07"


def test_explicit_legacy_pc_name_resolution_short_circuits_without_network(temp_directory: str) -> None:
    from client.client import resolve_runtime_pc_name

    config = ClientConfig(
        server_host="unreachable.invalid",
        server_port=1,
        pc_name="PC-09",
        display_name="Explicit Station",
        auto_connect=True,
        reconnect_interval_seconds=2,
        request_timeout_seconds=2,
        config_path=Path(temp_directory) / "config.json",
    )
    pc_name, display_name = resolve_runtime_pc_name(config)
    assert pc_name == "PC-09"
    assert display_name == "Explicit Station"


def test_resolution_never_writes_the_config_file() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
        config_path = Path(temp_directory) / "config.json"
        original_bytes = json.dumps(
            {
                "server_host": "192.168.0.109",
                "server_port": 18001,
                "auto_connect": True,
            }
        ).encode("utf-8")
        config_path.write_bytes(original_bytes)

        config = load_client_config(config_path)
        assert config.pc_name == ""

        sequence = FakeResolveSequence([{"status": "assigned", "pc_name": "PC-15"}])
        resolved = resolve_station_identity(
            resolve_fn=sequence,
            mac_provider=lambda: "AA:BB:CC:DD:EE:06",
            sleep_fn=no_sleep,
            log=lambda _message: None,
        )
        assert resolved.pc_name == "PC-15"
        assert config_path.read_bytes() == original_bytes, "resolution must never write back to config.json"


# =====================================================================
# Section D: live-server end-to-end -- resolved name actually reaches
# /register, /heartbeat, and /ws/{pc_name}; restart re-resolves; a server-
# side reassignment changes the runtime pc_name on the next resolution.
# =====================================================================

SERVER_URL = "http://127.0.0.1:8766"


def wait_for_server() -> None:
    for _ in range(40):
        try:
            response = requests.get(f"{SERVER_URL}/", timeout=1)
            if response.status_code == 200:
                return
        except requests.RequestException:
            time.sleep(0.25)
    raise RuntimeError("Server did not start")


def bootstrap_owner_and_layout(database_path: str) -> None:
    os.environ["SECRET_GROUNDS_DB_PATH"] = database_path
    from server.database import initialize_database
    from server.pc_layout_repository import PCLayoutRepository
    from server.schemas import PCLayoutUpdate, UserCreateRequest
    from server.user_repository import UserRepository

    initialize_database()
    users = UserRepository()
    if users.get_by_username("station-owner") is None:
        users.create_user(
            UserCreateRequest(username="station-owner", display_name="Owner", password="owner-pass", role="owner")
        )
    layouts = PCLayoutRepository()
    for pc_name in ("PC-30", "PC-31"):
        layouts.upsert_layout(
            pc_name,
            PCLayoutUpdate(
                display_name=pc_name,
                area_label="TEST",
                layout_order=10,
                is_vip=False,
                is_enabled=True,
                income_group_id=None,
            ),
        )


def login_owner() -> requests.Session:
    session = requests.Session()
    response = session.post(
        f"{SERVER_URL}/auth/login",
        json={"username": "station-owner", "password": "owner-pass"},
        timeout=5,
    )
    response.raise_for_status()
    return session


def run_live_server_scenarios() -> None:
    from client.client import SecretGroundsClient
    from client.station_resolver import build_http_resolve_fn
    from client.websocket_transport import SimpleWebSocketClient

    database_path = str(PROJECT_ROOT / "tests" / "smoke_client_station_resolution.sqlite3")
    if Path(database_path).exists():
        Path(database_path).unlink()

    environment = os.environ.copy()
    environment["SECRET_GROUNDS_DB_PATH"] = database_path
    bootstrap_owner_and_layout(database_path)

    server_process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server.main:app", "--host", "127.0.0.1", "--port", "8766"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        wait_for_server()
        owner_session = login_owner()

        # --- unclaimed does not register/connect --------------------------
        resolve_fn = build_http_resolve_fn(SERVER_URL, request_timeout=5)
        first_response = resolve_fn("AA:BB:CC:DD:EE:30", "10.0.0.30")
        assert first_response["status"] == "unclaimed"
        assert first_response.get("pc_name") is None

        # The real proof that "unclaimed does not register/connect" is
        # structural, not observational: resolve_station_identity() only
        # ever returns once the server reports "assigned" -- nothing was
        # registered for this MAC above, and nothing downstream (register/
        # heartbeat/websocket) was even attempted yet.

        # --- owner assigns the MAC -> PC-30, then unclaimed -> assigned ---
        assign_response = owner_session.post(
            f"{SERVER_URL}/stations/AA:BB:CC:DD:EE:30/assign",
            json={"pc_name": "PC-30"},
            timeout=5,
        )
        assign_response.raise_for_status()
        assert assign_response.json()["pc_name"] == "PC-30"

        sequence_after_assignment = build_http_resolve_fn(SERVER_URL, request_timeout=5)
        resolved = resolve_station_identity(
            resolve_fn=sequence_after_assignment,
            mac_provider=lambda: "AA:BB:CC:DD:EE:30",
            sleep_fn=no_sleep,
            log=lambda _message: None,
        )
        assert resolved.pc_name == "PC-30"

        # --- resolved name reaches /register, /heartbeat, /ws/{pc_name} ---
        client = SecretGroundsClient(
            server_url=SERVER_URL,
            pc_name=resolved.pc_name,
            display_name=resolved.display_name,
            heartbeat_interval=5,
            request_timeout=5,
            enable_lock_screen=False,
        )
        try:
            assert client.build_payload()["pc_name"] == "PC-30"
            assert client.register_once() is True
            assert client.send_heartbeat() is True
            assert client.build_websocket_url() == f"ws://127.0.0.1:8766/ws/PC-30"

            with SimpleWebSocketClient(client.build_websocket_url(), timeout=5):
                pass  # a successful connect proves the server accepted pc_name=PC-30
        finally:
            client.stop()

        # --- restart resolves from the server again (no local caching) ---
        second_resolution_resolve_fn = build_http_resolve_fn(SERVER_URL, request_timeout=5)
        second_resolved = resolve_station_identity(
            resolve_fn=second_resolution_resolve_fn,
            mac_provider=lambda: "AA:BB:CC:DD:EE:30",
            sleep_fn=no_sleep,
            log=lambda _message: None,
        )
        assert second_resolved.pc_name == "PC-30"

        # --- server-side reassignment changes the runtime PC on next start
        reassign_response = owner_session.post(
            f"{SERVER_URL}/stations/AA:BB:CC:DD:EE:30/reassign",
            json={"pc_name": "PC-31"},
            timeout=5,
        )
        reassign_response.raise_for_status()

        post_reassignment_resolve_fn = build_http_resolve_fn(SERVER_URL, request_timeout=5)
        third_resolved = resolve_station_identity(
            resolve_fn=post_reassignment_resolve_fn,
            mac_provider=lambda: "AA:BB:CC:DD:EE:30",
            sleep_fn=no_sleep,
            log=lambda _message: None,
        )
        assert third_resolved.pc_name == "PC-31", "reassignment must change the runtime pc_name on the next resolve"

        print("Live-server station-resolution scenarios passed.")
    finally:
        server_process.terminate()
        try:
            server_process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server_process.kill()
        stdout, stderr = server_process.communicate()
        if server_process.returncode not in (0, None) and server_process.returncode != 1 and stderr:
            print("Server stderr (informational):")
            print(stderr[-2000:])
        if Path(database_path).exists():
            Path(database_path).unlink()


def main() -> None:
    fast_tests = [
        test_wired_adapter_preferred_over_wifi,
        test_wifi_never_selected_even_alone,
        test_virtual_ethernet_adapter_rejected_when_physical_wired_exists,
        test_virtual_ethernet_adapter_rejected_even_when_it_is_the_only_ethernet_typed_one,
        test_down_wired_adapter_not_selected,
        test_no_suitable_wired_adapter_fails_safely,
        test_adapter_enumeration_failure_fails_safely_not_with_a_guess,
        test_get_wired_mac_address_normalizes_and_selects_correctly,
        test_client_mac_normalization_matches_server_convention,
        test_automatic_mode_calls_stations_resolve_with_mac_and_ip,
        test_unclaimed_response_does_not_produce_an_identity_and_keeps_retrying,
        test_malformed_or_failed_resolve_never_invents_an_identity,
        test_mac_detection_failure_keeps_waiting_without_calling_resolve,
        test_resolution_stops_cleanly_when_bounded_and_never_assigned,
        test_resolution_never_writes_the_config_file,
    ]
    for test in fast_tests:
        test()
        print(f"{test.__name__} passed.")

    temp_dir_tests = [
        test_generic_config_with_no_pc_name_key_is_accepted,
        test_generic_config_with_blank_pc_name_is_accepted,
        test_generic_config_with_blank_display_name_is_accepted,
        test_explicit_pc_name_config_still_loads_and_validates_format,
        test_explicit_legacy_pc_name_resolution_short_circuits_without_network,
    ]
    for test in temp_dir_tests:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_directory:
            test(temp_directory)
        print(f"{test.__name__} passed.")

    run_live_server_scenarios()
    print("Client station-resolution (Stage 3) smoke tests passed.")


if __name__ == "__main__":
    main()
