"""Reference-quality Downloads dashboard."""

from __future__ import annotations

import enum
import logging
import time
from pathlib import Path

try:
    from shiboken6 import isValid as _shiboken_is_valid
except ImportError:
    _shiboken_is_valid = None

from PyQt6 import QtCore, QtGui, QtWidgets

from minerva.app.app_state import AppState
from minerva.app.download_controller import DownloadController
from minerva.app.page_id import PageId
from minerva.app.pages.base import BasePage
from minerva.domain.downloads import (
    DOWNLOAD_ACTIVE_STATUSES,
    DOWNLOAD_DEAD_STATUSES,
    DownloadRuntime,
    DownloadStatus,
)
from minerva.ui.icons import Icons
from minerva.ui.models.delegates import ActionDelegate, IconDelegate
from minerva.ui.models.download_delegates import DownloadProgressDelegate
from minerva.ui.models.download_model import (
    _DOWNLOAD_COLUMNS,
    DownloadRecord,
    format_speed,
)
from minerva.ui.models.torrent_group import TorrentGroupTreeModel
from minerva.ui.notifications import NotificationService
from minerva.ui.widgets.activity_list import ActivityList
from minerva.ui.widgets.content_state import ContentState
from minerva.ui.widgets.cover_art import CoverProvider
from minerva.ui.widgets.download_inspector import DownloadInspector
from minerva.ui.widgets.empty_state import EmptyState
from minerva.ui.widgets.metric_card import MetricCard, MetricKind
from minerva.ui.widgets.metric_strip import MetricStrip
from minerva.ui.widgets.page_header import PageHeader
from minerva.ui.widgets.segmented_control import SegmentedControl
from minerva.ui.widgets.speed_chart import SpeedChart
from minerva.ui.widgets.surface_panel import SurfacePanel
from minerva_db import DEFAULT_INDEX_PATH, MinervaDB

log = logging.getLogger(__name__)


class DownloadPageState(enum.Enum):
    EMPTY = "empty"
    RESULTS = "results"


class _DownloadFilterProxy(QtCore.QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._text = ""
        self._status = "all"
        # Disable automatic re-sort/re-filter on source changes; we
        # apply them explicitly on the events that actually matter.
        # Runtime telemetry ticks no longer trigger a cascade re-filter
        # across all rows.
        self.setDynamicSortFilter(False)

    def setSourceModel(self, source_model):  # noqa: N802
        previous = self.sourceModel()
        if previous is not None:
            try:
                previous.modelReset.disconnect(self.invalidateFilter)
            except (TypeError, RuntimeError):
                pass
            try:
                previous.dataChanged.disconnect(self._re_sort)
            except (TypeError, RuntimeError):
                pass
        super().setSourceModel(source_model)
        if source_model is not None:
            # Structural changes: re-apply the filter so newly visible
            # rows are filtered and removed rows disappear from the view.
            source_model.modelReset.connect(self.invalidateFilter)
            # Per-cell data changes: re-apply the current sort so row
            # order stays consistent with the active sort column. The
            # filter is intentionally NOT re-applied here — runtime ticks
            # don't change filter decisions (text and status are stable
            # per record; the haystack only mutates when torrent_name
            # changes, which is rare and cheap to re-check on demand).
            source_model.dataChanged.connect(self._re_sort)

    def _re_sort(self, *_args) -> None:
        col = self.sortColumn()
        if col >= 0:
            self.sort(col, self.sortOrder())

    def set_text(self, text: str) -> None:
        self._text = text.strip().casefold()
        self.invalidateFilter()

    def set_status(self, status: str) -> None:
        self._status = status
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):  # noqa: N802
        model = self.sourceModel()
        if not isinstance(model, TorrentGroupTreeModel):
            return True

        # If parent is valid, we're checking a child row (file under a group)
        if source_parent.isValid():
            record = model.file_at(source_parent.row(), source_row)
            if record is None:
                return False
            return self._accepts_record(record)

        # Parent is invalid — this is a group row. Accept if any child
        # passes the filter.
        group = model.group_at(source_row)
        if group is None:
            return False
        for record in group.files:
            if self._accepts_record(record):
                return True
        return False

    def _accepts_record(self, record: DownloadRecord) -> bool:
        if record is None:
            return False
        status_groups = {
            "active": DOWNLOAD_ACTIVE_STATUSES - {DownloadStatus.QUEUED},
            "queued": {DownloadStatus.QUEUED},
            "completed": {DownloadStatus.COMPLETED},
            "failed": DOWNLOAD_DEAD_STATUSES,
        }
        if self._status != "all" and self._status in status_groups:
            if record.status not in status_groups[self._status]:
                return False
        if self._text and self._text not in record.haystack:
            return False
        return True


class DownloadsPage(BasePage):
    """Queue, telemetry, task inspector, throughput and recent activity."""

    def __init__(self, app_state: AppState) -> None:
        super().__init__(app_state)
        self.setObjectName("pageSurface")
        self._settings = QtCore.QSettings("MinervaFixDAT", "MinervaGUI")
        self._notifications = NotificationService(self)
        self._controller: DownloadController | None = None
        self._library_items: dict[int, object] = {}
        self._cover_provider = CoverProvider(
            self._settings.value("cover_dir", "covers", str)
        )
        # Cached index-DB connection. Reopened only if ``index_path`` changes.
        self._minerva_db: MinervaDB | None = None
        self._minerva_db_path: str | None = None
        # Tracks the queue id of the record last shown in the inspector so
        # we can skip cover-art decode on every runtime tick.
        self._inspector_record_id: str | None = None
        # Idempotency: signature of the last successful queue refresh,
        # used to short-circuit duplicate ``queue_changed`` emissions
        # (the controller fires both ``queue_changed`` and
        # ``app_state.queue_changed`` in lockstep and this page is
        # connected to both).
        self._last_refresh_signature: tuple | None = None
        # Speed-throttle: last total download speed observed on a
        # runtime tick. ``_update_dashboard`` is skipped when this is
        # unchanged, because no aggregate-bearing field can have moved.
        self._last_total_down: float | None = None
        # Tracks whether the user explicitly chose a filter tab. When
        # ``False`` the smart default is applied on first data load.
        self._torrent_engine_connected = True  # native session runs in-process
        self._resolve_controller()

        self._model = TorrentGroupTreeModel([], _DOWNLOAD_COLUMNS, parent=self)
        self._proxy = _DownloadFilterProxy(self)
        self._proxy.setSourceModel(self._model)

        self._build_ui()
        self._wire_signals()
        self._restore_layout()
        self._refresh_from_controller()

    def _build_ui(self) -> None:
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        self._header = PageHeader(
            "Downloads",
            "Manage the active queue, monitor progress, and control native torrent engine tasks.",
        )
        self._pause_all_btn = self._header.add_action("Pause all", Icons.pause())
        self._resume_all_btn = self._header.add_action("Resume all", Icons.play())
        self._pause_selected_btn = self._header.add_action("Pause selected", Icons.pause())
        # Selection-scoped actions remain available to tests, shortcuts and
        # context menus, but no longer consume permanent header space.
        self._resume_selected_btn = self._header.add_action("Resume selected", Icons.play())
        self._cancel_selected_btn = self._header.add_action(
            "Remove selected", Icons.trash(), danger=True
        )
        for button in (
            self._pause_selected_btn,
            self._resume_selected_btn,
            self._cancel_selected_btn,
        ):
            button.hide()
        root.addWidget(self._header)

        # ── KPI strip ──────────────────────────────────────────────────
        self._active_metric = MetricCard(
            "Active", "0", MetricKind.INFO,
            icon=Icons.download(), subtitle="No active transfers",
        )
        self._queued_metric = MetricCard(
            "Queued", "0", MetricKind.NEUTRAL,
            icon=Icons.queue(),
        )
        self._failed_metric = MetricCard(
            "Failed", "0", MetricKind.ERROR,
            icon=Icons.error(),
        )
        self._completed_metric = MetricCard(
            "Completed", "0", MetricKind.SUCCESS,
            icon=Icons.check(),
        )
        self._metric_strip = MetricStrip([
            self._active_metric,
            self._queued_metric,
            self._failed_metric,
            self._completed_metric,
        ])
        # Keep the metric cards as the backing presentation model, but use a
        # compact operational status strip instead of four dashboard cards.
        self._metric_strip.hide()
        self._status_strip = self._build_status_strip()
        root.addWidget(self._status_strip)

        self._empty_state = EmptyState(
            icon=Icons.download(),
            title="Your download queue is empty",
            description="Select games in Library or approve report matches to add them here.",
            action_text="Open Library",
        )
        self._empty_state.action_clicked.connect(self._open_library)
        self._content_state = ContentState()
        self._content_state.set_empty(self._empty_state)
        self._results_widget = self._build_content_widget()
        self._content_state.set_content(self._results_widget)
        root.addWidget(self._content_state, 1)

    def _build_status_strip(self) -> QtWidgets.QFrame:
        strip = QtWidgets.QFrame()
        strip.setObjectName("downloadStatusStrip")
        layout = QtWidgets.QHBoxLayout(strip)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(18)

        self._session_state = QtWidgets.QLabel("●  Session active")
        self._session_state.setObjectName("downloadSessionState")
        layout.addWidget(self._session_state)
        layout.addStretch(1)

        self._status_values: dict[str, QtWidgets.QLabel] = {}
        for key, title in (
            ("active", "Active"),
            ("queued", "Queued"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ):
            label = QtWidgets.QLabel(f"{title}  0")
            label.setObjectName("downloadStatusMetric")
            label.setProperty("kind", key)
            layout.addWidget(label)
            self._status_values[key] = label
        return strip

    def _build_content_widget(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Queue panel ─────────────────────────────────────────────────
        self._queue_panel = SurfacePanel("Download Queue")
        self._queue_panel.body_layout.setSpacing(0)


        # Toolbar: search + status tabs
        toolbar = QtWidgets.QFrame()
        toolbar.setObjectName("downloadToolbar")
        toolbar_layout = QtWidgets.QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 8, 0, 8)
        toolbar_layout.setSpacing(10)
        self._search = QtWidgets.QLineEdit()
        self._search.setObjectName("downloadSearch")
        self._search.setPlaceholderText("Search downloads\u2026")
        self._search.setClearButtonEnabled(True)
        toolbar_layout.addWidget(self._search, 1)
        self._segments = SegmentedControl([
            ("all", "All"),
            ("active", "Active"),
            ("queued", "Queued"),
            ("completed", "Completed"),
            ("failed", "Failed"),
        ])
        toolbar_layout.addWidget(self._segments)
        self._queue_panel.body_layout.addWidget(toolbar)

        # Tree view — groups by torrent
        self._view = QtWidgets.QTreeView()
        self._view.setObjectName("downloadsTable")
        self._view.setModel(self._proxy)
        self._view.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._view.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self._view.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self._view.setAlternatingRowColors(True)
        self._view.setSortingEnabled(False)  # tree sorting not supported by proxy
        self._view.setRootIsDecorated(True)
        self._view.setUniformRowHeights(True)
        self._view.setAnimated(False)
        self._view.setIndentation(16)
        self._view.setAllColumnsShowFocus(True)
        self._view.setExpandsOnDoubleClick(True)
        self._view.setItemsExpandable(True)
        self._view.collapseAll()
        header = self._view.header()
        header.setMinimumSectionSize(24)
        header.setDefaultSectionSize(34)
        header.setStretchLastSection(False)
        for col in range(len(_DOWNLOAD_COLUMNS)):
            header.setSectionResizeMode(col, QtWidgets.QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        column_widths = {
            0: 32,   # status icon
            2: 96,   # progress
            3: 86,   # speed
            4: 68,   # ETA
            5: 52,   # seeds
            6: 56,   # ratio
            7: 72,   # actions
        }
        for col, width in column_widths.items():
            header.resizeSection(col, width)

        # Delegates for icon, progress, and action columns
        self._view.setItemDelegateForColumn(0, IconDelegate(self._view))
        self._progress_delegate = DownloadProgressDelegate(parent=self._view)
        self._view.setItemDelegateForColumn(2, self._progress_delegate)
        self._action_delegate = ActionDelegate(self._view)
        self._view.setItemDelegateForColumn(7, self._action_delegate)
        self._action_delegate.action_triggered.connect(self._on_action)
        self._bottom_tabs = QtWidgets.QTabWidget()
        self._bottom_tabs.setObjectName("downloadBottomTabs")
        self._bottom_tabs.setMinimumHeight(150)
        self._bottom_tabs.setMaximumHeight(220)
        self._bottom_tabs.setVisible(False)
        self._bottom_tabs.tabBar().setObjectName("bottomTabBar")

        self._speed_chart = SpeedChart()
        self._bottom_tabs.addTab(self._speed_chart, "Throughput")

        activity_tab = QtWidgets.QWidget()
        activity_tab_layout = QtWidgets.QVBoxLayout(activity_tab)
        activity_tab_layout.setContentsMargins(0, 0, 0, 0)
        activity_tab_layout.setSpacing(0)
        activity_header_row = QtWidgets.QHBoxLayout()
        activity_title = QtWidgets.QLabel("Recent activity")
        activity_title.setObjectName("panelTitle")
        activity_header_row.addWidget(activity_title)
        activity_header_row.addStretch(1)
        self._activity_list = ActivityList(max_items=50)
        self._clear_activity_btn = QtWidgets.QPushButton("Clear")
        self._clear_activity_btn.setObjectName("linkButton")
        self._clear_activity_btn.clicked.connect(self._activity_list.clear)
        activity_header_row.addWidget(self._clear_activity_btn)
        activity_tab_layout.addLayout(activity_header_row)
        activity_tab_layout.addWidget(self._activity_list)
        self._bottom_tabs.addTab(activity_tab, "Activity")

        # Workspace: table stacked above bottom chart
        workspace = QtWidgets.QWidget()
        workspace_layout = QtWidgets.QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self._view, 1)
        workspace_layout.addWidget(self._bottom_tabs)

        # Inspector
        self._inspector = DownloadInspector()
        self._inspector.pause_requested.connect(self._pause_record)
        self._inspector.resume_requested.connect(self._resume_record)
        self._inspector.retry_requested.connect(self._retry_record)
        self._inspector.remove_requested.connect(self._remove_record)
        self._inspector.open_folder_requested.connect(self._open_folder)

        # Plain splitter: queue table (left) + inspector (right)
        self._main_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self._main_splitter.setObjectName("downloadWorkspaceSplitter")
        self._main_splitter.setChildrenCollapsible(True)
        self._main_splitter.setHandleWidth(1)
        self._main_splitter.addWidget(workspace)
        self._main_splitter.addWidget(self._inspector)
        self._main_splitter.setStretchFactor(0, 1)
        self._main_splitter.setStretchFactor(1, 0)
        self._main_splitter.setSizes([1100, 0])
        self._inspector.setVisible(False)
        self._queue_panel.body_layout.addWidget(self._main_splitter, 1)

        # Summary bar
        self._summary = QtWidgets.QFrame()
        self._summary.setObjectName("downloadSummaryBar")
        summary_layout = QtWidgets.QHBoxLayout(self._summary)
        summary_layout.setContentsMargins(0, 8, 0, 8)
        self._range_label = QtWidgets.QLabel("0 downloads")
        self._range_label.setObjectName("downloadSummaryText")
        summary_layout.addWidget(self._range_label)
        self._error_summary = QtWidgets.QLabel("")
        self._error_summary.setObjectName("downloadErrorSummary")
        self._error_summary.setVisible(False)
        summary_layout.addWidget(self._error_summary)
        summary_layout.addStretch(1)
        self._selected_label = QtWidgets.QLabel("0 selected")
        self._selected_label.setObjectName("downloadSummaryText")
        self._selected_label.hide()
        summary_layout.addWidget(self._selected_label)
        self._speed_label = QtWidgets.QLabel("0 B/s down")
        self._speed_label.setObjectName("downloadSummarySpeed")
        self._speed_label.hide()
        summary_layout.addWidget(self._speed_label)
        self._queue_panel.body_layout.addWidget(self._summary)

        layout.addWidget(self._queue_panel, 1)
        return widget

    def _wire_signals(self) -> None:
        self._search.textChanged.connect(self._proxy.set_text)
        self._segments.current_changed.connect(self._on_status_filter)
        self._pause_all_btn.clicked.connect(self._pause_all)
        self._resume_all_btn.clicked.connect(self._resume_all)
        self._pause_selected_btn.clicked.connect(self._pause_selected)
        self._resume_selected_btn.clicked.connect(self._resume_selected)
        self._cancel_selected_btn.clicked.connect(self._cancel_selected)
        self._view.selectionModel().selectionChanged.connect(
            self._on_selection_changed
        )
        self._view.setContextMenuPolicy(
            QtCore.Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._view.customContextMenuRequested.connect(self._show_context_menu)
        # Keyboard shortcuts for common queue operations
        QtGui.QShortcut(
            QtGui.QKeySequence.StandardKey.Delete, self._view,
            self._cancel_selected,
        )
        QtGui.QShortcut(
            QtGui.QKeySequence("Space"), self._view,
            self._toggle_pause_resume_selected,
        )
        self._app_state.torrent_engine_state_changed.connect(self._on_torrent_engine_state_changed)
        self._app_state.queue_changed.connect(self._refresh_from_controller)

        wm = getattr(self._app_state, "worker_manager", None)
        if wm is not None:
            wm.downloads_changed.connect(self._refresh_from_controller)

        if self._controller is not None:
            self._wire_controller_signals()


    def _wire_controller_signals(self) -> None:
        """Connect controller signals. Safe to call multiple times."""
        if self._controller is None:
            return
        self._controller.activity_event.connect(self._on_activity_event)
        self._controller.queue_changed.connect(self._refresh_from_controller)
        self._controller.runtime_changed.connect(self._on_runtime_changed)

    def _resolve_controller(self) -> None:
        shell = self.window()
        # ponytail: getattr needed here — page may not be in an AppShell yet
        # (e.g. during __init__ in unit tests). Action methods use
        # _get_controller() which retries this after embedding.
        controller = getattr(shell, "download_controller", None)
        if controller is not None:
            self._controller = controller

    def _get_controller(self) -> DownloadController | None:
        if self._controller is not None:
            # Detect dead QObject (deleted by reload_download_controller)
            if _shiboken_is_valid is not None:
                if not _shiboken_is_valid(self._controller):
                    self._controller = None
            else:
                try:
                    self._controller.queue_changed  # touch to test validity
                except RuntimeError:
                    self._controller = None
        if self._controller is None:
            self._resolve_controller()
            if self._controller is not None:
                self._wire_controller_signals()
        return self._controller

    def _refresh_from_controller(self) -> None:
        controller = self._get_controller()
        if controller is not None:
            try:
                queue = controller.get_queue()
            except Exception as exc:
                log.warning("Failed to refresh download queue", exc_info=True)
                self._notifications.error("Queue unavailable", str(exc))
                return
        else:
            wm = getattr(self._app_state, "worker_manager", None)
            if wm is not None:
                queue = wm.get_downloads()
            else:
                return

        # Dedup: the controller emits both queue_changed and
        # app_state.queue_changed in lockstep; this page is connected to
        # both. Skip if the queue contents haven't changed since the last
        # refresh.
        sig = tuple(
            (getattr(dl, "id", i), getattr(dl, "status", ""), getattr(dl, "torrent_hash", None))
            for i, dl in enumerate(queue)
        )
        if sig == self._last_refresh_signature:
            return
        self._last_refresh_signature = sig

        records = []
        for index, dl in enumerate(queue):
            # QueueRecord.status is a raw string (the enum's .value);
            # normalize to DownloadStatus so downstream code can use
            # .value, .name, and set membership uniformly.
            raw_status = dl.status
            try:
                status = DownloadStatus(raw_status)
            except (ValueError, TypeError):
                status = DownloadStatus.QUEUED
            if hasattr(dl, "filename"):
                record = DownloadRecord(
                    id=getattr(dl, "id", index + 1),
                    queue_id=getattr(dl, "queue_id", str(getattr(dl, "id", index + 1))),
                    file_id=getattr(dl, "file_id", None) or index + 1,
                    filename=dl.filename,
                    url=getattr(dl, "url", ""),
                    status=status,
                    torrent_name=getattr(dl, "torrent_name", "") or dl.filename,
                )
            else:
                record = DownloadRecord(
                    id=getattr(dl, "id", index + 1),
                    queue_id=getattr(dl, "id", str(index + 1)),
                    file_id=getattr(dl, "file_id", None) or index + 1,
                    filename=getattr(dl, "filename", ""),
                    url="",
                    status=status,
                )
            records.append(record)

        self._model.set_records(records)
        # Reset inspector cache so the next _sync_inspector_if_changed
        # actually refreshes — the selected record may have new data.
        self._inspector_record_id = None
        if records:
            self._content_state.set_content(self._results_widget)
        else:
            self._content_state.set_empty(self._empty_state)

    def _load_library_items(
        self, file_ids: list[int]
    ) -> dict[int, object]:
        if not file_ids:
            return {}
        try:
            db = self._get_minerva_db()
        except Exception:
            log.debug("Could not open index database", exc_info=True)
            return {}
        try:
            return {
                item.id: item
                for item in db.get_files_by_ids(file_ids)
            }
        except Exception:
            log.debug(
                "Could not enrich queue with library metadata", exc_info=True
            )
            return {}

    def _get_minerva_db(self) -> MinervaDB:
        """Return a cached :class:`MinervaDB`; reopen if ``index_path`` changed.

        Opening a SQLite handle on every queue refresh was the dominant
        UI-thread cost in the Downloads page's hot path. The handle is
        cheap to keep alive for the page lifetime; we only reopen when the
        configured index path changes (rare, user-driven via Settings).
        """
        settings_path = self._settings.value(
            "index_path", str(DEFAULT_INDEX_PATH), str
        )
        if self._minerva_db is None or self._minerva_db_path != settings_path:
            self._minerva_db = MinervaDB(settings_path)
            self._minerva_db_path = settings_path
        return self._minerva_db

    def _on_runtime_changed(self, runtimes: list[DownloadRuntime]) -> None:
        runtime_map = {runtime.record_id: runtime for runtime in runtimes}
        changed_tree_positions: list[tuple[int, int]] = []  # (group_row, child_row)
        for flat_idx in range(self._model.record_count()):
            record = self._model.flat_record_at(flat_idx)
            if record is None or record.queue_id not in runtime_map:
                continue
            runtime = runtime_map[record.queue_id]
            record.progress = runtime.progress
            record.speed = runtime.download_speed
            record.upload_speed = runtime.upload_speed
            record.eta_seconds = runtime.eta
            record.peers = runtime.peers
            record.seeds = runtime.seeds
            record.ratio = runtime.ratio
            record.save_path = runtime.save_path
            old_torrent_name = record.torrent_name
            record.torrent_name = runtime.torrent_name or record.torrent_name
            if record.torrent_name != old_torrent_name:
                record.haystack = " ".join(
                    (
                        record.filename,
                        record.destination,
                        record.torrent_name,
                        record.collection,
                        record.system,
                    )
                ).casefold()
            pos = self._model.flat_index_of(record.queue_id)
            if pos is not None:
                changed_tree_positions.append(pos)

        if changed_tree_positions:
            last_col = self._model.columnCount() - 1
            roles = [
                QtCore.Qt.ItemDataRole.DisplayRole,
                QtCore.Qt.ItemDataRole.UserRole,
            ]
            for group_row, child_row in changed_tree_positions:
                top_left = self._model.flat_index(group_row, child_row, 0)
                bottom_right = self._model.flat_index(group_row, child_row, last_col)
                self._model.dataChanged.emit(top_left, bottom_right, roles)

        total_down = sum(runtime.download_speed for runtime in runtimes)
        total_up = sum(runtime.upload_speed for runtime in runtimes)
        self._speed_chart.add_data_point(total_down, total_up)
        self._speed_label.setText(f"{format_speed(total_down)} down")
        # Show the bottom chart on first telemetry signal, hide when idle
        if total_down > 0 or total_up > 0:
            if not self._bottom_tabs.isVisible():
                self._bottom_tabs.setVisible(True)
        elif self._bottom_tabs.isVisible() and total_down == 0 and total_up == 0:
            self._bottom_tabs.setVisible(False)
        # Skip the aggregate dashboard work if total speed hasn't
        # changed — no aggregate-bearing field can have moved, so the
        # counts and subtitles are still valid.
        if total_down != self._last_total_down:
            self._last_total_down = total_down
            self._update_dashboard()

    def _update_dashboard(self) -> None:
        records = [
            self._model.flat_record_at(row) for row in range(self._model.record_count())
        ]
        records = [record for record in records if record is not None]
        active_states = DOWNLOAD_ACTIVE_STATUSES - {DownloadStatus.QUEUED}
        active = sum(record.status in active_states for record in records)
        completed = sum(
            record.status == DownloadStatus.COMPLETED for record in records
        )
        failed = sum(
            record.status == DownloadStatus.FAILED for record in records
        )
        queued = sum(
            record.status == DownloadStatus.QUEUED for record in records
        )
        total_down = sum(record.speed for record in records)
        cancelled = sum(
            record.status == DownloadStatus.CANCELLED for record in records
        )

        # ── KPI strip ───────────────────────────────────────────────────
        self._active_metric.set_value(str(active))
        self._active_metric.set_subtitle(
            f"{format_speed(total_down)} down"
            if active else "No active transfers"
        )
        self._queued_metric.set_value(str(queued))
        self._queued_metric.set_subtitle(
            "Waiting items" if queued else "No queued downloads"
        )
        self._failed_metric.set_value(str(failed))
        self._failed_metric.set_subtitle(
            f"{failed} failed" if failed else "No failures"
        )
        self._completed_metric.set_value(str(completed))
        self._completed_metric.set_subtitle(
            "Complete" if completed else "No completions"
        )
        self._status_values["active"].setText(f"Active  {active}")
        self._status_values["queued"].setText(f"Queued  {queued}")
        self._status_values["completed"].setText(f"Completed  {completed}")
        self._status_values["failed"].setText(f"Failed  {failed}")

        # ── Segment counts ──────────────────────────────────────────────
        counts = {
            "all": len(records),
            "active": active,
            "queued": queued,
            "completed": completed,
            "failed": failed + cancelled,
        }
        self._segments.set_counts(counts)
        self._queue_panel.set_count(self._proxy.rowCount())
        self._range_label.setText(
            f"Showing {self._proxy.rowCount()} of {len(records)} downloads"
        )
        self._selected_label.setText(
            f"{len(self._selected_queue_ids())} selected"
        )

        # ── Error summary grouping ──────────────────────────────────────
        failed_records = [r for r in records if r.status == DownloadStatus.FAILED]
        if failed_records:
            err_cats: dict[str, int] = {}
            for r in failed_records:
                err = (r.error_message or "").lower()
                if any(kw in err for kw in ("missing", "not found", "source")):
                    cat = "missing source"
                elif any(kw in err for kw in ("torrent_engine", "torrent")):
                    cat = "native torrent engine errors"
                elif any(kw in err for kw in ("destination", "path", "conflict")):
                    cat = "destination conflicts"
                else:
                    cat = "other"
                err_cats[cat] = err_cats.get(cat, 0) + 1
            parts = [f"{v} {k}" for k, v in err_cats.items()]
            self._error_summary.setText(
                f"{len(failed_records)} failed: {', '.join(parts)}"
            )
            self._error_summary.setVisible(True)
        else:
            self._error_summary.setVisible(False)

        # ── Action button states ────────────────────────────────────────
        torrent_engine_ok = self._torrent_engine_connected
        torrent_engine_tip = "" if torrent_engine_ok else "native torrent engine is disconnected"
        self._pause_all_btn.setEnabled(
            torrent_engine_ok and any(record.status in active_states for record in records)
        )
        self._pause_all_btn.setToolTip(torrent_engine_tip)
        self._resume_all_btn.setEnabled(
            torrent_engine_ok and any(
                record.status in {DownloadStatus.QUEUED, DownloadStatus.PAUSED}
                for record in records
            )
        )
        self._resume_all_btn.setToolTip(torrent_engine_tip)
        selected = self._selected_records()
        has_selected_active = any(
            r.status in active_states for r in selected
        )
        has_selected_resumable = any(
            r.status in {DownloadStatus.QUEUED, DownloadStatus.PAUSED}
            for r in selected
        )
        self._pause_selected_btn.setEnabled(torrent_engine_ok and has_selected_active)
        self._pause_selected_btn.setToolTip(torrent_engine_tip)
        self._resume_selected_btn.setEnabled(torrent_engine_ok and has_selected_resumable)
        self._resume_selected_btn.setToolTip(torrent_engine_tip)
        self._cancel_selected_btn.setEnabled(torrent_engine_ok and bool(selected))
        self._cancel_selected_btn.setToolTip(torrent_engine_tip)
        self._sync_inspector_if_changed()

    def _apply_smart_default_filter(self, records: list) -> None:
        """Switch to Active or Failed tab on first load when applicable."""
        active_states = {
            DownloadStatus.STARTING,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.PAUSED,
            DownloadStatus.SEEDING,
        }
        active = sum(r.status in active_states for r in records)
        failed = sum(r.status == DownloadStatus.FAILED for r in records)
        if active:
            target = "active"
        elif failed:
            target = "failed"
        else:
            return
        self._segments.set_current(target)
        self._proxy.set_status(target)

    def _on_selection_changed(self) -> None:
        # Selection changes only affect the inspector, the "X selected"
        # label, and batch button enable/disable — don't rebuild the
        # entire dashboard (KPIs, segment counts, error summary) on every
        # click.
        self._sync_inspector_if_changed()
        selected = self._selected_records()
        has_selection = bool(selected)
        self._inspector.setVisible(has_selection)
        if has_selection and self._main_splitter.sizes()[1] < 260:
            total = max(sum(self._main_splitter.sizes()), 900)
            self._main_splitter.setSizes([max(total - 360, 540), 360])
        self._selected_label.setText(
            f"{len(selected)} selected"
        )
        torrent_engine_ok = self._torrent_engine_connected
        torrent_engine_tip = "" if torrent_engine_ok else "native torrent engine is disconnected"
        active_states = {
            DownloadStatus.STARTING,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.SEEDING,
        }
        self._pause_selected_btn.setEnabled(
            torrent_engine_ok and any(r.status in active_states for r in selected)
        )
        self._pause_selected_btn.setToolTip(torrent_engine_tip)
        self._resume_selected_btn.setEnabled(
            torrent_engine_ok and any(
                r.status in {DownloadStatus.QUEUED, DownloadStatus.PAUSED}
                for r in selected
            )
        )
        self._resume_selected_btn.setToolTip(torrent_engine_tip)
        self._cancel_selected_btn.setEnabled(torrent_engine_ok and bool(selected))
        self._cancel_selected_btn.setToolTip(torrent_engine_tip)

    def _on_status_filter(self, key: str) -> None:
        self._user_set_filter = True
        self._proxy.set_status(key)
        self._settings.setValue("downloads/filter", key)
        self._update_dashboard()


    def _record_from_source(self, source: QtCore.QModelIndex) -> DownloadRecord | None:
        """Resolve a tree source index to a DownloadRecord."""
        if not source.isValid():
            return None
        parent = source.parent()
        if not parent.isValid():
            group = self._model.group_at(source.row())
            return group.files[0] if group and group.files else None
        return self._model.file_at(parent.row(), source.row())

    def _selected_queue_ids(self) -> list[str]:
        """Return queue_ids for all selected rows (expands groups to files)."""
        result: list[str] = []
        if not hasattr(self, "_view"):
            return result
        for proxy_index in self._view.selectionModel().selectedRows():
            source = self._proxy.mapToSource(proxy_index)
            parent = source.parent()
            if not parent.isValid():
                # Group row — collect all file IDs
                group = self._model.group_at(source.row())
                if group is not None:
                    result.extend(r.queue_id for r in group.files if r.queue_id)
            else:
                record = self._model.file_at(parent.row(), source.row())
                if record is not None and record.queue_id:
                    result.append(record.queue_id)
        return result

    def _selected_records(self) -> list[DownloadRecord]:
        """Return all selected records (expands groups to their files)."""
        result: list[DownloadRecord] = []
        if not hasattr(self, "_view"):
            return result
        for proxy_index in self._view.selectionModel().selectedRows():
            source = self._proxy.mapToSource(proxy_index)
            parent = source.parent()
            if not parent.isValid():
                group = self._model.group_at(source.row())
                if group is not None:
                    result.extend(group.files)
            else:
                record = self._model.file_at(parent.row(), source.row())
                if record is not None:
                    result.append(record)
        return result

    def _selected_record(self) -> DownloadRecord | None:
        """Return primary selected record (first file of group if group selected)."""
        selected = self._view.selectionModel().selectedRows()
        if selected:
            source = self._proxy.mapToSource(selected[0])
            parent = source.parent()
            if not parent.isValid():
                group = self._model.group_at(source.row())
                if group is not None and group.files:
                    return group.files[0]
                return None
            return self._model.file_at(parent.row(), source.row())
        active_states = {
            DownloadStatus.STARTING,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.SEEDING,
        }
        for row in range(self._model.record_count()):
            record = self._model.flat_record_at(row)
            if record is not None and record.status in active_states:
                return record
        return self._model.flat_record_at(0)

    def _sync_inspector(self) -> None:
        record = self._selected_record()
        if record is None:
            self._inspector.clear()
            return
        item = self._library_items.get(record.file_id or -1)
        cover = self._cover_provider.resolve(item) if item is not None else None
        self._inspector.set_record(record, cover)

    def _sync_inspector_if_changed(self) -> None:
        """Call :meth:`_sync_inspector` only when the selected record changed.

        ``_update_dashboard`` runs on every runtime tick. The inspector sync
        triggers a cover-art resolve which can hit the disk; we only want
        to pay that cost when the user actually selected a different row.
        """
        record = self._selected_record()
        record_id = record.queue_id if record is not None else None
        if record_id == self._inspector_record_id:
            return
        self._inspector_record_id = record_id
        self._sync_inspector()

    def _restore_selection(self, queue_ids: list[str]) -> None:
        if not queue_ids:
            if self._proxy.rowCount():
                self._view.setCurrentIndex(self._proxy.index(0, 0))
            return
        selection = self._view.selectionModel()
        for flat_idx in range(self._model.record_count()):
            record = self._model.flat_record_at(flat_idx)
            if record is None or record.queue_id not in queue_ids:
                continue
            pos = self._model.flat_index_of(record.queue_id)
            if pos is None:
                continue
            group_row, child_row = pos
            source_index = self._model.flat_index(group_row, child_row, 0)
            proxy = self._proxy.mapFromSource(source_index)
            if proxy.isValid():
                selection.select(
                    proxy,
                    QtCore.QItemSelectionModel.SelectionFlag.Select
                    | QtCore.QItemSelectionModel.SelectionFlag.Rows,
                )

    def _pause_all(self) -> None:
        controller = self._get_controller()
        if controller is not None:
            controller.pause_all()

    def _resume_all(self) -> None:
        controller = self._get_controller()
        if controller is not None:
            controller.resume_all()

    def _pause_selected(self) -> None:
        """Pause all selected downloads that are currently active."""
        active = {
            DownloadStatus.STARTING,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.SEEDING,
        }
        for record in self._selected_records():
            if record.status not in active:
                continue
            self._pause_one(record)

    def _resume_selected(self) -> None:
        """Resume all selected downloads that are paused or queued."""
        resumable = {DownloadStatus.QUEUED, DownloadStatus.PAUSED}
        for record in self._selected_records():
            if record.status not in resumable:
                continue
            self._resume_one(record)

    def _toggle_pause_resume_selected(self) -> None:
        """Space key: pause active selected, or resume paused selected."""
        selected = self._selected_records()
        if not selected:
            return
        active = {
            DownloadStatus.STARTING,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.SEEDING,
        }
        resumable = {DownloadStatus.QUEUED, DownloadStatus.PAUSED}
        has_active = any(r.status in active for r in selected)
        has_resumable = any(r.status in resumable for r in selected)
        if has_active:
            self._pause_selected()
        elif has_resumable:
            self._resume_selected()
    def _cancel_selected(self) -> None:
        ids = self._selected_queue_ids()
        if not ids:
            return
        if not self._notifications.confirm(
            "Remove selected downloads?",
            f"Remove {len(ids)} queue entries? Downloaded files will be kept.",
        ):
            return
        controller = self._get_controller()
        if controller is not None:
            controller.remove_many(ids, delete_files=False)

    def _pause_record(self, record_id: str) -> None:
        controller = self._get_controller()
        if controller is not None:
            controller.pause(record_id)

    def _resume_record(self, record_id: str) -> None:
        controller = self._get_controller()
        if controller is not None:
            controller.resume(record_id)

    def _pause_one(self, record: DownloadRecord) -> None:
        """Pause a single record via controller or WorkerManager fallback."""
        wm = getattr(self._app_state, "worker_manager", None)
        if wm is not None:
            wm.pause(record.id)
            return
        controller = self._get_controller()
        if controller is not None:
            controller.pause(record.queue_id)

    def _resume_one(self, record: DownloadRecord) -> None:
        """Resume a single record via controller or WorkerManager fallback."""
        wm = getattr(self._app_state, "worker_manager", None)
        if wm is not None:
            wm.resume(record.id)
            return
        controller = self._get_controller()
        if controller is not None:
            controller.resume(record.queue_id)

    def _retry_record(self, record_id: str) -> None:
        controller = self._get_controller()
        if controller is not None:
            controller.retry(record_id)

    def _remove_record(self, record_id: str) -> None:
        if not self._notifications.confirm(
            "Remove from queue?", "The downloaded file will be kept."
        ):
            return
        controller = self._get_controller()
        if controller is not None:
            controller.remove(record_id, delete_files=False)

    def _open_folder(self, value: str) -> None:
        if not value:
            return
        path = Path(value).expanduser()
        folder = path if path.is_dir() else path.parent
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(str(folder))
        )

    def _show_context_menu(self, position: QtCore.QPoint) -> None:
        # If right-clicked on a row not in the current selection, select it.
        clicked_index = self._view.indexAt(position)
        if clicked_index.isValid():
            source = self._proxy.mapToSource(clicked_index)
            clicked_record = self._record_from_source(source)
            clicked_id = clicked_record.queue_id if clicked_record is not None else None
            selected_ids = set(self._selected_queue_ids())
            if clicked_id and clicked_id not in selected_ids:
                self._view.setCurrentIndex(clicked_index)

        selected = self._selected_records()
        if not selected:
            return
        multi = len(selected) > 1
        menu = QtWidgets.QMenu(self)

        # Determine which batch actions are applicable
        active_states = {
            DownloadStatus.STARTING,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.SEEDING,
        }
        resumable_states = {DownloadStatus.QUEUED, DownloadStatus.PAUSED}
        has_active = any(r.status in active_states for r in selected)
        has_resumable = any(r.status in resumable_states for r in selected)
        if multi:
            if has_active:
                menu.addAction(
                    Icons.pause(),
                    f"Pause {len(selected)} selected",
                    self._pause_selected,
                )
            if has_resumable:
                menu.addAction(
                    Icons.play(),
                    f"Resume {len(selected)} selected",
                    self._resume_selected,
                )
            menu.addSeparator()
            menu.addAction(
                Icons.trash(),
                f"Remove {len(selected)} from queue",
                self._cancel_selected,
            )
        else:
            record = selected[0]
            if record.status in active_states:
                menu.addAction(
                    Icons.pause(),
                    "Pause",
                    lambda: self._pause_record(record.queue_id),
                )
            elif record.status in resumable_states:
                menu.addAction(
                    Icons.play(),
                    "Resume",
                    lambda: self._resume_record(record.queue_id),
                )
            if record.status == DownloadStatus.FAILED:
                menu.addAction(
                    Icons.retry(),
                    "Retry",
                    lambda: self._retry_record(record.queue_id),
                )
            menu.addAction(
                Icons.folder_open(),
                "Open folder",
                lambda: self._open_folder(
                    record.destination or record.save_path
                ),
            )
            menu.addSeparator()
            menu.addAction(
                Icons.trash(),
                "Remove from queue",
                lambda: self._remove_record(record.queue_id),
            )
        menu.exec(self._view.viewport().mapToGlobal(position))

    def _on_activity_event(self, category: str, message: str) -> None:
        self._activity_list.add_event(
            category, message, time.strftime("%H:%M:%S")
        )

    def _on_action(self, proxy_index: QtCore.QModelIndex, action: str) -> None:
        source_index = self._proxy.mapToSource(proxy_index)
        record = self._record_from_source(source_index)
        if record is None:
            return
        wm = getattr(self._app_state, "worker_manager", None)
        if wm is not None:
            if action == "pause":
                wm.pause(record.id)
            elif action == "resume":
                wm.resume(record.id)
            elif action == "remove":
                wm.remove(record.id)
            return
        controller = self._get_controller()
        if controller is not None:
            if action == "pause":
                controller.pause(record.queue_id)
            elif action == "resume":
                controller.resume(record.queue_id)
            elif action == "remove":
                controller.remove(record.queue_id, delete_files=False)

    def _on_torrent_engine_state_changed(self, connected: object) -> None:
        """Native session is always connected — this is a no-op."""
        self._torrent_engine_connected = True

    def _open_library(self) -> None:
        shell = self.window()
        if hasattr(shell, "show_page"):
            shell.show_page(PageId.LIBRARY)

    def _open_settings(self) -> None:
        shell = self.window()
        if hasattr(shell, "show_page"):
            shell.show_page(PageId.SETTINGS)

    def _restore_layout(self) -> None:
        filter_key = self._settings.value("downloads/filter", None, str)
        if filter_key is not None:
            self._segments.set_current(filter_key)
            self._proxy.set_status(filter_key)
            self._user_set_filter = True
        if hasattr(self, "_main_splitter"):
            value = self._settings.value("downloads/workspace_splitter")
            if isinstance(value, list) and value and self._inspector.isVisible():
                self._main_splitter.setSizes([int(part) for part in value])
        tab_index = self._settings.value("downloads/bottom_tab", 0, int)
        if hasattr(self, "_bottom_tabs"):
            self._bottom_tabs.setCurrentIndex(tab_index)
        header_state = self._settings.value("downloads/header_state")
        if header_state and hasattr(self, "_view"):
            self._view.header().restoreState(header_state)

    def _save_layout(self) -> None:
        mw = getattr(self, "_main_splitter", None)
        self._settings.setValue(
            "downloads/workspace_splitter",
            mw.sizes() if mw else [],
        )
        self._settings.setValue(
            "downloads/bottom_tab", self._bottom_tabs.currentIndex()
        )
        self._settings.setValue(
            "downloads/header_state",
            self._view.header().saveState(),
        )

    def activate(self) -> None:
        self._refresh_from_controller()

    def deactivate(self) -> None:
        self._save_layout()

    def prepare_close(self) -> None:
        self._save_layout()
