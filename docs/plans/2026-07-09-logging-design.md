# Logging System Design

**Date:** 2026-07-09
**Status:** Approved

## Problem

The app has ~20 modules calling `logging.getLogger(__name__)` but no root
logging configuration anywhere. `bootstrap.py` and `minerva_cli.py` never call
`basicConfig`/`dictConfig`. Result: INFO/DEBUG messages vanish entirely,
WARNING+ goes to stderr via Python's default `lastResort` handler with no
formatting, and nothing persists across sessions. There is no way for users
or developers to see what the app is doing, trace failures, or produce a log
file for bug reports.

Additionally, `torrent_monitor.py` still logs stale `"QbitMonitor started"`
messages left over from the qBittorrent → native libtorrent migration, and
two modules (`bootstrap.py`, `http_download.py`) use `logger` while every
other module uses `log`.

## Goals

- **Debugging download failures** — trace torrent engine errors, match misses,
  queue routing issues with per-download context.
- **User support / bug reports** — a persistent log file users can send.
- **General observability** — understand active downloads, match operations,
  background tasks at a glance.

## Architecture

A single `minerva/logging_config.py` module provides
`configure_logging(level)` — called once at startup from both
`bootstrap.main()` and the CLI entry point. It attaches two handlers to the
root logger:

1. **RotatingFileHandler** → platform-appropriate log directory
   (`~/.local/share/MinervaFixDAT/logs/minerva.log`), 5 MB × 3 backups,
   plain text, DEBUG+.
2. **StreamHandler** → stderr, WARNING+ by default (DEBUG if `--verbose`).

Contextual fields (torrent_hash, file_id, record_id) are added via `extra=`
on new log calls — no change to existing call sites required.

## Components

### 1. `minerva/logging_config.py` (new)

- `configure_logging(level: str | int = "INFO") -> Path` — idempotent;
  attaches handlers if not already attached, returns log file path.
- `get_log_file_path() -> Path` — resolves platform path via
  `QStandardPaths` (or `~/.local/share/MinervaFixDAT/logs/` on Linux).
- Format: `%(asctime)s %(levelname)-7s %(name)s %(message)s` for both
  handlers.
- File handler: `RotatingFileHandler(path, maxBytes=5_000_000,
  backupCount=3, encoding="utf-8")` at DEBUG+.
- Console handler: `StreamHandler(sys.stderr)` at WARNING+
  (overridable via `--verbose`).

### 2. `minerva/app/bootstrap.py` (modify `main()`)

- Call `configure_logging()` at the top of `main()`, before anything else.
- Parse `--verbose`/`-v` (→ DEBUG console) and `--debug` (→ DEBUG
  everywhere) from `sys.argv`.
- Read `MINERVA_LOG_LEVEL` env var as fallback.

### 3. `minerva_cli.py` (modify)

- Call `configure_logging()` at import time or in `main()`.
- Add `--verbose`/`-v` flag to the top-level argparse.

### 4. `minerva/domain/settings.py` (modify `SettingsDraft`)

- Add `log_level: str = "INFO"` field.

### 5. `minerva/app/pages/settings.py` (modify Advanced page)

- Add a "Logging" card to the Advanced page:
  - Log level dropdown (DEBUG / INFO / WARNING / ERROR).
  - Read-only display of the log file path.
  - "Open log file" button → opens in system text editor via
    `QDesktopServices`.
- Wire level changes through `_load_draft` / `_form_to_draft` /
  `_persist_draft`.
- On save, call `configure_logging(new_level)` to apply live.

### 6. Stale log message fixes

- `torrent_monitor.py`: `"QbitMonitor started/stopped"` →
  `"NativeMonitor started/stopped"`.
- Normalize `logger` → `log` in `bootstrap.py` and `http_download.py`.

## Data Flow

```
Startup:
  bootstrap.main() / minerva_cli.main()
    → configure_logging(level from env/CLI/QSettings)
      → RotatingFileHandler (DEBUG+, rotating 5MB×3)
      → StreamHandler (WARNING+, or DEBUG if --verbose)
    → all log.info/debug/warning/error calls now go somewhere

Runtime level change:
  SettingsPage._on_save()
    → _persist_draft() stores log_level in QSettings
    → configure_logging(new_level)  # idempotent, reconfigures handlers
    → logging.getLogger().setLevel(new_level)

Contextual fields:
  log.info("Added torrent (paused): %s -> %s", name, hash[:8],
           extra={"torrent_hash": hash, "file_id": fid})
  → LoggerAdapter or Filter formats extra fields into the message
```

## Error Handling

- Log directory creation failure (e.g. read-only filesystem) → fall back to
  console-only logging with a warning, don't crash startup.
- `configure_logging` is idempotent and wrapped in try/except so a logging
  misconfiguration never prevents app launch.
- File handler encoding errors → `encoding="utf-8"` with `errors="replace"`.

## Testing

- Unit test `configure_logging`: verify handlers attached, levels set
  correctly, idempotency on second call.
- Unit test `get_log_file_path`: returns expected path, creates parent
  directory.
- Unit test: log file actually receives written messages at the configured
  level.
- Unit test: rotation triggers at size threshold (use smaller threshold in
  test).
- Existing tests should be unaffected — they don't configure logging and
  Python's default `lastResort` handler still works.

## Out of Scope

- Full in-app log viewer panel (deferred — file + open button covers the
  "send me your logs" use case).
- Structured/JSON logging (plain text chosen).
- Per-module log levels (one global level keeps it simple).
- Retroactive `extra=` additions to all existing log call sites — opt-in
  for new code.
