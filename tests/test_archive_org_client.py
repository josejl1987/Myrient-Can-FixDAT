"""Tests for the archive.org search client and candidate provider."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from minerva.domain.sources import Candidate, CandidateProvider, DownloadSource
from minerva.services.archive_org import (
    ArchiveOrgCandidateProvider,
    ArchiveOrgSearchClient,
    _collection_boost,
    _is_rom_file,
    _keyword_overlap,
)
from minerva_db import DatEntry

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text())


# ── Helper unit tests ─────────────────────────────────────────────────────────

class TestIsRomFile:
    def test_recognised_extensions(self):
        assert _is_rom_file("game.chd") is True
        assert _is_rom_file("game.iso") is True
        assert _is_rom_file("game.zip") is True
        assert _is_rom_file("game.nes") is True
        assert _is_rom_file("game.gb") is True
        assert _is_rom_file("game.gbc") is True
        assert _is_rom_file("game.gba") is True

    def test_non_rom_extension(self):
        assert _is_rom_file("readme.txt") is False
        assert _is_rom_file("cover.png") is False
        assert _is_rom_file("game.exe") is False
        assert _is_rom_file("") is False

    def test_case_insensitive(self):
        assert _is_rom_file("GAME.CHD") is True
        assert _is_rom_file("Game.Zip") is True
        assert _is_rom_file("game.NES") is True

    def test_paths_with_directories(self):
        assert _is_rom_file("ntsc/007 - The World Is Not Enough (USA).chd") is True
        assert _is_rom_file("roms/nes/game.nes") is True


class TestCollectionBoost:
    def test_known_collection(self):
        assert _collection_boost("redump") == 1.30
        assert _collection_boost("no-intro") == 1.30

    def test_unknown_collection(self):
        assert _collection_boost("random_stuff") == 1.0

    def test_none_collection(self):
        assert _collection_boost(None) == 1.0

    def test_collection_list(self):
        result = _collection_boost(["random", "redump", "other"])
        assert result == 1.30

    def test_case_insensitive(self):
        assert _collection_boost("No-Intro") == 1.30
        assert _collection_boost("REDUMP") == 1.30

    def test_first_match_wins(self):
        # Should return the boost for the *first* known collection found
        result = _collection_boost(["opensource_media", "redump"])
        assert result == 1.20  # opensource_media comes first


class TestKeywordOverlap:
    def test_full_overlap(self):
        kw = {"final", "fantasy"}
        assert _keyword_overlap(kw, "Final Fantasy VII PSX") == 1.0

    def test_partial_overlap(self):
        kw = {"final", "fantasy", "viii"}
        result = _keyword_overlap(kw, "Final Fantasy VIII")
        assert result == 1.0  # All three appear

    def test_no_overlap(self):
        kw = {"zelda"}
        assert _keyword_overlap(kw, "Mario Bros") == 0.0

    def test_empty_keywords(self):
        assert _keyword_overlap(set(), "anything") == 0.0


# ── Client tests ──────────────────────────────────────────────────────────────

class TestArchiveOrgSearchClient:
    def test_search_returns_items(self):
        """search_items returns a list of dicts with identifiers."""
        fixture = _load_fixture("archive_org_search.json")
        assert isinstance(fixture, list)

        def fake_search_items(query, **kwargs):
            return iter(fixture)

        client = ArchiveOrgSearchClient()
        with patch("minerva.services.archive_org.search_items", side_effect=fake_search_items):
            results = client.search_items("Final Fantasy")

        assert len(results) > 0
        assert "identifier" in results[0]
        assert results[0]["identifier"] == fixture[0]["identifier"]

    def test_search_respects_rows_param(self):
        """The rows parameter is forwarded to the IA API."""
        fixture = _load_fixture("archive_org_search.json")
        captured = {}

        def fake_search_items(query, **kwargs):
            params = kwargs.get("params", {})
            captured["rows"] = params.get("rows")
            return iter(fixture)

        client = ArchiveOrgSearchClient()
        with patch("minerva.services.archive_org.search_items", side_effect=fake_search_items):
            client.search_items("Final Fantasy", rows=10)

        assert captured.get("rows") == 10

    def test_get_item_returns_files(self):
        """get_item returns an object with .files, .metadata, .identifier."""
        fixture = _load_fixture("archive_org_item.json")
        assert isinstance(fixture, dict)

        class FakeItem:
            def __init__(self, data):
                self.files = data.get("files", [])
                self.metadata = data.get("metadata", {})
                self.identifier = data.get("metadata", {}).get("identifier", "")
                self.server = "ia800600.us.archive.org"

        with patch("minerva.services.archive_org.get_item", return_value=FakeItem(fixture)):
            client = ArchiveOrgSearchClient()
            item = client.get_item("psx-ntsc-chd-zstd")

        assert len(item.files) > 0
        chd_files = [f for f in item.files if f.get("name", "").endswith(".chd")]
        assert len(chd_files) > 0

    def test_build_download_url(self):
        url = ArchiveOrgSearchClient.build_download_url(
            "psx-ntsc-chd-zstd", "ntsc/007 - Bond.chd"
        )
        assert url.startswith("https://archive.org/download/")
        assert "psx-ntsc-chd-zstd" in url
        assert "%20" in url  # URL-encoded space

    def test_caching(self):
        """search_items should cache results within TTL."""
        fixture = _load_fixture("archive_org_search.json")
        call_count = 0

        def fake_search_items(query, **kwargs):
            nonlocal call_count
            call_count += 1
            return iter(fixture)

        client = ArchiveOrgSearchClient()
        # Use a unique query to avoid hitting a cache entry from another test
        with patch("minerva.services.archive_org.search_items", side_effect=fake_search_items):
            client.search_items("caching-test-unique-query")
            client.search_items("caching-test-unique-query")
            client.search_items("caching-test-unique-query")

        assert call_count == 1, "Expected only 1 call, got %d" % call_count


# ── Candidate provider tests ──────────────────────────────────────────────────

class TestArchiveOrgCandidateProvider:
    def test_provides_protocol(self):
        """ArchiveOrgCandidateProvider satisfies the CandidateProvider protocol."""
        provider = ArchiveOrgCandidateProvider()
        assert isinstance(provider, CandidateProvider)

    def test_search_returns_candidates(self, monkeypatch):
        """search() returns Candidate objects for a DatEntry match."""
        search_fixture = _load_fixture("archive_org_search.json")
        item_fixture = _load_fixture("archive_org_item.json")

        assert isinstance(search_fixture, list)
        assert isinstance(item_fixture, dict)

        call_log = {"search": False, "item": False}

        def fake_search_items(query, **kwargs):
            call_log["search"] = True
            # Filter fixture to items whose title contains the query
            return iter(
                [r for r in search_fixture if query.lower() in r.get("title", "").lower()]
            )

        class FakeItem:
            def __init__(self, data):
                self.files = data.get("files", [])
                self.metadata = data.get("metadata", {})
                self.identifier = data.get("metadata", {}).get("identifier", "")
                self.server = "ia800600.us.archive.org"

        def fake_get_item(identifier):
            call_log["item"] = True
            return FakeItem(item_fixture)

        entry = DatEntry(filename="Final Fantasy VIII (USA).chd", size=495937978)

        patches = [
            patch("minerva.services.archive_org.search_items", side_effect=fake_search_items),
            patch("minerva.services.archive_org.get_item", side_effect=fake_get_item),
        ]
        with patches[0], patches[1]:
            provider = ArchiveOrgCandidateProvider()
            candidates = provider.search(entry)

        assert call_log["search"], "search_items was never called"
        assert call_log["item"], "get_item was never called"
        assert len(candidates) > 0

        candidate = candidates[0]
        assert isinstance(candidate, Candidate)
        assert candidate.source in (
            DownloadSource.ARCHIVE_ORG_HTTP,
            DownloadSource.ARCHIVE_ORG_TORRENT,
        )
        assert candidate.source_ref.startswith("final-fantasy-viii-psx-ps-2-fmcb-pops-vcd-crash-fix/")
        assert candidate.confidence > 0.0

    def test_search_with_no_results(self):
        """When no search results match, candidates list is empty."""
        def fake_search_items(query, **kwargs):
            return iter([])

        def fake_get_item(identifier):
            return None  # Not called anyway

        entry = DatEntry(filename="Zelda - Ocarina of Time.n64", size=33554432)

        with patch("minerva.services.archive_org.search_items", side_effect=fake_search_items):
            with patch("minerva.services.archive_org.get_item", side_effect=fake_get_item):
                provider = ArchiveOrgCandidateProvider()
                candidates = provider.search(entry)

        assert candidates == []

    def test_search_empty_query(self, monkeypatch):
        """An empty query produces no candidates."""
        entry = DatEntry(filename=".chd", size=0)  # No meaningful stem
        provider = ArchiveOrgCandidateProvider()
        candidates = provider.search(entry)
        assert candidates == []
