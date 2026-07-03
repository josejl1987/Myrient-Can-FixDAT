"""Tests for download_queue schema migration (source columns)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from minerva_state import MinervaState


def test_fresh_db_has_source_columns(tmp_path):
    """A freshly-created state DB must have source and source_ref columns."""
    db_path = tmp_path / "state.db"
    MinervaState(db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(download_queue)")}
    assert "source" in cols
    assert "source_ref" in cols


def test_legacy_db_gets_source_columns(tmp_path):
    """An existing DB without source columns must be migrated on open."""
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY, path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL, collection TEXT, system TEXT,
                imported_at TEXT NOT NULL, requested_count INTEGER NOT NULL,
                matched_count INTEGER NOT NULL DEFAULT 0,
                fuzzy_count INTEGER NOT NULL DEFAULT 0,
                unmatched_count INTEGER NOT NULL DEFAULT 0,
                ready_count INTEGER NOT NULL DEFAULT 0,
                review_required_count INTEGER NOT NULL DEFAULT 0,
                not_found_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'draft'
            );
            CREATE TABLE IF NOT EXISTS report_entries (
                id TEXT PRIMARY KEY, report_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL, filename TEXT NOT NULL,
                size INTEGER NOT NULL, automatic_file_id INTEGER,
                automatic_method TEXT, automatic_confidence REAL,
                decision TEXT NOT NULL DEFAULT 'pending',
                selected_file_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS download_queue (
                id TEXT PRIMARY KEY, file_id INTEGER NOT NULL,
                report_entry_id TEXT, status TEXT NOT NULL DEFAULT 'queued',
                qbit_hash TEXT, destination TEXT NOT NULL, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS activity_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL, message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            INSERT INTO download_queue
                (id, file_id, status, destination, created_at, updated_at)
            VALUES ('rec1', 42, 'queued', '/tmp/rom.zip', '2026-01-01', '2026-01-01')
        """)
        conn.commit()

    MinervaState(db_path=db_path)
    with sqlite3.connect(db_path) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(download_queue)")}
        assert "source" in cols
        assert "source_ref" in cols
        row = conn.execute(
            "SELECT source, source_ref FROM download_queue WHERE id='rec1'"
        ).fetchone()
        assert row[0] == "minerva_torrent"
        assert row[1] is None


def test_queue_record_round_trips_source_fields(tmp_path):
    """QueueRecord with source/source_ref persists and reloads."""
    from minerva.domain.downloads import QueueRecord
    from minerva.domain.sources import DownloadSource

    state = MinervaState(db_path=tmp_path / "state.db")
    record = QueueRecord(
        id="rec2",
        file_id=0,
        status="queued",
        destination="/tmp/game.zip",
        created_at="2026-07-03",
        updated_at="2026-07-03",
        source=DownloadSource.ARCHIVE_ORG_HTTP.value,
        source_ref="psx-collection/Game.chd",
    )
    state.save_queue_record(record)

    records = state.list_queue()
    found = next(r for r in records if r.id == "rec2")
    assert found.source == "archive_org_http"
    assert found.source_ref == "psx-collection/Game.chd"
