"""MatchDetailPanel — inline match review panel replacing InspectorScaffold.

Provides: candidate list, per-entry approve/ignore, bulk actions
(approve all exact, approve fuzzy above threshold), CDRomance search,
comparison view, export/generate DAT.

Occupies the inspector slot in the ResponsiveWorkspace 3-zone layout.
"""

from __future__ import annotations

import html as html_mod
import logging
import webbrowser
from urllib.parse import quote, unquote

from PyQt6 import QtCore, QtWidgets

from minerva.app.app_state import AppState
from minerva.domain.reports import ReviewEntry
from minerva.domain.sources import DownloadSource
from minerva.services.archive_org import ArchiveOrgCandidateProvider
from minerva.ui.icons import Icons
from minerva_db import DatEntry

log = logging.getLogger(__name__)


# ── CDRomance / RetroGameTalk search helpers ──────────────────────────────

_SYSTEM_TO_CDR_SLUG = {
    "Sony - PlayStation": "psx-iso",
    "Sony - PlayStation 2": "ps2-iso",
    "Sony - PlayStation Portable": "psp",
    "Sony - PlayStation Vita": "vita",
    "Sega - Dreamcast": "dc-iso",
    "Sega - Saturn": "sega_saturn_isos",
    "Sega - Mega Drive - Genesis": "sega_genesis_roms",
    "Sega - Sega CD": "sega_cd_isos",
    "Sega - 32X": "sega_32x_roms",
    "Sega - Master System": "sms_roms",
    "Sega - Game Gear": "game-gear",
    "Nintendo - Wii": "wii-iso",
    "Nintendo - GameCube": "gamecube",
    "Nintendo - Nintendo 64": "n64-roms",
    "Nintendo - Super Nintendo": "snes-rom",
    "Nintendo - Nintendo Entertainment System": "nes-roms",
    "Nintendo - Famicom Disk System": "famicom_disk_system",
    "Nintendo - Nintendo DS": "nds-roms",
    "Nintendo - Game Boy Advance": "gba-roms",
    "Nintendo - Game Boy Color": "gameboy-color-roms",
    "Nintendo - Game Boy": "gameboy-roms",
    "NEC - PC Engine - TurboGrafx-16": "turbografx-16",
    "NEC - PC Engine CD - TurboGrafx-CD": "turbografx-cd",
    "NEC - PC-FX": "pc-fx",
    "SNK - Neo Geo Pocket Color": "neo-geo-pocket",
    "SNK - Neo Geo CD": "neo-geo-cd",
    "Panasonic - 3DO Interactive Multiplayer": "3do-iso",
    "Bandai - WonderSwan": "wonderswan",
    "Microsoft - MSX": "msx-roms",
    "Microsoft - Windows": "windows",
    "DOS": "msdos",
}


def _source_badge(source: DownloadSource) -> str:
    """Return an HTML badge string for a candidate source."""
    badges = {
        DownloadSource.MINERVA_TORRENT: (
            '<span style="background:#3b82f6;color:white;padding:1px 6px;'
            'border-radius:3px;font-size:10px;">Minerva</span>'
        ),
        DownloadSource.ARCHIVE_ORG_TORRENT: (
            '<span style="background:#22c55e;color:white;padding:1px 6px;'
            'border-radius:3px;font-size:10px;">archive.org \U0001f310</span>'
        ),
        DownloadSource.ARCHIVE_ORG_HTTP: (
            '<span style="background:#f97316;color:white;padding:1px 6px;'
            'border-radius:3px;font-size:10px;">archive.org \u2b07</span>'
        ),
    }
    return badges.get(source, '<span style="color:#888;">unknown</span>')


class _TaskSignals(QtCore.QObject):
    """Signals for thread-pool task notification."""
    succeeded = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()


class _ArchiveOrgSearchTask(QtCore.QRunnable):
    """Search archive.org on a worker thread."""

    def __init__(self, provider: ArchiveOrgCandidateProvider, dat: object, system: str) -> None:
        super().__init__()
        self._provider = provider
        self._dat = dat
        self._system = system
        self.signals = _TaskSignals()

    def run(self) -> None:
        results: object = None
        exc: Exception | None = None
        try:
            results = self._provider.search(self._dat, self._system)
        except Exception as e:
            exc = e

        # Emit results or error — the signals QObject may have been deleted
        # during shutdown (RuntimeError), which we suppress gracefully.
        if exc is not None:
            _safe_emit(self.signals, "failed", str(exc))
        else:
            _safe_emit(self.signals, "succeeded", results)
        _safe_emit(self.signals, "finished")


def _safe_emit(signals: QtCore.QObject, signal_name: str, *args: object) -> None:
    """Emit *signal_name* on *signals*, ignoring RuntimeError from deleted QObject."""
    try:
        sig = getattr(signals, signal_name)
        sig.emit(*args)
    except RuntimeError:
        pass  # C++ QObject already deleted during shutdown

class MatchDetailPanel(QtWidgets.QWidget):
    """Inline match detail panel — replaces InspectorScaffold in the
    3-zone Reports workspace.

    Parameters
    ----------
    app_state : AppState
        Shared application state for signal bus access.
    """

    decision_changed = QtCore.pyqtSignal(str, str, str)  # report_id, entry_id, decision
    bulk_decision_requested = QtCore.pyqtSignal(str, str, int)  # report_id, filter_type, threshold
    archive_org_candidate_selected = QtCore.pyqtSignal(str, str, str, str)  # report_id, entry_id, source, source_ref

    def __init__(
        self,
        app_state: AppState,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_state = app_state
        self._current_report_id: str | None = None
        self._current_entry_id: str | None = None
        self._current_system: str | None = None
        self._current_filename: str | None = None
        self._archive_org_provider = ArchiveOrgCandidateProvider()
        # Cache DB instance — MinervaDB() opens a new SQLite connection each call
        from minerva_db import MinervaDB
        self._db = MinervaDB()
        self._running_tasks: set[_ArchiveOrgSearchTask] = set()

        self.setObjectName("matchDetailPanel")
        self.setMinimumWidth(310)

        self._build_ui()

    # ── Public API ──────────────────────────────────────────────────────

    @property
    def current_entry_id(self) -> str | None:
        return self._current_entry_id

    @property
    def comparison_widget(self) -> QtWidgets.QWidget:
        return self._comparison

    @property
    def export_button(self) -> QtWidgets.QPushButton:
        return self._export_btn

    @property
    def generate_dat_button(self) -> QtWidgets.QPushButton:
        return self._generate_btn

    def show_entry(self, report_id: str, entry: ReviewEntry, system: str | None = None) -> None:
        """Display match details for *entry* in *report_id*."""
        self._current_report_id = report_id
        self._current_entry_id = entry.id
        self._current_system = system
        self._current_filename = entry.filename
        # Build match info from cached DB instance
        db = self._db

        match_name = "—"
        match_region = "—"
        match_source = "—"
        match_size = "—"
        if entry.automatic_file_id is not None:
            items = db.get_files_by_ids([entry.automatic_file_id])
            if items:
                it = items[0]
                match_name = it.basename
                match_region = ", ".join(it.regions) if it.regions else "—"
                match_source = it.source_torrent or "—"
                match_size = f"{it.size / 1024 / 1024:.1f} MB"

        size_str = f"{entry.size / 1024 / 1024:.1f} MB" if entry.size else "—"
        method = entry.automatic_method or "none"
        confidence = f"{entry.automatic_confidence:.0%}" if entry.automatic_confidence is not None else "—"

        self._comparison_detail.setText(
            f"<table cellpadding='2'>"
            f"<tr><td><b>Resolution</b></td><td>{entry.resolution.value}</td></tr>"
            f"<tr><td><b>Decision</b></td><td>{entry.decision}</td></tr>"
            f"<tr><td><b>Method</b></td><td>{method}</td></tr>"
            f"<tr><td><b>Confidence</b></td><td>{confidence}</td></tr>"
            f"<tr><td><b>Matched file</b></td><td>{match_name}</td></tr>"
            f"<tr><td><b>Region</b></td><td>{match_region}</td></tr>"
            f"<tr><td><b>Source</b></td><td>{match_source}</td></tr>"
            f"<tr><td><b>Requested size</b></td><td>{size_str}</td></tr>"
            f"<tr><td><b>Actual size</b></td><td>{match_size}</td></tr>"
            f"</table>"
        )

        # Build Minerva candidates synchronously
        candidates_html = self._build_minerva_candidates_html(db, entry, system)
        placeholder = candidates_html + '<br><span style="color:#888;">Searching archive.org...</span>'
        self._candidates_detail.setText(placeholder)

        # Launch async archive.org search
        dat = DatEntry(filename=entry.filename, size=entry.size)
        scope_system = system or ""
        self._fetch_archive_org_candidates(dat, scope_system, candidates_html)

        self._approve_btn.setEnabled(True)
        self._ignore_btn.setEnabled(True)

    def clear(self) -> None:
        """Clear the panel — no entry selected."""
        self._current_report_id = None
        self._current_entry_id = None
        self._current_system = None
        self._current_filename = None
        self._title_label.setText("Select an entry to review")
        self._comparison_detail.setText("No entry selected")
        self._candidates_detail.setText("No entry selected")
        self._approve_btn.setEnabled(False)
        self._ignore_btn.setEnabled(False)

    def approve_entry(self) -> None:
        """Approve the current entry — emit decision_changed with 'accept'."""
        if self._current_report_id and self._current_entry_id:
            self.decision_changed.emit(
                self._current_report_id, self._current_entry_id, "accept"
            )

    def ignore_entry(self) -> None:
        """Ignore the current entry — emit decision_changed with 'reject'."""
        if self._current_report_id and self._current_entry_id:
            self.decision_changed.emit(
                self._current_report_id, self._current_entry_id, "reject"
            )

    def approve_all_exact(self) -> None:
        """Bulk-approve all exact-match entries in the current report."""
        if self._current_report_id is None:
            return
        self.bulk_decision_requested.emit(self._current_report_id, "exact", 0)

    def approve_fuzzy_above_threshold(self, threshold: int) -> None:
        """Bulk-approve fuzzy matches above *threshold*% confidence."""
        if self._current_report_id is None:
            return
        self._threshold_value = threshold
        self.bulk_decision_requested.emit(self._current_report_id, "fuzzy", threshold)

    def build_cdromance_url(self, game_name: str, system: str | None = None) -> str:
        """Build a CDRomance search URL scoped to *system*."""
        slug = _SYSTEM_TO_CDR_SLUG.get(system or "")
        encoded = quote(game_name)
        if slug:
            return f"https://cdromance.org/{slug}/?s={encoded}"
        return f"https://cdromance.org/?s={encoded}"

    # ── UI construction ─────────────────────────────────────────────────

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # ── Header ──────────────────────────────────────────────────────
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(8)
        heading = QtWidgets.QLabel("Match Detail")
        heading.setObjectName("sectionTitle")
        header.addWidget(heading)
        header.addStretch(1)
        root.addLayout(header)

        self._title_label = QtWidgets.QLabel("Select an entry to review")
        self._title_label.setObjectName("inspectorTitle")
        self._title_label.setWordWrap(True)
        root.addWidget(self._title_label)

        sep = QtWidgets.QFrame()
        sep.setObjectName("separator")
        sep.setFixedHeight(1)
        root.addWidget(sep)

        # ── Per-entry actions ───────────────────────────────────────────
        action_row = QtWidgets.QHBoxLayout()
        action_row.setSpacing(8)

        self._approve_btn = QtWidgets.QPushButton("Approve")
        self._approve_btn.setIcon(Icons.check())
        self._approve_btn.setObjectName("primaryButton")
        self._approve_btn.setFixedHeight(32)
        self._approve_btn.setEnabled(False)
        self._approve_btn.clicked.connect(self.approve_entry)
        action_row.addWidget(self._approve_btn)

        self._ignore_btn = QtWidgets.QPushButton("Ignore")
        self._ignore_btn.setIcon(Icons.skip())
        self._ignore_btn.setObjectName("secondaryButton")
        self._ignore_btn.setFixedHeight(32)
        self._ignore_btn.setEnabled(False)
        self._ignore_btn.clicked.connect(self.ignore_entry)
        action_row.addWidget(self._ignore_btn)

        self._cdr_btn = QtWidgets.QPushButton("CDRomance")
        self._cdr_btn.setIcon(Icons.search())
        self._cdr_btn.setObjectName("secondaryButton")
        self._cdr_btn.setFixedHeight(32)
        self._cdr_btn.clicked.connect(self._on_cdromance)
        action_row.addWidget(self._cdr_btn)

        action_row.addStretch(1)
        root.addLayout(action_row)

        # ── Comparison view ─────────────────────────────────────────────
        self._comparison = QtWidgets.QWidget()
        comp_layout = QtWidgets.QVBoxLayout(self._comparison)
        comp_layout.setContentsMargins(0, 0, 0, 0)
        comp_layout.setSpacing(8)
        self._comparison_title = QtWidgets.QLabel("Comparison")
        self._comparison_title.setObjectName("sectionTitle")
        comp_layout.addWidget(self._comparison_title)
        self._comparison_detail = QtWidgets.QLabel("No entry selected")
        self._comparison_detail.setObjectName("mutedLabel")
        self._comparison_detail.setWordWrap(True)
        comp_layout.addWidget(self._comparison_detail)

        self._candidates_title = QtWidgets.QLabel("Top candidates")
        self._candidates_title.setObjectName("sectionTitle")
        comp_layout.addWidget(self._candidates_title)

        self._candidates_detail = QtWidgets.QLabel("No entry selected")
        self._candidates_detail.setObjectName("mutedLabel")
        self._candidates_detail.setWordWrap(True)
        comp_layout.addWidget(self._candidates_detail)
        self._candidates_detail.linkActivated.connect(self._on_link_activated)

        comp_layout.addStretch(1)
        root.addWidget(self._comparison, 1)

        # ── Bulk actions ────────────────────────────────────────────────
        bulk_sep = QtWidgets.QFrame()
        bulk_sep.setObjectName("separator")
        bulk_sep.setFixedHeight(1)
        root.addWidget(bulk_sep)

        bulk_label = QtWidgets.QLabel("Bulk actions")
        bulk_label.setObjectName("sectionTitle")
        root.addWidget(bulk_label)

        threshold_row = QtWidgets.QHBoxLayout()
        self._threshold_spin = QtWidgets.QSpinBox()
        self._threshold_spin.setRange(50, 100)
        self._threshold_spin.setValue(95)
        self._threshold_spin.setSuffix("% confidence")
        threshold_row.addWidget(self._threshold_spin)
        root.addLayout(threshold_row)

        bulk_btn_row1 = QtWidgets.QHBoxLayout()
        bulk_btn_row1.setSpacing(6)
        approve_exact_btn = QtWidgets.QPushButton("Approve exact")
        approve_exact_btn.setIcon(Icons.check())
        approve_exact_btn.setFixedHeight(28)
        approve_exact_btn.clicked.connect(self.approve_all_exact)
        bulk_btn_row1.addWidget(approve_exact_btn)

        approve_fuzzy_btn = QtWidgets.QPushButton("Approve fuzzy")
        approve_fuzzy_btn.setIcon(Icons.status_warning())
        approve_fuzzy_btn.setFixedHeight(28)
        approve_fuzzy_btn.clicked.connect(
            lambda: self.approve_fuzzy_above_threshold(self._threshold_spin.value())
        )
        bulk_btn_row1.addWidget(approve_fuzzy_btn)
        root.addLayout(bulk_btn_row1)

        # ── Export / Generate ────────────────────────────────────────────
        export_row = QtWidgets.QHBoxLayout()
        export_row.setSpacing(6)
        self._export_btn = QtWidgets.QPushButton("Export")
        self._export_btn.setIcon(Icons.download())
        self._export_btn.setFixedHeight(28)
        export_row.addWidget(self._export_btn)

        self._generate_btn = QtWidgets.QPushButton("Generate DAT")
        self._generate_btn.setIcon(Icons.file())
        self._generate_btn.setFixedHeight(28)
        export_row.addWidget(self._generate_btn)
        root.addLayout(export_row)

        root.addStretch(1)

    # ── Candidates helper ──────────────────────────────────────────────────

    def _build_minerva_candidates_html(self, db, entry: ReviewEntry, system: str | None) -> str:
        """Build HTML showing top Minerva match candidates (sync)."""
        dat = DatEntry(filename=entry.filename, size=entry.size)
        scope_system = system or ""
        results = db.match_dat_detailed([dat], collection="", system=scope_system, candidate_limit=5)

        if not results.get("results"):
            return "No candidates found"

        candidates = results["results"][0].get("candidates", [])
        if not candidates:
            return "No candidates found"

        # Get regions for all candidates
        file_ids = [c["file_id"] for c in candidates]
        regions_map = db.get_file_regions(file_ids)

        rows = []
        for c in candidates:
            fid = c["file_id"]
            conf = c.get("confidence", 0)
            method = c.get("method", "?")
            title = c.get("title", "—")
            regions = regions_map.get(fid, [])
            region_str = ", ".join(regions) if regions else "—"
            is_selected = fid == entry.automatic_file_id
            marker = "▶ " if is_selected else "  "
            conf_str = f"{conf:.0%}"
            style = "font-weight:bold;" if is_selected else ""
            rows.append(
                f"<tr style='{style}'><td>{marker}</td>"
                f"<td>{title[:50]}</td><td>{conf_str}</td>"
                f"<td>{method}</td><td>{region_str}</td>"
                f"<td>{_source_badge(DownloadSource.MINERVA_TORRENT)}</td></tr>"
            )


        return (
            "<table cellpadding='2' width='100%'>"
        "<tr><td></td><td><b>Title</b></td><td><b>Conf</b></td>"
        "<td><b>Method</b></td><td><b>Region</b></td><td><b>Source</b></td></tr>"
            + "".join(rows) + "</table>"
        )

    def _fetch_archive_org_candidates(
        self, dat: object, system: str, minerva_html: str
    ) -> None:
        """Fetch archive.org candidates in a worker thread."""
        task = _ArchiveOrgSearchTask(self._archive_org_provider, dat, system)

        def on_succeeded(candidates: object) -> None:
            candidates_list = list(candidates) if candidates else []
            if not candidates_list:
                self._candidates_detail.setText(minerva_html)
                return

            rows = []
            for ac in candidates_list:
                conf_str = f"{ac.confidence:.0%}"
                title = html_mod.escape(ac.title[:50])
                source_badge_html = _source_badge(ac.source)
                seeders = str(ac.seeders) if ac.seeders is not None else "\u2014"
                # URL-encode source_ref to safely embed in href
                href_val = f"archive_org:{ac.source.value}:{quote(ac.source_ref, safe='')}"
                method = html_mod.escape(str(ac.method))
                rows.append(
                    f"<tr><td>  </td>"
                    f'<td><a href="{href_val}" style="color:#4a9eff;text-decoration:none;">{title}</a></td>'
                    f"<td>{conf_str}</td>"
                    f"<td>{method}</td><td>{seeders}</td>"
                    f"<td>{source_badge_html}</td></tr>"
                )

            # Replace the closing </table> with new rows + closing tag
            merged = minerva_html.removesuffix("</table>") + "".join(rows) + "</table>"
            self._candidates_detail.setText(merged)

        def on_failed(msg: str) -> None:
            log.debug("Archive.org candidate fetch failed: %s", msg)
            self._candidates_detail.setText(
                minerva_html + '<br><span style="color:#888;">Archive.org unavailable</span>'
            )

        def cleanup() -> None:
            self._running_tasks.discard(task)

        task.signals.succeeded.connect(on_succeeded)
        task.signals.failed.connect(on_failed)
        task.signals.finished.connect(cleanup)
        self._running_tasks.add(task)
        QtCore.QThreadPool.globalInstance().start(task)

    def _on_link_activated(self, url: str) -> None:
        """Handle clicks on candidate links — emit archive_org_candidate_selected."""
        if url.startswith("archive_org:"):
            rest = url[len("archive_org:"):]
            # Split on first colon to separate source from source_ref
            colon_idx = rest.find(":")
            if colon_idx > 0:
                source = rest[:colon_idx]
                source_ref = unquote(rest[colon_idx + 1:])
                if self._current_report_id and self._current_entry_id:
                    self.archive_org_candidate_selected.emit(
                        self._current_report_id, self._current_entry_id, source, source_ref
                    )

    # ── CDRomance search ────────────────────────────────────────────────

    def _on_cdromance(self) -> None:
        """Open CDRomance search in the browser."""
        if self._current_entry_id is None:
            return
        # Use the entry filename for the search query
        search_name = getattr(self, "_current_filename", None) or self._current_entry_id
        url = self.build_cdromance_url(
            search_name,
            self._current_system,
        )
        webbrowser.open(url)
