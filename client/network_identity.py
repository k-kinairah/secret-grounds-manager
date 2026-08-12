"""Wired Ethernet NIC identification, isolated from every other client
concern (session/WebSocket/lock-screen logic never imports this module's
internals -- only the normalized MAC string it produces).

uuid.getnode() is deliberately NOT used for station identity: on Windows it
has no adapter-selection logic at all and can return a Wi-Fi, Bluetooth,
VPN, or virtual adapter's address depending on OS enumeration order, with
no guarantee of stability across reboots. This module instead enumerates
every network adapter Windows reports and applies an explicit, conservative
selection policy: physical + Ethernet-typed + up + not virtual-by-name.

select_wired_adapter() is a pure function over a list of NetworkAdapter
records so it can be fully unit-tested with fabricated adapter lists,
independent of the real ctypes/Windows enumeration in
list_windows_network_adapters().
"""

from __future__ import annotations

import ctypes
import platform
from dataclasses import dataclass
from typing import Callable


# Stable, documented IANA interface types (ifType) -- see RFC 2863 / the
# Windows IF_TYPE_* constants. Only Ethernet is ever a candidate; Wi-Fi is
# never selected even as a fallback, since this shop's stations are wired.
IF_TYPE_ETHERNET_CSMACD = 6
IF_TYPE_IEEE80211 = 71
IF_TYPE_SOFTWARE_LOOPBACK = 24
IF_TYPE_TUNNEL = 131

IF_OPER_STATUS_UP = 1

# Second-line defense: some virtualization stacks (Hyper-V vSwitch, VMware,
# VPN clients) present their virtual adapter as ordinary Ethernet-typed
# (ifType 6), so the ifType filter alone cannot exclude them. Matched
# case-insensitively against the adapter's OS-reported description.
_VIRTUAL_DESCRIPTION_MARKERS = (
    "virtual",
    "vpn",
    "hyper-v",
    "vmware",
    "virtualbox",
    "tap-windows",
    "tap adapter",
    "loopback",
    "tunnel",
    "bluetooth",
    "wan miniport",
    "teredo",
    "pseudo",
)


class WiredMacDetectionError(RuntimeError):
    """Raised whenever a usable wired Ethernet MAC cannot be confidently
    determined. Callers must treat this as "keep waiting," never as
    license to guess a different adapter or fall back to a fabricated
    identity."""


@dataclass(frozen=True)
class NetworkAdapter:
    name: str
    description: str
    mac_address: str | None
    if_type: int
    is_up: bool
    is_physical: bool = True


def is_virtual_adapter(adapter: NetworkAdapter) -> bool:
    if not adapter.is_physical:
        return True
    description_lower = adapter.description.lower()
    return any(marker in description_lower for marker in _VIRTUAL_DESCRIPTION_MARKERS)


def select_wired_adapter(adapters: list[NetworkAdapter]) -> NetworkAdapter | None:
    """Conservative, deterministic selection: the first UP, physical,
    Ethernet-typed, non-virtual, MAC-bearing adapter -- or None if no such
    adapter exists. Never selects Wi-Fi, loopback, or tunnel adapters
    (excluded by if_type), and never selects a description-flagged virtual
    Ethernet adapter (Hyper-V/VMware/VPN/etc.)."""
    candidates = [
        adapter
        for adapter in adapters
        if adapter.if_type == IF_TYPE_ETHERNET_CSMACD
        and adapter.is_up
        and adapter.mac_address
        and not is_virtual_adapter(adapter)
    ]
    if not candidates:
        return None
    return candidates[0]


def normalize_mac_address(value: str | None) -> str | None:
    """Same normalization convention as server/schemas.py's
    normalize_mac_address: strip separators, require 12 hex digits, format
    as AA:BB:CC:DD:EE:FF uppercase. Duplicated (not imported) because the
    client ships without the server package -- see
    docs/PACKAGING_READINESS.md. Returns None on invalid input rather than
    raising, since callers here treat "no usable MAC" as a single outcome
    regardless of whether an adapter was missing or malformed."""
    if value is None:
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    normalized = cleaned.replace("-", "").replace(":", "").replace(".", "")
    if len(normalized) != 12 or any(character not in "0123456789abcdefABCDEF" for character in normalized):
        return None
    return ":".join(normalized[index : index + 2].upper() for index in range(0, 12, 2))


def get_wired_mac_address(
    adapter_source: Callable[[], list[NetworkAdapter]] | None = None,
) -> str:
    """Returns a normalized MAC address for the physical wired Ethernet
    adapter, or raises WiredMacDetectionError with a clear, loggable
    reason. adapter_source is injectable for tests; production callers
    omit it and get the real Windows enumeration."""
    list_adapters = adapter_source or list_windows_network_adapters

    try:
        adapters = list_adapters()
    except WiredMacDetectionError:
        raise
    except Exception as error:
        raise WiredMacDetectionError(f"Could not enumerate network adapters: {error}") from error

    selected = select_wired_adapter(adapters)
    if selected is None:
        seen = ", ".join(f"{adapter.description!r} (ifType={adapter.if_type})" for adapter in adapters) or "none"
        raise WiredMacDetectionError(
            "No active physical wired Ethernet adapter was found. "
            f"Adapters seen: {seen}."
        )

    normalized = normalize_mac_address(selected.mac_address)
    if normalized is None:
        raise WiredMacDetectionError(
            f"Wired adapter {selected.description!r} reported an unusable MAC address."
        )
    return normalized


# --- Real Windows enumeration (ctypes, no new pip dependency) ---------------


class _IP_ADAPTER_ADDRESSES(ctypes.Structure):
    pass


_IP_ADAPTER_ADDRESSES._fields_ = [
    ("Length", ctypes.c_ulong),
    ("IfIndex", ctypes.c_ulong),
    ("Next", ctypes.POINTER(_IP_ADAPTER_ADDRESSES)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.c_void_p),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_ubyte * 8),
    ("PhysicalAddressLength", ctypes.c_ulong),
    ("Flags", ctypes.c_ulong),
    ("Mtu", ctypes.c_ulong),
    ("IfType", ctypes.c_ulong),
    ("OperStatus", ctypes.c_uint),
    # Additional fields exist in later Windows versions; nothing beyond
    # this point is read, so they are intentionally left undeclared.
]

_AF_UNSPEC = 0
_ERROR_BUFFER_OVERFLOW = 111
_ERROR_SUCCESS = 0
_GAA_FLAG_SKIP_ANYCAST = 0x0002
_GAA_FLAG_SKIP_MULTICAST = 0x0004
_GAA_FLAG_SKIP_DNS_SERVER = 0x0008
# Microsoft's own GetAdaptersAddresses sample code uses 15000 bytes as the
# starting guess; the call reports back the required size on overflow so
# this only needs to be a reasonable first attempt, not exact.
_INITIAL_BUFFER_SIZE = 15000


def _raw_get_adapters_addresses() -> ctypes.Array:
    flags = _GAA_FLAG_SKIP_ANYCAST | _GAA_FLAG_SKIP_MULTICAST | _GAA_FLAG_SKIP_DNS_SERVER
    size = ctypes.c_ulong(_INITIAL_BUFFER_SIZE)
    buffer = ctypes.create_string_buffer(size.value)
    iphlpapi = ctypes.windll.iphlpapi  # type: ignore[attr-defined]

    result = iphlpapi.GetAdaptersAddresses(
        _AF_UNSPEC, flags, None, ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_ADDRESSES)), ctypes.byref(size)
    )
    if result == _ERROR_BUFFER_OVERFLOW:
        buffer = ctypes.create_string_buffer(size.value)
        result = iphlpapi.GetAdaptersAddresses(
            _AF_UNSPEC, flags, None, ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_ADDRESSES)), ctypes.byref(size)
        )
    if result != _ERROR_SUCCESS:
        raise WiredMacDetectionError(f"GetAdaptersAddresses failed with Windows error code {result}.")
    return buffer


def list_windows_network_adapters() -> list[NetworkAdapter]:
    if platform.system().lower() != "windows":
        raise WiredMacDetectionError("Wired adapter detection is only implemented for Windows.")

    buffer = _raw_get_adapters_addresses()
    adapters: list[NetworkAdapter] = []
    current_ptr = ctypes.cast(buffer, ctypes.POINTER(_IP_ADAPTER_ADDRESSES))
    while current_ptr:
        entry = current_ptr.contents
        mac_length = entry.PhysicalAddressLength
        mac_bytes = bytes(entry.PhysicalAddress[:mac_length]) if mac_length else b""
        mac_address = ":".join(f"{byte:02X}" for byte in mac_bytes) if mac_bytes else None
        adapters.append(
            NetworkAdapter(
                name=(entry.AdapterName.decode("ascii", errors="ignore") if entry.AdapterName else ""),
                description=entry.Description or "",
                mac_address=mac_address,
                if_type=entry.IfType,
                is_up=(entry.OperStatus == IF_OPER_STATUS_UP),
            )
        )
        current_ptr = entry.Next
    return adapters
