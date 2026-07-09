"""Tests for NativeTorrentSession — libtorrent-backed torrent session."""

from __future__ import annotations

import tempfile
from pathlib import Path

import libtorrent as lt
import pytest

from minerva.native_torrent import NativeTorrentSession


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def tmp_dir(tmp_path):
    return tmp_path


@pytest.fixture
def test_torrent(tmp_dir):
    """Create a tiny .torrent file with one 1KB file."""
    data_dir = tmp_dir / "data"
    data_dir.mkdir()
    (data_dir / "rom.zip").write_bytes(b"x" * 1024)

    fs = lt.file_storage()
    lt.add_files(fs, str(data_dir / "rom.zip"))
    ct = lt.create_torrent(fs)
    ct.add_tracker("udp://tracker.example.com:80/announce")
    lt.set_piece_hashes(ct, str(data_dir))
    torrent_path = tmp_dir / "test.torrent"
    with open(torrent_path, "wb") as f:
        f.write(lt.bencode(ct.generate()))
    return torrent_path


@pytest.fixture
def session(tmp_dir):
    """Create and start a NativeTorrentSession, auto-stopped after test."""
    s = NativeTorrentSession(
        save_dir=tmp_dir / "save",
        seed_ratio=2.0,
        seed_time_hours=1,
    )
    s.start()
    yield s
    s.stop()


# ============================================================================
# Construction
# ============================================================================


class TestConstruction:
    def test_creates_save_dir(self, tmp_dir):
        save = tmp_dir / "newdir"
        s = NativeTorrentSession(save_dir=save)
        assert save.exists()

    def test_defaults(self):
        s = NativeTorrentSession()
        assert s._seed_ratio == 2.0
        assert s._seed_time_seconds == 48 * 3600

    def test_custom_policy(self):
        s = NativeTorrentSession(seed_ratio=3.0, seed_time_hours=72)
        assert s._seed_ratio == 3.0
        assert s._seed_time_seconds == 72 * 3600


# ============================================================================
# Lifecycle
# ============================================================================


class TestLifecycle:
    def test_start_creates_session(self, tmp_dir):
        s = NativeTorrentSession(save_dir=tmp_dir)
        assert s.is_logged_in is False
        s.start()
        assert s.is_logged_in is True
        s.stop()
        assert s.is_logged_in is False

    def test_start_is_idempotent(self, tmp_dir):
        s = NativeTorrentSession(save_dir=tmp_dir)
        s.start()
        s.start()  # no crash
        s.stop()

    def test_stop_clears_handles(self, tmp_dir, test_torrent):
        s = NativeTorrentSession(save_dir=tmp_dir / "save")
        s.start()
        s.add_torrent_paused(str(test_torrent), str(tmp_dir / "save"))
        assert len(s._handles) == 1
        s.stop()
        assert len(s._handles) == 0

    def test_clone_returns_self(self, tmp_dir):
        s = NativeTorrentSession(save_dir=tmp_dir)
        assert s.clone() is s


# ============================================================================
# Torrent operations
# ============================================================================


class TestTorrentOps:
    def test_add_torrent_paused(self, session, test_torrent):
        h = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        assert len(h) == 40  # SHA1 hex

    def test_get_files(self, session, test_torrent):
        h = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        files = session.get_files(h)
        assert len(files) == 1
        assert files[0]["name"] == "rom.zip"
        assert files[0]["size"] == 1024

    def test_set_file_priority(self, session, test_torrent):
        h = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        session.set_file_priority(h, [0], 0)  # skip
        session.set_file_priority(h, [0], 1)  # normal
        # no crash

    def test_pause_resume(self, session, test_torrent):
        h = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        session.resume(h)
        session.pause(h)
        # no crash

    def test_get_torrent_info(self, session, test_torrent):
        h = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        info = session.get_torrent_info(h)
        assert info is not None
        assert info["hash"] == h
        assert "progress" in info
        assert "state" in info
        assert "dlspeed" in info

    def test_list_torrents(self, session, test_torrent):
        session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        torrents = session.list_torrents()
        assert len(torrents) == 1

    def test_list_torrents_filtered(self, session, test_torrent):
        h = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        # Filter by hash
        torrents = session.list_torrents([h])
        assert len(torrents) == 1
        # Non-matching hash
        torrents = session.list_torrents(["deadbeef" * 5])
        assert len(torrents) == 0

    def test_delete_torrent(self, session, test_torrent):
        h = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        session.delete_torrent(h)
        assert session.get_torrent_info(h) is None

    def test_rename(self, session, test_torrent):
        h = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        session.rename(h, "New Name")
        # no crash

    def test_get_files_unknown_hash(self, session):
        assert session.get_files("nonexistent") == []

    def test_get_torrent_info_unknown_hash(self, session):
        assert session.get_torrent_info("nonexistent") is None


# ============================================================================
# Seeding policy
# ============================================================================


class TestSeedingPolicy:
    def test_settings_applied(self, tmp_dir):
        s = NativeTorrentSession(
            save_dir=tmp_dir,
            seed_ratio=3.0,
            seed_time_hours=72,
            max_active_downloads=5,
            max_active_seeds=10,
        )
        s.start()
        settings = s._session.get_settings()
        assert settings["seed_time_limit"] == 72 * 3600
        assert settings["active_downloads"] == 5
        assert settings["active_seeds"] == 10
        s.stop()


# ============================================================================
# Compatibility
# ============================================================================


class TestCompatibility:
    def test_test_connection(self, tmp_dir):
        s = NativeTorrentSession(save_dir=tmp_dir)
        s.start()
        version = s.test_connection()
        assert "libtorrent" in version
        s.stop()

    def test_login_noop(self, tmp_dir):
        s = NativeTorrentSession(save_dir=tmp_dir)
        assert s.login() is True
        s.stop()

    def test_compute_info_hash(self, test_torrent):
        h = NativeTorrentSession.compute_info_hash(test_torrent)
        assert h is not None
        assert len(h) == 40


# ============================================================================
# Seeding policy enforcement
# ============================================================================


def _mock_status(**overrides):
    """Build a SimpleNamespace mimicking lt.torrent_status for policy tests."""
    from types import SimpleNamespace
    defaults = dict(
        is_seeding=False,
        total_upload=0,
        total_download=0,
        seeding_time=0,
        name="test",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestEnforceSeedingPolicy:
    def test_ratio_reached_pauses(self, session, test_torrent):
        """Torrent that has reached seed_ratio should be paused."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        handle = session._get_handle(info_hash)
        status = _mock_status(
            is_seeding=True, total_upload=300, total_download=100,
        )
        session._enforce_seeding_policy(handle, status)
        assert handle.status().paused

    def test_seed_time_reached_pauses(self, session, test_torrent):
        """Torrent that has seeded long enough should be paused."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        handle = session._get_handle(info_hash)
        # seed_time_hours=1 in fixture → 3600 seconds
        status = _mock_status(
            is_seeding=True, total_upload=10, total_download=100,
            seeding_time=3600,
        )
        session._enforce_seeding_policy(handle, status)
        assert handle.status().paused

    def test_not_seeding_does_not_pause(self, session, test_torrent):
        """A non-seeding torrent should not be paused by seeding policy."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        handle = session._get_handle(info_hash)
        status = _mock_status(is_seeding=False)
        session._enforce_seeding_policy(handle, status)
        # Already paused (added paused), but the policy didn't call pause()
        assert handle.status().paused

    def test_ratio_below_target_does_not_pause(self, session, test_torrent):
        """Torrent below seed_ratio should not be paused by ratio check."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        handle = session._get_handle(info_hash)
        handle.resume()
        status = _mock_status(
            is_seeding=True, total_upload=50, total_download=100,
            seeding_time=0,
        )
        session._enforce_seeding_policy(handle, status)
        assert not handle.status().paused


# ============================================================================
# _handle_alert — exception isolation (F11)
# ============================================================================


class TestHandleAlert:
    def test_unknown_alert_does_not_raise(self, session):
        """An unknown alert type should not raise."""
        from types import SimpleNamespace
        fake = SimpleNamespace()
        # No handle attribute, no recognizable type
        session._handle_alert(fake)

    def test_handler_exception_does_not_propagate(self, session, monkeypatch):
        """If _enforce_seeding_policy raises, _handle_alert must catch it."""
        from types import SimpleNamespace

        def boom(handle, status):
            raise RuntimeError("boom")

        monkeypatch.setattr(session, "_enforce_seeding_policy", boom)

        # Create a fake alert with a dynamic type name
        FakeAlert = type("fake_alert", (), {})
        fake = FakeAlert()
        fake_handle = SimpleNamespace()
        fake_handle.is_valid = lambda: True
        fake_handle.status = lambda: _mock_status()
        fake.handle = fake_handle
        # Should not raise
        session._handle_alert(fake)


# ============================================================================
# _status_to_dict telemetry correctness (F20)
# ============================================================================


class TestStatusToDict:
    def test_ratio_zero_when_no_download(self, session, test_torrent):
        """Ratio should be 0.0 when all_time_download is 0."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        status = _mock_status(
            all_time_download=0, all_time_upload=0,
        )
        # Need all fields _status_to_dict reads
        _fill_status(status)
        result = session._status_to_dict(status, info_hash)
        assert result["ratio"] == 0.0

    def test_ratio_calculated_correctly(self, session, test_torrent):
        """Ratio should be upload/download."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        status = _mock_status(all_time_download=100, all_time_upload=250)
        _fill_status(status)
        result = session._status_to_dict(status, info_hash)
        assert result["ratio"] == 2.5

    def test_num_leechs_excludes_seeds(self, session, test_torrent):
        """num_leechs should be num_peers - num_seeds, not num_peers."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        status = _mock_status()
        _fill_status(status, num_seeds=4, num_peers=10)
        result = session._status_to_dict(status, info_hash)
        assert result["num_leechs"] == 6

    def test_num_leechs_never_negative(self, session, test_torrent):
        """num_leechs should clamp to 0 if seeds > peers (edge case)."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        status = _mock_status()
        _fill_status(status, num_seeds=10, num_peers=5)
        result = session._status_to_dict(status, info_hash)
        assert result["num_leechs"] == 0

    def test_seed_time_remaining_negative_when_no_upload(self, session, test_torrent):
        """seed_time_remaining should be -1 when upload_rate is 0."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        status = _mock_status(is_seeding=True, upload_rate=0)
        _fill_status(status, all_time_download=100, all_time_upload=50)
        result = session._status_to_dict(status, info_hash)
        assert result["seed_time_remaining"] == -1


def _fill_status(status, **overrides):
    """Fill in fields _status_to_dict reads that _mock_status doesn't set."""
    from types import SimpleNamespace
    import libtorrent as lt
    defaults = dict(
        state=lt.torrent_status.downloading,
        paused=False,
        progress=0.0,
        download_rate=0,
        upload_rate=0,
        total_wanted=1024,
        total_wanted_done=0,
        save_path="/tmp",
        num_seeds=0,
        num_peers=0,
        all_time_download=0,
        all_time_upload=0,
        is_seeding=False,
        seeding_time=0,
        name="test",
    )
    defaults.update(overrides)
    for k, v in defaults.items():
        if not hasattr(status, k):
            setattr(status, k, v)


# ============================================================================
# get_files priority key (F21)
# ============================================================================


class TestGetFilesPriority:
    def test_files_have_priority_key(self, session, test_torrent):
        """Each file dict should have a 'priority' key."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        files = session.get_files(info_hash)
        assert len(files) > 0
        for f in files:
            assert "priority" in f
            assert isinstance(f["priority"], int)


# ============================================================================
# rename_file path-component matching (F22)
# ============================================================================


class TestRenameFileMatching:
    def test_exact_match_renames(self, session, test_torrent, monkeypatch):
        """'rom.zip' should match the actual file 'rom.zip'."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        rename_calls = []
        handle = session._get_handle(info_hash)
        monkeypatch.setattr(
            handle, "rename_file",
            lambda idx, new_path: rename_calls.append((idx, new_path)),
        )
        session.rename_file(info_hash, "rom.zip", "newrom.zip")
        assert len(rename_calls) == 1
        assert rename_calls[0] == (0, "newrom.zip")

    def test_no_substring_collision(self, session, test_torrent):
        """'badrom.zip' should NOT match 'rom.zip' via substring suffix."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        session.rename_file(info_hash, "badrom.zip", "newrom.zip")
        files = session.get_files(info_hash)
        assert files[0]["name"] == "rom.zip"

# ============================================================================
# Resume data persistence (F1, F9)
# ============================================================================


class TestResumeData:
    def test_stop_writes_fastresume(self, tmp_dir, test_torrent):
        """stop() should flush .fastresume before destroying the session."""
        s = NativeTorrentSession(save_dir=tmp_dir / "save", seed_ratio=2.0)
        s.start()
        info_hash = s.add_torrent_paused(str(test_torrent), str(s._save_dir))
        s.stop()
        resume_file = s._save_dir / f"{info_hash}.fastresume"
        assert resume_file.is_file()
        assert resume_file.stat().st_size > 0

    def test_delete_torrent_cleans_fastresume(self, session, test_torrent):
        """delete_torrent should remove the .fastresume file."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        # Create a dummy .fastresume file
        resume_file = session._save_dir / f"{info_hash}.fastresume"
        resume_file.write_bytes(b"dummy")
        assert resume_file.is_file()
        session.delete_torrent(info_hash)
        assert not resume_file.exists()

    def test_re_add_after_delete_works(self, session, test_torrent):
        """Deleting and re-adding the same torrent should work cleanly."""
        info_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        session.delete_torrent(info_hash)
        new_hash = session.add_torrent_paused(str(test_torrent), str(session._save_dir))
        assert new_hash == info_hash


# ============================================================================
# NativeTorrentError (F24)
# ============================================================================


class TestNativeTorrentError:
    def test_list_torrents_raises_when_session_none(self, tmp_dir):
        from minerva.native_torrent import NativeTorrentError
        s = NativeTorrentSession(save_dir=tmp_dir)
        with pytest.raises(NativeTorrentError, match="Session not started"):
            s.list_torrents()

    def test_get_files_raises_when_session_none(self, tmp_dir):
        from minerva.native_torrent import NativeTorrentError
        s = NativeTorrentSession(save_dir=tmp_dir)
        with pytest.raises(NativeTorrentError, match="Session not started"):
            s.get_files("abc123")

    def test_delete_torrent_raises_when_session_none(self, tmp_dir):
        from minerva.native_torrent import NativeTorrentError
        s = NativeTorrentSession(save_dir=tmp_dir)
        with pytest.raises(NativeTorrentError, match="Session not started"):
            s.delete_torrent("abc123")

    def test_pause_raises_when_session_none(self, tmp_dir):
        from minerva.native_torrent import NativeTorrentError
        s = NativeTorrentSession(save_dir=tmp_dir)
        with pytest.raises(NativeTorrentError, match="Session not started"):
            s.pause("abc123")

    def test_resume_raises_when_session_none(self, tmp_dir):
        from minerva.native_torrent import NativeTorrentError
        s = NativeTorrentSession(save_dir=tmp_dir)
        with pytest.raises(NativeTorrentError, match="Session not started"):
            s.resume("abc123")

    def test_set_file_priority_raises_when_session_none(self, tmp_dir):
        from minerva.native_torrent import NativeTorrentError
        s = NativeTorrentSession(save_dir=tmp_dir)
        with pytest.raises(NativeTorrentError, match="Session not started"):
            s.set_file_priority("abc123", [0], 0)

    def test_rename_file_raises_when_session_none(self, tmp_dir):
        from minerva.native_torrent import NativeTorrentError
        s = NativeTorrentSession(save_dir=tmp_dir)
        with pytest.raises(NativeTorrentError, match="Session not started"):
            s.rename_file("abc123", "old", "new")

    def test_get_torrent_info_raises_when_session_none(self, tmp_dir):
        from minerva.native_torrent import NativeTorrentError
        s = NativeTorrentSession(save_dir=tmp_dir)
        with pytest.raises(NativeTorrentError, match="Session not started"):
            s.get_torrent_info("abc123")
