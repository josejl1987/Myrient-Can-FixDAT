# Batch Operations with FixDAT Reports — Design

**Date:** 2026-06-28
**Status:** Approved (single PR)
**Scope:** UI multi-select (foundation), folder import, CLI batch commands

## Problem

The reports dashboard operates one report at a time: `ReportNavigator` uses
`SingleSelection` and emits `current_report_changed` / `context_menu_requested`
for a single report. The only true batch op today is the global "Queue all ready"
header button (`svc.queue_all_ready()`). There is no way to act on a *subset* of
reports, no recursive folder import, and no headless batch path.

## Goals

1. Multi-select a subset of reports and run queue / rematch / export / remove
   over that subset, while keeping the one-report review flow (entry table +
   MatchDetailPanel bound to the *focused* report) unchanged.
2. Import an entire folder of `.dat` / `.csv` / `.fixdat` files recursively,
   best-effort per file.
3. Headless CLI subcommands for batch import / queue / rematch / list.

## Non-goals (YAGNI)

- Cross-report entry merging in the review table.
- Undo for batch delete (no undo infra exists).
- Parallelism in batch loops — sequential; SQLite serializes anyway.
- CLI `batch-export` (writes per-report files, awkward headless).

## Architecture

```
UI multi-select (foundation)  →  folder import  →  CLI batch commands
        shared service methods underneath
```

All three reuse the existing single-report service methods in loops. The service
layer gains two small additions only; no parallel `*_many` methods.

### Selection model split (foundation)

`ReportNavigator` switches `SingleSelection` → `ExtendedSelection`. Two
concepts stay distinct:

- **Focused report** (`current_report_changed`, unchanged) — drives the entry
  table and `MatchDetailPanel` via `ReportsPage._selected_report_id`.
- **Multi-selection set** (`selected_report_ids: set[str]`, new) — drives the
  batch toolbar and the batch portion of the context menu.

New signal: `selection_changed = pyqtSignal(list)` emitting the list of
`ReportSummary` under multi-selection.

### Batch toolbar (header)

Keep "Queue all ready" (global). Add, enabled only when
`len(selected_report_ids) >= 1`:

- "Queue selected"   → loops `svc.queue_ready(id)` over the set
- "Match selected"   → loops `svc.rematch_report(id)` over the set
- "Export selected"  → loops `svc.export_reviewed(id, dest)` over the set
- "Remove selected"  → confirm dialog, loops `delete_report(id)` over the set

Each batch action runs as one `TaskRunner` job that aggregates counts and posts
**one** `NotificationBanner` at the end — not N banners. A failure on one report
logs and continues; the notification reports partial results
(`added=X, failed=Y, see log`).

### Context menu

Keep existing single-report actions for the right-clicked report. Add a
"Apply to all selected" submenu when `len(selected_report_ids) > 1` with the
four batch actions. Right-click remains intuitive; batch ops are opt-in.

### Folder / bulk import

**UI:** "Import folder…" action beside "Import fix reports".
`QFileDialog.getExistingDirectory`, then walk recursively for
`*.dat *.csv *.fixdat`.

**Service path:** per file, call `svc.import_report(path)` then
`svc.match_report(report_id)`. **Correction from initial draft:** `import_report`
only parses + infers scope + persists with `status="draft"`; it does *not*
classify entries. Without `match_report`, imported reports stay in `draft` with
no READY/REVIEW classification — useless for queueing.

`ScopeInferenceRequired` → catch, log skip with reason, continue. Batch import is
best-effort; one file's ambiguous scope must not abort the whole run.

**Progress:** `TaskRunner` job with progress callback
(`imported N/M, skipped K`). Single notification at end with the summary.

### CLI batch commands

```
minerva batch-import <dir>                       # recursively import + match
minerva batch-queue [--filter NAME] [--no-reviewed] [--output-dir DIR]
minerva batch-rematch <report_id> [<report_id> ...]
minerva batch-list                               # reports with status counts
```

`ReportAcquisitionService` constructed via the existing factory with
`download_controller=None`. `_enqueue_file` already falls back to writing queue
records directly to `MinervaState`, which the GUI picks up later — no Qt dragged
into the CLI.

**Output dir correction:** `queue_ready` resolves destination via
`self._output_dir`, falling back to reading `QSettings` (which imports
`PyQt6.QtCore`). In CLI, pass `output_dir` explicitly to the service constructor
(default `downloads`); never rely on the `QSettings` fallback headless.

## Service layer changes

### New: `QueueResult` aggregator

Frozen dataclass `minerva.domain.reports.QueueResult` gains a `merge` classmethod
(or `__add__`) so UI and CLI aggregate uniformly:

```python
@classmethod
def merge(cls, *results: "QueueResult") -> "QueueResult":
    return cls(
        added=sum(r.added for r in results),
        skipped_active=sum(r.skipped_active for r in results),
        skipped_complete=sum(r.skipped_complete for r in results),
        skipped_missing=sum(r.skipped_missing for r in results),
    )
```

Replaces the ad-hoc manual aggregation currently inline in `queue_all_ready`
(`report_acquisition.py:1614-1624`) — that code path is refactored to use `merge`.

### New: `export_reviewed` service method

**Correction from initial draft:** there is no `svc.export_reviewed()` today.
`_export_reviewed` lives in `ReportsPage` (`reports.py:1053`) and calls
`MinervaDB().build_synthetic_dat(...)` directly. Extract a thin service method so
UI and CLI share one policy for "which entries count as approved":

```python
def export_reviewed(self, report_id: str, dest_path: Path) -> int:
    """Write a synthetic DAT of approved entries. Returns count written."""
```

Approved = `decision in {"accept", "fuzzy"}` and has a `selected_file_id` or
`automatic_file_id` (mirrors current `_export_reviewed`). Wraps
`MinervaDB.build_synthetic_dat`. `ReportsPage._export_reviewed` becomes a caller
of this method.

### Unchanged (called in loops)

- `queue_ready(report_id, *, include_reviewed=True) -> QueueResult`
- `rematch_report(report_id, policy=None) -> AcquisitionSummary`
- `import_report(path, scope=None) -> ReportSummary`  (+ `match_report` after)
- `queue_all_ready(*, name_filter="", include_reviewed=True) -> QueueResult`
- `delete_report` (on `AppState.reports`)

No parallel `*_many` methods — loops live where the policy (partial-failure
handling, notifications) actually lives.

## Error handling

- **Batch import:** per-file try/except. `ScopeInferenceRequired` → skip with
  reason. `ValueError("Empty report")` → skip. Continue. Summary reports
  `imported/skipped/failed`.
- **Batch queue/rematch/export:** per-report try/except, log + continue.
  Aggregate `QueueResult` / counts reflect successes only; failures reported in
  the notification body (`added=X, failed=Y, see log`).
- **Batch delete:** confirm dialog up front (destructive); per-report try/except,
  continue on failure.
- **CLI:** never raise out of a batch loop; print per-item failures and a summary
  line. Exit code 0 if any succeeded, 1 if all failed.

## Testing

- **Unit (no Qt):** `QueueResult.merge`; `export_reviewed` service method with a
  fake `MinervaDB`; CLI command functions with a fake
  `ReportAcquisitionService`; folder walker (recursion, extension filter, empty
  dir, `ScopeInferenceRequired` skip).
- **UI (pytest-qt, `tests/test_queue_preview_dialog.py` style):** navigator
  multi-select signal emission; batch toolbar enable/disable by selection size;
  batch action calls the service N times with the selected ids; focused report
  unchanged after multi-select; context-menu batch submenu appears only when
  `len(selection) > 1`.
- **Regression:** single-report paths (`_selected_report_id` flow, single-report
  context menu, single "Queue report") unchanged.

## Deliverable

Single PR covering: `QueueResult.merge`, `export_reviewed` service method,
`ReportNavigator` ExtendedSelection + `selection_changed` signal,
`ReportsPage` batch toolbar + context-menu batch submenu, "Import folder…"
action, CLI `batch-import` / `batch-queue` / `batch-rematch` / `batch-list`
subcommands, and tests for each.
