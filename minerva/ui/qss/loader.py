"""QSS loading with a defined cascade order.

Order (concatenated in this sequence):

    base → panels → inspectors → actions → states → library → downloads → collections

All page-specific QSS files are loaded globally so that shared selectors
defined in them (e.g. ``accentButton`` in library.qss) are available to
every page.  Pages that need overrides should target their own objectName
selectors, not invent new shared ones.
"""

from __future__ import annotations

from pathlib import Path

_HERE = Path(__file__).resolve().parent

_ORDER = [
    "base.qss",
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
    """Load and concatenate all QSS files in the canonical cascade order.

    Args:
        page_name: Deprecated — kept for backward compatibility.  All
            page-specific QSS files are now loaded globally so that
            shared selectors (``accentButton``, ``libraryTable``, etc.)
            are available on every page.
    """
    parts: list[str] = []
    for name in _ORDER:
        path = _HERE / name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


def all_qss_files() -> list[str]:
    """Return the full ordered list of QSS filenames (for diagnostics)."""
    return list(_ORDER)
