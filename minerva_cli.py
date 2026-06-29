"""Thin CLI for the Minerva Myrient-Can-FixDAT index.

Usage:
    python minerva_cli.py index [--rebuild]
    python minerva_cli.py find <query> [--collection <col>] [--system <sys>] [--limit <n>]
    python minerva_cli.py download <file_id> [--dest <path>]
    python minerva_cli.py stats
    python minerva_cli.py check
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Import only from the production modules
from minerva_db import DEFAULT_INDEX_PATH, DEFAULT_TORRENT_DIR, MinervaDB, build_index
from minerva_qbit import QBittorrentClient

# ── Formatting helpers ────────────────────────────────────────────────────────

def _format_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


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
    print(f"Found {total} results:")
    for row in rows:
        print(f"  {row['stem']:50s} {_format_bytes(row['size']):>10s}  "
              f"{row['collection']:20s} {row['system']}")


def command_download(args: argparse.Namespace) -> None:
    """Submit one selectively-enabled file to qBittorrent."""
    import os

    db = MinervaDB()
    spec = db.get_download_spec(args.file_id, DEFAULT_TORRENT_DIR)
    if spec is None:
        raise SystemExit(f"Unknown file_id: {args.file_id}")
    client = QBittorrentClient(
        os.environ.get("MINERVA_QBIT_URL", "http://localhost:8080"),
        os.environ.get("MINERVA_QBIT_USER", "admin"),
        os.environ.get("MINERVA_QBIT_PASS", ""),
    )
    client.login()
    torrent_hash = client.add_torrent_paused(str(spec.torrent_path), str(Path(args.dest).resolve()))
    files = client.get_files(torrent_hash)
    all_indices = [int(item.get("index", -1)) for item in files if item.get("index") is not None]
    client.set_file_priority(torrent_hash, all_indices, 0)
    client.set_file_priority(torrent_hash, [spec.select_index - 1], 1)
    client.resume(torrent_hash)
    print(f"Queued {spec.basename} ({torrent_hash}) → {Path(args.dest).resolve()}")


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
# ── CLI entrypoint ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Minerva CLI")
    sub = parser.add_subparsers(dest="command")

    p_index = sub.add_parser("index", help="Build/rebuild index")
    p_index.add_argument("--rebuild", action="store_true")

    p_find = sub.add_parser("find", help="Search library")
    p_find.add_argument("query")
    p_find.add_argument("--collection")
    p_find.add_argument("--system")
    p_find.add_argument("--limit", type=int, default=20)

    p_dl = sub.add_parser("download", help="Download file")
    p_dl.add_argument("file_id", type=int)
    p_dl.add_argument("--dest", default="downloads")

    sub.add_parser("stats", help="Index statistics")
    sub.add_parser("check", help="Integrity check")

    p_bi = sub.add_parser("batch-import", help="Import fixdat files from a folder")
    p_bi.add_argument("directory")

    p_bq = sub.add_parser("batch-queue", help="Queue ready entries from all reports")
    p_bq.add_argument("--filter", default="", help="Filter reports by name substring")
    p_bq.add_argument("--no-reviewed", action="store_true", help="Exclude reviewed entries")
    p_bq.add_argument("--output-dir", default="downloads", help="Destination root")

    p_br = sub.add_parser("batch-rematch", help="Re-run matching for reports")
    p_br.add_argument("report_ids", nargs="+")

    sub.add_parser("batch-list", help="List imported reports")

    args = parser.parse_args()

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

    cmd = commands.get(args.command)
    if cmd is None:
        parser.print_help()
        sys.exit(1)
    cmd(args)


if __name__ == "__main__":
    main()
