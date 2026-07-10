# Batch Operations with FixDAT Reports — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add multi-select batch operations (queue/rematch/export/remove) on a subset of fixdat reports, recursive folder import, and headless CLI batch commands — reusing existing single-report service methods in loops.

**Architecture:** UI multi-select is the foundation: `ReportNavigator` switches to `ExtendedSelection` with a split selection model — `currentChanged` drives the focused-report review flow (entry table + MatchDetailPanel, unchanged), `selectionChanged` drives a new `selection_changed` signal for batch toolbar enablement and actions. Folder import and CLI batch commands both reuse `ReportAcquisitionService.import_report` + `match_report` in loops, constructed without a download controller (CLI) or with one (UI). One new domain helper (`QueueResult.merge`) and one new service method (`export_reviewed`) are extracted; everything else loops existing methods.

**Tech Stack:** Python 3.14, PyQt6, pytest + pytest-qt, SQLite + FTS5, argparse.

**Design doc:** `minerva/docs/plans/2026-06-28-batch-operations-design.md`

---

## Key file locations (verified)

| Surface | Path | Notes |
|---|---|---|
| Domain `QueueResult` | `minerva/domain/reports.py:71-76` | Frozen dataclass — add `merge` classmethod |
| Service `import_report` | `minerva/services/report_acquisition.py:1041` | Does NOT match — must pair with `match_report` |
| Service `match_report` | `minerva/services/report_acquisition.py:1193` | Classifies entries |
| Service `queue_ready` | `minerva/services/report_acquisition.py:1370` | Resolves `output_dir` from `self._output_dir` or QSettings fallback |
| Service `rematch_report` | `minerva/services/report_acquisition.py:1589` | Single report_id |
| Service `queue_all_ready` | `minerva/services/report_acquisition.py:1600` | Has manual aggregation to refactor to `merge` |
| Service `__init__` | `minerva/services/report_acquisition.py:882` | `download_controller: ... \| None = None` |
| Service `_enqueue_file` | `minerva/services/report_acquisition.py:1314` | Falls back to direct DB write when no controller |
| UI `_export_reviewed` | `minerva/app/pages/reports.py:1053` | Currently in page, uses `MinervaDB.build_synthetic_dat` |
| UI `_import_file` | `minerva/app/pages/reports.py:664` | UI-only path, re-implements parsing |
| UI `_queue_report` | `minerva/app/pages/reports.py:1006` | Builds service inline |
| UI `_delete_selected` | `minerva/app/pages/reports.py:1092` | Confirm + `delete_report` |
| UI `_show_report_menu` | `minerva/app/pages/reports.py:1109` | Single-report context menu |
| UI header | `minerva/app/pages/reports.py:248-262` | "Queue all ready" + per-report buttons |
| Navigator | `minerva/ui/widgets/report_navigator.py:209-409` | `SingleSelection` at :301, `selectionChanged→current_report_changed` at :308 |
| `TaskRunner` | `minerva/app/task_runner.py` | `wrap_result(fn, *args)` + `signals.progress(int,int)` |
| CLI | `minerva_cli.py:99-133` | Subparser table at :121 |
| Icons | `minerva/ui/icons.py:202-216` | `add`, `refresh`, `trash`, `file`, `download`, `folder_open` available |
| Test pattern | `tests/test_report_acquisition_service.py:1-108` | In-memory SQLite index + fakes |

**Service construction pattern (from `_queue_report:1021`):**
```python
svc = ReportAcquisitionService(
    state=self._app_state.reports._state,
    download_controller=controller,
    output_dir=output_dir,
)
```
CLI uses the same with `download_controller=None` and explicit `output_dir`.

---

## Phase 1 — Domain: `QueueResult.merge`

### Task 1.1: Failing test for `QueueResult.merge`

**Files:**
- Test: `tests/test_queue_result_merge.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_queue_result_merge.py -v`
Expected: FAIL with `AttributeError: type object 'QueueResult' has no attribute 'merge'`

### Task 1.2: Implement `merge`

**Files:**
- Modify: `minerva/domain/reports.py:71-76`

**Step 1: Add the `merge` classmethod**

Current (lines 71-76):
```python
@dataclass(frozen=True)
class QueueResult:
    added: int            # records added to the download queue
    skipped_active: int   # already queued/downloading
    skipped_complete: int # already completed
    skipped_missing: int  # not_found entries skipped
```

Add after the field declarations, before any other methods:
```python
    @classmethod
    def merge(cls, *results: "QueueResult") -> "QueueResult":
        """Sum field-wise across multiple results. Empty call → zero result."""
        return cls(
            added=sum(r.added for r in results),
            skipped_active=sum(r.skipped_active for r in results),
            skipped_complete=sum(r.skipped_complete for r in results),
            skipped_missing=sum(r.skipped_missing for r in results),
        )
```

**Step 2: Run test to verify it passes**

Run: `python -m pytest tests/test_queue_result_merge.py -v`
Expected: PASS (3 tests)

### Task 1.3: Refactor `queue_all_ready` to use `merge`

**Files:**
- Modify: `minerva/services/report_acquisition.py:1614-1625`

**Step 1: Replace the manual aggregation loop**

Current (lines 1614-1625):
```python
        total = QueueResult(
            added=0, skipped_active=0, skipped_complete=0, skipped_missing=0,
        )
        for r in reports:
            sub = self.queue_ready(r.id, include_reviewed=include_reviewed)
            total = QueueResult(
                added=total.added + sub.added,
                skipped_active=total.skipped_active + sub.skipped_active,
                skipped_complete=total.skipped_complete + sub.skipped_complete,
                skipped_missing=total.skipped_missing + sub.skipped_missing,
            )
        return total
```

Replace with:
```python
        sub_results = [
            self.queue_ready(r.id, include_reviewed=include_reviewed)
            for r in reports
        ]
        return QueueResult.merge(*sub_results)
```

**Step 2: Run existing service tests to verify no regression**

Run: `python -m pytest tests/test_report_acquisition_service.py -v -k queue`
Expected: PASS — no behavior change, only refactor.

### Task 1.4: Commit Phase 1

```bash
git add minerva/domain/reports.py minerva/services/report_acquisition.py tests/test_queue_result_merge.py
git commit -m "feat(reports): add QueueResult.merge aggregator

Extracted from queue_all_ready's manual aggregation loop so UI and CLI
batch operations can sum QueueResults uniformly."
```

---

## Phase 2 — Service: `export_reviewed` method

### Task 2.1: Failing test for `export_reviewed`

**Files:**
- Test: `tests/test_report_acquisition_service.py` (append) — or new `tests/test_export_reviewed.py`

**Step 1: Write the failing test**

Create `tests/test_export_reviewed.py`:
```python
"""Tests for ReportAcquisitionService.export_reviewed."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from minerva.domain.reports import ReportScope, ReviewEntry
from minerva.services.report_acquisition import ReportAcquisitionService
from minerva_state import MinervaState


@pytest.fixture()
def state_db(tmp_path):
    state = MinervaState(path=str(tmp_path / "state.db"))
    yield state


def _seed_report_with_decisions(svc: ReportAcquisitionService, state: MinervaState) -> str:
    """Import a minimal report and set some decisions. Returns report_id."""
    report_id = "test-export-report"
    from minerva.domain.reports import ReportSummary
    from datetime import datetime, timezone
    report = ReportSummary(
        id=report_id, path="test.dat", name="Test",
        collection="Nintendo", system="Nintendo - Game Boy Color",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=3, status="reviewed",
    )
    state.save_report(report)
    entries = [
        ReviewEntry(id=f"{report_id}_0", report_id=report_id, ordinal=0,
                    filename="game-a.zip", size=1024,
                    decision="accept", selected_file_id=1),
        ReviewEntry(id=f"{report_id}_1", report_id=report_id, ordinal=1,
                    filename="game-b.zip", size=2048,
                    decision="fuzzy", automatic_file_id=2),
        ReviewEntry(id=f"{report_id}_2", report_id=report_id, ordinal=2,
                    filename="game-c.zip", size=4096,
                    decision="reject"),
    ]
    state.replace_entries(report_id, entries)
    return report_id


def test_export_reviewed_writes_dat_with_approved_entries(state_db, tmp_path, monkeypatch):
    svc = ReportAcquisitionService(state=state_db)
    report_id = _seed_report_with_decisions(svc, state_db)
    dest = tmp_path / "out.dat"

    # Stub MinervaDB.get_files_by_ids to avoid needing the real index
    from minerva_db import MinervaDB
    class _FakeFile:
        def __init__(self, fid, stem, basename, size):
            self.id = fid; self.stem = stem; self.basename = basename; self.size = size
    class _FakeDB:
        def get_files_by_ids(self, ids):
            table = {1: _FakeFile(1,"game-a","game-a.zip",1024),
                     2: _FakeFile(2,"game-b","game-b.zip",2048)}
            return [table[i] for i in ids if i in table]
        def build_synthetic_dat(self, rows, name, system, collection):
            return f"<dat name={name!r} system={system!r} collection={collection!r} rows={len(rows)}/>"
    monkeypatch.setattr("minerva.services.report_acquisition.MinervaDB", lambda: _FakeDB())

    count = svc.export_reviewed(report_id, dest)
    assert count == 2  # accept + fuzzy, not reject
    assert dest.exists()
    content = dest.read_text(encoding="utf-8")
    assert "game-a" in content
    assert "game-b" in content


def test_export_reviewed_zero_when_nothing_approved(state_db, tmp_path, monkeypatch):
    svc = ReportAcquisitionService(state=state_db)
    from minerva.domain.reports import ReportSummary
    from datetime import datetime, timezone
    report_id = "no-approved"
    state_db.save_report(ReportSummary(
        id=report_id, path="x.dat", name="X", collection="", system="",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=1, status="reviewed"))
    state_db.replace_entries(report_id, [
        ReviewEntry(id=f"{report_id}_0", report_id=report_id, ordinal=0,
                    filename="g.zip", size=10, decision="reject")])
    dest = tmp_path / "out.dat"
    count = svc.export_reviewed(report_id, dest)
    assert count == 0
    assert not dest.exists()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_export_reviewed.py -v`
Expected: FAIL with `AttributeError: 'ReportAcquisitionService' object has no attribute 'export_reviewed'`

### Task 2.2: Implement `export_reviewed`

**Files:**
- Modify: `minerva/services/report_acquisition.py` (add method near `rematch_report`, ~line 1589)

**Step 1: Add the method**

Insert before `def queue_all_ready` (line 1600):
```python
    def export_reviewed(self, report_id: str, dest_path: Path) -> int:
        """Write a synthetic DAT of approved entries to *dest_path*.

        Approved = ``decision in {"accept", "fuzzy"}`` and has a
        ``selected_file_id`` or ``automatic_file_id``. Returns the count
        written; writes nothing and returns 0 if no approved entries.

        Wraps ``MinervaDB.build_synthetic_dat`` — same policy as the
        former ``ReportsPage._export_reviewed`` so UI and CLI share one
        definition of "approved".
        """
        entries = self._state.get_entries(report_id)
        ids = [
            entry.selected_file_id or entry.automatic_file_id
            for entry in entries
            if entry.decision in {"accept", "fuzzy"}
            and (entry.selected_file_id or entry.automatic_file_id)
        ]
        if not ids:
            return 0
        db = self._db or MinervaDB()
        items = db.get_files_by_ids(ids)
        if not items:
            return 0
        rows = [
            {"stem": item.stem, "basename": item.basename, "size": item.size}
            for item in items
        ]
        report = self._state.get_report(report_id)
        name = report.name if report else "reviewed"
        system = report.system if report else ""
        collection = report.collection if report else ""
        dest_path.write_text(
            db.build_synthetic_dat(rows, f"{name} reviewed", system, collection),
            encoding="utf-8",
        )
        return len(items)
```

**Step 2: Run test to verify it passes**

Run: `python -m pytest tests/test_export_reviewed.py -v`
Expected: PASS (2 tests)

### Task 2.3: Refactor `ReportsPage._export_reviewed` to call the service

**Files:**
- Modify: `minerva/app/pages/reports.py:1053-1085`

**Step 1: Replace the method body**

Current (lines 1053-1085) re-implements the policy inline. Replace with:
```python
    def _export_reviewed(self) -> None:
        report = self._selected_report()
        if report is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export reviewed DAT", f"{report.name}-reviewed.dat", "DAT files (*.dat)",
        )
        if not path:
            return
        try:
            svc = self._build_acquisition_service()
            count = svc.export_reviewed(report.id, Path(path))
            if count == 0:
                NotificationBanner.show_warning(self, "Nothing approved", "Review and approve matches first")
            else:
                NotificationBanner.show_success(self, "DAT exported", f"{path} ({count} entries)")
        except Exception as exc:
            log.error("_export_reviewed failed", exc_info=True)
            NotificationBanner.show_error(self, "Export failed", str(exc))
```

**Note:** `_build_acquisition_service` is added in Task 3.4. For this task, temporarily keep a local service construction inline to keep tests green, then consolidate in 3.4.

**Step 2: Run the UI export tests (if any) and smoke**

Run: `python -m pytest tests/ -v -k export`
Expected: PASS — behavior identical.

### Task 2.4: Commit Phase 2

```bash
git add minerva/services/report_acquisition.py minerva/app/pages/reports.py tests/test_export_reviewed.py
git commit -m "feat(reports): extract export_reviewed into service

Move the 'which entries count as approved' policy out of ReportsPage
into ReportAcquisitionService.export_reviewed so UI and CLI share one
definition. UI method becomes a thin caller."
```

---

## Phase 3 — UI: ReportNavigator multi-select (foundation)

### Task 3.1: Failing test for navigator multi-select signal

**Files:**
- Test: `tests/test_report_navigator_multiselect.py`

**Step 1: Write the failing test**

```python
"""Tests for ReportNavigator ExtendedSelection + selection_changed signal."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PyQt6 import QtCore, QtWidgets

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from minerva.domain.reports import ReportSummary
from minerva.ui.widgets.report_navigator import ReportNavigator


def _make_report(rid: str, name: str = "R") -> ReportSummary:
    from datetime import datetime, timezone
    return ReportSummary(
        id=rid, path=f"{rid}.dat", name=name,
        collection="Nintendo", system="Nintendo - Game Boy Color",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=5, status="reviewed",
    )


@pytest.fixture()
def nav(qtbot):
    navigator = ReportNavigator()
    qtbot.addWidget(navigator)
    return navigator


def test_selection_mode_is_extended(nav):
    assert nav.view.selectionMode() == QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection


def test_current_report_changed_fires_on_focus_not_on_ctrl_click(nav, qtbot):
    nav.set_reports([_make_report("a"), _make_report("b"), _make_report("c")])
    received_focus = []
    nav.current_report_changed.connect(lambda r: received_focus.append(r.id))

    # Click first — focus + selection
    nav.view.setCurrentIndex(nav.proxy.index(0, 0))
    assert received_focus == ["a"]

    # Ctrl-click third — adds to selection, focus moves
    received_focus.clear()
    nav.view.selectionModel().select(
        nav.proxy.index(2, 0),
        QtCore.QItemSelectionModel.SelectionFlag.Select
        | QtCore.QItemSelectionModel.SelectionFlag.Current,
    )
    # current_report_changed should fire because focus moved (currentChanged),
    # not because selection changed.
    assert received_focus == ["c"]


def test_selection_changed_emits_all_selected(nav, qtbot):
    nav.set_reports([_make_report("a"), _make_report("b"), _make_report("c")])
    received: list[list[str]] = []
    nav.selection_changed.connect(lambda reports: received.append([r.id for r in reports]))

    # Select a + c
    sm = nav.view.selectionModel()
    sm.select(nav.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    sm.select(nav.proxy.index(2, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)

    assert received[-1] == ["a", "c"]


def test_selected_reports_returns_summary_list(nav, qtbot):
    nav.set_reports([_make_report("a"), _make_report("b")])
    sm = nav.view.selectionModel()
    sm.select(nav.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    sm.select(nav.proxy.index(1, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    selected = nav.selected_reports()
    assert {r.id for r in selected} == {"a", "b"}


def test_focus_preserved_when_selection_grows(nav, qtbot):
    nav.set_reports([_make_report("a"), _make_report("b"), _make_report("c")])
    nav.view.setCurrentIndex(nav.proxy.index(0, 0))  # focus a
    # Add c to selection without changing focus
    nav.view.selectionModel().select(
        nav.proxy.index(2, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    assert nav.current_report() is not None
    assert nav.current_report().id == "a"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_report_navigator_multiselect.py -v`
Expected: FAIL — `SingleSelection` assert fails, no `selection_changed` signal, no `selected_reports` method.

### Task 3.2: Switch to ExtendedSelection and split signals

**Files:**
- Modify: `minerva/ui/widgets/report_navigator.py:212-214` (signals), `:301` (mode), `:308` (wiring), `:382-396` (current + selection methods)

**Step 1: Add `selection_changed` signal (line 212-214)**

Current:
```python
    current_report_changed = QtCore.pyqtSignal(object)
    report_activated = QtCore.pyqtSignal(object)
    context_menu_requested = QtCore.pyqtSignal(object, QtCore.QPoint)
```
Add after:
```python
    selection_changed = QtCore.pyqtSignal(list)  # list[ReportSummary]
```

**Step 2: Switch selection mode (line 301)**

Current:
```python
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.SingleSelection)
```
Replace:
```python
        self.view.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
```

**Step 3: Split signal wiring (line 308-310)**

Current:
```python
        self.view.selectionModel().selectionChanged.connect(self._on_selection_changed)
        self.view.doubleClicked.connect(self._on_activated)
        self.view.customContextMenuRequested.connect(self._on_context_menu)
```
Replace:
```python
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)
        self.view.selectionModel().selectionChanged.connect(self._on_selection_set_changed)
        self.view.doubleClicked.connect(self._on_activated)
        self.view.customContextMenuRequested.connect(self._on_context_menu)
```

**Step 4: Replace `_on_selection_changed` and add `selected_reports` (lines 382-396)**

Current:
```python
    def current_report(self) -> ReportSummary | None:
        proxy_index = self.view.currentIndex()
        if not proxy_index.isValid():
            return None
        source = self.proxy.mapToSource(proxy_index)
        return self.model.report_at(source.row())

    def _on_selection_changed(self) -> None:
        report = self.current_report()
        if report is not None:
            self.current_report_changed.emit(report)
```
Replace with:
```python
    def current_report(self) -> ReportSummary | None:
        proxy_index = self.view.currentIndex()
        if not proxy_index.isValid():
            return None
        source = self.proxy.mapToSource(proxy_index)
        return self.model.report_at(source.row())

    def selected_reports(self) -> list[ReportSummary]:
        """All reports under the current multi-selection (filtered by proxy)."""
        reports: list[ReportSummary] = []
        for proxy_index in self.view.selectionModel().selectedIndexes():
            source = self.proxy.mapToSource(proxy_index)
            report = self.model.report_at(source.row())
            if report is not None and report not in reports:
                reports.append(report)
        return reports

    def _on_current_changed(self, current: QtCore.QModelIndex, _previous: QtCore.QModelIndex) -> None:
        """Focus moved — drives the entry table + MatchDetailPanel review flow."""
        source = self.proxy.mapToSource(current) if current.isValid() else None
        report = self.model.report_at(source.row()) if source and source.isValid() else None
        self.current_report_changed.emit(report)

    def _on_selection_set_changed(self, _selected, _deselected) -> None:
        """Multi-selection set changed — drives the batch toolbar."""
        self.selection_changed.emit(self.selected_reports())
```

**Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_report_navigator_multiselect.py -v`
Expected: PASS (5 tests)

### Task 3.3: Make `_show_report_menu` preserve multi-selection

**Files:**
- Modify: `minerva/app/pages/reports.py:1109-1113`

**Context:** `setCurrentIndex` in ExtendedSelection can collapse the selection set. The right-clicked report should become the *current/focus* index without clearing the existing selection, and the multi-selection should include the right-clicked report.

**Step 1: Replace the index-setting block**

Current (lines 1110-1113):
```python
        row = self._navigator.model.index_of(report.id)
        if row >= 0:
            self._navigator.view.setCurrentIndex(self._navigator.model.index(row, 0))
        self._selected_report_id = report.id
```
Replace:
```python
        row = self._navigator.model.index_of(report.id)
        if row >= 0:
            proxy_row = self._navigator._find_proxy_row(report.id)
            proxy_idx = self._navigator.proxy.index(proxy_row, 0)
            # Ensure the right-clicked report is in the selection without
            # collapsing an existing multi-selection. In ExtendedSelection,
            # setCurrentIndex alone does not alter the selection set.
            self._navigator.view.setCurrentIndex(proxy_idx)
            sm = self._navigator.view.selectionModel()
            if not sm.isSelected(proxy_idx):
                sm.select(proxy_idx, QtCore.QItemSelectionModel.SelectionFlag.Select)
        self._selected_report_id = report.id
```

**Note:** `_find_proxy_row` is already public-ish (line 372). If it's private, access it via the navigator — keep the access narrow.

**Step 2: Run any existing reports-page tests + the new multiselect tests**

Run: `python -m pytest tests/test_report_navigator_multiselect.py tests/ -v -k "report or navigator"`
Expected: PASS.

### Task 3.4: Add `_build_acquisition_service` helper + batch toolbar state

**Files:**
- Modify: `minerva/app/pages/reports.py:239` (state), `:248-262` (header), `:1006-1025` (extract helper)

**Step 1: Add multi-selection state (after line 239)**

After `self._selected_report_id: str | None = None` add:
```python
        self._selected_report_ids: set[str] = set()  # multi-selection → batch toolbar
```

**Step 2: Extract `_build_acquisition_service` from `_queue_report`**

Add a new method (near the other private helpers, e.g. after `_show_report`):
```python
    def _build_acquisition_service(self) -> ReportAcquisitionService:
        """Construct the service with the current controller + output dir."""
        shell = self.window()
        controller = getattr(shell, "download_controller", None)
        output_dir = QtCore.QSettings("MinervaFixDAT", "MinervaGUI").value(
            "output_dir", "downloads", str,
        )
        return ReportAcquisitionService(
            state=self._app_state.reports._state,
            download_controller=controller,
            output_dir=output_dir,
        )
```

Then simplify `_queue_report` (lines 1006-1025) to use it:
```python
    def _queue_report(self, report_id: str) -> None:
        """Queue ready and approved entries for a single report."""
        try:
            controller = getattr(self.window(), "download_controller", None)
            if controller is None:
                NotificationBanner.show_error(
                    self, "Queue failed", "The external torrent client controller is not initialised",
                )
                return
            controller.reconcile()
            svc = self._build_acquisition_service()
            result = svc.queue_ready(report_id, include_reviewed=True)
            log.info(
                "queue_ready(%s): added=%d, skipped_active=%d, "
                "skipped_complete=%d, skipped_missing=%d",
                report_id[:8], result.added, result.skipped_active,
                result.skipped_complete, result.skipped_missing,
            )
            if result.added == 0:
                NotificationBanner.show_warning(
                    self, "Nothing queued",
                    f"All {result.skipped_active} already active, "
                    f"{result.skipped_complete} already complete, "
                    f"{result.skipped_missing} missing.",
                )
            else:
                NotificationBanner.show_success(
                    self, "Queue updated",
                    f"Queued {result.added} ROMs "
                    f"({result.skipped_active} already active, "
                    f"{result.skipped_missing} missing).",
                )
        except Exception as exc:
            log.error("_queue_report failed", exc_info=True)
            NotificationBanner.show_error(self, "Queue failed", str(exc))
```

**Step 3: Run existing tests to verify no regression**

Run: `python -m pytest tests/ -v -k "queue or report"`
Expected: PASS.

### Task 3.5: Commit Phase 3 (navigator foundation)

```bash
git add minerva/ui/widgets/report_navigator.py minerva/app/pages/reports.py tests/test_report_navigator_multiselect.py
git commit -m "feat(reports): switch navigator to ExtendedSelection

Split selection into focus (currentChanged → current_report_changed,
drives entry table + MatchDetailPanel) and set (selectionChanged → new
selection_changed, drives batch toolbar). Adds selected_reports().
_show_report_menu preserves existing multi-selection on right-click.

No behavior change for single-select flows."
```

---

## Phase 4 — UI: Batch toolbar actions

### Task 4.1: Failing test for batch toolbar wiring

**Files:**
- Test: `tests/test_reports_page_batch.py`

**Step 1: Write the failing test**

```python
"""Tests for ReportsPage batch toolbar actions over a multi-selection."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore, QtWidgets

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from minerva.app.app_state import AppState
from minerva.app.pages.reports import ReportsPage
from minerva.domain.reports import ReportSummary, QueueResult


def _make_report(rid: str) -> ReportSummary:
    from datetime import datetime, timezone
    return ReportSummary(
        id=rid, path=f"{rid}.dat", name=f"Report-{rid}",
        collection="Nintendo", system="Nintendo - Game Boy Color",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=3, status="reviewed",
    )


@pytest.fixture()
def page(qtbot, monkeypatch):
    # Stub ReportAcquisitionService so no real DB needed
    fake_svc = MagicMock()
    fake_svc.queue_ready.return_value = QueueResult(added=1, skipped_active=0, skipped_complete=0, skipped_missing=0)
    monkeypatch.setattr(
        "minerva.app.pages.reports.ReportAcquisitionService",
        lambda **kw: fake_svc,
    )
    monkeypatch.setattr(
        "minerva.app.pages.reports.MinervaDB", MagicMock(),
    )
    app_state = AppState()
    p = ReportsPage(app_state)
    qtbot.addWidget(p)
    return p, fake_svc


def test_batch_buttons_disabled_with_no_selection(page):
    p, _ = page
    assert not p._queue_selected_btn.isEnabled()
    assert not p._rematch_selected_btn.isEnabled()
    assert not p._export_selected_btn.isEnabled()
    assert not p._delete_selected_btn.isEnabled()


def test_batch_buttons_enabled_when_selection_nonempty(page, qtbot):
    p, _ = page
    p._navigator.set_reports([_make_report("a"), _make_report("b")])
    sm = p._navigator.view.selectionModel()
    sm.select(p._navigator.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    qtbot.waitUntil(lambda: p._queue_selected_btn.isEnabled())
    assert p._queue_selected_btn.isEnabled()


def test_queue_selected_calls_service_per_report(page, qtbot):
    p, fake_svc = page
    p._navigator.set_reports([_make_report("a"), _make_report("b"), _make_report("c")])
    sm = p._navigator.view.selectionModel()
    sm.select(p._navigator.proxy.index(0, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    sm.select(p._navigator.proxy.index(2, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)

    # Stub the controller so _build_acquisition_service path works
    p.window().download_controller = MagicMock()

    p._queue_selected()

    called_ids = [call.kwargs.get("report_id") or call.args[0]
                  for call in fake_svc.queue_ready.call_args_list]
    assert set(called_ids) == {"a", "c"}
    assert fake_svc.queue_ready.call_count == 2
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_reports_page_batch.py -v`
Expected: FAIL — no `_queue_selected_btn`, `_rematch_selected_btn`, etc.

### Task 4.2: Add batch toolbar buttons

**Files:**
- Modify: `minerva/app/pages/reports.py:252-262` (header)

**Step 1: Add the four batch buttons after the existing per-report buttons**

After `self._delete_btn` line, before the `setEnabled(False)` lines:
```python
        self._queue_all_btn = self._header.add_action("Queue all ready", Icons.download())
        self._delete_btn = self._header.add_action("Remove", Icons.trash(), danger=True)
        # ── Batch actions (multi-select) ───────────────────────────────
        self._queue_selected_btn = self._header.add_action("Queue selected", Icons.download())
        self._rematch_selected_btn = self._header.add_action("Match selected", Icons.refresh())
        self._export_selected_btn = self._header.add_action("Export selected", Icons.file())
        self._delete_selected_btn = self._header.add_action("Remove selected", Icons.trash(), danger=True)
        self._rematch_btn.setEnabled(False)
        self._delete_btn.setEnabled(False)
        self._queue_all_btn.setEnabled(False)
        # Batch buttons start disabled — enabled by selection_changed
        for btn in (self._queue_selected_btn, self._rematch_selected_btn,
                    self._export_selected_btn, self._delete_selected_btn):
            btn.setEnabled(False)
        self._import_btn.clicked.connect(self._on_import)
        self._rematch_btn.clicked.connect(self._rematch_selected)
        self._queue_all_btn.clicked.connect(self._queue_all_ready)
        self._delete_btn.clicked.connect(self._delete_selected)
        self._queue_selected_btn.clicked.connect(self._queue_selected)
        self._rematch_selected_btn.clicked.connect(self._rematch_selected_set)
        self._export_selected_btn.clicked.connect(self._export_selected_set)
        self._delete_selected_btn.clicked.connect(self._delete_selected_set)
```

**Step 2: Wire `selection_changed` to enable/disable the buttons**

In the navigator-wiring block (after line 290):
```python
        self._navigator.selection_changed.connect(self._on_report_selection_changed)
```

Add the handler near `_on_report_selected`:
```python
    def _on_report_selection_changed(self, reports: list[ReportSummary]) -> None:
        self._selected_report_ids = {r.id for r in reports}
        has_selection = bool(self._selected_report_ids)
        for btn in (self._queue_selected_btn, self._rematch_selected_btn,
                    self._export_selected_btn, self._delete_selected_btn):
            btn.setEnabled(has_selection)
```

**Step 3: Run test to verify it passes (buttons exist + enable)**

Run: `python -m pytest tests/test_reports_page_batch.py -v -k "disabled or enabled"`
Expected: PASS for those two.

### Task 4.3: Implement batch action methods

**Files:**
- Modify: `minerva/app/pages/reports.py` (add four methods near `_queue_report`)

**Step 1: Add `_queue_selected`, `_rematch_selected_set`, `_export_selected_set`, `_delete_selected_set`**

```python
    def _queue_selected(self) -> None:
        """Queue ready+approved entries across all selected reports."""
        ids = sorted(self._selected_report_ids)
        if not ids:
            return
        try:
            controller = getattr(self.window(), "download_controller", None)
            if controller is None:
                NotificationBanner.show_error(
                    self, "Queue failed", "The external torrent client controller is not initialised",
                )
                return
            controller.reconcile()
            svc = self._build_acquisition_service()
            results = [svc.queue_ready(rid, include_reviewed=True) for rid in ids]
            total = QueueResult.merge(*results)
            log.info("batch queue (%d reports): added=%d, skipped_active=%d, "
                     "skipped_complete=%d, skipped_missing=%d",
                     len(ids), total.added, total.skipped_active,
                     total.skipped_complete, total.skipped_missing)
            if total.added == 0:
                NotificationBanner.show_warning(
                    self, "Nothing queued",
                    f"Across {len(ids)} reports: {total.skipped_active} active, "
                    f"{total.skipped_complete} complete, {total.skipped_missing} missing.",
                )
            else:
                NotificationBanner.show_success(
                    self, "Queue updated",
                    f"Queued {total.added} ROMs from {len(ids)} reports "
                    f"({total.skipped_active} active, {total.skipped_missing} missing).",
                )
        except Exception as exc:
            log.error("_queue_selected failed", exc_info=True)
            NotificationBanner.show_error(self, "Queue failed", str(exc))

    def _rematch_selected_set(self) -> None:
        """Re-run matching across all selected reports (background)."""
        ids = sorted(self._selected_report_ids)
        if not ids:
            return
        self._set_state(ReportsPageState.LOADING)
        self._generation += 1
        gen = self._generation
        task = TaskRunner.wrap_result(self._rematch_set_worker, ids)
        task.signals.result.connect(lambda payload: self._on_rematch_result(gen, "", payload))
        task.signals.error.connect(lambda details: self._on_match_error(gen, "", details))
        self._pool.start(task)

    def _rematch_set_worker(self, ids: list[str]) -> dict:
        """Runs in worker thread. Best-effort per report."""
        svc = ReportAcquisitionService(state=self._app_state.reports._state)
        succeeded = []
        failed = []
        for rid in ids:
            try:
                svc.rematch_report(rid)
                succeeded.append(rid)
            except Exception as exc:
                log.warning("batch rematch failed for %s: %s", rid[:8], exc)
                failed.append(rid)
        return {"succeeded": succeeded, "failed": failed}

    def _export_selected_set(self) -> None:
        """Export a reviewed DAT per selected report into a chosen folder."""
        ids = sorted(self._selected_report_ids)
        if not ids:
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Export reviewed DATs to folder")
        if not folder:
            return
        try:
            svc = self._build_acquisition_service()
            written = 0
            for rid in ids:
                report = self._app_state.reports.get_report(rid)
                if report is None:
                    continue
                dest = Path(folder) / f"{report.name}-reviewed.dat"
                try:
                    count = svc.export_reviewed(rid, dest)
                    if count:
                        written += 1
                except Exception as exc:
                    log.warning("batch export failed for %s: %s", rid[:8], exc)
            if written == 0:
                NotificationBanner.show_warning(
                    self, "Nothing exported", "No approved entries across selected reports",
                )
            else:
                NotificationBanner.show_success(
                    self, "DATs exported", f"{written} files written to {folder}",
                )
        except Exception as exc:
            log.error("_export_selected_set failed", exc_info=True)
            NotificationBanner.show_error(self, "Export failed", str(exc))

    def _delete_selected_set(self) -> None:
        """Delete all selected reports after confirmation."""
        ids = sorted(self._selected_report_ids)
        if not ids:
            return
        if QtWidgets.QMessageBox.question(
            self, "Remove reports",
            f"Remove {len(ids)} report(s) and their review decisions?",
        ) != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        failed = 0
        for rid in ids:
            try:
                self._app_state.reports.delete_report(rid)
            except Exception as exc:
                log.error("delete_report %s failed", rid[:8], exc_info=True)
                failed += 1
        self._selected_report_ids.clear()
        if self._selected_report_id in ids:
            self._selected_report_id = None
        self.refresh()
        if failed:
            NotificationBanner.show_warning(
                self, "Partial delete", f"{failed} of {len(ids)} reports could not be removed",
            )
```

**Step 2: Run the batch tests**

Run: `python -m pytest tests/test_reports_page_batch.py -v`
Expected: PASS (all three tests).

### Task 4.4: Add batch entries to navigator context menu

**Files:**
- Modify: `minerva/app/pages/reports.py:1114-1134` (the `_show_report_menu` body)

**Step 1: Add a batch submenu when more than one report is selected**

After the existing single-report action block (before `chosen = menu.exec(global_pos)`), insert:
```python
        # ── Batch submenu (only when multi-selected) ───────────────────
        selected_ids = {r.id for r in self._navigator.selected_reports()}
        if report.id not in selected_ids:
            selected_ids = {report.id}
        if len(selected_ids) > 1:
            menu.addSeparator()
            batch = menu.addMenu(Icons.queue(), f"Apply to {len(selected_ids)} selected")
            b_queue = batch.addAction(Icons.download(), "Queue selected")
            b_rematch = batch.addAction(Icons.refresh(), "Match selected")
            b_export = batch.addAction(Icons.file(), "Export selected")
            menu.addSeparator()
            b_remove = batch.addAction(Icons.trash(), "Remove selected")
            chosen_batch = menu.exec(global_pos)
            if chosen_batch == b_queue:
                self._queue_selected()
            elif chosen_batch == b_rematch:
                self._rematch_selected_set()
            elif chosen_batch == b_export:
                self._export_selected_set()
            elif chosen_batch == b_remove:
                self._delete_selected_set()
            return
        chosen = menu.exec(global_pos)
```

**Note:** The structure above nests `batch` as a submenu (Qt shows its items inline when the submenu is hovered). The `chosen_batch` capture handles the case where the user picks a batch item; otherwise falls through to `chosen = menu.exec(...)`. Adjust: actually `menu.exec` is called once — restructure so a single `menu.exec` covers both. The correct pattern:

```python
        # Build batch submenu items (if multi-selected)
        batch_actions: dict[QtGui.QAction, str] = {}
        if len(selected_ids) > 1:
            menu.addSeparator()
            batch = menu.addMenu(Icons.queue(), f"Apply to {len(selected_ids)} selected")
            batch_actions[batch.addAction(Icons.download(), "Queue selected")] = "queue"
            batch_actions[batch.addAction(Icons.refresh(), "Match selected")] = "rematch"
            batch_actions[batch.addAction(Icons.file(), "Export selected")] = "export"
            menu.addSeparator()
            batch_actions[menu.addAction(Icons.trash(), "Remove selected")] = "remove"

        chosen = menu.exec(global_pos)
        if chosen == queue:
            self._queue_report(report.id)
        elif chosen == review:
            self._review_selected()
        elif chosen == rematch:
            self._rematch_selected()
        elif chosen == export:
            self._export_reviewed()
        elif chosen == reveal:
            self._open_source_folder()
        elif chosen == remove:
            self._delete_selected()
        elif chosen in batch_actions:
            action = batch_actions[chosen]
            if action == "queue":
                self._queue_selected()
            elif action == "rematch":
                self._rematch_selected_set()
            elif action == "export":
                self._export_selected_set()
            elif action == "remove":
                self._delete_selected_set()
```

Replace the whole tail of `_show_report_menu` (from `chosen = menu.exec(global_pos)` onward) with this unified version.

**Step 2: Run tests**

Run: `python -m pytest tests/test_reports_page_batch.py tests/test_report_navigator_multiselect.py -v`
Expected: PASS.

### Task 4.5: Commit Phase 4

```bash
git add minerva/app/pages/reports.py tests/test_reports_page_batch.py
git commit -m "feat(reports): batch queue/rematch/export/remove on selected reports

Add four batch toolbar buttons (Queue/Rematch/Export/Remove selected) that
loop the existing single-report service methods over the multi-selection.
Aggregate QueueResult via .merge and post a single NotificationBanner.
Context menu gains a batch submenu when >1 report is selected.

Focused-report review flow (entry table, MatchDetailPanel) unchanged."
```

---

## Phase 5 — Folder import

### Task 5.1: Failing test for the folder walker

**Files:**
- Test: `tests/test_folder_import.py`

**Step 1: Write the failing test**

```python
"""Tests for recursive folder import of fixdat files."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from minerva.services.report_acquisition import ReportAcquisitionService


def _write_fake_dat(path: Path, name: str = "Test", n: int = 1) -> None:
    path.write_text(
        f'<?xml version="1.0"?>\n<dataframe name="{name}">\n'
        + "".join(f'<game name="title-{i}.zip" size="1024"/>\n' for i in range(n))
        + '</dataframe>\n',
        encoding="utf-8",
    )


def test_collect_fixdat_files_recursive(tmp_path):
    root = tmp_path / "reports"
    (root / "sub").mkdir(parents=True)
    (root / "a.dat").write_text("x")
    (root / "sub" / "b.csv").write_text("x")
    (root / "sub" / "c.fixdat").write_text("x")
    (root / "ignored.txt").write_text("x")
    (root / "notes.md").write_text("x")

    files = ReportAcquisitionService.collect_fixdat_files(root)
    exts = {f.suffix.lower() for f in files}
    assert exts <= {".dat", ".csv", ".fixdat"}
    names = {f.name for f in files}
    assert {"a.dat", "b.csv", "c.fixdat"} <= names
    assert "ignored.txt" not in names
    assert "notes.md" not in names


def test_collect_fixdat_files_empty_dir(tmp_path):
    files = ReportAcquisitionService.collect_fixdat_files(tmp_path)
    assert files == []


def test_import_folder_imports_and_matches_each(tmp_path, monkeypatch):
    # Set up two valid DATs
    root = tmp_path / "datset"
    root.mkdir()
    _write_fake_dat(root / "a.dat", "A", 1)
    _write_fake_dat(root / "b.dat", "B", 2)

    svc = MagicMock(spec=ReportAcquisitionService)
    # import_report returns a fake ReportSummary
    from minerva.domain.reports import ReportSummary
    from datetime import datetime, timezone
    svc.import_report.side_effect = lambda path: ReportSummary(
        id=path.stem, path=str(path), name=path.stem, collection="Nintendo",
        system="Nintendo - Game Boy Color",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=1, status="draft")
    svc.match_report.return_value = None

    summary = ReportAcquisitionService.import_folder(svc, root)

    assert svc.import_report.call_count == 2
    assert svc.match_report.call_count == 2
    assert summary.imported == 2
    assert summary.skipped == 0
    assert summary.failed == 0


def test_import_folder_skips_scope_inference_required(tmp_path, monkeypatch):
    root = tmp_path / "datset"
    root.mkdir()
    _write_fake_dat(root / "ambiguous.dat", "A", 1)

    svc = MagicMock(spec=ReportAcquisitionService)
    from minerva.domain.reports import ScopeInferenceRequired
    svc.import_report.side_effect = ScopeInferenceRequired([("a",), ("b",)])

    summary = ReportAcquisitionService.import_folder(svc, root)
    assert summary.imported == 0
    assert summary.skipped == 1
    assert summary.failed == 0


def test_import_folder_continues_after_failure(tmp_path):
    root = tmp_path / "datset"
    root.mkdir()
    _write_fake_dat(root / "good.dat", "Good", 1)
    _write_fake_dat(root / "bad.dat", "Bad", 1)

    calls = []
    from minerva.domain.reports import ReportSummary
    from datetime import datetime, timezone
    def fake_import(path):
        calls.append(path)
        if path.name == "bad.dat":
            raise ValueError("parse error")
        return ReportSummary(
            id=path.stem, path=str(path), name=path.stem, collection="",
            system="", imported_at=datetime.now(timezone.utc).isoformat(),
            requested_count=1, status="draft")

    svc = MagicMock(spec=ReportAcquisitionService)
    svc.import_report.side_effect = fake_import
    svc.match_report.return_value = None

    summary = ReportAcquisitionService.import_folder(svc, root)
    assert summary.imported == 1
    assert summary.failed == 1
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_folder_import.py -v`
Expected: FAIL — no `collect_fixdat_files` or `import_folder` methods.

### Task 5.2: Implement `collect_fixdat_files` and `import_folder`

**Files:**
- Modify: `minerva/services/report_acquisition.py` (add near `import_report`, ~line 1100)

**Step 1: Add the domain types and methods**

First add a small result dataclass to `minerva/domain/reports.py` (after `QueueResult`):
```python
@dataclass(frozen=True)
class FolderImportSummary:
    imported: int
    skipped: int   # scope inference ambiguous / empty report
    failed: int    # parse error or other exception
```

Then add the methods to `ReportAcquisitionService` (after `import_report`):
```python
    _FIXDAT_EXTENSIONS = {".dat", ".csv", ".fixdat"}

    @staticmethod
    def collect_fixdat_files(root: Path) -> list[Path]:
        """Recursively collect .dat/.csv/.fixdat files under *root*."""
        files: list[Path] = []
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in ReportAcquisitionService._FIXDAT_EXTENSIONS:
                files.append(path)
        return sorted(files)

    @staticmethod
    def import_folder(
        service: "ReportAcquisitionService",
        root: Path,
    ) -> FolderImportSummary:
        """Import + match every fixdat file under *root* (recursive).

        Best-effort: ScopeInferenceRequired / empty-report → skip with
        reason; any other exception → failed; log + continue. Never raises.
        """
        from minerva.domain.reports import FolderImportSummary
        imported = skipped = failed = 0
        for path in ReportAcquisitionService.collect_fixdat_files(root):
            try:
                service.import_report(path)
            except ScopeInferenceRequired as exc:
                log.info("import_folder: skip %s (scope ambiguous: %d candidates)",
                         path, len(exc.candidates) if hasattr(exc, "candidates") else 0)
                skipped += 1
                continue
            except ValueError as exc:
                log.info("import_folder: skip %s (%s)", path, exc)
                skipped += 1
                continue
            except Exception as exc:
                log.warning("import_folder: failed %s: %s", path, exc)
                failed += 1
                continue
            try:
                service.match_report(service._state.list_reports()[-1].id
                                     if service._state.list_reports() else "")
            except Exception as exc:
                log.warning("import_folder: match failed for %s: %s", path, exc)
                failed += 1
                continue
            imported += 1
        return FolderImportSummary(imported=imported, skipped=skipped, failed=failed)
```

**Refine:** The `match_report` call above is clumsy — `import_report` returns the `ReportSummary`, so capture it:

```python
    @staticmethod
    def import_folder(
        service: "ReportAcquisitionService",
        root: Path,
    ) -> FolderImportSummary:
        from minerva.domain.reports import FolderImportSummary
        imported = skipped = failed = 0
        for path in ReportAcquisitionService.collect_fixdat_files(root):
            try:
                report = service.import_report(path)
            except ScopeInferenceRequired as exc:
                log.info("import_folder: skip %s (scope ambiguous)", path)
                skipped += 1
                continue
            except ValueError as exc:
                log.info("import_folder: skip %s (%s)", path, exc)
                skipped += 1
                continue
            except Exception as exc:
                log.warning("import_folder: failed %s: %s", path, exc)
                failed += 1
                continue
            try:
                service.match_report(report.id)
            except Exception as exc:
                log.warning("import_folder: match failed for %s: %s", path, exc)
                failed += 1
                continue
            imported += 1
        return FolderImportSummary(imported=imported, skipped=skipped, failed=failed)
```

Add `FolderImportSummary` to the imports at the top of `report_acquisition.py` and to `minerva/domain/reports.py` exports.

**Step 2: Run test to verify it passes**

Run: `python -m pytest tests/test_folder_import.py -v`
Expected: PASS (5 tests).

### Task 5.3: Add "Import folder…" UI action

**Files:**
- Modify: `minerva/app/pages/reports.py:252` (header), `:657` (`_on_import`)

**Step 1: Add an "Import folder" button beside "Add report"**

After `self._import_btn = self._header.add_action("Add report", Icons.add(), primary=True)` add:
```python
        self._import_folder_btn = self._header.add_action("Import folder", Icons.folder_open())
```
And wire it (near the other `clicked.connect` lines):
```python
        self._import_folder_btn.clicked.connect(self._on_import_folder)
```

**Step 2: Add the `_on_import_folder` method** (near `_on_import`):
```python
    def _on_import_folder(self) -> None:
        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Import fix reports from folder")
        if not folder:
            return
        self._set_state(ReportsPageState.LOADING)
        self._generation += 1
        gen = self._generation
        svc = self._build_acquisition_service()
        task = TaskRunner.wrap_result(
            ReportAcquisitionService.import_folder, svc, Path(folder),
        )
        task.signals.result.connect(
            lambda payload: self._on_folder_import_result(gen, Path(folder), payload),
        )
        task.signals.error.connect(
            lambda details: self._on_match_error(gen, "", details),
        )
        self._pool.start(task)

    def _on_folder_import_result(self, generation: int, folder: Path, payload) -> None:
        if generation != self._generation:
            return
        # payload is OperationResult wrapping FolderImportSummary
        from minerva.ui.result import OperationResult
        summary = payload.payload if isinstance(payload, OperationResult) else payload
        self.refresh()
        if summary.failed or summary.skipped:
            NotificationBanner.show_warning(
                self, "Folder import complete",
                f"Imported {summary.imported}, skipped {summary.skipped}, "
                f"failed {summary.failed} from {folder}",
            )
        else:
            NotificationBanner.show_success(
                self, "Folder imported",
                f"Imported {summary.imported} report(s) from {folder}",
            )
```

**Step 3: Run tests**

Run: `python -m pytest tests/test_folder_import.py tests/test_reports_page_batch.py -v`
Expected: PASS.

### Task 5.4: Commit Phase 5

```bash
git add minerva/services/report_acquisition.py minerva/domain/reports.py minerva/app/pages/reports.py tests/test_folder_import.py
git commit -m "feat(reports): recursive folder import of fixdat files

Add ReportAcquisitionService.collect_fixdat_files + import_folder that
walks a directory and calls import_report + match_report per file,
best-effort (ScopeInferenceRequired → skip). UI 'Import folder' action
runs it via TaskRunner with a single summary notification."
```

---

## Phase 6 — CLI batch commands

### Task 6.1: Failing test for CLI batch commands

**Files:**
- Test: `tests/test_cli_batch.py`

**Step 1: Write the failing test**

```python
"""Tests for minerva_cli batch-* subcommands (headless, no Qt)."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import minerva_cli


def test_batch_list_prints_reports(capsys, monkeypatch):
    from minerva.domain.reports import ReportSummary
    from datetime import datetime, timezone
    fake_svc = MagicMock()
    fake_svc.list_reports.return_value = [
        ReportSummary(id="r1", path="a.dat", name="Report-A",
                      collection="Nintendo", system="Nintendo - Game Boy Color",
                      imported_at=datetime.now(timezone.utc).isoformat(),
                      requested_count=10, status="ready"),
        ReportSummary(id="r2", path="b.dat", name="Report-B",
                      collection="Sega", system="Sega - Mega Drive",
                      imported_at=datetime.now(timezone.utc).isoformat(),
                      requested_count=5, status="reviewed"),
    ]
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda: fake_svc)
    minerva_cli.command_batch_list(MagicMock())
    out = capsys.readouterr().out
    assert "Report-A" in out
    assert "Report-B" in out
    assert "ready" in out


def test_batch_queue_calls_queue_all_ready(capsys, monkeypatch):
    from minerva.domain.reports import QueueResult
    fake_svc = MagicMock()
    fake_svc.queue_all_ready.return_value = QueueResult(added=7, skipped_active=1, skipped_complete=2, skipped_missing=0)
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda: fake_svc)
    args = MagicMock(filter="", no_reviewed=False, output_dir="downloads")
    minerva_cli.command_batch_queue(args)
    out = capsys.readouterr().out
    assert "Queued 7" in out
    fake_svc.queue_all_ready.assert_called_once()


def test_batch_rematch_loops_over_ids(capsys, monkeypatch):
    fake_svc = MagicMock()
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda: fake_svc)
    args = MagicMock(report_ids=["r1", "r2", "r3"])
    minerva_cli.command_batch_rematch(args)
    out = capsys.readouterr().out
    assert fake_svc.rematch_report.call_count == 3
    assert "3" in out  # success count


def test_batch_import_walks_folder(capsys, monkeypatch, tmp_path):
    # Write two fake DATs
    (tmp_path / "a.dat").write_text('<?xml?><dataframe name="A"></dataframe>')
    (tmp_path / "b.dat").write_text('<?xml?><dataframe name="B"></dataframe>')
    fake_svc = MagicMock()
    monkeypatch.setattr(minerva_cli, "_build_batch_service", lambda: fake_svc)
    args = MagicMock(directory=str(tmp_path))
    minerva_cli.command_batch_import(args)
    out = capsys.readouterr().out
    assert fake_svc.import_report.call_count == 2
    assert fake_svc.match_report.call_count == 2
    assert "2" in out
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli_batch.py -v`
Expected: FAIL — no `command_batch_*` or `_build_batch_service`.

### Task 6.2: Implement `_build_batch_service` and batch commands

**Files:**
- Modify: `minerva_cli.py` (add after existing commands, ~line 95)

**Step 1: Add the service factory + commands**

After `command_check` (line 95) and before `def main`:
```python
# ── Batch commands ────────────────────────────────────────────────────────────

def _build_batch_service(output_dir: str | Path = "downloads"):
    """Construct a headless ReportAcquisitionService (no Qt, no controller).

    _enqueue_file falls back to writing queue records directly to
    MinervaState so the GUI picks them up later.
    """
    from minerva.services.report_acquisition import ReportAcquisitionService
    from minerva_state import MinervaState
    state = MinervaState()
    return ReportAcquisitionService(state=state, download_controller=None, output_dir=output_dir)


def command_batch_import(args: argparse.Namespace) -> None:
    """Recursively import + match every fixdat file under a directory."""
    from pathlib import Path
    from minerva.services.report_acquisition import ReportAcquisitionService
    root = Path(args.directory)
    if not root.is_dir():
        print(f"Not a directory: {root}")
        sys.exit(1)
    svc = _build_batch_service()
    summary = ReportAcquisitionService.import_folder(svc, root)
    print(f"Imported {summary.imported}, skipped {summary.skipped}, failed {summary.failed}")


def command_batch_queue(args: argparse.Namespace) -> None:
    """Queue ready entries across all (or filtered) reports."""
    svc = _build_batch_service(output_dir=args.output_dir)
    result = svc.queue_all_ready(
        name_filter=args.filter or "",
        include_reviewed=not args.no_reviewed,
    )
    print(f"Queued {result.added} ROMs "
          f"(skipped: {result.skipped_active} active, "
          f"{result.skipped_complete} complete, "
          f"{result.skipped_missing} missing)")
    if result.added == 0 and result.skipped_missing > 0:
        sys.exit(1)


def command_batch_rematch(args: argparse.Namespace) -> None:
    """Re-run matching for each listed report_id."""
    svc = _build_batch_service()
    succeeded = failed = 0
    for rid in args.report_ids:
        try:
            svc.rematch_report(rid)
            succeeded += 1
        except Exception as exc:
            print(f"  failed {rid[:8]}: {exc}")
            failed += 1
    print(f"Rematched {succeeded} report(s), {failed} failed")
    if failed and not succeeded:
        sys.exit(1)


def command_batch_list(args: argparse.Namespace) -> None:
    """List imported reports with status."""
    svc = _build_batch_service()
    reports = svc._state.list_reports()
    if not reports:
        print("No reports imported.")
        return
    print(f"{'NAME':30s} {'STATUS':10s} {'COLLECTION':15s} {'SYSTEM':30s}")
    print("-" * 85)
    for r in reports:
        print(f"{r.name[:30]:30s} {r.status[:10]:10s} "
              f"{(r.collection or '')[:15]:15s} {(r.system or '')[:30]:30s}")
    print(f"\n{len(reports)} report(s)")
```

**Step 2: Add the subparsers to `main`** (after `sub.add_parser("check", ...)` at line 117):
```python
    p_bi = sub.add_parser("batch-import", help="Import fixdat files from a folder")
    p_bi.add_argument("directory")

    p_bq = sub.add_parser("batch-queue", help="Queue ready entries from all reports")
    p_bq.add_argument("--filter", default="", help="Filter reports by name substring")
    p_bq.add_argument("--no-reviewed", action="store_true", help="Exclude reviewed entries")
    p_bq.add_argument("--output-dir", default="downloads", help="Destination root")

    p_br = sub.add_parser("batch-rematch", help="Re-run matching for reports")
    p_br.add_argument("report_ids", nargs="+")

    sub.add_parser("batch-list", help="List imported reports")
```

And add to the commands dict (line 121):
```python
    commands = {
        "index": command_index,
        "find": command_find,
        "download": command_download,
        "stats": command_stats,
        "check": command_check,
        "batch-import": command_batch_import,
        "batch-queue": command_batch_queue,
        "batch-rematch": command_batch_rematch,
        "batch-list": command_batch_list,
    }
```

**Step 3: Run test to verify it passes**

Run: `python -m pytest tests/test_cli_batch.py -v`
Expected: PASS (4 tests).

### Task 6.3: Commit Phase 6

```bash
git add minerva_cli.py tests/test_cli_batch.py
git commit -m "feat(cli): add batch-import, batch-queue, batch-rematch, batch-list

Headless subcommands constructing ReportAcquisitionService without a
download_controller — _enqueue_file writes queue records to MinervaState
for the GUI to pick up. batch-queue takes --output-dir to avoid the
QSettings fallback path that imports PyQt6."
```

---

## Phase 7 — Regression + verification

### Task 7.1: Regression test for single-report paths

**Files:**
- Test: `tests/test_reports_page_regression.py`

**Step 1: Write regression tests**

```python
"""Regression: single-report flows unchanged after multi-select refactor."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PyQt6 import QtCore

_HERE = Path(__file__).parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from minerva.app.app_state import AppState
from minerva.app.pages.reports import ReportsPage
from minerva.domain.reports import ReportSummary


def _make_report(rid: str) -> ReportSummary:
    from datetime import datetime, timezone
    return ReportSummary(
        id=rid, path=f"{rid}.dat", name=f"R-{rid}",
        collection="Nintendo", system="Nintendo - Game Boy Color",
        imported_at=datetime.now(timezone.utc).isoformat(),
        requested_count=2, status="reviewed",
    )


@pytest.fixture()
def page(qtbot, monkeypatch):
    monkeypatch.setattr("minerva.app.pages.reports.ReportAcquisitionService", MagicMock())
    monkeypatch.setattr("minerva.app.pages.reports.MinervaDB", MagicMock())
    app_state = AppState()
    p = ReportsPage(app_state)
    qtbot.addWidget(p)
    return p


def test_single_click_focuses_report_and_loads_entries(page, qtbot):
    page._navigator.set_reports([_make_report("a"), _make_report("b")])
    page._navigator.view.setCurrentIndex(page._navigator.proxy.index(1, 0))
    assert page._selected_report_id == "b"


def test_current_report_unchanged_when_selection_grows(page, qtbot):
    page._navigator.set_reports([_make_report("a"), _make_report("b"), _make_report("c")])
    page._navigator.view.setCurrentIndex(page._navigator.proxy.index(0, 0))
    assert page._selected_report_id == "a"
    # Add to selection without changing focus
    page._navigator.view.selectionModel().select(
        page._navigator.proxy.index(2, 0), QtCore.QItemSelectionModel.SelectionFlag.Select)
    # Focus still a — entry table/MMDetail bound to focused report, not selection
    assert page._selected_report_id == "a"
    assert page._selected_report_ids == {"a", "c"}


def test_queue_all_ready_still_works(page, qtbot):
    """The global 'Queue all ready' button must remain functional."""
    page._navigator.set_reports([_make_report("a")])
    page.window().download_controller = MagicMock()
    # Should not raise
    assert page._queue_all_btn.isEnabled()
```

**Step 2: Run regression tests**

Run: `python -m pytest tests/test_reports_page_regression.py -v`
Expected: PASS.

### Task 7.2: Run full test suite

Run: `python -m pytest tests/ -v`
Expected: All tests PASS, including pre-existing ones. Investigate any failures.

### Task 7.3: Typecheck + lint

Run: `python -m mypy minerva/domain/reports.py minerva/services/report_acquisition.py minerva_cli.py minerva/app/pages/reports.py minerva/ui/widgets/report_navigator.py --ignore-missing-imports`
Expected: No new errors.

Run: `python -m ruff check minerva/ minerva_cli.py tests/test_queue_result_merge.py tests/test_export_reviewed.py tests/test_report_navigator_multiselect.py tests/test_reports_page_batch.py tests/test_folder_import.py tests/test_cli_batch.py tests/test_reports_page_regression.py`
Expected: Clean.

### Task 7.4: Manual smoke test (GUI)

Run: `./minerva.sh`
- Import 2+ reports
- Ctrl-click to multi-select → batch buttons enable
- "Queue selected" → single notification, queue updated
- "Match selected" → loading state, refresh, single notification
- "Remove selected" → confirm dialog, reports removed
- Right-click a multi-selected report → "Apply to N selected" submenu
- "Import folder" → recursive import, summary notification
- Single-click still focuses → entry table loads

### Task 7.5: Manual smoke test (CLI)

```bash
python minerva_cli.py batch-list
python minerva_cli.py batch-import <folder>
python minerva_cli.py batch-queue --output-dir downloads
python minerva_cli.py batch-rematch <id1> <id2>
```
Expected: each command prints a summary, no crashes, exit code 0 on success.

### Task 7.6: Final commit + PR

```bash
git add tests/test_reports_page_regression.py
git commit -m "test(reports): regression for single-report flows after multi-select"
```

Then create the PR per the branch-pr skill — title: "feat: batch operations with fixdat reports (UI multi-select, folder import, CLI batch)".
