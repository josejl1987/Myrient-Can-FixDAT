"""Tests for SettingsDraft log_level field."""

from __future__ import annotations

from minerva.domain.settings import SettingsDraft


def test_settings_draft_has_log_level_default():
    draft = SettingsDraft()
    assert draft.log_level == "INFO"


def test_settings_draft_accepts_log_level():
    draft = SettingsDraft(log_level="DEBUG")
    assert draft.log_level == "DEBUG"
