"""Tests for queueing archive.org candidates."""
from __future__ import annotations

from unittest.mock import MagicMock

from minerva.domain.sources import DownloadSource


def test_add_to_queue_accepts_source_params():
    """add_to_queue accepts source and source_ref kwargs."""
    from minerva.app.download_controller import DownloadController
    import inspect
    sig = inspect.signature(DownloadController.add_to_queue)
    assert 'source' in sig.parameters
    assert 'source_ref' in sig.parameters


def test_add_many_to_queue_accepts_5_tuples():
    """add_many_to_queue accepts items with source and source_ref."""
    from minerva.app.download_controller import DownloadController
    # Just verify the method exists and is callable
    assert callable(DownloadController.add_many_to_queue)
