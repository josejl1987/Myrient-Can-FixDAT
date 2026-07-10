# tests/test_http_download.py
"""Tests for the HTTP download adapter."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from minerva.services.http_download import HttpDownloadAdapter


def test_http_download_streams_to_destination(tmp_path):
    """Adapter streams chunks to the destination file."""
    dest = tmp_path / "game.zip"
    url = "https://archive.org/download/test-item/game.zip"

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.iter_content.return_value = [b"chunk1", b"chunk2", b"chunk3"]
    fake_response.headers = {"content-length": "15"}
    fake_response.raise_for_status = MagicMock()

    adapter = HttpDownloadAdapter()
    with patch("minerva.services.http_download.requests.get", return_value=fake_response):
        progress_events: list[float] = []
        adapter.download(
            url=url,
            destination=dest,
            on_progress=lambda p: progress_events.append(p),
        )

    assert dest.exists()
    assert dest.read_bytes() == b"chunk1chunk2chunk3"
    assert len(progress_events) > 0
    assert progress_events[-1] == 1.0


def test_http_download_retries_on_429(tmp_path):
    """Adapter retries with backoff on HTTP 429."""
    dest = tmp_path / "game.zip"
    url = "https://archive.org/download/test-item/game.zip"

    fake_response_ok = MagicMock()
    fake_response_ok.status_code = 200
    fake_response_ok.iter_content.return_value = [b"data"]
    fake_response_ok.headers = {"content-length": "4"}
    fake_response_ok.raise_for_status = MagicMock()

    fake_response_429 = MagicMock()
    fake_response_429.status_code = 429
    fake_response_429.iter_content.return_value = []
    fake_response_429.headers = {}
    fake_response_429.raise_for_status = MagicMock()

    adapter = HttpDownloadAdapter(max_retries=3, backoff_base=0.01)
    with patch(
        "minerva.services.http_download.requests.get",
        side_effect=[fake_response_429, fake_response_429, fake_response_ok],
    ):
        with patch("minerva.services.http_download.time.sleep"):
            adapter.download(url=url, destination=dest)

    assert dest.exists()
    assert dest.read_bytes() == b"data"


def test_http_download_raises_on_max_retries(tmp_path):
    """Adapter raises after exceeding max retries."""
    dest = tmp_path / "game.zip"
    url = "https://archive.org/download/test-item/game.zip"

    fake_response_429 = MagicMock()
    fake_response_429.status_code = 429
    fake_response_429.iter_content.return_value = []
    fake_response_429.headers = {}
    fake_response_429.raise_for_status = MagicMock()

    adapter = HttpDownloadAdapter(max_retries=2, backoff_base=0.01)
    with patch(
        "minerva.services.http_download.requests.get",
        return_value=fake_response_429,
    ):
        with patch("minerva.services.http_download.time.sleep"):
            try:
                adapter.download(url=url, destination=dest)
                raise AssertionError("Should have raised")
            except RuntimeError as exc:
                assert "Rate limited" in str(exc) or "429" in str(exc)
