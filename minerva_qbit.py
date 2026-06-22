"""
minerva_qbit — qBittorrent Web API v2 client
============================================

Pure-Python client for the qBittorrent Web UI.  No Qt, no GUI deps — safe
to import from CLI scripts (``poc_minerva.py``) and GUI frontends alike.

The class is extracted from ``minerva_gui.py`` so the batch-download logic
can be shared between the GUI worker thread and the POC CLI.

Typical usage::

    client = QBittorrentClient("http://localhost:8080", "admin", "adminadmin")
    client.login()

    h = client.add_torrent_paused("/path/to/file.torrent", "/save/dir")
    files = client.get_files(h)
    client.set_file_priority(h, [i for i, f in enumerate(files) if skip], 0)
    client.resume(h)

    # Poll for completion
    while True:
        info = client.get_torrent_info(h)
        if info and info["progress"] >= 1.0:
            break
        time.sleep(1)
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import logging
import requests

from minerva_db import bdecode

log = logging.getLogger(__name__)


# ── Torrent info-hash computation ──────────────────────────────────────────

def compute_info_hash(torrent_path: Path) -> str | None:
    """Compute the v1 info-hash of a ``.torrent`` file.

    The info-hash is the SHA-1 digest of the *raw* bencoded ``info`` dict
    exactly as it appears in the file.  Re-encoding the decoded dict can
    produce a different byte sequence (key order, whitespace, etc.), so we
    slice the original bytes instead.

    Returns:
        The 40-char lowercase hex SHA-1 digest, or ``None`` if the file
        cannot be parsed or has no ``info`` dict.
    """
    try:
        data = torrent_path.read_bytes()
        info_pos = data.find(b"4:info")
        if info_pos == -1:
            return None
        start = info_pos + len(b"4:info")
        # Decode just to find where the raw info dict ends.
        _, end = bdecode(data, start)
        return hashlib.sha1(data[start:end]).hexdigest().lower()
    except Exception:
        log.warning("compute_info_hash: failed for %s", torrent_path, exc_info=True)
        return None


# ── Typed exceptions ───────────────────────────────────────────────────────

class QBittorrentError(RuntimeError):
    """Base exception for qBittorrent client errors."""


class QBittorrentConnectionError(QBittorrentError):
    """Raised when the client cannot connect to the qBittorrent Web UI."""


class QBittorrentApiError(QBittorrentError):
    """Raised when the qBittorrent API returns an error response."""


__all__ = [
    "QBittorrentClient",
    "QBittorrentError",
    "QBittorrentConnectionError",
    "QBittorrentApiError",
]


# ── Client ─────────────────────────────────────────────────────────────────

class QBittorrentClient:
    """Client for qBittorrent Web API (v2)."""

    def __init__(self, url: str = "http://localhost:8080",
                 username: str = "admin", password: str = "adminadmin"):
        self._url = url.rstrip("/")
        self._session = requests.Session()
        self._username = username
        self._password = password
        self._logged_in = False
        self._api_version: int = 0  # 0=unknown, 4=v4.x, 5=v5.x
        self._version_checked: bool = False

    @property
    def is_logged_in(self) -> bool:
        """Whether the client has a valid session."""
        return self._logged_in

    def clone(self) -> "QBittorrentClient":
        """Return a new client with the same endpoint and credentials.

        The clone owns an independent ``requests.Session`` and is therefore
        safe to use from another worker thread.
        """
        return QBittorrentClient(self._url, self._username, self._password)

    def login(self) -> bool:
        """Authenticate with qBittorrent Web UI.

        Returns:
            ``True`` on successful authentication.

        Raises:
            QBittorrentConnectionError: If the host is unreachable.
            QBittorrentApiError: If the API rejects credentials or
                returns an unexpected response.
        """
        try:
            r = self._session.post(
                f"{self._url}/api/v2/auth/login",
                data={"username": self._username, "password": self._password},
                timeout=5,
            )
            if r.status_code in (200, 204):
                has_sid = any(
                    k.startswith("QBT_SID") or k == "SID"
                    for k in self._session.cookies.keys()
                )
                if has_sid:
                    self._logged_in = True
                    return True
                raise QBittorrentApiError(
                    "Login succeeded but no SID cookie set"
                )
            elif r.status_code == 403 or "Fails" in r.text:
                raise QBittorrentApiError("Authentication failed")
            else:
                raise QBittorrentApiError(
                    f"Login returned {r.status_code}: {r.text[:200]}"
                )
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e

    def test_connection(self) -> str:
        """Test connection and return the qBittorrent version string.

        Returns:
            The version string (e.g. ``"v4.6.0"``).

        Raises:
            QBittorrentConnectionError: If the host is unreachable.
            QBittorrentApiError: If the API returns a non-200 response.
        """
        try:
            r = self._session.get(
                f"{self._url}/api/v2/app/version", timeout=5,
            )
            if r.status_code == 200:
                return r.text.strip()
            raise QBittorrentApiError(
                f"test_connection returned {r.status_code}: {r.text[:200]}"
            )
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e

    def add_torrent_paused(
        self,
        torrent_path: str,
        save_path: str,
        *,
        poll_attempts: int = 15,
        poll_interval: float = 0.2,
    ) -> str | None:
        """Add a torrent in paused state.

        Computes the torrent info-hash directly from the file so the correct
        torrent can be targeted regardless of naming collisions or latency.

        Returns:
            The torrent hash on success, or ``None`` if the torrent was
            accepted but the hash could not be resolved yet.

        Raises:
            QBittorrentConnectionError: If the host is unreachable.
            QBittorrentApiError: If the API returns an unexpected status.
            QBittorrentError: If the torrent file cannot be read.
        """
        self._ensure_api_version()
        info_hash = compute_info_hash(Path(torrent_path))
        try:
            with open(torrent_path, "rb") as f:
                # ponytail: send both keys so the call works on qBittorrent v4
                # (which knows "paused") and v5 (which knows "stopped") even if
                # our version detection is wrong.
                r = self._session.post(
                    f"{self._url}/api/v2/torrents/add",
                    files={"torrents": (Path(torrent_path).name, f)},
                    data={"savepath": save_path, "paused": "true", "stopped": "true"},
                    timeout=15,
                )
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e
        except OSError as e:
            raise QBittorrentError(
                f"Cannot read torrent file {torrent_path}"
            ) from e

        if info_hash is None:
            log.warning(
                "add_torrent_paused: could not compute info-hash from '%s'; "
                "falling back to name-based lookup",
                torrent_path,
            )
            return self._find_hash_by_torrent_name(Path(torrent_path).stem)

        # Poll qBittorrent until it reports the torrent we just added.
        # ponytail: adaptive delay — first check after 0.1s, then 0.2s;
        # returns as soon as the hash appears.
        import time
        delay = 0.1
        for attempt in range(poll_attempts):
            time.sleep(delay)
            info = self.get_torrent_info(info_hash)
            if info is not None:
                return info_hash
            delay = min(delay * 2, poll_interval)
        log.warning(
            "add_torrent_paused: hash %s not found in qBittorrent after %ds; "
            "removing orphaned torrent",
            info_hash, int(poll_attempts * poll_interval),
        )
        # Clean up the orphaned torrent so it doesn't linger paused in
        # qBittorrent with no way for the app to track or manage it.
        try:
            self.delete_torrent(info_hash, delete_files=False)
        except Exception:
            log.warning(
                "add_torrent_paused: could not remove orphaned torrent %s",
                info_hash, exc_info=True,
            )
        return None

    def get_files(self, torrent_hash: str) -> list[dict]:
        """Get file list for a torrent.

        Each entry has ``'index'``, ``'name'``, etc.

        Raises:
            QBittorrentConnectionError: If the host is unreachable.
            QBittorrentApiError: If the API returns a non-200 response.
        """
        try:
            r = self._session.get(
                f"{self._url}/api/v2/torrents/files",
                params={"hash": torrent_hash},
                timeout=5,
            )
            if r.status_code == 200:
                return r.json()
            raise QBittorrentApiError(
                f"get_files returned {r.status_code}: {r.text[:200]}"
            )
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e

    def set_file_priority(
        self, torrent_hash: str, file_ids: list[int], priority: int = 0,
    ):
        """Set file priority.  0 = skip, 1 = normal.

        Raises:
            QBittorrentConnectionError: If the host is unreachable.
            QBittorrentApiError: If the API returns a non-200 response.
        """
        if not file_ids:
            return
        try:
            ids = "|".join(str(i) for i in file_ids)
            r = self._session.post(
                f"{self._url}/api/v2/torrents/filePrio",
                data={
                    "hash": torrent_hash,
                    "id": ids,
                    "priority": str(priority),
                },
                timeout=5,
            )
            if r.status_code != 200:
                raise QBittorrentApiError(
                    f"set_file_priority returned {r.status_code}:"
                    f" {r.text[:200]}"
                )
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e

    def _action(self, endpoint: str, torrent_hash: str) -> None:
        """POST to a torrent action endpoint (pause/resume/start/stop)."""
        r = self._session.post(
            f"{self._url}/api/v2/torrents/{endpoint}",
            data={"hashes": torrent_hash},
            timeout=5,
        )
        if r.status_code != 200:
            raise QBittorrentApiError(
                f"{endpoint} returned {r.status_code}: {r.text[:200]}"
            )

    def _try_action(self, primary: str, fallback: str, torrent_hash: str) -> None:
        """Try primary action endpoint; fall back to alternate on HTTP 404.

        qBittorrent v5 uses ``start``/``stop`` while v4 uses ``resume``/``pause``.
        Detection can be wrong (proxies, custom builds, version strings), so we
        auto-correct when the first endpoint does not exist without making an
        extra round-trip on happy paths.
        """
        try:
            self._action(primary, torrent_hash)
        except QBittorrentApiError as exc:
            if "404" not in str(exc) and "does not exist" not in str(exc).lower():
                raise
            try:
                self._action(fallback, torrent_hash)
            except QBittorrentApiError:
                raise exc from None

    def pause(self, torrent_hash: str):
        """Pause a running torrent.

        Raises:
            QBittorrentConnectionError: If the host is unreachable.
            QBittorrentApiError: If the API returns a non-200 response.
        """
        self._ensure_api_version()
        primary = self._pause_endpoint()
        fallback = "pause" if primary == "stop" else "stop"
        try:
            self._try_action(primary, fallback, torrent_hash)
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e

    def resume(self, torrent_hash: str):
        """Resume a paused torrent.

        Raises:
            QBittorrentConnectionError: If the host is unreachable.
            QBittorrentApiError: If the API returns a non-200 response.
        """
        self._ensure_api_version()
        primary = self._resume_endpoint()
        fallback = "resume" if primary == "start" else "start"
        try:
            self._try_action(primary, fallback, torrent_hash)
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e

    def list_torrents(self, hashes: list[str] | None = None) -> list[dict]:
        """Return torrent snapshots, optionally restricted to *hashes*."""
        self._ensure_api_version()
        params = {}
        if hashes:
            params[self._info_hash_param()] = "|".join(hashes)
        try:
            r = self._session.get(
                f"{self._url}/api/v2/torrents/info",
                params=params,
                timeout=10,
            )
            if r.status_code == 200:
                return list(r.json())
            raise QBittorrentApiError(
                f"list_torrents returned {r.status_code}: {r.text[:200]}"
            )
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e

    def get_torrent_info(self, torrent_hash: str) -> dict | None:
        """Get torrent status info.

        Returns:
            A dict with torrent properties, or ``None`` if the torrent
            list is empty (e.g. hash not found).

        Raises:
            QBittorrentConnectionError: If the host is unreachable.
            QBittorrentApiError: If the API returns a non-200 response.
        """
        self._ensure_api_version()
        try:
            r = self._session.get(
                f"{self._url}/api/v2/torrents/info",
                params={self._info_hash_param(): torrent_hash},
                timeout=5,
            )
            if r.status_code == 200:
                items = r.json()
                if items:
                    return items[0]
                return None
            raise QBittorrentApiError(
                f"get_torrent_info returned {r.status_code}: {r.text[:200]}"
            )
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e

    def delete_torrent(self, torrent_hash: str, delete_files: bool = False):
        """Remove torrent from qBittorrent.

        Raises:
            QBittorrentConnectionError: If the host is unreachable.
            QBittorrentApiError: If the API returns a non-200 response.
        """
        try:
            r = self._session.post(
                f"{self._url}/api/v2/torrents/delete",
                data={
                    "hashes": torrent_hash,
                    "deleteFiles": "true" if delete_files else "false",
                },
                timeout=5,
            )
            if r.status_code != 200:
                raise QBittorrentApiError(
                    f"delete_torrent returned {r.status_code}: {r.text[:200]}"
                )
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e

    def rename(self, torrent_hash: str, new_name: str) -> None:
        """Rename a torrent in qBittorrent.

        POST /api/v2/torrents/rename with hash and name params.

        Raises:
            QBittorrentConnectionError: If the host is unreachable.
            QBittorrentApiError: If the API returns a non-200 response.
        """
        try:
            r = self._session.post(
                f"{self._url}/api/v2/torrents/rename",
                data={"hash": torrent_hash, "name": new_name},
                timeout=5,
            )
            if r.status_code != 200:
                raise QBittorrentApiError(
                    f"rename returned {r.status_code}: {r.text[:200]}"
                )
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e

    # ── Internal helpers ────────────────────────────────────────────────────

    def _ensure_api_version(self) -> None:
        """Detect API version if not already checked."""
        if not self._version_checked:
            self._api_version = self._detect_api_version()
            self._version_checked = True

    def _detect_api_version(self) -> int:
        """Query the app/version endpoint and return 4 or 5.

        Defaults to 4 if detection fails.
        """
        try:
            ver = self.test_connection().strip()
            # Some proxies return JSON-encoded strings like '"v5.0.0"'.
            if ver.startswith('"') and ver.endswith('"'):
                ver = ver[1:-1].strip()
            if ver.startswith("v5") or ver.startswith("5"):
                return 5
            if ver.startswith("v4") or ver.startswith("4"):
                return 4
            import re
            m = re.match(r"v?(\d+)", ver)
            if m:
                return int(m.group(1))
            return 4
        except Exception:
            log.warning(
                "Could not detect API version, defaulting to v4",
                exc_info=True,
            )
            return 4

    def _resume_endpoint(self) -> str:
        """Return the resume endpoint name for the detected API version."""
        return "start" if self._api_version >= 5 else "resume"

    def _pause_endpoint(self) -> str:
        """Return the pause endpoint name for the detected API version."""
        return "stop" if self._api_version >= 5 else "pause"

    def _info_hash_param(self) -> str:
        """Return the hash parameter name for torrents/info queries.

        Both qBittorrent v4 and v5 use ``hashes`` (plural) to filter the
        ``torrents/info`` list. ``torrents/files`` uses ``hash`` (singular).
        """
        return "hashes"

    def _find_hash_by_torrent_name(self, name: str) -> str | None:
        """Find recently-added torrent by name with exact match, then token match.

        Returns:
            The torrent hash, or ``None`` if no matching torrent is found.

        Raises:
            QBittorrentConnectionError: If the host is unreachable.
            QBittorrentApiError: If the API returns a non-200 response.
        """
        try:
            r = self._session.get(
                f"{self._url}/api/v2/torrents/info",
                timeout=10,
            )
            if r.status_code == 200:
                nm = name.lower().rstrip(".").removesuffix(".torrent")
                best = None
                best_added = -1
                for t in r.json():
                    tn = t.get("name", "").lower().rstrip(".").removesuffix(".torrent")
                    # Exact match first (safest)
                    if tn == nm:
                        return t["hash"]
                    # Token match: every word in the query must appear somewhere in the name.
                    # Filter out single-char / punctuation-only tokens (e.g. '-')
                    # that would trivially match every torrent name.
                    all_tokens = nm.split()
                    tokens = [
                        t for t in all_tokens
                        if len(t) >= 2 and not all(c in " -_.!?/\\" for c in t)
                    ]
                    if len(tokens) >= 3 and all(token in tn for token in tokens):
                        added = t.get("added_on", 0)
                        if added > best_added:
                            best_added = added
                            best = t["hash"]
                return best
            raise QBittorrentApiError(
                f"torrents/info returned {r.status_code}: {r.text[:200]}"
            )
        except requests.ConnectionError as e:
            raise QBittorrentConnectionError(
                f"Cannot connect to {self._url}"
            ) from e
        except requests.RequestException as e:
            raise QBittorrentApiError(f"Request failed: {e}") from e
