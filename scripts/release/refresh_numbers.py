"""The numbers that describe a tier's published text, made to agree with it again after masking by hand: each session
file's `bytes` and `sha256` (of its masked content, as the build sets them) and each message row's `word_count` and
`char_count` (`len(content.split())`, `len(content)`). mask_finding.py changes text and, until 2026-11-05, left these
as they were (the review of 11-05: 168 session files and 333 message rows of the masked release; 302 session files
were stale before that, from earlier hand masks). A session's own `bytes` is the size as fetched, before masking, and
is left alone.

Without --write, counts what disagrees. With --write, rewrites only the files that need it (beside, then moved over;
a hard-linked copy keeps its original), after checking that every string in the table, nested ones too, is the same
before and after: the text the scanners read does not change, so their results stand. Prints counts only.

python refresh_numbers.py TIER [--write] [--procs 8]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mask_finding import nested_strings, replace_file, write_like  # noqa: E402


def _texts(t: pa.Table) -> list:
    """Every string of the table, nested ones too, in order, but the files' `sha256` (a digest this script sets): what
    must not change."""
    return [s for col in t.columns for x in col.to_pylist() for s in nested_strings(x, lambda n: n != "sha256")]


def fix_file(job: tuple[str, str, bool]) -> Counter:
    kind, path, write = job
    p = Path(path)
    t = pq.read_table(p)
    got = Counter()
    if kind == "session":
        files = t["files"].to_pylist()
        for row in files:
            for f in row or []:
                data = (f.get("content") or "").encode("utf-8", "surrogatepass")
                b, h = len(data), hashlib.sha256(data).hexdigest()
                if f.get("bytes") != b or f.get("sha256") != h:
                    got["session files"] += 1
                    f["bytes"], f["sha256"] = b, h
        if got and write:
            i = t.schema.get_field_index("files")
            new = t.set_column(i, t.schema.field(i), pa.array(files, t.schema.field(i).type))
    else:
        text = t["content"].to_pylist()
        wc, cc = t["word_count"].to_pylist(), t["char_count"].to_pylist()
        for r, x in enumerate(text):
            x = x or ""
            if wc[r] != len(x.split()) or cc[r] != len(x):
                got["message rows"] += 1
                wc[r], cc[r] = len(x.split()), len(x)
        if got and write:
            new = t
            for name, v in (("word_count", wc), ("char_count", cc)):
                i = new.schema.get_field_index(name)
                new = new.set_column(i, new.schema.field(i), pa.array(v, new.schema.field(i).type))
    if got and write:
        if _texts(t) != _texts(new) or not new.schema.equals(t.schema, check_metadata=True):
            raise RuntimeError(f"refused: rewriting {p.name} would change its text or schema")
        replace_file(p, lambda tmp: write_like(new, p, tmp))
        got["files rewritten"] += 1
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tier", type=Path)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--procs", type=int, default=8)
    a = ap.parse_args()
    jobs = [("session", str(p), a.write) for p in sorted((a.tier / "data").rglob("*.parquet"))]
    jobs += [("message", str(p), a.write) for sub in ("conversations", "conversations_subagents")
             for p in sorted((a.tier / sub).rglob("*.parquet"))]
    if not jobs:
        sys.exit(f"refused: no session or message tables under {a.tier}")
    jobs.sort(key=lambda j: -os.path.getsize(j[1]))
    total = Counter()
    with ProcessPoolExecutor(a.procs) as ex:
        for c in ex.map(fix_file, jobs, chunksize=1):
            total.update(c)
    print(("rewritten: " if a.write else "disagree: ") + json.dumps(dict(total)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
