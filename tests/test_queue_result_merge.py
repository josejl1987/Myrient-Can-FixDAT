"""Tests for QueueResult.merge aggregation."""

from __future__ import annotations

from minerva.domain.reports import QueueResult


def test_merge_empty_returns_zero():
    result = QueueResult.merge()
    assert result == QueueResult(0, 0, 0, 0)


def test_merge_single_returns_same_values():
    single = QueueResult(added=3, skipped_active=1, skipped_complete=2, skipped_missing=4)
    assert QueueResult.merge(single) == single


def test_merge_multiple_sums_all_fields():
    a = QueueResult(added=5, skipped_active=1, skipped_complete=2, skipped_missing=3)
    b = QueueResult(added=10, skipped_active=4, skipped_complete=5, skipped_missing=6)
    c = QueueResult(added=0, skipped_active=0, skipped_complete=0, skipped_missing=1)
    result = QueueResult.merge(a, b, c)
    assert result.added == 15
    assert result.skipped_active == 5
    assert result.skipped_complete == 7
    assert result.skipped_missing == 10
