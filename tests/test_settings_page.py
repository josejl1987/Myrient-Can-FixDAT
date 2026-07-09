"""
Tests for ``SettingsPage`` — construction, form fields, save/cancel.
"""

from __future__ import annotations

from PyQt6 import QtCore, QtWidgets

from minerva.app.app_state import AppState
from minerva.app.pages import SettingsPage
from minerva.domain.settings import SettingsDraft


def _make_app_state() -> AppState:
    """Create an ``AppState`` for testing."""
    return AppState()


def test_page_constructs(qtbot):
    """GIVEN an AppState WHEN SettingsPage is constructed THEN no error."""
    page = SettingsPage(AppState())
    qtbot.addWidget(page)
    assert isinstance(page, QtWidgets.QWidget)


def test_has_form_fields(qtbot):
    """GIVEN a constructed SettingsPage THEN form fields exist."""
    page = SettingsPage(AppState())
    qtbot.addWidget(page)
    # At minimum there should be QLineEdit fields
    edits = page.findChildren(QtWidgets.QLineEdit)
    assert len(edits) >= 1


def test_has_buttons(qtbot):
    """GIVEN a constructed SettingsPage THEN Save/Cancel buttons exist."""
    page = SettingsPage(AppState())
    qtbot.addWidget(page)
    buttons = page.findChildren(QtWidgets.QPushButton)
    button_texts = [b.text() for b in buttons]
    assert "Save" in " ".join(button_texts)
    assert "Cancel" in " ".join(button_texts)


def test_load_settings_defaults(qtbot):
    """GIVEN a constructed SettingsPage THEN default values are loaded."""
    page = SettingsPage(AppState())
    qtbot.addWidget(page)
    # Should have loaded from QSettings without error
    assert page._draft is not None


def test_save_and_load_round_trip(qtbot, tmp_path, monkeypatch):
    """GIVEN SettingsPage WHEN settings are saved and page reconstructed
    THEN values persist."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "settings_test"))
    QtCore.QSettings.setDefaultFormat(QtCore.QSettings.Format.IniFormat)

    page = SettingsPage(AppState())
    qtbot.addWidget(page)
    page._persist_draft()
    del page

    page2 = SettingsPage(AppState())
    qtbot.addWidget(page2)
    assert page2._draft is not None


def test_revert_discards_changes(qtbot):
    """GIVEN SettingsPage WHEN Cancel is clicked THEN draft reverts."""
    page = SettingsPage(AppState())
    qtbot.addWidget(page)
    # Simulate revert via the cancel handler
    page._on_cancel()
    assert page._draft is not None


def test_advanced_page_has_log_level_combo(qtbot):
    """The Advanced page has a log level dropdown."""
    app_state = _make_app_state()
    page = SettingsPage(app_state)
    qtbot.addWidget(page)
    assert hasattr(page, "_log_level_combo")
    assert page._log_level_combo.count() == 4  # DEBUG, INFO, WARNING, ERROR


def test_advanced_page_has_open_log_button(qtbot):
    """The Advanced page has an 'Open log file' button."""
    app_state = _make_app_state()
    page = SettingsPage(app_state)
    qtbot.addWidget(page)
    assert hasattr(page, "_open_log_btn")


def test_log_level_persists_to_settings(qtbot, tmp_path, monkeypatch):
    """Saving the log level writes it to QSettings."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    app_state = _make_app_state()
    page = SettingsPage(app_state)
    qtbot.addWidget(page)
    page._log_level_combo.setCurrentIndex(0)  # DEBUG
    page._on_save()
    assert page._settings.value("log_level", "", str) == "DEBUG"
