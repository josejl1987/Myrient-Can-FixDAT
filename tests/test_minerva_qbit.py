"""
Unit tests for minerva_qbit
============================
Run with: python -m pytest tests/test_minerva_qbit.py -v
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from minerva_qbit import (
    QBittorrentClient,
    QBittorrentApiError,
    QBittorrentConnectionError,
    compute_info_hash,
)


def _make_session():
    """Return a MagicMock that quacks like requests.Session()."""
    return MagicMock()


def _ok_response(status=200, json_data=None, cookies=None, text=""):
    """Build a mock requests.Response."""
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.json.return_value = json_data if json_data is not None else []
    r.cookies.keys.return_value = cookies or []
    return r


class TestQBittorrentClientInit(unittest.TestCase):
    def test_default_url_has_trailing_slash_stripped(self):
        c = QBittorrentClient(url="http://localhost:8080/")
        self.assertEqual(c._url, "http://localhost:8080")

    def test_credentials_stored(self):
        c = QBittorrentClient("http://q", "alice", "secret")
        self.assertEqual(c._username, "alice")
        self.assertEqual(c._password, "secret")
        self.assertFalse(c._logged_in)


class TestLogin(unittest.TestCase):
    def test_login_success_204_with_sid_cookie(self):
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.cookies.keys.return_value = ["QBT_SID_8080"]
        c._session.post.return_value = _ok_response(status=204)
        self.assertTrue(c.login())
        self.assertTrue(c._logged_in)

    def test_login_success_200_with_sid_cookie(self):
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.cookies.keys.return_value = ["SID"]
        c._session.post.return_value = _ok_response(status=200)
        self.assertTrue(c.login())

    def test_login_failure_204_without_sid_cookie(self):
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.cookies.keys.return_value = ["other_cookie"]
        c._session.post.return_value = _ok_response(status=204)
        with self.assertRaises(QBittorrentApiError):
            c.login()
        self.assertFalse(c._logged_in)

    def test_login_failure_500(self):
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.post.return_value = _ok_response(status=500)
        with self.assertRaises(QBittorrentApiError):
            c.login()

    def test_login_network_error(self):
        import requests
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.post.side_effect = requests.ConnectionError("boom")
        with self.assertRaises(QBittorrentConnectionError):
            c.login()


class TestTestConnection(unittest.TestCase):
    def test_returns_version_on_200(self):
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.get.return_value = _ok_response(status=200, text="v4.6.0\n")
        ver = c.test_connection()
        self.assertEqual(ver, "v4.6.0")

    def test_raises_on_500(self):
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.get.return_value = _ok_response(status=500, text="oops")
        with self.assertRaises(QBittorrentApiError):
            c.test_connection()

    def test_raises_on_network_failure(self):
        import requests
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.get.side_effect = requests.ConnectionError("nope")
        with self.assertRaises(QBittorrentConnectionError):
            c.test_connection()


class TestAddTorrentPaused(unittest.TestCase):
    def test_returns_computed_hash_when_torrent_appears(self):
        """Hash is computed from the file; after add, polling by hash finds it."""
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.post.return_value = _ok_response(status=200)
        # get_torrent_info polls until the hash appears
        c._session.get.return_value = _ok_response(
            status=200,
            json_data=[{"hash": "abc123", "name": "test", "progress": 0.0}],
        )
        # Build a minimal valid torrent file in-memory
        torrent_data = _make_minimal_torrent()
        with patch.object(Path, "read_bytes", return_value=torrent_data):
            with patch("builtins.open", MagicMock()):
                h = c.add_torrent_paused("/tmp/test.torrent", "/save")
        self.assertIsNotNone(h)
        assert h is not None
        self.assertEqual(len(h), 40)

    def test_returns_none_when_hash_never_appears(self):
        """If the computed hash never shows up in qBittorrent, return None."""
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.post.return_value = _ok_response(status=200)
        c._session.get.return_value = _ok_response(status=200, json_data=[])
        torrent_data = _make_minimal_torrent()
        with patch.object(Path, "read_bytes", return_value=torrent_data):
            with patch("builtins.open", MagicMock()):
                h = c.add_torrent_paused(
                    "/tmp/test.torrent", "/save",
                    poll_attempts=1, poll_interval=0.0,
                )
        self.assertIsNone(h)

    def test_falls_back_to_name_match_when_hash_unparseable(self):
        """If compute_info_hash fails, fall back to name-based lookup."""
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.post.return_value = _ok_response(status=200)
        c._session.get.return_value = _ok_response(
            status=200,
            json_data=[{"name": "test", "hash": "FALLBACK", "added_on": 1}],
        )
        # Garbage data that can't be bdecoded
        with patch.object(Path, "read_bytes", return_value=b"NOT_BENCODED"):
            with patch("builtins.open", MagicMock()):
                h = c.add_torrent_paused("/tmp/test.torrent", "/save")
        self.assertEqual(h, "FALLBACK")

    def test_raises_on_500(self):
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.post.return_value = _ok_response(status=500)
        m = mock_open(read_data=b"x")
        with patch("builtins.open", m, create=True):
            with self.assertRaises(QBittorrentApiError):
                c.add_torrent_paused("/tmp/x.torrent", "/save")

    def test_polls_by_computed_hash_not_name(self):
        """The GET polls /torrents/info?hashes=<computed_hash>, not the name list."""
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.post.return_value = _ok_response(status=200)
        c._session.get.return_value = _ok_response(
            status=200,
            json_data=[{"hash": KNOWN_INFO_HASH, "name": "test", "progress": 0.0}],
        )
        torrent_data = _make_minimal_torrent()
        with patch.object(Path, "read_bytes", return_value=torrent_data):
            with patch("builtins.open", MagicMock()):
                h = c.add_torrent_paused("/tmp/test.torrent", "/save")
        self.assertEqual(h, KNOWN_INFO_HASH)
        get_calls = [call for call in c._session.get.call_args_list
                     if "torrents/info" in call.args[0]]
        self.assertTrue(get_calls)
        params = get_calls[0].kwargs.get("params") or get_calls[0].kwargs.get("params", {})
        self.assertEqual(params.get("hashes"), KNOWN_INFO_HASH)

    def test_ignores_same_named_wrong_torrent(self):
        """A pre-existing torrent with the same name but different hash must not win."""
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.post.return_value = _ok_response(status=200)
        # First poll returns an old torrent with a matching name but wrong hash,
        # second poll returns the real one.
        responses = [
            _ok_response(status=200, json_data=[
                {"hash": "WRONGRIGHT1234", "name": "test", "progress": 0.0}
            ]),
            _ok_response(status=200, json_data=[
                {"hash": KNOWN_INFO_HASH, "name": "test", "progress": 0.0}
            ]),
        ]
        c._session.get.side_effect = responses
        torrent_data = _make_minimal_torrent()
        with patch.object(Path, "read_bytes", return_value=torrent_data):
            with patch("builtins.open", MagicMock()):
                h = c.add_torrent_paused("/tmp/test.torrent", "/save")
        self.assertEqual(h, KNOWN_INFO_HASH)


class TestAddTorrentPausedVersionAware(unittest.TestCase):
    """Verify version-dependent parameter names in add_torrent_paused."""

    def test_add_sends_both_paused_keys(self):
        """Both v4 (paused) and v5 (stopped) keys are sent for compatibility."""
        c = QBittorrentClient("http://q", "u", "p")
        c._session = _make_session()
        c._session.post.return_value = _ok_response(status=200)
        c._session.get.return_value = _ok_response(
            status=200, json_data=[{"hash": KNOWN_INFO_HASH, "progress": 0.0}])
        c._version_checked = True
        c._api_version = 5
        torrent_data = _make_minimal_torrent()
        with patch.object(Path, "read_bytes", return_value=torrent_data):
            with patch("builtins.open", MagicMock()):
                c.add_torrent_paused("/tmp/test.torrent", "/save")
        # The POST to torrents/add should use both keys
        post_calls = [call for call in c._session.post.call_args_list
                      if "torrents/add" in call.args[0]]
        self.assertTrue(post_calls)
        self.assertEqual(post_calls[0].kwargs["data"].get("paused"), "true")
        self.assertEqual(post_calls[0].kwargs["data"].get("stopped"), "true")


class TestComputeInfoHash(unittest.TestCase):
    def test_returns_hex_sha1_of_info_dict(self):
        torrent_data = _make_minimal_torrent()
        with patch.object(Path, "read_bytes", return_value=torrent_data):
            h = compute_info_hash(Path("/fake.torrent"))
        self.assertIsNotNone(h)
        assert h is not None
        self.assertEqual(h, KNOWN_INFO_HASH)
        self.assertEqual(len(h), 40)
        # Verify it's a valid hex string
        int(h, 16)

    def test_returns_none_on_garbage_data(self):
        with patch.object(Path, "read_bytes", return_value=b"NOT_BENCODED"):
            h = compute_info_hash(Path("/fake.torrent"))
        self.assertIsNone(h)

    def test_returns_none_on_missing_info_key(self):
        # b'd4:spam4:eggse' is valid bencode but has no 'info' key
        data = b"d4:spam4:eggse"
        with patch.object(Path, "read_bytes", return_value=data):
            h = compute_info_hash(Path("/fake.torrent"))
        self.assertIsNone(h)


class TestGetFiles(unittest.TestCase):
    def test_returns_file_list(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(
            status=200, json_data=[{"index": 0, "name": "a.rom"},
                                   {"index": 1, "name": "b.rom"}])
        files = c.get_files("HASH")
        self.assertEqual(len(files), 2)
        self.assertEqual(files[0]["name"], "a.rom")

    def test_raises_on_500(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(status=500)
        with self.assertRaises(QBittorrentApiError):
            c.get_files("HASH")


class TestSetFilePriority(unittest.TestCase):
    def test_skips_when_empty(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c.set_file_priority("HASH", [])
        c._session.post.assert_not_called()

    def test_posts_with_pipe_separated_ids(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.return_value = _ok_response()
        c.set_file_priority("HASH", [0, 2, 5], priority=0)
        call = c._session.post.call_args
        self.assertEqual(call.args[0], "http://localhost:8080/api/v2/torrents/filePrio")
        self.assertEqual(call.kwargs["data"]["hash"], "HASH")
        self.assertEqual(call.kwargs["data"]["id"], "0|2|5")
        self.assertEqual(call.kwargs["data"]["priority"], "0")


class TestResume(unittest.TestCase):
    def test_v4_resume_endpoint(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.return_value = _ok_response()
        c._version_checked = True
        c._api_version = 4
        c.resume("HASH")
        call = c._session.post.call_args
        self.assertEqual(call.args[0], "http://localhost:8080/api/v2/torrents/resume")
        self.assertEqual(call.kwargs["data"]["hashes"], "HASH")

    def test_v5_resume_endpoint(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.return_value = _ok_response()
        c._version_checked = True
        c._api_version = 5
        c.resume("HASH")
        call = c._session.post.call_args
        self.assertEqual(call.args[0], "http://localhost:8080/api/v2/torrents/start")
        self.assertEqual(call.kwargs["data"]["hashes"], "HASH")

    def test_falls_back_to_alternate_on_404(self):
        """If the detected endpoint returns 404, retry the alternate endpoint."""
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.side_effect = [
            _ok_response(status=404, text="Endpoint does not exist"),
            _ok_response(),
        ]
        c._version_checked = True
        c._api_version = 5
        c.resume("HASH")
        calls = [call.args[0] for call in c._session.post.call_args_list]
        self.assertEqual(calls, [
            "http://localhost:8080/api/v2/torrents/start",
            "http://localhost:8080/api/v2/torrents/resume",
        ])

    def test_raises_original_when_both_fail(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.return_value = _ok_response(status=500)
        c._version_checked = True
        c._api_version = 5
        with self.assertRaises(QBittorrentApiError) as cm:
            c.resume("HASH")
        self.assertIn("start returned 500", str(cm.exception))


class TestPause(unittest.TestCase):
    def test_v4_pause_endpoint(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.return_value = _ok_response()
        c._version_checked = True
        c._api_version = 4
        c.pause("HASH")
        call = c._session.post.call_args
        self.assertEqual(call.args[0], "http://localhost:8080/api/v2/torrents/pause")
        self.assertEqual(call.kwargs["data"]["hashes"], "HASH")

    def test_v5_pause_endpoint(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.return_value = _ok_response()
        c._version_checked = True
        c._api_version = 5
        c.pause("HASH")
        call = c._session.post.call_args
        self.assertEqual(call.args[0], "http://localhost:8080/api/v2/torrents/stop")
        self.assertEqual(call.kwargs["data"]["hashes"], "HASH")

    def test_falls_back_to_alternate_on_404(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.side_effect = [
            _ok_response(status=404, text="Endpoint does not exist"),
            _ok_response(),
        ]
        c._version_checked = True
        c._api_version = 4
        c.pause("HASH")
        calls = [call.args[0] for call in c._session.post.call_args_list]
        self.assertEqual(calls, [
            "http://localhost:8080/api/v2/torrents/pause",
            "http://localhost:8080/api/v2/torrents/stop",
        ])


class TestGetTorrentInfo(unittest.TestCase):
    def test_returns_first_item(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(
            status=200, json_data=[{"progress": 0.5, "state": "downloading"}])
        c._version_checked = True
        c._api_version = 4
        info = c.get_torrent_info("HASH")
        self.assertEqual(info["progress"], 0.5)

    def test_returns_none_on_empty(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(status=200, json_data=[])
        c._version_checked = True
        c._api_version = 4
        self.assertIsNone(c.get_torrent_info("HASH"))

    def test_uses_hashes_param(self):
        """torrents/info filters with the plural `hashes` on all versions."""
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(
            status=200, json_data=[{"progress": 1.0}])
        c._version_checked = True
        c._api_version = 5
        c.get_torrent_info("SOMEHASH")
        call = c._session.get.call_args
        self.assertEqual(call.kwargs["params"], {"hashes": "SOMEHASH"})


class TestDeleteTorrent(unittest.TestCase):
    def test_delete_with_files(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.return_value = _ok_response()
        c.delete_torrent("HASH", delete_files=True)
        call = c._session.post.call_args
        self.assertEqual(call.kwargs["data"]["deleteFiles"], "true")

    def test_delete_without_files(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.return_value = _ok_response()
        c.delete_torrent("HASH", delete_files=False)
        call = c._session.post.call_args
        self.assertEqual(call.kwargs["data"]["deleteFiles"], "false")


class TestListTorrents(unittest.TestCase):
    def test_uses_hashes_param(self):
        """torrents/info filters with the plural `hashes` on all versions."""
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(status=200, json_data=[])
        c._version_checked = True
        c._api_version = 5
        c.list_torrents(hashes=["HASH1", "HASH2"])
        call = c._session.get.call_args
        self.assertEqual(call.kwargs["params"], {"hashes": "HASH1|HASH2"})

    def test_no_params_when_no_hashes(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(status=200, json_data=[])
        c._version_checked = True
        c._api_version = 4
        c.list_torrents()
        call = c._session.get.call_args
        self.assertEqual(call.kwargs["params"], {})


class TestRename(unittest.TestCase):
    def test_posts_to_rename_endpoint(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.return_value = _ok_response()
        c.rename("HASH", "New Name")
        call = c._session.post.call_args
        self.assertEqual(call.args[0], "http://localhost:8080/api/v2/torrents/rename")
        self.assertEqual(call.kwargs["data"]["hash"], "HASH")
        self.assertEqual(call.kwargs["data"]["name"], "New Name")

    def test_raises_on_500(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.post.return_value = _ok_response(status=500)
        with self.assertRaises(QBittorrentApiError):
            c.rename("HASH", "New Name")


class TestVersionDetection(unittest.TestCase):
    def test_detects_v4(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(status=200, text="v4.6.0")
        self.assertEqual(c._detect_api_version(), 4)

    def test_detects_v5(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(status=200, text="v5.0.0")
        self.assertEqual(c._detect_api_version(), 5)

    def test_detects_v5_without_prefix(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(status=200, text="5.1.0")
        self.assertEqual(c._detect_api_version(), 5)

    def test_defaults_to_v4_on_connection_failure(self):
        import requests
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.side_effect = requests.ConnectionError("nope")
        self.assertEqual(c._detect_api_version(), 4)

    def test_defaults_to_v4_on_api_error(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(status=500)
        self.assertEqual(c._detect_api_version(), 4)

    def test_ensure_api_version_calls_detect_once(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(status=200, text="v5.0.0")
        self.assertFalse(c._version_checked)
        self.assertEqual(c._api_version, 0)
        c._ensure_api_version()
        self.assertTrue(c._version_checked)
        self.assertEqual(c._api_version, 5)
        # Second call should not re-detect
        c._session.get.return_value = _ok_response(status=200, text="v4.0.0")
        c._ensure_api_version()
        self.assertEqual(c._api_version, 5)


def _make_minimal_torrent() -> bytes:
    """Return bytes of a minimal valid bencoded .torrent with an ``info`` dict."""
    # d8:announce3:url4:infod4:name3:rom12:piece lengthi1e6:pieces1:xee
    return (
        b"d8:announce3:url4:infod4:name3:rom12:piece lengthi1e6:pieces1:xee"
    )


KNOWN_INFO_HASH = "fdc895f60547b60562e687d6f549269e121c0ecb"


class TestFindHashByTorrentName(unittest.TestCase):
    def test_returns_most_recent_match(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(
            status=200,
            json_data=[
                {"name": "old_name", "hash": "OLD", "added_on": 100},
                {"name": "Some_Torrent", "hash": "NEW", "added_on": 200},
                {"name": "unrelated", "hash": "X", "added_on": 300},
            ],
        )
        self.assertEqual(c._find_hash_by_torrent_name("Some_Torrent"), "NEW")

    def test_returns_none_when_no_match(self):
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(
            status=200,
            json_data=[{"name": "totally_different", "hash": "X",
                         "added_on": 100}],
        )
        self.assertIsNone(c._find_hash_by_torrent_name("Some_Torrent"))

    def test_punctuation_tokens_do_not_trivially_match(self):
        """'-' and other punctuation-only tokens must not match every name."""
        c = QBittorrentClient()
        c._session = _make_session()
        c._session.get.return_value = _ok_response(
            status=200,
            json_data=[
                {"name": "Minerva - Foo - Bar", "hash": "SHOULD_NOT_MATCH",
                 "added_on": 100},
            ],
        )
        # Without filtering, "-" would trivially match the hyphenated name above.
        self.assertIsNone(
            c._find_hash_by_torrent_name("Minerva_Myrient - No-Intro - Missing")
        )


if __name__ == "__main__":
    unittest.main()
