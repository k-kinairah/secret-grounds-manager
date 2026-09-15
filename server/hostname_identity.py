"""Derive a Secret Grounds station name from a booting client's Windows
computer name.

Used only by the /stations/resolve auto-adopt path: a diskless client in
"@hostname" identity mode reports its OS hostname, and if that hostname
maps cleanly to a station that the owner has *already* configured under PC
Layout, the server binds this MAC to it with no dashboard step. This
mirrors how iCafe8 / CCBoot-style setups already hand each seat a stable
per-MAC computer name before Windows boots.

This module is pure (a string in, a string or None out) so it can be
unit-tested without a server. It never invents a station: it only ever
normalizes a hostname into a *candidate* name; server/main.py still
requires a matching pc_layouts row before anything is bound.
"""

from __future__ import annotations

import re

# Windows' own auto-generated / placeholder names. A seat still carrying
# one of these has NOT been given a real identity by the diskless system,
# so adopting it would just recreate the "every PC is DESKTOP-XXolpoint"
# collision we are trying to avoid.
_PLACEHOLDER_PREFIXES = ("DESKTOP-", "WIN-", "MININT-", "MINWINPC")
_PLACEHOLDER_EXACT = {"", "LOCALHOST", "PC", "COMPUTER", "USER-PC"}

# A conservative shape for a real seat name: starts alphanumeric, then
# letters / digits / dash / underscore, 1..32 chars total. Anything with a
# space, a dot (beyond the domain suffix stripped below), or punctuation is
# rejected rather than guessed at.
_SANE_NAME = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")

# "PC14", "PC-14", "PC-014", "PC 14" (space already rejected above) all mean
# the same seat -- fold them to a canonical "PC-NN" so the owner only has to
# create the station once under PC Layout. Zero-padded to 2 digits (PC-01,
# not PC-1) to match seed_default_pc_layouts()'s own PC-01..PC-15 naming --
# a bare PC-5 would silently never match the seeded PC-05.
_PC_NUMBER = re.compile(r"^PC[-_]?0*([0-9]{1,3})$")


def derive_station_name_from_hostname(hostname: str | None) -> str | None:
    """Return the canonical station name a hostname maps to, or None if the
    hostname is empty, a Windows placeholder, or not a clean seat name.

    Normalization: strip any DNS suffix, upper-case, then fold the common
    PC-number spellings (PC5 / PC05 / PC-5 / PC-005) to "PC-05".
    """
    if not hostname:
        return None

    candidate = hostname.strip().split(".", 1)[0].strip().upper()
    if candidate in _PLACEHOLDER_EXACT:
        return None
    if any(candidate.startswith(prefix) for prefix in _PLACEHOLDER_PREFIXES):
        return None
    if not _SANE_NAME.match(candidate):
        return None

    pc_number_match = _PC_NUMBER.match(candidate)
    if pc_number_match is not None:
        return f"PC-{int(pc_number_match.group(1)):02d}"
    return candidate
