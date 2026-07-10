"""
Report Acquisition Service — single source of truth for the report-to-queue workflow.

Owns: scope inference, entry classification, queue construction, duplicate
exclusion, destination generation, status updates.  Qt-agnostic — operates
on domain types and talks to ``MinervaDB`` / ``MinervaState``.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from minerva.domain.downloads import DownloadControllerProtocol
from minerva.domain.reports import (
    AcquisitionConstraints,
    AcquisitionPlan,
    AcquisitionSummary,
    EmptyReportError,
    FolderImportSummary,
    MatchPolicy,
    QueueItemSpec,
    QueueResult,
    ReportOutcome,
    ReportScope,
    ReportSummary,
    ResolutionState,
    ReviewEntry,
    ScopeInferenceRequired,
    is_approved,
)
from minerva.domain.sources import DownloadSource
from minerva.parsers.csv_parser import parse_rv_fix_csv
from minerva.parsers.dat_parser import DatEntry, parse_dat_file
from minerva_db import MinervaDB
from minerva_state import MinervaState

log = logging.getLogger(__name__)

# ROMM slug map + destination path logic moved to minerva.romm.platforms/paths.
# Re-exported here for backward compatibility (tests, reports.py import from here).
from minerva.romm.paths import romm_destination  # noqa: E402
from minerva.romm.platforms import romm_slug_for, system_to_romm_slug  # noqa: E402

# Scope inference moved to minerva.romm.scope.
from minerva.romm.scope import (  # noqa: E402
    infer_scope_from_csv as _infer_scope_from_csv,
    infer_scope_from_path as _infer_scope_from_path,
    infer_scope_from_filename as _infer_scope_from_filename,
    infer_scope_from_system_slug as _infer_scope_from_system_slug,
    infer_scope_from_distribution as _infer_scope_from_distribution,
)

# ── Service ──────────────────────────────────────────────────────────────────


class ReportAcquisitionService:
    """Single source of truth for report → queue workflow.

    Owns: scope inference, classification, queue construction, duplicate
    exclusion, destination generation, status updates.
    """

    def __init__(
        self,
        *,
        db: MinervaDB | None = None,
        state: MinervaState | None = None,
        policy: MatchPolicy = MatchPolicy(),
        download_controller: DownloadControllerProtocol | None = None,
        romresolve_policy: object | None = None,
        output_dir: str | Path | None = None,
        source: str = "minerva_torrent",
        source_ref: str | None = None,
    ) -> None:
        self._db = db or MinervaDB()
        self._state = state or MinervaState()
        self._policy = policy
        self._download_controller: DownloadControllerProtocol | None = download_controller
        self._output_dir = Path(output_dir) if output_dir is not None else None
        self._source = source
        self._source_ref = source_ref
        if romresolve_policy is not None:
            self._romresolve_policy = romresolve_policy
        else:
            self._romresolve_policy = self._auto_load_romresolve_policy()

    @classmethod
    def from_parts(
        cls,
        *,
        db: MinervaDB,
        state: MinervaState,
        policy: MatchPolicy = MatchPolicy(),
        download_controller: DownloadControllerProtocol | None = None,
        romresolve_policy: object | None = None,
        output_dir: str | Path | None = None,
        source: str = "minerva_torrent",
        source_ref: str | None = None,
    ) -> ReportAcquisitionService:
        """Create a service using fully-initialized, injected dependencies.

        Use this in tests and harness code that has already opened its own
        MinervaDB/MinervaState instances and wants to avoid the implicit I/O
        performed by the default constructor.
        """
        instance = cls.__new__(cls)
        instance._db = db
        instance._state = state
        instance._policy = policy
        instance._download_controller = download_controller
        instance._output_dir = Path(output_dir) if output_dir is not None else None
        instance._source = source
        instance._source_ref = source_ref
        instance._romresolve_policy = romresolve_policy or instance._auto_load_romresolve_policy()
        return instance
    @staticmethod
    def _auto_load_romresolve_policy() -> object | None:
        """Try to load a romresolve policy from known locations."""
        import importlib

        try:
            from romresolve.policy import load_policy
        except ImportError:
            return None

        from pathlib import Path

        candidates = [
            Path(".romresolve/policies/policy.translated-en.yaml"),
            Path("romresolve/examples/policy.translated-en.yaml"),
        ]
        # Try relative to the romresolve package
        try:
            mod = importlib.import_module("romresolve")
            if mod.__file__:
                pkg_root = Path(mod.__file__).resolve().parent.parent.parent
                candidates.append(pkg_root / "examples" / "policy.translated-en.yaml")
        except Exception:
            log.debug("_auto_load_romresolve_policy: romresolve package not found")

        for path in candidates:
            if path.is_file():
                try:
                    p = load_policy(path)
                    log.info("Auto-loaded romresolve policy: %s from %s", p.profile_name, path)
                    return p
                except Exception:
                    log.warning("_auto_load_romresolve_policy: failed to load policy from %s", path, exc_info=True)
                    continue

        # Fallback: build a default in code
        try:
            from romresolve.policy import PolicyDocument

            p = PolicyDocument(
                schema=1,
                profile_id="default",
                profile_name="Default (auto)",
                extends=None,
                languages_preferred=("en",),
                languages_fallback=("en",),
                require_full_translation=False,
                regions_order=("World", "USA", "Europe", "Japan"),
                prefer_ntsc=True,
                content_exclude=("demo", "sample", "kiosk", "preview", "trade_demo", "beta", "prototype", "aftermarket"),
                preproduction=tuple(),
                replace_untranslated_original=False,
                allow_partial=True,
                prefer_latest_stable=True,
                enhancements=tuple(),
            )
            log.info("Using built-in default romresolve policy")
            return p
        except Exception:
            log.info("_auto_load_romresolve_policy: romresolve PolicyDocument unavailable, classification will be limited")
            return None

    # ── Scope inference ─────────────────────────────────────────────────── ───────────────────────────────────────────────────

    def infer_scope(self, path: Path) -> ReportScope:
        """Infer report scope from the file.  Raises ScopeInferenceRequired
        if no scope can be determined.
        """
        # 1. CSV columns
        if path.suffix.lower() == ".csv":
            scope = _infer_scope_from_csv(path)
            if scope is not None:
                return scope

        # 2. Path regex
        scope = _infer_scope_from_path(path)
        if scope is not None:
            return scope

        # 3. Filename pattern
        scope = _infer_scope_from_filename(path.stem)
        if scope is not None:
            return scope

        # 3b. Fast filename-to-system lookup from the index
        scope = _infer_scope_from_system_slug(path.stem)
        if scope is not None:
            return scope

        # 4. Candidate distribution — needs to parse entries first
        info = (
            parse_rv_fix_csv(path)
            if path.suffix.lower() == ".csv"
            else parse_dat_file(path)
        )
        if info.entries:
            scope = _infer_scope_from_distribution(list(info.entries), db=self._db)
            if scope is not None:
                return scope
            # 5. Raise with candidates
            db = self._db or MinervaDB()
            observed: dict[tuple[str | None, str | None], int] = {}
            for entry in info.entries[:30]:
                best, _second = db.get_two_best_candidates(entry)
                if best is not None:
                    key = (best["collection"], best["system"])
                    observed[key] = observed.get(key, 0) + 1
            candidates = sorted(
                [(c, s, count) for (c, s), count in observed.items()],
                key=lambda x: -x[2],
            )[:5]
            if candidates:
                raise ScopeInferenceRequired(candidates)

        raise ScopeInferenceRequired([])

    # ── Import ─────────────────────────────────────────────────────────────

    def import_report(
        self,
        path: Path,
        scope: ReportScope | None = None,
    ) -> ReportSummary:
        """Parse the file, infer scope, persist the report.

        If scope is None and inference is ambiguous, raise
        ScopeInferenceRequired carrying the candidates so the page
        can prompt once.
        """
        info = (
            parse_rv_fix_csv(path)
            if path.suffix.lower() == ".csv"
            else parse_dat_file(path)
        )
        if not info.entries:
            raise EmptyReportError(path)

        # Resolve scope — infer_scope returns ReportScope (never None)
        resolved_scope: ReportScope = scope if scope is not None else self.infer_scope(path)

        existing = self._state.get_report_by_path(path)
        report_id = existing.id if existing else uuid.uuid4().hex

        report = ReportSummary(
            id=report_id,
            path=str(path),
            name=info.name or path.stem,
            collection=resolved_scope.collection or info.collection,
            system=resolved_scope.system or info.system,
            imported_at=datetime.now(timezone.utc).isoformat(),
            requested_count=len(info.entries),
            status="draft",
        )

        if existing:
            self._state.update_report(
                report.id,
                path=report.path,
                name=report.name,
                collection=report.collection,
                system=report.system,
                requested_count=report.requested_count,
                status="draft",
            )
        else:
            self._state.save_report(report)

        entries = [
            ReviewEntry(
                id=f"{report.id}_{i}",
                report_id=report.id,
                ordinal=i,
                filename=entry.filename,
                size=entry.size,
            )
            for i, entry in enumerate(info.entries)
        ]
        self._state.replace_entries(report.id, entries)
        return report

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
        imported = skipped = failed = 0
        for path in ReportAcquisitionService.collect_fixdat_files(root):
            try:
                report = service.import_report(path)
            except ScopeInferenceRequired:
                log.info("import_folder: skip %s (scope ambiguous)", path)
                skipped += 1
                continue
            except EmptyReportError:
                log.info("import_folder: skip %s (empty)", path)
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

    def _classify(
        self,
        entry: DatEntry,
        scope: ReportScope,
        policy: MatchPolicy,
        conn: object | None = None,
    ) -> tuple[ResolutionState, int | None, str | None, float | None]:
        """Classify a single entry using margin-based classification.

        Returns (resolution, file_id, method, confidence).
        """
        collection = scope.collection or ""
        system = scope.system or ""

        best, second = self._db.get_two_best_candidates(
            entry, collection=collection, system=system, conn=conn,
        )

        # No candidate
        if best is None:
            return ResolutionState.NOT_FOUND, None, None, None

        # Below absolute confidence floor
        if best["confidence"] < 0.5:
            return ResolutionState.NOT_FOUND, best["file_id"], best["method"], best["confidence"]

        # Cross-system guard
        if policy.require_same_collection and collection:
            if best["collection"] != collection:
                return ResolutionState.NOT_FOUND, best["file_id"], best["method"], best["confidence"]
        if policy.require_same_system and system:
            if best["system"] != system:
                # When scope's system is known, a different-system candidate is NOT_FOUND
                return ResolutionState.NOT_FOUND, best["file_id"], best["method"], best["confidence"]

        # Exact match auto-accept
        if best["method"] == "exact" and policy.auto_accept_exact:
            return ResolutionState.READY, best["file_id"], best["method"], best["confidence"]

        # Margin-based fuzzy classification
        if best["confidence"] >= policy.fuzzy_min_confidence:
            if second is None or (best["confidence"] - second["confidence"]) >= policy.fuzzy_min_margin:
                return ResolutionState.READY, best["file_id"], best["method"], best["confidence"]

        # Everything else needs review
        return ResolutionState.REVIEW_REQUIRED, best["file_id"], best["method"], best["confidence"]

    def _try_romresolve(
        self,
        dat_entry: DatEntry,
        scope: ReportScope | None,
    ) -> int | None:
        """Try romresolve region/content scoring to disambiguate candidates.

        Returns the best file_id, or None if romresolve can't help.
        """
        if self._romresolve_policy is None:
            return None

        from romresolve.minerva import CandidateInfo, score_candidates

        collection = scope.collection or "" if scope else ""
        system = scope.system or "" if scope else ""

        detailed = self._db.match_dat_detailed(
            [dat_entry], collection=collection, system=system,
            candidate_limit=10,
        )

        if not detailed.get("results"):
            return None

        per_entry = detailed["results"][0]
        scored = per_entry.get("candidates", [])

        if not scored:
            return None

        if len(scored) == 1:
            return scored[0]["file_id"]

        candidates = [
            CandidateInfo(file_id=c["file_id"], basename=c["title"])
            for c in scored
        ]
        file_ids = [c.file_id for c in candidates]
        regions = self._db.get_file_regions(file_ids)
        tags = self._db.get_file_tags(file_ids)

        return score_candidates(candidates, regions, tags, self._romresolve_policy)

    def match_report(
        self,
        report_id: str,
        policy: MatchPolicy | None = None,
    ) -> AcquisitionSummary:
        """Run the matcher on every entry, classify by resolution, persist.

        Returns counts.  Uses replace_entries internally.
        """
        report = self._state.get_report(report_id)
        if report is None:
            raise ValueError(f"Report not found: {report_id}")

        policy = policy or self._policy
        scope = ReportScope(
            collection=report.collection,
            system=report.system,
        )

        # Load the original entries from the report file (or state for JSON)
        path = Path(report.path)
        clean_name = path.name.split("#")[0]
        if clean_name.lower().endswith(".json"):
            saved = self._state.get_entries(report_id)
            dat_entries = [
                DatEntry(filename=e.filename, size=e.size)
                for e in saved
            ]
        else:
            ext = Path(clean_name).suffix.lower()
            info = (
                parse_rv_fix_csv(path)
                if ext == ".csv"
                else parse_dat_file(path)
            )
            dat_entries = list(info.entries)

        entries: list[ReviewEntry] = []
        counts = AcquisitionSummary()
        collection = scope.collection or ""
        system = scope.system or ""
        # Open one SQLite connection for the entire batch — reusing the
        # page cache across entries is ~20x faster than opening per entry.
        import sqlite3
        from minerva_db import DEFAULT_INDEX_PATH, stem_from_romname
        _batch_db_path = str(self._db._path) if self._db is not None else str(DEFAULT_INDEX_PATH)
        _batch_conn = sqlite3.connect(_batch_db_path)
        _batch_conn.row_factory = sqlite3.Row
        _batch_conn.execute("PRAGMA mmap_size = 268435456")
        _batch_conn.execute("PRAGMA temp_store = MEMORY")
        _batch_conn.execute("PRAGMA cache_size = -8000000")
        try:
            # ── Fast path: batch tier-1 exact-stem lookup ───────────────
            # Query all stems in one SQL instead of 60K individual queries.
            stems_to_entries: dict[str, list[int]] = {}
            all_stems: list[str] = []
            for ordinal, dat_entry in enumerate(dat_entries):
                s = stem_from_romname(dat_entry.filename)
                stems_to_entries.setdefault(s, []).append(ordinal)
                all_stems.append(s)

            _batch_conn.execute("CREATE TEMP TABLE _match_stems (stem TEXT)")
            _batch_conn.executemany(
                "INSERT INTO _match_stems VALUES (?)", [(s,) for s in all_stems],
            )
            coll_sql = "AND f.collection = ?" if collection else ""
            sys_sql = "AND f.system = ?" if system else ""
            params = []
            if collection:
                params.append(collection)
            if system:
                params.append(system)
            stem_rows = _batch_conn.execute(
                f"SELECT f.* FROM files f "
                f"INNER JOIN _match_stems s ON f.stem = s.stem "
                f"WHERE 1=1 {coll_sql} {sys_sql} "
                f"ORDER BY f.stem, f.size DESC",
                params,
            ).fetchall()
            _batch_conn.execute("DROP TABLE _match_stems")

            stem_to_files: dict[str, list[sqlite3.Row]] = {}
            for row in stem_rows:
                stem_to_files.setdefault(row["stem"], []).append(row)

            # Classify entries that have exact stem matches (fast path)
            unmatched_ordinals: list[int] = []
            for ordinal, dat_entry in enumerate(dat_entries):
                s = all_stems[ordinal]
                candidates = stem_to_files.get(s, [])
                if candidates:
                    best = candidates[0]
                    confidence = 1.0
                    if dat_entry.size > 0 and best["size"] > 0:
                        size_diff = abs(best["size"] - dat_entry.size) / max(best["size"], 1)
                        if size_diff > 0.1:
                            confidence = 0.92

                    # Apply policy guards (same as _classify)
                    if confidence < 0.5:
                        resolution = ResolutionState.NOT_FOUND
                        decision = "pending"
                    elif policy.require_same_collection and collection and best["collection"] != collection:
                        resolution = ResolutionState.NOT_FOUND
                        decision = "pending"
                    elif policy.require_same_system and system and best["system"] != system:
                        resolution = ResolutionState.NOT_FOUND
                        decision = "pending"
                    elif not policy.auto_accept_exact:
                        resolution = ResolutionState.REVIEW_REQUIRED
                        decision = "pending"
                    else:
                        resolution = ResolutionState.READY
                        decision = "accept"

                    entries.append(ReviewEntry(
                        id=f"{report_id}_{ordinal}",
                        report_id=report_id,
                        ordinal=ordinal,
                        filename=dat_entry.filename,
                        size=dat_entry.size,
                        automatic_file_id=best["id"],
                        automatic_method="exact",
                        automatic_confidence=confidence,
                        resolution=resolution,
                        decision=decision,
                    ))
                    if resolution == ResolutionState.READY:
                        counts = AcquisitionSummary(
                            ready=counts.ready + 1,
                            review_required=counts.review_required,
                            not_found=counts.not_found,
                            ignored=counts.ignored,
                        )
                    elif resolution == ResolutionState.NOT_FOUND:
                        counts = AcquisitionSummary(
                            ready=counts.ready,
                            review_required=counts.review_required,
                            not_found=counts.not_found + 1,
                            ignored=counts.ignored,
                        )
                    else:
                        counts = AcquisitionSummary(
                            ready=counts.ready,
                            review_required=counts.review_required + 1,
                            not_found=counts.not_found,
                            ignored=counts.ignored,
                        )
                else:
                    unmatched_ordinals.append(ordinal)
            # ── Slow path: per-stem FTS5 queries with candidate cap ──────
            # Previous implementation batched all unmatched stems into a temp
            # table and JOINed against files_fts in one query, then fetched
            # ALL matching rows. With OR-joined tokens and no stopword filter,
            # a single stem like "the legend of zelda (usa, europe)" matched
            # ~472K rows; across many unmatched entries the result set grew
            # into millions of rows held in memory simultaneously → OOM.
            #
            # Fix: query each stem individually with a LIMIT cap. Using
            # title_keywords() (stopword-filtered, implicit AND) instead of
            # raw OR-joined tokens cuts match counts by ~3,900x (472K → 120
            # for the Zelda example), and LIMIT bounds the worst case.
            from minerva.matching.scoring import score_candidate, title_keywords
            from minerva_db import stems_match

            _FTS_CANDIDATE_LIMIT = 50

            matches_by_ordinal: dict[int, list[tuple]] = {}
            for ordinal in unmatched_ordinals:
                raw_stem = all_stems[ordinal]
                kws = title_keywords(raw_stem)
                if not kws:
                    matches_by_ordinal[ordinal] = []
                    continue
                fts_query = " ".join(kws)
                try:
                    rows = _batch_conn.execute(
                        "SELECT f.id, f.stem, f.collection, f.system, f.size, "
                        "bm25(files_fts) AS score "
                        "FROM files_fts "
                        "JOIN files f ON f.rowid = files_fts.rowid "
                        "WHERE files_fts MATCH ? "
                        "ORDER BY score "
                        "LIMIT ?",
                        (fts_query, _FTS_CANDIDATE_LIMIT),
                    ).fetchall()
                except Exception:
                    log.warning("FTS5 query failed for stem %r", raw_stem, exc_info=True)
                    rows = []
                matches_by_ordinal[ordinal] = [
                    (r["id"], r["stem"], r["collection"],
                     r["system"], r["size"], r["score"])
                    for r in rows
                ]

            for ordinal in unmatched_ordinals:
                dat_entry = dat_entries[ordinal]
                candidates = matches_by_ordinal.get(ordinal, [])

                if not candidates:
                    entries.append(ReviewEntry(
                        id=f"{report_id}_{ordinal}",
                        report_id=report_id,
                        ordinal=ordinal,
                        filename=dat_entry.filename,
                        size=dat_entry.size,
                        automatic_file_id=None,
                        automatic_method=None,
                        automatic_confidence=None,
                        resolution=ResolutionState.NOT_FOUND,
                        decision="reject",
                    ))
                    counts = AcquisitionSummary(
                        ready=counts.ready,
                        review_required=counts.review_required,
                        not_found=counts.not_found + 1,
                        ignored=counts.ignored,
                    )
                    continue

                # Score candidates and pick best
                best_file_id = None
                best_method = "fuzzy"
                best_confidence = 0.0
                tied_count = 0
                for file_id, file_stem, file_coll, file_sys, file_size, _score in candidates:
                    if not stems_match(all_stems[ordinal], file_stem):
                        continue
                    conf, _reasons = score_candidate(
                        all_stems[ordinal], dat_entry.size,
                        file_stem, file_size, file_coll, file_sys,
                        collection, system,
                    )
                    if conf > best_confidence:
                        best_confidence = conf
                        best_file_id = file_id
                        best_method = "exact" if conf >= 0.96 else "fuzzy"
                        tied_count = 1
                    elif conf == best_confidence:
                        tied_count += 1

                if best_file_id is not None and best_confidence >= 0.5:
                    matched_row = next(
                        (c for c in candidates if c[0] == best_file_id), None
                    )
                    if policy.require_same_system and system and matched_row and matched_row[3] != system:
                        resolution = ResolutionState.NOT_FOUND
                    elif tied_count > 1:
                        # Multiple candidates with identical confidence —
                        # let romresolve disambiguate by region/content policy.
                        resolution = ResolutionState.REVIEW_REQUIRED
                    else:
                        resolution = ResolutionState.READY if best_confidence >= policy.fuzzy_min_confidence else ResolutionState.REVIEW_REQUIRED
                    file_id = best_file_id
                    method = best_method
                    confidence = best_confidence
                else:
                    resolution = ResolutionState.NOT_FOUND
                    file_id = None
                    method = None
                    confidence = None

                if resolution == ResolutionState.REVIEW_REQUIRED:
                    romresolve_id = self._try_romresolve(dat_entry, scope)
                    if romresolve_id is not None:
                        resolution = ResolutionState.READY
                        file_id = romresolve_id
                        method = "romresolve"
                        confidence = 1.0

                if resolution == ResolutionState.READY:
                    decision = "accept"
                elif resolution == ResolutionState.REVIEW_REQUIRED:
                    decision = "pending"
                else:
                    decision = "reject"

                if resolution == ResolutionState.READY:
                    counts = AcquisitionSummary(
                        ready=counts.ready + 1,
                        review_required=counts.review_required,
                        not_found=counts.not_found,
                        ignored=counts.ignored,
                    )
                elif resolution == ResolutionState.REVIEW_REQUIRED:
                    counts = AcquisitionSummary(
                        ready=counts.ready,
                        review_required=counts.review_required + 1,
                        not_found=counts.not_found,
                        ignored=counts.ignored,
                    )
                elif resolution == ResolutionState.NOT_FOUND:
                    counts = AcquisitionSummary(
                        ready=counts.ready,
                        review_required=counts.review_required,
                        not_found=counts.not_found + 1,
                        ignored=counts.ignored,
                    )

                entries.append(ReviewEntry(
                    id=f"{report_id}_{ordinal}",
                    report_id=report_id,
                    ordinal=ordinal,
                    filename=dat_entry.filename,
                    size=dat_entry.size,
                    automatic_file_id=file_id,
                    automatic_method=method,
                    automatic_confidence=confidence,
                    resolution=resolution,
                    decision=decision,
                ))
        finally:
            _batch_conn.close()

        # Atomically replace entries
        self._state.replace_entries(report_id, entries)

        # Update report summary
        self._state.update_report(
            report_id,
            ready_count=counts.ready,
            review_required_count=counts.review_required,
            not_found_count=counts.not_found,
            status="ready",
        )

        self._state.add_event(
            "match",
            f"Matched report {report_id}: {counts.ready} ready, "
            f"{counts.review_required} review, {counts.not_found} not found",
        )

        return counts

    # ── Queue construction ─────────────────────────────────────────────────

    def _enqueue_file(
        self,
        file_id: int,
        destination: str,
        report_entry_id: str | None,
        source: str = "minerva_torrent",
        source_ref: str | None = None,
    ) -> bool:
        """Enqueue one file — routes through the download controller when
        available, falls back to direct DB write otherwise.

        Returns True if the file was successfully enqueued.
        """
        if self._download_controller is not None:
            record_id = self._download_controller.add_to_queue(
                file_id, destination, report_entry_id,
                source=source, source_ref=source_ref,
            )
            if report_entry_id is not None:
                self._state.update_entry_decision(report_entry_id, "accept", file_id)
            else:
                log.warning(
                    "_enqueue_file: report_entry_id is None for file_id=%d; "
                    "skipping entry decision update",
                    file_id,
                )
            return bool(record_id)

        # Fallback: write directly to DB (no native torrent engine submission,
        # no queue_changed signal).
        log.warning(
            "_enqueue_file: download_controller unavailable, writing "
            "queue record to DB without native torrent engine submission "
            "(file_id=%d)", file_id,
        )
        from minerva.domain.downloads import QueueRecord as QR

        record_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        record = QR(
            id=record_id,
            file_id=file_id,
            report_entry_id=report_entry_id,
            status="queued",
            destination=destination,
            created_at=now,
            updated_at=now,
            source=source,
            source_ref=source_ref,
        )
        self._state.save_queue_record(record)
        if report_entry_id is not None:
            self._state.update_entry_decision(report_entry_id, "accept", file_id)
        else:
            log.warning(
                "_enqueue_file: report_entry_id is None for file_id=%d; "
                "skipping entry decision update",
                file_id,
            )
        return True
    def _build_queue_specs(
        self,
        entries: list[ReviewEntry],
    ) -> list[QueueItemSpec]:
        """Convert approved entries into source-aware queue specs."""
        if not entries:
            return []

        output_root = self._output_dir if self._output_dir is not None else Path("downloads")

        # Batch resolve Minerva file metadata
        minerva_entries = [
            e for e in entries
            if e.selected_source == DownloadSource.MINERVA_TORRENT.value
        ]
        file_ids = [
            e.selected_file_id or e.automatic_file_id
            for e in minerva_entries
            if (e.selected_file_id or e.automatic_file_id) is not None
        ]
        file_map: dict[int, Any] = {}
        if file_ids:
            db = self._db or MinervaDB()
            file_map = {f.id: f for f in db.get_files_by_ids(file_ids)}

        # Batch resolve report systems for archive.org destinations
        report_ids = {e.report_id for e in entries if e.report_id}
        report_map: dict[str, str] = {}
        if report_ids:
            for rid in report_ids:
                report = self._state.get_report(rid)
                if report is not None:
                    report_map[rid] = report.system or ""

        specs: list[QueueItemSpec] = []
        for entry in entries:
            source_value = entry.selected_source or DownloadSource.MINERVA_TORRENT.value
            source = DownloadSource(source_value)

            if source == DownloadSource.MINERVA_TORRENT:
                file_id = entry.selected_file_id or entry.automatic_file_id
                if file_id is None:
                    continue
                item = file_map.get(file_id)
                if item is None:
                    continue
                destination = romm_destination(output_root, item.system, item.basename)
                specs.append(QueueItemSpec(
                    report_entry_id=entry.id,
                    source=source,
                    source_identity=None,
                    local_file_id=file_id,
                    destination=destination,
                    report_id=entry.report_id,
                ))
            else:
                source_ref = entry.selected_source_ref
                if source_ref is None:
                    continue
                basename = Path(source_ref).name
                system = report_map.get(entry.report_id, "")
                destination = romm_destination(output_root, system, basename)
                expected_size = entry.size if entry.size else None
                torrent_url: str | None = None
                torrent_member_path: str | None = None
                if source == DownloadSource.ARCHIVE_ORG_TORRENT:
                    identifier = source_ref.split("/", 1)[0]
                    torrent_url = f"https://archive.org/download/{identifier}/{identifier}_archive.torrent"
                    torrent_member_path = source_ref.split("/", 1)[1] if "/" in source_ref else basename
                specs.append(QueueItemSpec(
                    report_entry_id=entry.id,
                    source=source,
                    source_identity=source_ref,
                    local_file_id=0,
                    destination=destination,
                    report_id=entry.report_id,
                    expected_size=expected_size,
                    torrent_url=torrent_url,
                    torrent_member_path=torrent_member_path,
                ))
        return specs

    def _enqueue_spec(self, spec: QueueItemSpec) -> bool:
        """Enqueue one item via the download controller with full source metadata."""
        destination = str(spec.destination) if spec.destination is not None else ""
        if self._download_controller is not None:
            record_id = self._download_controller.add_to_queue(
                file_id=spec.local_file_id or 0,
                destination=destination,
                report_entry_id=spec.report_entry_id,
                source=spec.source.value,
                source_ref=spec.source_identity,
                report_id=spec.report_id,
                expected_size=spec.expected_size,
                expected_hash=spec.expected_hash,
                torrent_url=spec.torrent_url,
                torrent_member_path=spec.torrent_member_path,
            )
            if record_id is None:
                return False
            return True

        # Fallback: write directly to DB (no controller submission).
        log.warning(
            "_enqueue_spec: download_controller unavailable, writing "
            "queue record to DB without submission (source=%s)",
            spec.source.value,
        )
        from minerva.domain.downloads import QueueRecord as QR
        record_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        record = QR(
            id=record_id,
            file_id=spec.local_file_id or 0,
            report_entry_id=spec.report_entry_id,
            report_id=spec.report_id,
            status="queued",
            destination=destination,
            created_at=now,
            updated_at=now,
            source=spec.source.value,
            source_ref=spec.source_identity,
            expected_size=spec.expected_size,
            expected_hash=spec.expected_hash,
            torrent_url=spec.torrent_url,
            torrent_member_path=spec.torrent_member_path,
        )
        self._state.save_queue_record(record)
        return True

    def queue_entries(
        self,
        entries: list[ReviewEntry],
        *,
        include_reviewed: bool = True,
    ) -> QueueResult:
        """Queue approved entries from any source.

        Single canonical path for all UI routes. Converts each approved
        ReviewEntry into a source-aware queue record.
        """
        queueable = [e for e in entries if is_approved(e)]
        if not queueable:
            return QueueResult(
                added=0, skipped_active=0, skipped_complete=0, skipped_missing=0
            )

        # Build skip sets from existing queue.
        # Minerva records: match by file_id alone (destination may differ
        # on re-queue). Archive.org records: match by full identity tuple
        # since file_id=0 is shared across all AO records.
        queue_records = self._state.list_queue()
        minerva_active_ids: set[int] = set()
        minerva_completed_ids: set[int] = set()
        ao_keys: set[tuple[str, str | None, str]] = set()  # (source, source_ref, destination)
        ao_completed_keys: set[tuple[str, str | None, str]] = set()
        for rec in queue_records:
            is_active = rec.status in {
                "queued", "starting", "downloading", "paused", "seeding",
            }
            is_completed = rec.status == "completed"
            if rec.source == DownloadSource.MINERVA_TORRENT.value:
                if is_active:
                    minerva_active_ids.add(rec.file_id)
                elif is_completed:
                    minerva_completed_ids.add(rec.file_id)
            else:
                key = (rec.source, rec.source_ref, rec.destination)
                if is_active:
                    ao_keys.add(key)
                elif is_completed:
                    ao_completed_keys.add(key)

        specs = self._build_queue_specs(queueable)
        added = 0
        skipped_active = 0
        skipped_complete = 0
        skipped_missing = 0

        for spec in specs:
            if spec.source == DownloadSource.MINERVA_TORRENT:
                fid = spec.local_file_id
                if fid is not None and fid in minerva_active_ids:
                    skipped_active += 1
                    continue
                if fid is not None and fid in minerva_completed_ids:
                    skipped_complete += 1
                    continue
            else:
                key = (spec.source.value, spec.source_identity, str(spec.destination or ""))
                if key in ao_keys:
                    skipped_active += 1
                    continue
                if key in ao_completed_keys:
                    skipped_complete += 1
                    continue
            if self._enqueue_spec(spec):
                added += 1
            else:
                skipped_missing += 1

        return QueueResult(
            added=added,
            skipped_active=skipped_active,
            skipped_complete=skipped_complete,
            skipped_missing=skipped_missing,
        )

    def queue_ready(
        self,
        report_id: str,
        *,
        include_reviewed: bool = True,
    ) -> QueueResult:
        """Build queue records for every READY (and optionally reviewed) entry.

        Idempotent — skips entries already in the active/queued/completed
        state. Routes through the download controller when one is
        available so the controller can emit ``queue_changed`` and
        schedule native torrent engine submission.
        """
        entries = self._state.get_entries(report_id)
        if not entries:
            return QueueResult(
                added=0, skipped_active=0, skipped_complete=0, skipped_missing=0
            )

        # Gather ready and/or reviewed entries
        queueable: list[ReviewEntry] = []
        for entry in entries:
            if entry.resolution == ResolutionState.NOT_FOUND:
                continue
            if entry.resolution == ResolutionState.READY:
                queueable.append(entry)
            elif include_reviewed and is_approved(entry):
                queueable.append(entry)

        if not queueable:
            return QueueResult(
                added=0, skipped_active=0, skipped_complete=0, skipped_missing=0
            )

        return self.queue_entries(queueable, include_reviewed=include_reviewed)

    # ── Triage / outcome ───────────────────────────────────────────────────

    def compute_outcome(
        self,
        report_id: str,
        constraints: AcquisitionConstraints | None = None,
    ) -> ReportOutcome:
        """Compute the triage outcome for a report based on current state.

        Returns one of ``ReportOutcome`` values.
        """
        entries = self._state.get_entries(report_id)
        if not entries:
            return ReportOutcome.EMPTY

        safe = [e for e in entries if e.resolution == ResolutionState.READY]
        ambiguous = [e for e in entries if e.resolution == ResolutionState.REVIEW_REQUIRED]
        not_found = [e for e in entries if e.resolution == ResolutionState.NOT_FOUND]

        # Determine eligible entries by applying constraints
        eligible = self._filter_eligible(safe, constraints)

        if not eligible:
            if safe:
                return ReportOutcome.FILTERED_OUT
            if ambiguous:
                return ReportOutcome.NEEDS_REVIEW
            if not_found and not safe:
                return ReportOutcome.NO_SOURCE_MATCHES
            if self._all_already_present(report_id):
                return ReportOutcome.ALREADY_SATISFIED
            return ReportOutcome.EMPTY

        # Eligible entries exist — check if all are already completed
        if self._all_already_present(report_id):
            return ReportOutcome.ALREADY_SATISFIED

        return ReportOutcome.ACTIONABLE

    def _filter_eligible(
        self,
        safe_entries: list[ReviewEntry],
        constraints: AcquisitionConstraints | None,
    ) -> list[ReviewEntry]:
        """Filter safe entries by size constraints.

        Returns entries that pass all constraint checks.
        """
        if constraints is None:
            return safe_entries
        eligible: list[ReviewEntry] = []
        for entry in safe_entries:
            if constraints.max_file_bytes is not None and entry.size > constraints.max_file_bytes:
                continue
            # No collection/system/region filtering here — that requires
            # DB lookups and is done by the planner. The simple triage
            # check only filters by entry-level constraints.
            eligible.append(entry)
        return eligible

    def _all_already_present(self, report_id: str) -> bool:
        """Check if every entry in the report is already completed."""

        entries = self._state.get_entries(report_id)
        if not entries:
            return False
        queue = self._state.list_queue()
        completed_ids: set[int] = set()
        for rec in queue:
            if rec.status in {"completed", "seeding"}:
                completed_ids.add(rec.file_id)
        for entry in entries:
            file_id = entry.selected_file_id or entry.automatic_file_id
            if file_id is not None and file_id not in completed_ids:
                return False
        return True

    # ── queue_plan ─────────────────────────────────────────────────────────

    def queue_plan(self, plan: AcquisitionPlan) -> QueueResult:
        """Atomically add every PlannedFile in the plan to the download queue.

        The plan is the exact set of records queued — no re-running of
        filters or scope inference at queue time. Routes through the
        download controller when one is available.
        """

        queue_records = self._state.list_queue()
        active_file_ids: set[int] = set()
        for rec in queue_records:
            if rec.status in {"completed", "queued", "starting", "downloading", "paused", "seeding"}:
                active_file_ids.add(rec.file_id)

        added = 0
        skipped_active = 0
        for pf in plan.selected:
            if pf.file_id in active_file_ids:
                skipped_active += 1
                continue

            if self._enqueue_file(pf.file_id, str(pf.destination), pf.report_entry_id, source=self._source, source_ref=self._source_ref):
                added += 1

        self._state.add_event(
            "queue",
            f"Queued {added} ROMs from plan for {plan.report_id}",
        )

        return QueueResult(
            added=added,
            skipped_active=skipped_active,
            skipped_complete=0,
            skipped_missing=0,
        )

    # ── Rematch ────────────────────────────────────────────────────────────

    def rematch_report(
        self,
        report_id: str,
        policy: MatchPolicy | None = None,
    ) -> AcquisitionSummary:
        """Atomically replace entries and reclassify.

        Uses replace_entries() not save_entries().
        """
        return self.match_report(report_id, policy=policy)

    def export_reviewed(self, report_id: str, dest_path: Path) -> int:
        """Write a synthetic DAT of approved entries to *dest_path*.

        Approved = ``decision in {"accept", "fuzzy"}`` and has a
        ``selected_file_id`` or ``automatic_file_id``. Returns the count
        written; writes nothing and returns 0 if no approved entries or
        none resolve in the index.

        Wraps ``MinervaDB.build_synthetic_dat`` — same policy as the
        former ``ReportsPage._export_reviewed`` so UI and CLI share one
        definition of "approved".
        """
        entries = self._state.get_entries(report_id)
        ids = [
            entry.selected_file_id or entry.automatic_file_id
            for entry in entries
            if is_approved(entry)
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

    def queue_all_ready(
        self,
        *,
        name_filter: str = "",
        include_reviewed: bool = True,
    ) -> QueueResult:
        """Queue ready entries from every report matching the name filter.

        Iterates all reports (or those whose name contains *name_filter*)
        and calls ``queue_ready`` on each. Returns the aggregate result.
        """
        reports = self._state.list_reports()
        if name_filter:
            reports = [r for r in reports if name_filter in r.name]
        sub_results = [
            self.queue_ready(r.id, include_reviewed=include_reviewed)
            for r in reports
        ]
        return QueueResult.merge(*sub_results)
