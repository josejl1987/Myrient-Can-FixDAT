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

from minerva.domain.sources import Candidate, DownloadSource
from minerva.matching.scoring import core_title, title_keywords
from minerva.parsers.dat_parser import DatEntry
from minerva_db import stem_from_romname

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


def _build_archive_org_query(title: str) -> str:
    """Build a safe archive.org advanced-search query from a cleaned title.

    The archive.org search endpoint uses Lucene syntax, so bare words like
    ``sonic and knuckles`` are interpreted as boolean operators. We quote the
    title and restrict to ``mediatype:data`` to get ROM/data oriented items.
    """
    if not title:
        return ""
    # Escape quotes in the title so they don't break the phrase query.
    escaped = title.replace('"', '\\"')
    return f'mediatype:data AND title:"{escaped}"'


def _filename_similarity(dat_filename: str, rom_filename: str) -> float:
    """Normalized similarity between DAT entry filename and ROM filename."""
    from difflib import SequenceMatcher
    from pathlib import PurePosixPath
    dat_stem = PurePosixPath(dat_filename).stem.lower()
    rom_stem = PurePosixPath(rom_filename).stem.lower()
    return SequenceMatcher(None, dat_stem, rom_stem).ratio()


def _score_rom_file(
    prior: float,
    dat_filename: str,
    rom_filename: str,
    dat_size: int,
    rom_size: int,
) -> float:
    """Compute per-file confidence score.

    Combines the item-level prior (weight 0.3), filename similarity
    (weight 0.4), exact size match (+0.2), extension match (+0.05),
    and region evidence (+0.05). Capped at 0.99.
    """
    from pathlib import PurePosixPath
    filename_sim = _filename_similarity(dat_filename, rom_filename)
    size_bonus = 0.2 if dat_size > 0 and rom_size == dat_size else 0.0
    dat_ext = PurePosixPath(dat_filename).suffix.lower()
    rom_ext = PurePosixPath(rom_filename).suffix.lower()
    ext_bonus = 0.05 if dat_ext and dat_ext == rom_ext else 0.0
    # Region evidence: award bonus only when both filenames share the
    # same region token (agreement), not just presence in the ROM.
    region_tokens = {"usa", "europe", "japan", "world", "(u)", "(e)", "(j)", "(w)"}
    dat_lower = dat_filename.lower()
    rom_lower = rom_filename.lower()
    dat_regions = {t for t in region_tokens if t in dat_lower}
    rom_regions = {t for t in region_tokens if t in rom_lower}
    region_bonus = 0.05 if dat_regions and dat_regions & rom_regions else 0.0
    return min(prior * 0.3 + filename_sim * 0.4 + size_bonus + ext_bonus + region_bonus, 0.99)


def _infer_system_from_item(
    metadata: dict,
    collections: list,
    identifier: str,
) -> str:
    """Best-effort system inference from item metadata, not from the request.

    Returns "" if no system can be determined.
    """
    # Check collection names for known system hints.
    collection_map = {
        "nintendo_roms": "Nintendo",
        "sega_roms": "Sega",
        "no-intro": "",
        "redump": "",
        "tosec": "",
        "software": "",
        "softwarelibrary": "",
    }
    for col in collections:
        col_lower = str(col).lower()
        for key, sys_name in collection_map.items():
            if key in col_lower and sys_name:
                return sys_name
    # Check metadata for a 'subject' or 'description' field that might
    # contain a system name.
    subject = str(metadata.get("subject", "")).lower()
    system_hints = {
        "nes": "Nintendo - Nintendo Entertainment System",
        "snes": "Nintendo - Super Nintendo Entertainment System",
        "n64": "Nintendo - Nintendo 64",
        "gameboy": "Nintendo - Game Boy",
        "gba": "Nintendo - Game Boy Advance",
        "nds": "Nintendo - Nintendo DS",
        "genesis": "Sega - Mega Drive - Genesis",
        "megadrive": "Sega - Mega Drive - Genesis",
        "psx": "Sony - PlayStation",
        "ps1": "Sony - PlayStation",
    }
    for hint, sys_name in system_hints.items():
        if hint in subject:
            return sys_name
    return ""

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
        """Search Archive.org and return candidates matching *entry*.

        Scoring is per-file: each ROM file in an item gets an independent
        confidence score based on filename similarity, size match, and
        extension/region evidence. The item-level keyword overlap is used
        only as a weak prior.
        """
        stem = stem_from_romname(entry.filename)
        query = _build_archive_org_query(core_title(stem))
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

            # Keyword overlap between entry stem and item title (weak prior).
            overlap = _keyword_overlap(entry_keywords, item_title)

            # Collection confidence boost.
            boost = _collection_boost(collections if isinstance(collections, list) else (collections,))

            # Only consider items with non-zero keyword overlap.
            if overlap <= 0 and query.lower() not in item_title.lower():
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

            # Check for torrent availability — use actual torrent filename
            # from item files, not a constructed guess (P1-6).
            torrent_file = next(
                (f.get("name", "") for f in (item.files or [])
                 if f.get("name", "").endswith("_archive.torrent")),
                None,
            )
            has_torrent = torrent_file is not None
            source = DownloadSource.ARCHIVE_ORG_TORRENT if has_torrent else DownloadSource.ARCHIVE_ORG_HTTP
            torrent_url = (
                ArchiveOrgSearchClient.build_download_url(identifier, torrent_file)
                if has_torrent else None
            )

            # Item-level prior: keyword overlap * collection boost.
            prior = min(overlap * boost, 0.95)
            if prior <= 0:
                prior = 0.3

            # Infer system from item metadata, not from the request.
            item_system = _infer_system_from_item(
                item.metadata, collections if isinstance(collections, list) else [collections], identifier,
            )

            # Create one Candidate per ROM file with independent scoring.
            for rom in rom_files:
                filename = rom.get("name", "")
                size = int(rom.get("size", 0) or 0)
                source_ref = f"{identifier}/{filename}"

                if source_ref in seen:
                    continue
                seen.add(source_ref)

                # Per-file confidence (P1-4).
                file_conf = _score_rom_file(
                    prior=prior,
                    dat_filename=entry.filename,
                    rom_filename=filename,
                    dat_size=entry.size,
                    rom_size=size,
                )

                c = Candidate(
                    title=filename.rsplit("/", 1)[-1],  # basename
                    size=size,
                    confidence=file_conf,
                    method="keyword_overlap",
                    source=source,
                    source_ref=source_ref,
                    collection=item.metadata.get("collection", ""),
                    system=item_system,
                    regions=(),
                    reasons=[f"Matched via archive.org item '{item_title}'"],
                    torrent_url=torrent_url,
                )
                candidates.append(c)

        # Sort by confidence descending, then by size ascending.
        candidates.sort(key=lambda c: (-c.confidence, c.size))
        return candidates
