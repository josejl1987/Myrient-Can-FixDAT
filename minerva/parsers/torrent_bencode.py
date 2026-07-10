"""Pure-Python bencode decoder and torrent file parser.

Extracted from ``minerva_db.py`` — these are pure functions with no
database dependency.
"""

from __future__ import annotations

import re
from pathlib import Path

_MAX_DEPTH = 100


def bdecode(data: bytes, idx: int = 0, depth: int = 0) -> tuple:
    """Decode bencoded data. Returns (value, next_index)."""
    if depth > _MAX_DEPTH:
        raise ValueError(f"bencode nesting exceeds {_MAX_DEPTH} levels")
    c = data[idx:idx + 1]
    if c == b'd':
        idx += 1
        d: dict = {}
        while data[idx:idx + 1] != b'e':
            k, idx = bdecode(data, idx, depth + 1)
            v, idx = bdecode(data, idx, depth + 1)
            d[k] = v
        return d, idx + 1
    if c == b'l':
        idx += 1
        lst: list = []
        while data[idx:idx + 1] != b'e':
            v, idx = bdecode(data, idx, depth + 1)
            lst.append(v)
        return lst, idx + 1
    if c == b'i':
        end = data.index(b'e', idx)
        return int(data[idx + 1:end]), end + 1
    colon = data.index(b':', idx)
    n = int(data[idx:colon])
    start = colon + 1
    return data[start:start + n], start + n


def parse_torrent_files(path: Path) -> list[dict]:
    """Parse a .torrent file → list of file records.

    Returns: [{stem, basename, select_index, size, path_in_torrent}]
    """
    raw = path.read_bytes()
    t, _ = bdecode(raw)
    info = t.get(b'info', {})
    result: list[dict] = []
    if b'files' not in info:
        name = info.get(b'name', b'').decode('utf-8', errors='replace')
        length = info.get(b'length', 0) or 0
        stem = re.sub(r'\.[^.]+$', '', name)
        if stem:
            result.append({
                "stem": stem, "basename": name, "select_index": 1,
                "size": length, "path_in_torrent": name,
            })
        return result
    idx = 1
    for f in info[b'files']:
        full = b'/'.join(f[b'path']).decode('utf-8', errors='replace')
        length = f.get(b'length', 0) or 0
        # Skip BEP 47 pad files
        if full.startswith('.pad/') or '/.pad/' in full:
            idx += 1
            continue
        basename = full.rsplit('/', 1)[-1]
        if not basename:
            idx += 1
            continue
        stem = re.sub(r'\.[^.]+$', '', basename)
        if stem:
            result.append({
                "stem": stem, "basename": basename, "select_index": idx,
                "size": length, "path_in_torrent": full,
            })
        idx += 1
    return result
