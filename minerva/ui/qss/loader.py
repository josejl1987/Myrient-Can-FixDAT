"""QSS loading with a defined application-wide cascade order."""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent

_ORDER = [
    "base.qss",
    "layout.qss",
    "controls.qss",
    "tables.qss",
    "navigation.qss",
    "cards.qss",
    "panels.qss",
    "inspectors.qss",
    "actions.qss",
    "states.qss",
    "library.qss",
    "downloads.qss",
    "collections.qss",
    "reports.qss",
    "settings.qss",
]


def load_qss(page_name: str | None = None) -> str:
    """Load and concatenate the complete QSS cascade.

    ``page_name`` is retained for API compatibility; all selectors are loaded
    globally because shared widgets can appear on more than one page.
    """
    parts: list[str] = []
    for name in _ORDER:
        path = _HERE / name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def all_qss_files() -> list[str]:
    return list(_ORDER)
