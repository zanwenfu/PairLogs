"""Read-only checks on the folder to upload (the upload gate's items 5, 6 and 8), counts only, never a text:

- every session once: tier A's kept sessions (session rows with no duplicate_of) and tier B's pointers each hold a key
  once, no key is in both, and together they are the version's kept sessions (EXPECTED); every copy row in tier A
  points at a kept session of either tier;
- the tables join: every message row, helper-agent row, label, work label and attribution row names a session that
  one of the two tiers holds (a copy's key counts: its rows stay with it);
- tier B holds no text: no string in its tables, nested ones too, reads as text (40 or more characters with 5 or
  more spaces, or 10 or more Chinese, Japanese or Korean characters outside the columns that hold keys, paths and
  URLs, PATHLIKE: SpecStory names a session's file by its title, in any language), but
  in the pointers' `models`, where a few agents record a model's description ("an open model released by ..."), up
  to 100; and its other files are only those listed. A table the checks expect and do not find fails. SWE-chat's prompt labels name SWE-chat's own sessions, all 17,215 it labels, not only those
  tier B keeps (the others are copies of sessions collected from elsewhere): counted, not required to join.

python upload_checks.py TIER_A_DIR TIER_B_DIR EXPECTED
(the release's a-names/ and b/, or the upload folder and its tier-b/). Exits 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import duckdb
import pyarrow.parquet as pq

B_OTHER = {"changelog.json", "repositories.txt", "work_labels/meta.json"}
ALLOWED_TEXT = {"pointers.parquet: models": 100}   # column: how many strings may read as text
CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


PATHLIKE = {"session_key", "source_id", "session_id", "turn_id", "text_id", "task_pk", "checkpoint_pk", "found_url",
            "found_commit", "path", "repo", "repo_url"}   # a field name: keys, paths and URLs, whatever their script


def reads_as_text(s: str, name: str | None) -> bool:
    return (len(s) >= 40 and s.count(" ") >= 5) or (len(CJK.findall(s)) >= 10 and name not in PATHLIKE)


def named_strings(x, name: str | None):
    """Every string inside a value with the name of the field that holds it (a list's items carry the list's)."""
    if isinstance(x, str):
        yield name, x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield from named_strings(v, k)
    elif isinstance(x, list):
        for v in x:
            yield from named_strings(v, name)


def keys(con, sql: str) -> list:
    return [k for (k,) in con.execute(sql).fetchall()]


def strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for v in x.values():
            yield from strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from strings(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("a", type=Path)
    ap.add_argument("b", type=Path)
    ap.add_argument("expected", type=int)
    x = ap.parse_args()
    x.a, x.b = x.a.resolve(), x.b.resolve()
    con = duckdb.connect()
    con.execute("SET threads=8; SET memory_limit='48GB'")
    sess = f"read_parquet('{x.a}/data/*/*.parquet', union_by_name=true)"
    out, bad = {}, []

    a_kept = keys(con, f"select session_key from {sess} where duplicate_of is null")
    a_copies = keys(con, f"select session_key from {sess} where duplicate_of is not null")
    copy_of = keys(con, f"select duplicate_of from {sess} where duplicate_of is not null")
    b = keys(con, f"select session_key from read_parquet('{x.b}/pointers.parquet')")
    A, B = set(a_kept), set(b)
    out["every session once"] = {
        "tier A kept sessions": len(a_kept), "tier A kept sessions twice": len(a_kept) - len(A),
        "tier B pointers": len(b), "tier B pointers twice": len(b) - len(B),
        "in both tiers": len(A & B), "together": len(A | B), "expected": x.expected,
        "tier A copy rows": len(a_copies), "copies pointing at no kept session": sum(1 for k in copy_of if k not in A | B),
        "copy keys also kept keys": len(set(a_copies) & (A | B))}
    e = out["every session once"]
    if e["tier A kept sessions twice"] or e["tier B pointers twice"] or e["in both tiers"] or e["together"] != x.expected \
            or e["copies pointing at no kept session"] or e["copy keys also kept keys"]:
        bad.append("every session once")

    known = A | B | set(a_copies)
    joins = {}
    for name, base, glob in (("messages", x.a, "conversations/*/*.parquet"),
                             ("helper messages", x.a, "conversations_subagents/*/*.parquet"),
                             ("tier A work labels", x.a, "work_labels/sessions.parquet"),
                             ("tier A attribution", x.a, "attribution/*.parquet"),
                             ("tier B labels", x.b, "labels/conversations*/**/*.parquet"),
                             ("tier B work labels", x.b, "work_labels/sessions.parquet"),
                             ("tier B attribution", x.b, "attribution/*.parquet")):
        files = sorted(base.glob(glob))
        if not files or not all("session_key" in pq.ParquetFile(f).schema_arrow.names for f in files):
            joins[name] = "no files" if not files else "no session_key column"
            bad.append(f"join: {name}")
            continue
        ks = keys(con, f"select distinct session_key from read_parquet({[str(f) for f in files]}, union_by_name=true)")
        joins[name] = {"files": len(files), "sessions named": len(ks), "not in either tier": sum(1 for k in ks if k not in known)}
        if joins[name]["not in either tier"]:
            bad.append(f"join: {name}")
    swe = set(keys(con, f"select session_id from read_parquet('{x.b}/pointers.parquet') where source = 'swe-chat-v2'"))
    ids = keys(con, f"select distinct session_id from read_parquet('{x.b}/labels/swe_chat_prompts.parquet')")
    joins["SWE-chat prompt labels"] = {"sessions named": len(ids), "of them tier B's": sum(1 for k in ids if k in swe)}
    out["the tables join"] = joins

    longest, textlike, total = {}, {}, {}
    for p in sorted(x.b.rglob("*.parquet")):
        group = str(p.relative_to(x.b).parent) if p.parent != x.b else p.name
        pf = pq.ParquetFile(p)
        for batch in pf.iter_batches(batch_size=50_000):
            for name in batch.schema.names:
                if str(batch.schema.field(name).type) in ("int64", "bool", "double", "int32"):
                    continue
                vals = [ns for v in batch.column(name).to_pylist() for ns in named_strings(v, name)]
                k = f"{group}: {name}"
                longest[k] = max(longest.get(k, 0), max((len(s) for _, s in vals), default=0))
                textlike[k] = textlike.get(k, 0) + sum(1 for n, s in vals if reads_as_text(s, n))
                total[k] = total.get(k, 0) + len(vals)
    others = sorted(str(p.relative_to(x.b)) for p in x.b.rglob("*") if p.is_file() and p.suffix != ".parquet")
    out["tier B holds no text"] = {"longest string per column": longest, "other files": others,
                                   "strings that read as text, by column": {k: f"{n} of {total[k]}" for k, n in
                                                                            textlike.items() if n},
                                   "columns that read as text": sorted(k for k, n in textlike.items()
                                                                       if n > ALLOWED_TEXT.get(k, 0))}
    if out["tier B holds no text"]["columns that read as text"] or set(others) - B_OTHER:
        bad.append("tier B holds no text")

    out["failed"] = bad
    print(json.dumps(out, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
