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
from minerva.parsers.dat_parser import DatEntry

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
    from minerva.ui.theme import ThemeTokens
    t = ThemeTokens()
    badges = {
        DownloadSource.MINERVA_TORRENT: (
            f'<span style="background:{t.info_fg};color:white;padding:1px 6px;'
            f'border-radius:{t.radius_sm};font-size:10px;">Minerva</span>'
        ),
        DownloadSource.ARCHIVE_ORG_TORRENT: (
            f'<span style="background:{t.success};color:white;padding:1px 6px;'
            f'border-radius:{t.radius_sm};font-size:10px;">archive.org \U0001f310</span>'
        ),
        DownloadSource.ARCHIVE_ORG_HTTP: (
            f'<span style="background:{t.warning};color:white;padding:1px 6px;'
            f'border-radius:{t.radius_sm};font-size:10px;">archive.org \u2b07</span>'
        ),
    }
    return badges.get(source, f'<span style="color:{t.text_muted};">unknown</span>')


class _TaskSignals(QtCore.QObject):
    """Signals for thread-pool task notification."""
    succeeded = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    finished = QtCore.pyqtSignal()


class _MatchSearchTask(QtCore.QRunnable):
    """Run Minerva + archive.org candidate matching on a worker thread.

    Combines both searches into a single task so the UI only spawns one
    thread-pool job per entry selection, and the UI thread never blocks
    on SQLite FTS5 queries or network calls.
    """

    def __init__(
        self,
        db: object,
        provider: ArchiveOrgCandidateProvider,
        dat: object,
        system: str,
        automatic_file_id: int | None,
        *,
        ao_executor: object | None = None,
    ) -> None:
        super().__init__()
        self._provider = provider
        self._dat = dat
        self._system = system
        self._automatic_file_id = automatic_file_id
        self.signals = _TaskSignals()
        self._ao_executor = ao_executor
        # Extract the DB path so the worker thread can open its own connection
        # — sharing a MinervaDB across threads causes "database is locked"
        db_path = getattr(db, '_path', None) or getattr(db, 'index_path', None)
        if db_path is None:
            from minerva_db import DEFAULT_INDEX_PATH
            db_path = DEFAULT_INDEX_PATH
        self._db_path = db_path

    def run(self) -> None:
        result: dict | None = None
        exc: Exception | None = None
        try:
            # Open a thread-local DB connection — sharing the main thread's
            # MinervaDB instance causes "database is locked" under SQLite.
            from minerva_db import MinervaDB
            db = MinervaDB(str(self._db_path))

            # ── Minerva candidates (DB queries, off the UI thread) ──
            minerva_candidates = db.match_dat_detailed(
                [self._dat], collection="", system=self._system, candidate_limit=5,
            )
            minerva_rows: list[dict] = []
            if minerva_candidates.get("results"):
                raw_cands = minerva_candidates["results"][0].get("candidates", [])
                file_ids = [c["file_id"] for c in raw_cands]
                regions_map = db.get_file_regions(file_ids) if file_ids else {}
                for c in raw_cands:
                    fid = c["file_id"]
                    minerva_rows.append({
                        "file_id": fid,
                        "confidence": c.get("confidence", 0),
                        "method": c.get("method", "?"),
                        "title": c.get("title", "—"),
                        "regions": regions_map.get(fid, []),
                    })

            # ── Match info for the comparison table ──
            match_info: dict = {}
            if self._automatic_file_id is not None:
                items = db.get_files_by_ids([self._automatic_file_id])
                if items:
                    it = items[0]
                    match_info = {
                        "basename": it.basename,
                        "regions": it.regions,
                        "source_torrent": it.source_torrent,
                        "size": it.size,
                    }

            # ── Archive.org candidates (network call with 8s timeout) ──
            # Use the panel-injected executor (shared across searches) to
            # prevent thread accumulation. Staleness is handled by the
            # panel's generation check in on_succeeded/on_failed.
            import concurrent.futures
            ao_candidates: list = []
            ao_status: str = "ok"
            if self._ao_executor is not None:
                try:
                    future = self._ao_executor.submit(self._provider.search, self._dat, self._system)
                    ao_candidates = future.result(timeout=8.0)
                except concurrent.futures.TimeoutError:
                    log.warning("Archive.org search timed out after 8s — skipping")
                    ao_status = "timeout"
                    ao_candidates = []
                    future.cancel()
                except Exception as e:
                    log.debug("Archive.org search failed: %s", e)
                    ao_status = "error"
                    ao_candidates = []
            else:
                ao_status = "skipped"
                ao_candidates = []


            result = {
                "minerva": minerva_rows,
                "archive_org": ao_candidates,
                "archive_org_status": ao_status,
                "match_info": match_info,
            }

        except Exception as e:
            exc = e

        if exc is not None:
            _safe_emit(self.signals, "failed", str(exc))
        else:
            _safe_emit(self.signals, "succeeded", result)
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
    minerva_candidate_selected = QtCore.pyqtSignal(str, str, int)  # report_id, entry_id, file_id

    def __init__(
        self,
        app_state: AppState,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._app_state = app_state
        from minerva.ui.theme import ThemeTokens
        self._tokens = ThemeTokens()
        self._current_report_id: str | None = None
        self._current_entry_id: str | None = None
        self._current_system: str | None = None
        self._current_filename: str | None = None
        self._archive_org_provider = ArchiveOrgCandidateProvider()
        # Cache DB instance — MinervaDB() opens a new SQLite connection each call
        from minerva_db import MinervaDB
        self._db = MinervaDB()
        self._running_tasks: set[_MatchSearchTask] = set()
        # Bounded executor for archive.org searches — reused across all
        # _MatchSearchTask instances to prevent thread accumulation (P2-4).
        import concurrent.futures
        self._ao_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self._ao_generation = 0
        self.setObjectName("matchDetailPanel")
        self.setMinimumWidth(310)

        self._build_ui()
        self.destroyed.connect(self._shutdown_executor)

    def _shutdown_executor(self, *args) -> None:
        """Shut down the archive.org search executor on panel destruction."""
        if not getattr(self, "_ao_executor_shutdown", False):
            self._ao_executor_shutdown = True
            try:
                self._ao_executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                # Python <3.9: cancel_futures not supported
                self._ao_executor.shutdown(wait=False)

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
        """Display match details for *entry* in *report_id*.

        Shows a placeholder immediately, then launches a single worker
        thread that runs both the Minerva DB queries (match_dat_detailed,
        get_file_regions, get_files_by_ids) and the archive.org search.
        The UI never blocks on SQLite FTS5 or network I/O.
        """
        self._current_report_id = report_id
        self._current_entry_id = entry.id
        self._current_system = system
        self._current_filename = entry.filename
        self._title_label.setText(entry.filename)

        # Show static fields immediately from entry data (no DB access needed)
        size_str = f"{entry.size / 1024 / 1024:.1f} MB" if entry.size else "—"
        method = entry.automatic_method or "none"
        confidence = f"{entry.automatic_confidence:.0%}" if entry.automatic_confidence is not None else "—"

        self._comparison_detail.setText(
            f"<table cellpadding='2'>"
            f"<tr><td><b>Resolution</b></td><td>{entry.resolution.value}</td></tr>"
            f"<tr><td><b>Decision</b></td><td>{entry.decision}</td></tr>"
            f"<tr><td><b>Method</b></td><td>{method}</td></tr>"
            f"<tr><td><b>Confidence</b></td><td>{confidence}</td></tr>"
            f"<tr><td><b>Requested size</b></td><td>{size_str}</td></tr>"
            f"</table>"
        )
        self._candidates_detail.setText(
            '<span style="color:#888;">Searching Minerva index and archive.org...</span>'
        )
        self._approve_btn.setEnabled(True)
        self._ignore_btn.setEnabled(True)

        # Launch combined search on a worker thread
        dat = DatEntry(filename=entry.filename, size=entry.size)
        scope_system = system or ""
        self._ao_generation += 1
        my_generation = self._ao_generation
        task = _MatchSearchTask(
            self._db, self._archive_org_provider, dat, scope_system,
            entry.automatic_file_id,
            ao_executor=self._ao_executor,
        )
        entry_id = entry.id
        automatic_file_id = entry.automatic_file_id

        def on_succeeded(result: object) -> None:
            # Ignore stale results if the user selected a different entry
            # or a newer search was started.
            if self._current_entry_id != entry_id or my_generation != self._ao_generation:
                return
            payload = dict(result) if result else {}
            self._render_match_results(payload, automatic_file_id, size_str)

        def on_failed(msg: str) -> None:
            if self._current_entry_id != entry_id or my_generation != self._ao_generation:
                return
            self._candidates_detail.setText(
                f'<span style="color:#c00;">Search failed: {html_mod.escape(msg)}</span>'
            )

        def cleanup() -> None:
            self._running_tasks.discard(task)

        task.signals.succeeded.connect(on_succeeded)
        task.signals.failed.connect(on_failed)
        task.signals.finished.connect(cleanup)
        self._running_tasks.add(task)
        QtCore.QThreadPool.globalInstance().start(task)

    def clear(self) -> None:
        """Clear the panel — no entry selected."""
        self._current_report_id = None
        self._current_entry_id = None
        self._current_system = None
        self._current_filename = None
        self._title_label.setText("Select an entry to review")
        self._comparison_detail.setText(
            '<span style="color:#888;">No entry selected</span>'
        )
        self._candidates_detail.setText(
            '<span style="color:#888;">No entry selected</span>'
        )
        self._approve_btn.setEnabled(False)
        self._ignore_btn.setEnabled(False)

    def _render_match_results(
        self, payload: dict, automatic_file_id: int | None, size_str: str,
    ) -> None:
        """Render the combined Minerva + archive.org results on the UI thread."""
        # ── Comparison table (enriched with DB lookups) ──
        match_info = payload.get("match_info") or {}
        minerva_rows = payload.get("minerva") or []

        # If there's no automatic match but Minerva found a top candidate,
        # use it as the effective match so the comparison table shows real data
        # and the "Approve" button has a file_id to work with.
        if not match_info and minerva_rows:
            top = minerva_rows[0]
            match_info = {
                "basename": top.get("title", "—"),
                "regions": top.get("regions", []),
                "source_torrent": "Minerva index",
                "size": 0,
            }
            # Auto-select the top candidate if not already selected
            top_fid = top["file_id"]
            if automatic_file_id is None and self._current_report_id and self._current_entry_id:
                self.minerva_candidate_selected.emit(
                    self._current_report_id, self._current_entry_id, top_fid
                )

        # Fill comparison table from match_info
        match_name = "—"
        match_region = "—"
        match_source = "—"
        match_size = "—"
        if match_info:
            match_name = html_mod.escape(match_info.get("basename", "—"))
            regions = match_info.get("regions") or ()
            match_region = ", ".join(regions) if regions else "—"
            match_source = html_mod.escape(str(match_info.get("source_torrent") or "—"))
            sz = match_info.get("size") or 0
            match_size = f"{sz / 1024 / 1024:.1f} MB" if sz else "—"

        self._comparison_detail.setText(
            f"<table cellpadding='2'>"
            f"<tr><td><b>Matched file</b></td><td>{match_name}</td></tr>"
            f"<tr><td><b>Region</b></td><td>{match_region}</td></tr>"
            f"<tr><td><b>Source</b></td><td>{match_source}</td></tr>"
            f"<tr><td><b>Requested size</b></td><td>{size_str}</td></tr>"
            f"<tr><td><b>Actual size</b></td><td>{match_size}</td></tr>"
            f"</table>"
        )

        # ── Candidates table ──
        ao_candidates = payload.get("archive_org") or []
        ao_status = payload.get("archive_org_status", "ok")

        if not minerva_rows and not ao_candidates:
            message = "No candidates found"
            if ao_status == "timeout":
                message += "\nArchive.org timed out"
            elif ao_status == "error":
                message += "\nArchive.org unavailable"
            self._candidates_detail.setText(message)
            return

        rows_html: list[str] = []
        for c in minerva_rows:
            fid = c["file_id"]
            conf = c.get("confidence", 0)
            method = c.get("method", "?")
            title = html_mod.escape(str(c.get("title", "—"))[:50])
            regions = c.get("regions") or []
            region_str = ", ".join(regions) if regions else "—"
            is_selected = fid == automatic_file_id
            marker = "▶ " if is_selected else "  "
            conf_str = f"{conf:.0%}"
            style = "font-weight:bold;" if is_selected else ""
            badge = _source_badge(DownloadSource.MINERVA_TORRENT)
            rows_html.append(
                f"<tr style='{style}'><td>{marker}</td>"
                f"<td>{title}</td><td>{conf_str}</td>"
                f"<td>{html_mod.escape(str(method))}</td><td>{html_mod.escape(region_str)}</td>"
                f"<td>{badge}</td></tr>"
            )

        for ac in ao_candidates[:5]:
            conf_str = f"{ac.confidence:.0%}"
            title = html_mod.escape(ac.title[:50])
            source_badge_html = _source_badge(ac.source)
            seeders = str(ac.seeders) if ac.seeders is not None else "—"
            href_val = f"archive_org:{ac.source.value}:{quote(ac.source_ref, safe='')}".replace('"', '%22')
            method = html_mod.escape(str(ac.method))
            rows_html.append(
                f"<tr><td>  </td>"
                f'<td><a href="{href_val}" style="color:{self._tokens.info_fg};text-decoration:none;">{title}</a></td>'
                f"<td>{conf_str}</td>"
                f"<td>{method}</td><td>{seeders}</td>"
                f"<td>{source_badge_html}</td></tr>"
            )

        status_footer = ""
        if ao_status != "ok" and not ao_candidates:
            status_label = "Archive.org unavailable" if ao_status == "error" else "Archive.org timed out"
            status_footer = f"<tr><td></td><td colspan='5' style='color:{self._tokens.text_muted};'>{status_label}</td></tr>"
        elif ao_candidates:
            status_footer = (
                f"<tr><td></td><td colspan='5' style='color:{self._tokens.text_muted};font-size:10px;'>"
                "Click an archive.org link to select it as the download source"
                "</td></tr>"
            )

        self._candidates_detail.setText(
            "<table cellpadding='2' width='100%'>"
            "<tr><td></td><td><b>Title</b></td><td><b>Conf</b></td>"
            "<td><b>Method</b></td><td><b>Region</b></td><td><b>Source</b></td></tr>"
            + "".join(rows_html) + status_footer + "</table>"
        )

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
