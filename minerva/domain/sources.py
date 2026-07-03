"""Domain types for multi-source download candidates.

Abstraction over candidate origins so the match review screen can merge
results from the Minerva torrent index and external sources (archive.org)
into a single candidate list.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from minerva_db import DatEntry


class DownloadSource(str, enum.Enum):
    """Where a downloadable file originates."""
    MINERVA_TORRENT = "minerva_torrent"
    ARCHIVE_ORG_TORRENT = "archive_org_torrent"
    ARCHIVE_ORG_HTTP = "archive_org_http"


@dataclass(frozen=True, slots=True)
class Candidate:
    """A unified candidate from any source."""
    title: str
    size: int
    confidence: float
    method: str
    source: DownloadSource
    source_ref: str
    collection: str = ""
    system: str = ""
    regions: tuple[str, ...] = ()
    reasons: list[str] = field(default_factory=list)
    seeders: int | None = None
    torrent_url: str | None = None


@runtime_checkable
class CandidateProvider(Protocol):
    """Search for candidates matching a DAT entry."""
    def search(self, entry: DatEntry, system: str | None = None) -> list[Candidate]: ...
