"""Where the second scanner's chosen values stand, to judge them by eye without seeing them: for each value (from
second_scan.py --values-out, private), up to K places in the tier's published text, each as the text around it with
the value, and every other chosen value, replaced by its shape (letters a, digits 9, the rest kept) in «».

python hit_contexts.py TIER VALUES.jsonl HITS.jsonl FILES.txt [--per 2] [--width 70]
(HITS: second_scan.py's hits file, for each value's detector; FILES: the published files to read, as sort_hits.py
wrote them.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

FREE = ("content", "tool_input_json", "command", "file_path", "pattern")


def shape(v: str) -> str:
    return re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", v))


def texts(tier: Path, rel: str, value: str):
    """The published texts of one file that hold the value."""
    p = tier / rel
    if p.suffix != ".parquet":
        raw = p.read_bytes().decode("utf-8", "replace")
        if value in raw:
            yield raw
        return
    t = pq.read_table(p)
    cols = [c for c in FREE if c in t.schema.names] if rel.startswith("conversations") else \
        [f.name for f in t.schema if str(f.type) in ("string", "large_string")]
    for c in cols:
        hit = pc.fill_null(pc.match_substring(t[c], value), False)
        for s in t[c].filter(hit).to_pylist():
            yield s
    if "files" in t.schema.names:
        for fl in t["files"].to_pylist():
            for f in fl or []:
                if value in (f.get("content") or ""):
                    yield f["content"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tier", type=Path)
    ap.add_argument("values", type=Path)
    ap.add_argument("hits", type=Path)
    ap.add_argument("files", type=Path)
    ap.add_argument("--per", type=int, default=2)
    ap.add_argument("--width", type=int, default=70)
    a = ap.parse_args()
    values = sorted({json.loads(l) for l in open(a.values, encoding="utf-8") if l.strip()}, key=lambda v: (-len(v), v))
    sha = {hashlib.sha256(v.encode("utf-8", "surrogatepass")).hexdigest(): v for v in values}
    det, place = {}, {}
    for line in open(a.hits, encoding="utf-8"):
        h = json.loads(line)
        if h["sha256"] in sha:
            det.setdefault(h["sha256"], h["detector"])
            place.setdefault(h["sha256"], []).append(h["published_file"])
    files = [l.strip() for l in open(a.files) if l.strip()]
    hide = re.compile("|".join(re.escape(v) for v in values))
    for s, v in sorted(sha.items(), key=lambda x: (det.get(x[0], ""), x[0])):
        shown = 0
        for rel in [f for f in place.get(s, []) if f in files][:3]:
            for text in texts(a.tier, rel, v):
                for m in re.finditer(re.escape(v), text):
                    lo, hi = max(0, m.start() - a.width), min(len(text), m.end() + a.width)
                    around = hide.sub(lambda x: "«" + shape(x.group(0)) + "»", text[lo:hi])
                    around = around.replace("\n", "⏎").replace("\r", "")
                    print(f"{det.get(s, '?'):14} {s[:8]} {rel[-48:]:48} …{around}…")
                    shown += 1
                    if shown >= a.per:
                        break
                if shown >= a.per:
                    break
            if shown >= a.per:
                break
        if not shown:
            print(f"{det.get(s, '?'):14} {s[:8]} not found in the files named")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
