"""Thin CLI for the Minerva Myrient-Can-FixDAT index.

Usage:
    python minerva_cli.py index [--rebuild]
    python minerva_cli.py find <query> [--collection <col>] [--system <sys>] [--limit <n>] [--json]
    python minerva_cli.py download <file_id> [--dest <path>]
    python minerva_cli.py stats
    python minerva_cli.py check
    python minerva_cli.py batch-import <dir> [--json]
    python minerva_cli.py batch-queue [--filter <s>] [--json] [--source <s>] [--source-ref <s>]
    python minerva_cli.py batch-rematch <id>... [--json]
    python minerva_cli.py batch-list [--json]
    python minerva_cli.py completed [--json] [--since <iso>]
    python minerva_cli.py lookup-batch '<json_array>'
"""

from __future__ import annotations

import argparse
import json as _json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# Import only from the production modules
from minerva.logging_config import configure_logging
from minerva_db import DEFAULT_INDEX_PATH, DEFAULT_TORRENT_DIR, MinervaDB, build_index
from minerva.native_torrent import NativeTorrentSession

# ── Formatting helpers ────────────────────────────────────────────────────────

def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _emit_json(data: object) -> None:
    """Print data as JSON to stdout, flush immediately."""
    print(_json.dumps(data, default=str), flush=True)


# ── Commands ──────────────────────────────────────────────────────────────────

def command_index(args: argparse.Namespace) -> None:
    """Build or rebuild the torrent index."""
    import time
    t0 = time.time()
    build_index(DEFAULT_TORRENT_DIR, DEFAULT_INDEX_PATH)
    print(f"Done in {time.time() - t0:.1f}s")


def command_find(args: argparse.Namespace) -> None:
    """Search the library."""
    db = MinervaDB()
    rows, total = db.search(
        query=args.query,
        collection=args.collection or "",
        system=args.system or "",
        limit=args.limit or 20,
    )
    if getattr(args, "json", False):
        _emit_json([
            {"id": row["id"], "stem": row["stem"], "size": row["size"],
             "collection": row["collection"], "system": row["system"],
             "basename": row["basename"]}
            for row in rows
        ])
        return
    print(f"Found {total} results:")
    for row in rows:
        print(f"  {row['stem']:50s} {_format_bytes(row['size']):>10s}  "
              f"{row['collection']:20s} {row['system']}")


def command_download(args: argparse.Namespace) -> None:
    """Submit one selectively-enabled file to the native libtorrent engine."""
    import os

    db = MinervaDB()
    spec = db.get_download_spec(args.file_id, DEFAULT_TORRENT_DIR)
    if spec is None:
        raise SystemExit(f"Unknown file_id: {args.file_id}")

    destination = Path(args.dest).expanduser().resolve()
    seed_ratio = float(os.environ.get("MINERVA_TORRENT_SEED_RATIO", "2.0"))
    seed_time_hours = int(os.environ.get("MINERVA_TORRENT_SEED_TIME_HOURS", "48"))
    client = NativeTorrentSession(
        save_dir=destination,
        seed_ratio=seed_ratio,
        seed_time_hours=seed_time_hours,
    )
    client.start()
    try:
        torrent_hash = client.add_torrent_paused(str(spec.torrent_path), str(destination))
        files = client.get_files(torrent_hash)
        all_indices = [
            int(item.get("index", -1))
            for item in files
            if item.get("index") is not None
        ]
        client.set_file_priority(torrent_hash, all_indices, 0)
        client.set_file_priority(torrent_hash, [spec.torrent_file_index], 1)
        client.resume(torrent_hash)
        print(f"Started {spec.basename} ({torrent_hash}) → {destination}")

        deadline = time.monotonic() + args.timeout if args.timeout else None
        while True:
            info = client.get_torrent_info(torrent_hash)
            if info is None:
                raise RuntimeError("Torrent disappeared from the native session")
            progress = float(info.get("progress", 0.0))
            if progress >= 1.0:
                print(f"Completed {spec.basename}")
                return
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(f"Download timed out at {progress:.1%}")
            time.sleep(1)
    finally:
        client.stop()


def command_stats(args: argparse.Namespace) -> None:
    """Show index statistics."""
    db = MinervaDB()
    overview = db.get_index_overview()
    print(f"Collections: {overview.collections}")
    print(f"Systems:     {overview.systems}")
    print(f"Files:       {overview.files}")
    print(f"DB size:     {_format_bytes(overview.database_size)}")
    print(f"Last build:  {overview.last_build or 'never'}")


def command_check(args: argparse.Namespace) -> None:
    """Run integrity check."""
    db = MinervaDB()
    result = db.run_integrity_check()
    print(f"Integrity: {result.get('quick_check', 'unknown')}")


# ── Batch commands ────────────────────────────────────────────────────────────

def _build_batch_service(
    output_dir: str | Path = "downloads",
    source: str = "romresolve",
    source_ref: str | None = None,
):
    """Construct a headless ReportAcquisitionService (no Qt, no controller).

    _enqueue_file falls back to writing queue records directly to
    MinervaState so the GUI picks them up later.
    """
    from minerva.services.report_acquisition import ReportAcquisitionService
    from minerva_state import MinervaState
    state = MinervaState()
    return ReportAcquisitionService(
        state=state, download_controller=None,
        output_dir=output_dir, source=source, source_ref=source_ref,
    )


def command_batch_import(args: argparse.Namespace) -> None:
    """Recursively import + match every fixdat file under a directory."""
    from minerva.services.report_acquisition import ReportAcquisitionService
    root = Path(args.directory)
    if not root.is_dir():
        print(f"Not a directory: {root}")
        sys.exit(1)
    svc = _build_batch_service()
    summary = ReportAcquisitionService.import_folder(svc, root)
    if getattr(args, "json", False):
        _emit_json({"imported": summary.imported, "skipped": summary.skipped, "failed": summary.failed})
        return
    print(f"Imported {summary.imported}, skipped {summary.skipped}, failed {summary.failed}")


def command_batch_queue(args: argparse.Namespace) -> None:
    """Queue ready entries across all (or filtered) reports."""
    svc = _build_batch_service(
        output_dir=args.output_dir,
        source=args.source,
        source_ref=args.source_ref,
    )
    result = svc.queue_all_ready(
        name_filter=args.filter or "",
        include_reviewed=not args.no_reviewed,
    )
    if getattr(args, "json", False):
        _emit_json({
            "added": result.added,
            "skipped_active": result.skipped_active,
            "skipped_complete": result.skipped_complete,
            "skipped_missing": result.skipped_missing,
        })
        return
    print(f"Queued {result.added} ROMs "
          f"(skipped: {result.skipped_active} active, "
          f"{result.skipped_complete} complete, "
          f"{result.skipped_missing} missing)")


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
    if getattr(args, "json", False):
        _emit_json({"succeeded": succeeded, "failed": failed})
        return
    print(f"Rematched {succeeded} report(s), {failed} failed")


def command_batch_list(args: argparse.Namespace) -> None:
    """List imported reports with status."""
    svc = _build_batch_service()
    reports = svc._state.list_reports()
    if getattr(args, "json", False):
        _emit_json([
            {"id": r.id, "name": r.name, "status": r.status,
             "collection": r.collection or "", "system": r.system or ""}
            for r in reports
        ])
        return
    if not reports:
        print("No reports imported.")
        return
    print(f"{'NAME':30s} {'STATUS':10s} {'COLLECTION':15s} {'SYSTEM':30s}")
    print("-" * 85)
    for r in reports:
        print(f"{r.name[:30]:30s} {r.status[:10]:10s} "
              f"{(r.collection or '')[:15]:15s} {(r.system or '')[:30]:30s}")
    print(f"\n{len(reports)} report(s)")


def _parse_iso(raw: str) -> datetime:
    """Parse an ISO datetime string, handling 'Z' suffix.

    Raises ValueError if *raw* is not a valid ISO datetime.
    """
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw)


def command_completed(args: argparse.Namespace) -> None:
    """List completed/seeding downloads from the queue."""
    from datetime import datetime
    from minerva_state import MinervaState
    state = MinervaState()
    records = state.list_queue()
    done = [r for r in records if r.status in ("completed", "seeding")]
    if getattr(args, "since", None):
        try:
            since_dt = _parse_iso(args.since)
        except ValueError:
            print(f"Error: invalid --since ISO datetime: {args.since}", file=sys.stderr)
            sys.exit(1)
        done = [
            r for r in done
            if (_parse_iso(r.updated_at) if r.updated_at else datetime.min) >= since_dt
        ]
    if getattr(args, "json", False):
        _emit_json([
            {"file_id": r.file_id, "destination": r.destination,
             "status": r.status, "updated_at": r.updated_at,
             "source": r.source, "source_ref": r.source_ref}
            for r in done
        ])
        return
    print(f"Found {len(done)} completed downloads:")
    for r in done:
        print(f"  {r.file_id:8d}  {r.status:10s}  {r.destination}")


def command_lookup_batch(args: argparse.Namespace) -> None:
    """Batch-lookup files by stem+size, returning file_id + metadata."""
    try:
        queries = _json.loads(args.queries) if args.queries else []
    except _json.JSONDecodeError as exc:
        print(f"Error: invalid JSON in queries: {exc}", file=sys.stderr)
        sys.exit(1)
    db = MinervaDB()
    results = []
    for q in queries:
        stem = q.get("stem", "")
        size = q.get("size", 0)
        exact = db.find_file_id_by_stem_and_size(stem, size)
        if exact is None:
            results.append({"stem": stem, "size": size, "status": "missing"})
        elif isinstance(exact, list):
            results.append({
                "stem": stem, "size": size, "status": "exact",
                "candidates": [
                    {"file_id": m.file_id, "collection": m.collection,
                     "system": m.system, "basename": m.basename,
                     "size": m.size, "path_full": m.path_full}
                    for m in exact
                ],
            })
        else:
            results.append({
                "stem": stem, "status": "exact",
                "file_id": exact.file_id, "collection": exact.collection,
                "system": exact.system, "basename": exact.basename,
                "size": exact.size, "path_full": exact.path_full,
            })
    _emit_json(results)


# ── CLI entrypoint ────────────────────────────────────────────────────────────

def main() -> None:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-v", "--verbose", action="store_true", help="Enable verbose (DEBUG) logging")
    parser = argparse.ArgumentParser(description="Minerva CLI", parents=[common])
    sub = parser.add_subparsers(dest="command")

    p_index = sub.add_parser("index", help="Build/rebuild index", parents=[common])
    p_index.add_argument("--rebuild", action="store_true")

    p_find = sub.add_parser("find", help="Search library", parents=[common])
    p_find.add_argument("query")
    p_find.add_argument("--collection")
    p_find.add_argument("--system")
    p_find.add_argument("--limit", type=int, default=20)
    p_find.add_argument("--json", action="store_true", help="Emit JSON")

    p_dl = sub.add_parser("download", help="Download file", parents=[common])
    p_dl.add_argument("file_id", type=int)
    p_dl.add_argument("--dest", default="downloads")
    p_dl.add_argument("--timeout", type=int, default=0, help="Maximum seconds to wait; 0 waits indefinitely")

    sub.add_parser("stats", help="Index statistics", parents=[common])
    sub.add_parser("check", help="Integrity check", parents=[common])

    p_bi = sub.add_parser("batch-import", help="Import fixdat files from a folder", parents=[common])
    p_bi.add_argument("directory")
    p_bi.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    p_bq = sub.add_parser("batch-queue", help="Queue ready entries from all reports", parents=[common])
    p_bq.add_argument("--filter", default="", help="Filter reports by name substring")
    p_bq.add_argument("--no-reviewed", action="store_true", help="Exclude reviewed entries")
    p_bq.add_argument("--output-dir", default="downloads", help="Destination root")
    p_bq.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    p_bq.add_argument("--source", default="romresolve", help="Download source tag")
    p_bq.add_argument("--source-ref", default=None, help="Origin reference (e.g. workspace path)")

    p_br = sub.add_parser("batch-rematch", help="Re-run matching for reports", parents=[common])
    p_br.add_argument("report_ids", nargs="+")
    p_br.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    p_bl = sub.add_parser("batch-list", help="List imported reports", parents=[common])
    p_bl.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    p_comp = sub.add_parser("completed", help="List completed/seeding downloads", parents=[common])
    p_comp.add_argument("--json", action="store_true")
    p_comp.add_argument("--since", default=None, help="ISO datetime filter")

    p_lb = sub.add_parser("lookup-batch", help="Batch lookup files by stem+size", parents=[common])
    p_lb.add_argument("queries", help="JSON array of {stem, size} objects")

    args = parser.parse_args()

    # ── Logging setup ────────────────────────────────────────────────
    console_level = "DEBUG" if args.verbose else "WARNING"
    env_level = os.environ.get("MINERVA_LOG_LEVEL", "INFO")
    try:
        configure_logging(env_level, console_level=console_level)
    except Exception:
        pass

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
        "completed": command_completed,
        "lookup-batch": command_lookup_batch,
    }

    cmd = commands.get(args.command)
    if cmd is None:
        parser.print_help()
        sys.exit(1)
    cmd(args)


if __name__ == "__main__":
    main()
