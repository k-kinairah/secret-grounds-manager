"""Automatic (generic/diskless-image) station identity resolution.

Isolated from session/WebSocket/lock-screen logic by design: this module's
only job is to turn "no configured pc_name" into a resolved runtime pc_name
by asking the server's /stations/resolve endpoint, or to keep the caller
informed that the station is still waiting. See client/network_identity.py
for the wired-NIC MAC detection this depends on.

The server's station_assignments record is the sole authority here: this
module never fabricates, guesses, or derives a pc_name from IP address,
hostname, connection order, or MAC contents -- it only ever returns exactly
what the server reports, or keeps retrying.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from client.network_identity import WiredMacDetectionError, get_wired_mac_address
from client.utils import get_local_ip


RESOLVE_POLL_INTERVAL_SECONDS = 5
WAITING_MESSAGE = "This computer is waiting for station assignment."

MacProvider = Callable[[], str]
ResolveFn = Callable[[str, str], dict[str, Any]]
SleepFn = Callable[[float], None]
LogFn = Callable[[str], None]


class StationResolutionError(RuntimeError):
    """Raised only if resolution is stopped (max_attempts exhausted, used
    only by tests) before an identity was ever assigned -- production
    callers never pass max_attempts and this is never raised in practice."""


@dataclass(frozen=True)
class ResolvedStation:
    pc_name: str
    display_name: str | None


def build_http_resolve_fn(
    server_url: str,
    request_timeout: int,
    session: requests.Session | None = None,
    hostname: str | None = None,
) -> ResolveFn:
    """Production ResolveFn: POSTs to the server's /stations/resolve and
    returns the parsed JSON body. Kept separate from the polling loop below
    so the loop itself never depends on requests/HTTP directly and can be
    driven by a fake in tests.

    hostname is sent only in "@hostname" identity mode; when present the
    server may auto-bind this MAC to a matching, pre-configured station.
    """
    http_client = session or requests
    cleaned_hostname = hostname.strip() if isinstance(hostname, str) else None

    def resolve_fn(mac_address: str, ip_address: str) -> dict[str, Any]:
        body: dict[str, str] = {"mac_address": mac_address, "ip_address": ip_address}
        if cleaned_hostname:
            body["hostname"] = cleaned_hostname
        response = http_client.post(
            f"{server_url.rstrip('/')}/stations/resolve",
            json=body,
            timeout=request_timeout,
        )
        response.raise_for_status()
        return response.json()

    return resolve_fn


def resolve_station_identity(
    resolve_fn: ResolveFn,
    mac_provider: MacProvider = get_wired_mac_address,
    ip_provider: Callable[[], str] = get_local_ip,
    poll_interval_seconds: int = RESOLVE_POLL_INTERVAL_SECONDS,
    sleep_fn: SleepFn = time.sleep,
    log: LogFn = print,
    max_attempts: int | None = None,
) -> ResolvedStation:
    """Blocks (retrying at poll_interval_seconds) until the server reports
    this MAC as assigned, then returns the resolved station.

    Every failure mode here -- MAC detection failure, an unreachable
    server, a malformed response, or an explicit 'unclaimed' status -- is
    treated identically as "keep waiting," logged clearly, and retried.
    Nothing in this function ever invents a pc_name; it only ever returns
    what the server's response actually said. max_attempts is test-only
    (bounds an otherwise-infinite loop without real sleeping); production
    callers never set it.
    """
    attempt = 0
    while max_attempts is None or attempt < max_attempts:
        attempt += 1

        try:
            mac_address = mac_provider()
        except WiredMacDetectionError as error:
            log(f"Could not determine this PC's wired network identity: {error}")
            sleep_fn(poll_interval_seconds)
            continue

        ip_address = ip_provider()

        try:
            payload = resolve_fn(mac_address, ip_address)
        except Exception as error:
            # Deliberately broad: resolve_fn is an injectable boundary that
            # may raise anything from a transport error to a JSON-decode
            # error. Any failure here means "could not resolve right now,"
            # never a reason to fabricate an identity or crash startup.
            log(f"Could not reach the Secret Grounds server to resolve station identity: {error}")
            sleep_fn(poll_interval_seconds)
            continue

        if not isinstance(payload, dict):
            log("Station resolution received an unexpected response shape from the server.")
            sleep_fn(poll_interval_seconds)
            continue

        if payload.get("status") == "assigned":
            pc_name = payload.get("pc_name")
            if isinstance(pc_name, str) and pc_name.strip():
                display_name = payload.get("display_name")
                resolved_pc_name = pc_name.strip()
                log(f"Resolved runtime station identity: {resolved_pc_name}")
                return ResolvedStation(
                    pc_name=resolved_pc_name,
                    display_name=display_name if isinstance(display_name, str) and display_name.strip() else None,
                )
            log("Server reported this station as assigned but sent no usable pc_name; treating as unassigned.")
        else:
            log(WAITING_MESSAGE)

        sleep_fn(poll_interval_seconds)

    raise StationResolutionError("Station resolution stopped before an identity was assigned.")
