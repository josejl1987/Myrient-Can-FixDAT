# Archive.org Download Source — Design

**Date:** 2026-07-03
**Status:** Approved
**Scope:** Parallel download source (archive.org) in match review, with torrent-or-HTTP download routing

## Problem

Minerva Can FixDAT downloads ROMs exclusively from the local Minerva torrent
index via qBittorrent. When a DAT/fix-report entry has no match in the index,
the only recourse is the "CDRomance" external browser link — no in-app
download path exists for alternative sources.

Archive.org hosts large ROM preservation collections (No-Intro, Redump,
software libraries) with both torrent and HTTP download availability.
Adding it as a **parallel candidate source** in the match review screen
gives users in-app download access to files the Minerva index doesn't
cover.

The current pipeline is monolithic to Minerva torrents:

```
DAT entry → match_dat_detailed() [local SQLite] → candidate{file_id}
→ DownloadController → DownloadFileSpec{torrent_path, qbit_file_index}
→ qBittorrent selective download
```

Archive.org breaks three assumptions:
1. **Candidates** come from a network API, not the local index — no `file_id`.
2. **File specs** point to an HTTP URL or an archive.org torrent, not a
   local `.torrent` file.
3. **Download routing** must choose between torrent (via qBittorrent) and
   HTTP (direct stream), depending on torrent availability and seeders.

## Goals

1. Show archive.org candidates alongside Minerva candidates in the match
   review screen, tagged with their source, so the user picks per-file
   which source to use.
2. Download from archive.org via torrent when available/seeded, falling
   back to HTTP direct download otherwise.
3. Search archive.org broadly by title, then boost/rank results from
   known preservation collections.

## Non-goals (YAGNI)

- A standalone archive.org browser page (separate from DAT matching).
- Indexing archive.org into the local SQLite FTS5 index.
- Uploading/seeding back to archive.org.
- Supporting archive.org items that require authentication (loans, etc.).
- Parallel multi-source download of the same file.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Match Review Page                                          │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ CandidateProvider (Protocol)                          │  │
│  │  ├─ MinervaCandidateProvider (wraps match_dat_detailed)│ │
│  │  └─ ArchiveOrgCandidateProvider (wraps search API)    │  │
│  │         ↓ merged candidates (tagged with source)      │  │
│  └──────────────────────────────────────────────────────┘  │
│         ↓ user selects a candidate                          │
│  DownloadController.add_to_queue(source, source_ref, ...)   │
│         ↓ routes by source                                  │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ DownloadSource dispatcher                             │  │
│  │  ├─ MINERVA_TORRENT → existing qBittorrent path      │  │
│  │  ├─ ARCHIVE_ORG_TORRENT → fetch .torrent → qBittorrent│  │
│  │  └─ ARCHIVE_ORG_HTTP → HttpDownloadAdapter (stream)  │  │
│  └──────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

## Components

### 1. DownloadSource enum + Candidate dataclass

**New file:** `minerva/domain/sources.py`

```python
class DownloadSource(str, enum.Enum):
    MINERVA_TORRENT = "minerva_torrent"
    ARCHIVE_ORG_TORRENT = "archive_org_torrent"
    ARCHIVE_ORG_HTTP = "archive_org_http"

@dataclass(frozen=True, slots=True)
class Candidate:
    """Unified candidate from any source."""
    title: str
    size: int
    confidence: float
    method: str          # "exact" | "fuzzy" | "archive_org_search"
    source: DownloadSource
    source_ref: str      # file_id for Minerva, "identifier/filename" for archive.org
    collection: str = ""
    system: str = ""
    regions: tuple[str, ...] = ()
    reasons: list[str] = field(default_factory=list)
    seeders: int | None = None       # archive.org torrent seeders
    torrent_url: str | None = None   # archive.org torrent URL
```

### 2. CandidateProvider protocol

**New file:** `minerva/domain/sources.py` (same file)

```python
@runtime_checkable
class CandidateProvider(Protocol):
    def search(self, entry: DatEntry, system: str | None) -> list[Candidate]: ...
```

The existing `match_dat_detailed()` becomes the Minerva provider's
implementation. The match review screen queries both providers and merges
candidates, tagged with source.

### 3. ArchiveOrgSearchClient

**New file:** `minerva/services/archive_org.py`

Uses the official `internetarchive` Python library (v5.10.1, added as a
project dependency via `uv add internetarchive`). The library wraps
archive.org's three HTTP APIs (advancedsearch.php, scrape API, metadata
API) and handles URL encoding, pagination, and retries internally.

**Phase 1 — item search** via `search_items`:
```python
from internetarchive import search_items

# Lucene syntax: title, collection, mediatype, format filters
results = search_items(
    f'title:"{entry_title}" AND mediatype:data',
    fields=["identifier", "title", "collection", "downloads"],
)
for r in results:
    identifier = r["identifier"]
    title = r.get("title", "")
```

**Phase 2 — file metadata** via `get_item`:
```python
from internetarchive import get_item

item = get_item(identifier)
rom_files = [
    f for f in item.files
    if _is_rom_file(f["name"])  # .zip, .7z, .chd, .iso, .nes, .gb, etc.
]
# Each file dict: name, size, format, md5, crc32, sha1, source
# Download URL: https://archive.org/download/<identifier>/<filename>
```

Apply the same normalization pipeline (`stem_from_romname`) to score
archive.org file names against the DAT entry.

**Collection boosting:** Known preservation collections get a confidence
boost:
- `no-intro`, `no_intro` → +0.05
- `redump` → +0.05
- `*-chd-zstd-redump` → +0.05
- `softwarelibrary_*` → +0.02

**Torrent detection:** Check the item's file list for `*_archive.torrent`
entries. Archive.org generates these for some items but not all (verified:
psx-ntsc-chd-zstd has 0 torrent files). When no torrent is available,
the candidate is tagged `ARCHIVE_ORG_HTTP`. The item's `downloads` count
serves as a popularity proxy.

**Caching:** LRU cache with 5-minute TTL per query (reuse the existing
`minerva_db.LRUCache` pattern). Identifiers cached independently.

### 4. ArchiveOrgCandidateProvider

**New file:** `minerva/services/archive_org.py` (same file)

Implements `CandidateProvider`. Given a `DatEntry`:
1. Searches archive.org via `ArchiveOrgSearchClient`.
2. For each item, finds the best-matching file (normalized title score).
3. Checks torrent availability + seeders.
4. Returns `Candidate` list with `source` set to `ARCHIVE_ORG_TORRENT`
   (if torrent available + seeders > 0) or `ARCHIVE_ORG_HTTP`.

### 5. HttpDownloadAdapter

**New file:** `minerva/services/http_download.py`

Streams files via `requests.get(url, stream=True)` with chunked writes
to the destination path. Reports progress through the same
`DownloadRuntime` telemetry (bytes downloaded, speed, ETA). Runs in
`QThreadPool` via the existing `_FunctionTask` mechanism.

Archive.org download URL format:
`https://archive.org/download/<identifier>/<filename>`

**Rate limiting:** Max 2 concurrent HTTP downloads. Retry with
exponential backoff on HTTP 429 (Too Many Requests).

**Progress reporting:** Updates `DownloadRuntime` every 500ms (or every
1MB, whichever comes first) to avoid flooding the signal bus.

### 6. Schema migration (state DB)

The state DB (`minerva_state.py`) uses additive `ALTER TABLE ADD COLUMN`
migrations via `_migrate_legacy_columns`. Add a new migration step:

```python
def _migrate_add_source_columns(self, c: sqlite3.Connection) -> None:
    """Add source and source_ref columns to download_queue."""
    existing = {r[1] for r in c.execute("PRAGMA table_info(download_queue)").fetchall()}
    if "source" not in existing:
        c.execute(
            "ALTER TABLE download_queue "
            "ADD COLUMN source TEXT NOT NULL DEFAULT 'minerva_torrent'"
        )
    if "source_ref" not in existing:
        c.execute(
            "ALTER TABLE download_queue ADD COLUMN source_ref TEXT"
        )
```

Existing records auto-migrate to `source='minerva_torrent'`,
`source_ref=NULL` (resolved from `file_id` at read time, as today).

### 7. QueueRecord changes

`minerva/domain/downloads.py` `QueueRecord` gains two fields:

```python
source: str = DownloadSource.MINERVA_TORRENT.value
source_ref: str | None = None
```

`QUEUE_COLUMNS` in `minerva_state.py` gains `"source"`, `"source_ref"`.
The `_row_to_queue_record` and `save_queue_record` / insert/update SQL
are updated to read/write these columns.

### 8. DownloadController routing

`_schedule_record_submission` gains source-based dispatch:

```python
def _schedule_record_submission(self, record: QueueRecord) -> None:
    if record.source == DownloadSource.MINERVA_TORRENT.value:
        self._submit_torrent(record)                 # existing path
    elif record.source == DownloadSource.ARCHIVE_ORG_TORRENT.value:
        self._submit_archive_org_torrent(record)     # fetch .torrent, then qbit
    elif record.source == DownloadSource.ARCHIVE_ORG_HTTP.value:
        self._submit_http(record)                    # HttpDownloadAdapter
```

**`_submit_archive_org_torrent`:** Downloads the archive.org `.torrent`
file to a temp dir, then adds it to qBittorrent via the existing
`add_torrent_paused` → `set_file_priority` → `resume` flow. The
`source_ref` carries `identifier/filename` to locate the right file
within the torrent.

**`_submit_http`:** Delegates to `HttpDownloadAdapter`. The `source_ref`
carries `identifier/filename` to build the download URL.

### 9. Match review UI changes

`MatchDetailPanel._build_candidates_html` queries both providers and
merges results:

```python
candidates = []
minerva_candidates = MinervaCandidateProvider(db).search(dat, system)
ao_candidates = archive_org_provider.search(dat, system)  # async, cached
candidates = minerva_candidates + ao_candidates
candidates.sort(key=lambda c: -c.confidence)
```

The candidates table gains a **Source** column showing a badge:
- `Minerva` (blue) — local torrent
- `archive.org 🌐` (green) — torrent download
- `archive.org ⬇` (orange) — HTTP download

The "Approve" action sets `selected_file_id` for Minerva candidates (existing
behavior) or records `source` + `source_ref` for archive.org candidates
before enqueuing.

**Async loading:** Archive.org search runs in a background thread
(`_FunctionTask`) to avoid blocking the UI. The candidates list shows
"Searching archive.org…" until results arrive, then updates in place.

## Error handling

- **Archive.org API timeout/unreachable:** Candidates list shows
  "Archive.org unavailable" in place of archive.org candidates; Minerva
  candidates still display normally. No blocking error.
- **HTTP download failure (network drop, 404):** Record marked `FAILED`
  with error message in `QueueRecord.error`. Retryable via existing
  `retry()` — which re-runs `_schedule_record_submission`.
- **Torrent fetch failure (archive.org .torrent 404):** Falls back to
  HTTP automatically. Logged as an activity event.
- **Rate limiting (429):** `HttpDownloadAdapter` retries with exponential
  backoff (1s, 2s, 4s, 8s, max 16s). After 5 attempts, marks `FAILED`
  with "Rate limited by archive.org, retry later."
- **Disk full during HTTP stream:** Standard `OSError` → `FAILED` with
  the OS error message.

## Testing

- **Unit (no Qt):**
  - `ArchiveOrgSearchClient` with mocked `internetarchive` calls (fixture
    JSON from real archive.org API responses — `search_items` and
    `get_item` monkeypatched).
  - `ArchiveOrgCandidateProvider` scoring: title normalization, collection
    boost, torrent-vs-HTTP source assignment.
  - Schema migration: add columns on existing v3 DB (test with a DB
    fixture that has the old schema).
  - `QueueRecord` round-trips `source` / `source_ref` through state DB.
  - `HttpDownloadAdapter` progress reporting (mocked `requests.get`
    stream).

- **Integration (pytest-qt):**
  - `DownloadController` routes by source: Minerva → qbit mock,
    ARCHIVE_ORG_TORRENT → torrent fetch mock → qbit,
    ARCHIVE_ORG_HTTP → requests mock.
  - Match review shows merged candidates with source badges.
  - Archive.org unavailability doesn't break Minerva candidate display.

- **Regression:**
  - Existing Minerva torrent download path unchanged.
  - Existing queue records (pre-migration) still work.
  - `match_dat_detailed` output shape unchanged (wrapped, not modified).

## Deliverable

Implementation plan (via writing-plans skill) covering:
1. `minerva/domain/sources.py` — `DownloadSource`, `Candidate`,
   `CandidateProvider` protocol.
2. `minerva/services/archive_org.py` — `ArchiveOrgSearchClient`,
   `ArchiveOrgCandidateProvider`.
3. `minerva/services/http_download.py` — `HttpDownloadAdapter`.
4. `minerva_state.py` — schema migration, `QUEUE_COLUMNS`, row
   mapping, insert/update SQL.
5. `minerva/domain/downloads.py` — `QueueRecord` new fields.
6. `minerva/app/download_controller.py` — source-based dispatch in
   `_schedule_record_submission`, new `_submit_archive_org_torrent` and
   `_submit_http` methods.
7. `minerva/ui/widgets/match_detail_panel.py` — merged candidates with
   source badges, async archive.org search.
8. Tests for each component above.
