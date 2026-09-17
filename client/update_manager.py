from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from pathlib import Path
from threading import Lock, Thread

import requests


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
    # Mirrors server/update_service.py's comparison intentionally -- the two
    # run in separate processes (this one frozen into the customer-PC exe)
    # so there is nothing to import between them. A simplified reading of
    # semver precedence: same base version, a final release outranks any
    # "-something" pre-release of it (this repo's own VERSION is
    # "1.0.0-rc.2" today, and "1.0.0" must compare as newer than that once
    # it ships).
    base, _, prerelease = version.partition("-")
    is_release = 0 if prerelease else 1
    return (_numeric_tuple(base), is_release, _numeric_tuple(prerelease))


def is_newer_version(candidate: str, current: str) -> bool:
    return _version_key(candidate) > _version_key(current)


class UpdateManager:
    """Downloads a staged client update in the background and hands off to
    the process supervisor to apply it.

    This process can never safely replace its own running .exe/_internal
    files -- Windows keeps them open, and mid-session file replacement could
    corrupt a live customer session. Instead it stages the new build fully
    verified under updates\\staged\\<version>\\, and only once the PC is
    genuinely idle (Decision #18: no active session means the lock screen is
    already showing) does it write updates\\pending-apply.json and exit.
    scripts\\run-client-supervised.ps1 already restarts an exited client
    unconditionally -- that is how it already recovers from a customer
    killing the client in Task Manager -- so it is the natural place to
    perform the actual file swap while nothing has the new files open.
    """

    def __init__(
        self,
        server_url: str,
        current_version: str,
        install_root: Path,
        request_timeout: int,
        http_session: requests.Session | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.current_version = current_version
        self.install_root = install_root
        self.updates_root = install_root.parent / "updates"
        self.staged_dir = self.updates_root / "staged"
        self.pending_apply_marker = self.updates_root / "pending-apply.json"
        self.request_timeout = request_timeout
        self.http_session = http_session or requests.Session()
        self._lock = Lock()
        self._in_progress_version: str | None = None

    def consider_update(self, version: str, sha256: str, size_bytes: int) -> None:
        if not version or not is_newer_version(version, self.current_version):
            return
        with self._lock:
            if self._already_staged(version) or self._in_progress_version == version:
                return
            self._in_progress_version = version
        Thread(
            target=self._download_and_stage,
            args=(version, sha256, size_bytes),
            daemon=True,
            name="secret-grounds-update-download",
        ).start()

    def check_server_for_update(self) -> None:
        """Poll fallback for a PC that was offline when the server pushed
        update_available -- call once per fresh WebSocket connection."""
        try:
            response = self.http_session.get(
                f"{self.server_url}/updates/client/manifest",
                timeout=self.request_timeout,
            )
        except requests.RequestException:
            return
        if response.status_code != 200:
            return
        try:
            payload = response.json()
            version = str(payload["version"])
            sha256 = str(payload["sha256"])
            size_bytes = int(payload["size_bytes"])
        except (ValueError, KeyError, TypeError):
            return
        self.consider_update(version, sha256, size_bytes)

    def _already_staged(self, version: str) -> bool:
        return (self.staged_dir / version / "staged.ok").is_file()

    def _download_and_stage(self, version: str, expected_sha256: str, size_bytes: int) -> None:
        try:
            version_dir = self.staged_dir / version
            if version_dir.exists():
                shutil.rmtree(version_dir, ignore_errors=True)
            version_dir.mkdir(parents=True, exist_ok=True)
            zip_path = version_dir / "update.zip"

            print(f"Downloading client update {version} ({size_bytes} bytes)...")
            digest = hashlib.sha256()
            with self.http_session.get(
                f"{self.server_url}/updates/client/package/{version}",
                stream=True,
                timeout=max(self.request_timeout, 30),
            ) as response:
                response.raise_for_status()
                with zip_path.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        file.write(chunk)
                        digest.update(chunk)

            actual_sha256 = digest.hexdigest()
            if actual_sha256.lower() != expected_sha256.lower():
                print(
                    f"Update {version} failed sha256 verification "
                    f"(expected {expected_sha256}, got {actual_sha256}); discarding."
                )
                shutil.rmtree(version_dir, ignore_errors=True)
                return

            extract_dir = version_dir / "package"
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path) as archive:
                archive.extractall(extract_dir)
            zip_path.unlink(missing_ok=True)

            if not (extract_dir / "SecretGroundsClient.exe").is_file():
                print(f"Update {version} package is missing SecretGroundsClient.exe; discarding.")
                shutil.rmtree(version_dir, ignore_errors=True)
                return

            (version_dir / "staged.ok").write_text(version, encoding="utf-8")
            print(f"Client update {version} downloaded, verified, and staged.")
        except Exception as error:
            print(f"Downloading client update {version} failed: {error}")
            shutil.rmtree(self.staged_dir / version, ignore_errors=True)
        finally:
            with self._lock:
                if self._in_progress_version == version:
                    self._in_progress_version = None

    def has_staged_update(self) -> str | None:
        if not self.staged_dir.is_dir():
            return None
        best_version: str | None = None
        for candidate_dir in self.staged_dir.iterdir():
            if not candidate_dir.is_dir() or not (candidate_dir / "staged.ok").is_file():
                continue
            version = candidate_dir.name
            if not is_newer_version(version, self.current_version):
                continue
            if best_version is None or is_newer_version(version, best_version):
                best_version = version
        return best_version

    def apply_if_idle(self, is_idle: bool) -> bool:
        """If a verified update is staged and the PC is idle, write the
        marker the supervisor script applies on next relaunch and return
        True so the caller can exit the process. Never call this while a
        session is active."""
        if not is_idle:
            return False
        version = self.has_staged_update()
        if version is None:
            return False
        self.updates_root.mkdir(parents=True, exist_ok=True)
        self.pending_apply_marker.write_text(
            json.dumps({"version": version}, indent=2),
            encoding="utf-8",
        )
        print(f"Update {version} is staged and the PC is idle; exiting for the supervisor to apply it.")
        return True
