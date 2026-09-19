from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


def _numeric_tuple(value: str) -> tuple[int, ...]:
    # Only the leading numeric run of each dot-separated component is kept
    # (so "rc" reads as 0 and "2" as 2).
    parts: list[int] = []
    for chunk in value.split("."):
        digits = ""
        for character in chunk:
            if character.isdigit():
                digits += character
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _version_key(version: str) -> tuple[tuple[int, ...], int, tuple[int, ...]]:
    # A simplified reading of semver precedence: same base version, a final
    # release outranks any "-something" pre-release of it (this repo's own
    # VERSION is "1.0.0-rc.2" today, and "1.0.0" must compare as newer than
    # that once it ships). Two pre-releases of the same base fall back to
    # comparing their suffix numerically (rc.3 > rc.2).
    base, _, prerelease = version.partition("-")
    is_release = 0 if prerelease else 1
    return (_numeric_tuple(base), is_release, _numeric_tuple(prerelease))


def is_newer_version(candidate: str, current: str) -> bool:
    return _version_key(candidate) > _version_key(current)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class UpdateManifest:
    version: str
    sha256: str
    size_bytes: int
    filename: str


class UpdateService:
    """Owns the Cashier PC's client-update staging area.

    An operator builds a release on a dev machine (scripts\\stage-client-
    update.ps1) and copies the resulting <version>.zip and <version>.json
    into updates\\incoming\\ on the Cashier PC -- no internet access
    required. scan_incoming() validates that drop (version newer than this
    server, sha256 matches) and promotes it exactly once into
    updates\\staged\\<version>\\, which get_available_update()/
    get_package_path() then serve to customer-PC clients over HTTP so
    nobody has to walk a new build out to every gaming PC by hand.
    """

    def __init__(self, updates_root: Path, current_version: str) -> None:
        self.updates_root = updates_root
        self.incoming_dir = updates_root / "incoming"
        self.staged_dir = updates_root / "staged"
        self.current_version = current_version
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.staged_dir.mkdir(parents=True, exist_ok=True)
        self._last_scan_error: str | None = None

    @property
    def last_scan_error(self) -> str | None:
        return self._last_scan_error

    def _read_staged_manifest(self) -> UpdateManifest | None:
        candidates = sorted(self.staged_dir.glob("*/manifest.json"))
        if not candidates:
            return None
        manifest_path = candidates[-1]
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = UpdateManifest(
                version=str(payload["version"]),
                sha256=str(payload["sha256"]),
                size_bytes=int(payload["size_bytes"]),
                filename=str(payload["filename"]),
            )
        except (OSError, KeyError, ValueError, json.JSONDecodeError):
            return None
        if not (manifest_path.parent / manifest.filename).is_file():
            return None
        return manifest

    def get_available_update(self) -> UpdateManifest | None:
        manifest = self._read_staged_manifest()
        if manifest is None or not is_newer_version(manifest.version, self.current_version):
            return None
        return manifest

    def get_package_path(self, version: str) -> Path | None:
        manifest = self._read_staged_manifest()
        if manifest is None or manifest.version != version:
            return None
        package_path = self.staged_dir / version / manifest.filename
        return package_path if package_path.is_file() else None

    def scan_incoming(self) -> UpdateManifest | None:
        """Validate and promote one drop from incoming/ into staged/.

        Returns the newly staged manifest, or None if there was nothing new
        or nothing valid to stage (see last_scan_error for why)."""
        manifest_files = sorted(self.incoming_dir.glob("*.json"))
        if not manifest_files:
            self._last_scan_error = None
            return None

        best_manifest: UpdateManifest | None = None
        best_manifest_path: Path | None = None
        for manifest_path in manifest_files:
            try:
                payload = json.loads(manifest_path.read_text(encoding="utf-8"))
                candidate = UpdateManifest(
                    version=str(payload["version"]),
                    sha256=str(payload["sha256"]).lower(),
                    size_bytes=int(payload["size_bytes"]),
                    filename=str(payload["filename"]),
                )
            except (OSError, KeyError, ValueError, json.JSONDecodeError) as error:
                self._last_scan_error = f"{manifest_path.name}: invalid manifest ({error})"
                continue
            if not (manifest_path.parent / candidate.filename).is_file():
                self._last_scan_error = f"{manifest_path.name}: package file {candidate.filename} is missing"
                continue
            # If the operator left more than one drop in incoming/, take the
            # highest version and leave the rest untouched rather than
            # guessing which one was meant -- they stay visible for cleanup.
            if best_manifest is None or is_newer_version(candidate.version, best_manifest.version):
                best_manifest, best_manifest_path = candidate, manifest_path

        if best_manifest is None or best_manifest_path is None:
            return None

        if not is_newer_version(best_manifest.version, self.current_version):
            self._last_scan_error = (
                f"{best_manifest_path.name}: version {best_manifest.version} is not newer than "
                f"this server's own {self.current_version}"
            )
            return None

        already_staged = self._read_staged_manifest()
        if already_staged is not None and already_staged.version == best_manifest.version:
            return None

        package_path = best_manifest_path.parent / best_manifest.filename
        actual_sha256 = _sha256_file(package_path)
        if actual_sha256 != best_manifest.sha256:
            self._last_scan_error = (
                f"{best_manifest_path.name}: sha256 mismatch (manifest says {best_manifest.sha256}, "
                f"file hashes to {actual_sha256}) -- rebuild the package rather than editing the manifest"
            )
            return None

        destination_dir = self.staged_dir / best_manifest.version
        if destination_dir.exists():
            shutil.rmtree(destination_dir)
        destination_dir.mkdir(parents=True)
        shutil.move(str(package_path), str(destination_dir / best_manifest.filename))
        (destination_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "version": best_manifest.version,
                    "sha256": best_manifest.sha256,
                    "size_bytes": best_manifest.size_bytes,
                    "filename": best_manifest.filename,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        best_manifest_path.unlink(missing_ok=True)

        for stale_dir in self.staged_dir.iterdir():
            if stale_dir.is_dir() and stale_dir.name != best_manifest.version:
                shutil.rmtree(stale_dir, ignore_errors=True)

        self._last_scan_error = None
        return best_manifest
