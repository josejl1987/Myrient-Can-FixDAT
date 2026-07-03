"""Archive.org search client and candidate provider.

Provides a search client wrapping the internetarchive library, plus a
CandidateProvider that searches Archive.org for ROM files matching a DAT entry.
"""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import quote

from internetarchive import get_item, search_items

from minerva.domain.sources import Candidate, CandidateProvider, DownloadSource
from minerva_db import DatEntry, core_title, stem_from_romname, title_keywords

log = logging.getLogger(__name__)

# ── ROM file extensions ───────────────────────────────────────────────────────
_ROM_EXTENSIONS = frozenset({
    ".zip", ".7z",
    ".chd", ".iso", ".bin", ".cue", ".gdi", ".mds", ".ccd", ".img", ".nrg",
    ".mdf", ".cso", ".ciso", ".rvz", ".wbfs", ".wad", ".wud", ".xci", ".nsp",
    ".nsz",
    ".nes", ".unf", ".unif",
    ".sfc", ".smc", ".swc", ".fig",
    ".gb", ".gbc", ".gba", ".nds", ".dsi", ".ids",
    ".n64", ".z64", ".v64",
    ".pce", ".sgx",
    ".md", ".gen", ".smd",
    ".a26", ".a52", ".col", ".int", ".lnx",
    ".ngp", ".ngc",
    ".vb", ".j64", ".jag",
    ".ws", ".wsc",
    ".rom",
    ".x68k",
    ".st", ".msa", ".dsk", ".adf", ".adz", ".ipf",
    ".d64", ".t64", ".tap", ".prg", ".p00", ".crt",
})

# ── Collection confidence boosts ─────────────────────────────────────────────
_COLLECTION_BOOSTS: dict[str, float] = {
    "opensource_media": 1.20,
    "no-intro": 1.30,
    "redump": 1.30,
    "tosec": 1.25,
    "software": 1.15,
    "softwarelibrary": 1.10,
    "cdromimages": 1.15,
    "nintendo_roms": 1.15,
    "sega_roms": 1.15,
}

# ── Cache settings ────────────────────────────────────────────────────────────
_CACHE_TTL = 300  # 5 minutes


def _is_rom_file(filename: str) -> bool:
    """Return True if *filename* ends with a known ROM/ROM-hosting extension."""
    _, _, ext = filename.rpartition(".")
    return f".{ext.lower()}" in _ROM_EXTENSIONS


def _collection_boost(collection: str | list[str] | None) -> float:
    """Return a confidence multiplier for known preservation collections.

    *collection* may be a single collection name (string) or a list of
    collection names (as returned by the IA metadata API).
    """
    if not collection:
        return 1.0
    names: list[str] = collection if isinstance(collection, list) else [collection]
    for name in names:
        boost = _COLLECTION_BOOSTS.get(name.lower())
        if boost is not None:
            return boost
    return 1.0


def _keyword_overlap(entry_keywords: set[str], title: str) -> float:
    """Return the fraction of *entry_keywords* that appear in *title*.

    Returns 0.0 when entry_keywords is empty, since there's nothing to match.
    """
    if not entry_keywords:
        return 0.0
    title_lower = title.lower()
    matches = sum(1 for kw in entry_keywords if kw in title_lower)
    return matches / len(entry_keywords)


# ── In-memory TTL cache decorator ─────────────────────────────────────────────
def _ttl_cache(ttl: int = _CACHE_TTL):
    """Decorator that caches the wrapped method's return value for *ttl* seconds.

    Cache is per-method (one dict per decorated method).  Cache keys are
    positional+keyword arguments, so different callers with different args get
    independent entries.
    """
    def decorator(method):
        cache: dict[str, tuple[float, Any]] = {}

        def wrapper(self, *args, **kwargs):
            key = str((args, tuple(sorted(kwargs.items()))))
            now = time.monotonic()
            cached = cache.get(key)
            if cached is not None and (now - cached[0]) < ttl:
                return cached[1]
            result = method(self, *args, **kwargs)
            cache[key] = (now, result)
            return result

        return wrapper

    return decorator


# ── Client ────────────────────────────────────────────────────────────────────
class ArchiveOrgSearchClient:
    """Client for searching and retrieving items from archive.org.

    Wraps the ``internetarchive`` library with a simple caching layer.
    """

    @_ttl_cache()
    def search_items(self, query: str, rows: int = 20) -> list[dict[str, Any]]:
        """Search archive.org items.

        Returns a list of result dicts (one per item).  Each dict contains the
        fields returned by the IA advanced search API; ``identifier`` is always
        present.
        """
        results: list[dict[str, Any]] = []
        for result in search_items(query, params={"rows": rows}):
            results.append(dict(result))
        return results

    @_ttl_cache()
    def get_item(self, identifier: str):
        """Fetch an item's full metadata + file listing.

        Returns an ``internetarchive.Item`` which has ``.files`` (list of dicts),
        ``.metadata`` (dict), ``.identifier`` (str), and ``.server`` (str).
        """
        return get_item(identifier)

    @staticmethod
    def build_download_url(identifier: str, filename: str) -> str:
        """Build the HTTPS download URL for a file hosted on archive.org."""
        return f"https://archive.org/download/{identifier}/{quote(filename)}"


# ── Candidate provider ────────────────────────────────────────────────────────
class ArchiveOrgCandidateProvider:
    """CandidateProvider that searches Archive.org for ROM matches.

    Implements the ``CandidateProvider`` protocol.
    """

    def __init__(self, client: ArchiveOrgSearchClient | None = None) -> None:
        self._client = client or ArchiveOrgSearchClient()

    def search(self, entry: DatEntry, system: str | None = None) -> list[Candidate]:
        """Search Archive.org and return candidates matching *entry*."""
        stem = stem_from_romname(entry.filename)
        query = core_title(stem)
        if not query:
            return []

        entry_keywords = title_keywords(stem)
        results = self._client.search_items(query, rows=20)

        candidates: list[Candidate] = []
        seen: set[str] = set()  # dedup by source_ref

        for result in results:
            identifier = result.get("identifier", "")
            item_title = result.get("title", "")
            collections = result.get("collection", []) or []

            # Optional system filter: limit to items where collection or
            # identifier hints at the target system.
            # (Applied loosely here; strict filtering is the caller's
            # responsibility.)

            # Keyword overlap between entry stem and item title.
            overlap = _keyword_overlap(entry_keywords, item_title)

            # Collection confidence boost.
            boost = _collection_boost(collections if isinstance(collections, list) else (collections,))

            # Only consider items with non-zero keyword overlap.
            if overlap <= 0 and not query.lower() in item_title.lower():
                continue

            # Fetch item metadata (files, torrents).
            try:
                item = self._client.get_item(identifier)
            except Exception:
                log.warning("Failed to fetch item %s", identifier, exc_info=True)
                continue

            # Filter to ROM files.
            rom_files = [f for f in (item.files or []) if _is_rom_file(f.get("name", ""))]
            if not rom_files:
                continue

            # Check for torrent availability.
            has_torrent = any(
                f.get("name", "").endswith("_archive.torrent")
                for f in (item.files or [])
            )

            # Determine source.
            source = DownloadSource.ARCHIVE_ORG_TORRENT if has_torrent else DownloadSource.ARCHIVE_ORG_HTTP
            torrent_url = f"https://archive.org/download/{identifier}/{identifier}_archive.torrent" if has_torrent else None

            # Build confidence score.
            # Base: keyword overlap (0.0–1.0) * collection boost, capped at 0.95
            # to leave room for more precise matching layers.
            base_conf = min(overlap * boost, 0.95)
            if base_conf <= 0:
                base_conf = 0.3  # minimum confidence for any match

            # Create one Candidate per ROM file.
            for rom in rom_files:
                filename = rom.get("name", "")
                size = int(rom.get("size", 0) or 0)
                source_ref = f"{identifier}/{filename}"

                if source_ref in seen:
                    continue
                seen.add(source_ref)

                c = Candidate(
                    title=filename.rsplit("/", 1)[-1],  # basename
                    size=size,
                    confidence=base_conf,
                    method="keyword_overlap",
                    source=source,
                    source_ref=source_ref,
                    collection=item.metadata.get("collection", ""),
                    system=system or "",
                    regions=(),
                    reasons=[f"Matched via archive.org item '{item_title}'"],
                    torrent_url=torrent_url,
                )
                candidates.append(c)

        # Sort by confidence descending, then by size ascending.
        candidates.sort(key=lambda c: (-c.confidence, c.size))
        return candidates
