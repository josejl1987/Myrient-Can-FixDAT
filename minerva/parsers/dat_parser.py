"""DAT file parser for No-Intro / Redump / RetroAchievements XML DATs.

Extracted from ``minerva_db.py`` — pure parsing functions with no
database dependency.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

COLLECTION_NO_INTRO = "No-Intro"
COLLECTION_REDUMP = "Redump"
COLLECTION_RETRO_ACHIEVEMENTS = "RetroAchievements"


@dataclass(frozen=True, slots=True)
class DatEntry:
    """A single rom entry from a DAT file."""

    filename: str
    size: int


@dataclass(frozen=True, slots=True)
class DatInfo:
    """Parsed DAT file metadata and entries."""

    entries: tuple[DatEntry, ...]
    name: str | None
    collection: str | None
    system: str | None


_DAT_PREFIX = re.compile(
    r"(?i)^(?:fixdat|romresolve\s+fixdat)\s*[—\-:]\s*|^(?:fixdat|romresolve\s+fixdat)[_\s-]*"
)
_DAT_DATE_SUFFIX = re.compile(
    r"\s*\((?:\d{8}-\d{6}|\d{4}-\d{2}-\d{2}(?:[ T]\d{2}[-:]\d{2}[-:]\d{2})?)\)$"
)
_DAT_META_SUFFIX = re.compile(
    r"\s*\((?:Retool[^)]*|Fresh1G1R[^)]*|No-Intro[^)]*|Redump[^)]*|MAMERedump[^)]*|Hearto[^)]*)\)$",
    re.IGNORECASE,
)


def _clean_dat_system_name(label: str | None) -> str | None:
    """Normalize DAT labels into a usable system name.

    Strips common fixDat prefixes and trailing metadata such as retool tags,
    release labels, and date stamps.
    """
    if not label:
        return None

    cleaned = label.strip()
    cleaned = _DAT_PREFIX.sub("", cleaned)

    while True:
        new = _DAT_DATE_SUFFIX.sub("", cleaned)
        new = _DAT_META_SUFFIX.sub("", new)
        new = new.strip()
        if new == cleaned:
            break
        cleaned = new

    cleaned = cleaned.strip(" _-")
    return cleaned or None


def parse_dat_file(path: Path) -> DatInfo:
    """Parse a No-Intro / Redump / RetroAchievements DAT XML file.

    Args:
        path: Path to the .dat file.

    Returns:
        DatInfo with entries and inferred metadata.

    Raises:
        FileNotFoundError: If path doesn't exist.
        ET.ParseError: If the file isn't valid XML.
    """
    if not path.exists():
        raise FileNotFoundError(f"DAT file not found: {path}")

    tree = ET.parse(path)
    root = tree.getroot()
    header = root.find("header")

    dat_name = None
    collection = None
    system = None
    clean_dat_name = None
    clean_path_name = _clean_dat_system_name(path.stem)

    if header is not None:
        name_elem = header.find("name")
        if name_elem is not None and name_elem.text:
            dat_name = name_elem.text.strip()
            clean_dat_name = _clean_dat_system_name(dat_name)

        url_elem = header.find("url")
        if url_elem is not None and url_elem.text:
            dat_url = url_elem.text.strip().lower()
            if "redump.org" in dat_url:
                collection = COLLECTION_REDUMP
                system = clean_dat_name or clean_path_name or dat_name
            elif "no-intro.org" in dat_url or "no-intro" in dat_url:
                collection = COLLECTION_NO_INTRO
                system = clean_dat_name or clean_path_name or dat_name
            elif "retroachievements.org" in dat_url:
                collection = COLLECTION_RETRO_ACHIEVEMENTS
                system = clean_dat_name or clean_path_name or dat_name

        homepage_elem = header.find("homepage")
        if homepage_elem is not None and homepage_elem.text:
            if "retroachievements.org" in homepage_elem.text.strip().lower():
                collection = COLLECTION_RETRO_ACHIEVEMENTS
                system = clean_dat_name or clean_path_name or dat_name

    if collection is None and dat_name:
        lower = dat_name.lower()
        if "no-intro" in lower or "no intro" in lower:
            collection = COLLECTION_NO_INTRO
        elif "redump" in lower:
            collection = COLLECTION_REDUMP
        elif "retroachievements" in lower or "ra - " in lower:
            collection = COLLECTION_RETRO_ACHIEVEMENTS

    if system is None:
        system = clean_dat_name or clean_path_name or dat_name

    entries: list[DatEntry] = []
    for game in root.iter("game"):
        for rom in game.iter("rom"):
            fname = (rom.get("name") or "").strip()
            size_str = (rom.get("size") or "").strip()
            if not fname:
                continue

            try:
                size = int(size_str)
            except ValueError:
                size = 0

            entries.append(DatEntry(filename=fname, size=size))

    return DatInfo(
        entries=tuple(entries),
        name=dat_name,
        collection=collection,
        system=system,
    )
