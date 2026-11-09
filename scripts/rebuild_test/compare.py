"""Tier B's rebuild test: compare a version rebuilt from the sources with the released pointers and label tables.

For each sampled pointer session (sample.parquet, from sample.py):
- found again: a rebuilt session row with the same session_key (kept, or now a copy of another rebuilt session);
- its raw files: the same paths with the same sha256 (the hash of the masked file, as the release lists it); else
  grown (a file larger now) or changed; the commit it was found at;
- its typed rows: tier B's label table lists each typed row's text id (sha256 of its text, 24 hex); a rebuilt row at
  the same position with the same id takes our labels as released; the typed mark and actor compared row by row.
A pointer whose repository gave no session at all is counted apart (repository gone, private or unreachable).
Prints counts only, never text.

python compare.py SAMPLE.parquet REBUILT_VERSION_DIR TIER_B_DIR OUT.json
"""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

SAMPLE, REBUILT, TIER_B, OUT = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4])


def text_id(text):
    return hashlib.sha256((text or "").encode("utf-8", errors="surrogateescape")).hexdigest()[:24]


sample = pq.read_table(SAMPLE).to_pylist()
keys = {r["session_key"] for r in sample}

rebuilt, repos_read = {}, Counter()
for f in sorted(REBUILT.glob("data/*/*.parquet")):
    names = pq.read_schema(f).names
    cols = [c for c in ("session_key", "source", "repo", "duplicate_of", "found_commit", "n_user_messages",
                        "n_tool_calls", "files") if c in names]
    for r in pq.read_table(f, columns=cols).to_pylist():
        repos_read[(r.get("source"), r.get("repo"))] += 1
        if r["session_key"] in keys:
            rebuilt.setdefault(r["session_key"], []).append(r)

rows_new = {}
for f in sorted(REBUILT.glob("conversations/*/*.parquet")):
    t = pq.read_table(f, columns=["session_key", "turn_number", "typed_by_developer", "actor", "content"])
    t = t.filter(pc.is_in(t["session_key"], value_set=pa.array(sorted(keys))))
    for r in t.to_pylist():
        rows_new[(r["session_key"], r["turn_number"])] = (bool(r["typed_by_developer"]), r["actor"],
                                                          text_id(r["content"]) if r["typed_by_developer"] else None)

rows_old = {}
for f in sorted(TIER_B.glob("labels/conversations/*/*.parquet")):
    t = pq.read_table(f, columns=["session_key", "turn_number", "typed_by_developer", "actor", "text_id"])
    t = t.filter(pc.is_in(t["session_key"], value_set=pa.array(sorted(keys))))
    for r in t.to_pylist():
        rows_old[(r["session_key"], r["turn_number"])] = (bool(r["typed_by_developer"]), r["actor"], r["text_id"])

out = {"pointers": len(sample), "by_source": {}}
for src in sorted({r["source"] for r in sample}):
    c = Counter()
    for p in (r for r in sample if r["source"] == src):
        c["pointers"] += 1
        got = rebuilt.get(p["session_key"])
        if not got:
            c["not found: repository gave no session" if not repos_read[(src, p["repo"])]
              else "not found: session gone from a repository read"] += 1
            continue
        g = next((x for x in got if not x.get("duplicate_of")), got[0])
        c["found"] += 1
        c["found, kept" if not g.get("duplicate_of") else "found, now a copy"] += 1
        old = {x["path"]: (x["sha256"], x["bytes"]) for x in p["files"] or []}
        new = {x["path"]: (x["sha256"], x["bytes"]) for x in g.get("files") or []}
        if old == new:
            c["files identical"] += 1
        elif set(old) <= set(new) and all(new[k][1] >= old[k][1] for k in old):
            c["files grown"] += 1
        else:
            c["files changed"] += 1
        pc_, gc_ = p.get("found_commit") or None, g.get("found_commit") or None
        c["same commit" if pc_ == gc_ else "commit not recorded (found by the commit search, which needs a token)"
          if gc_ is None else "other commit"] += 1
        c["same n_user_messages"] += p.get("n_user_messages") == g.get("n_user_messages")
        c["same n_tool_calls"] += p.get("n_tool_calls") == g.get("n_tool_calls")
        olds = {k: v for k, v in rows_old.items() if k[0] == p["session_key"]}
        news = {k: v for k, v in rows_new.items() if k[0] == p["session_key"]}
        c["label rows"] += len(olds)
        c["label rows present again"] += sum(1 for k in olds if k in news)
        c["typed mark same"] += sum(1 for k, v in olds.items() if k in news and news[k][0] == v[0])
        c["actor same"] += sum(1 for k, v in olds.items() if k in news and news[k][1] == v[1])
        typed = [k for k, v in olds.items() if v[0]]
        c["typed rows (labels)"] += len(typed)
        c["typed rows, same text id at the same position"] += sum(1 for k in typed if k in news and news[k][2] == olds[k][2])
        ids_new = {v[2] for v in news.values() if v[2]}
        c["typed rows, text id anywhere in the rebuilt session"] += sum(1 for k in typed if olds[k][2] in ids_new)
    out["by_source"][src] = dict(c)
tot = Counter()
for c in out["by_source"].values():
    tot.update(c)
out["all"] = dict(tot)
out["rebuilt sessions in all"] = sum(repos_read.values())
OUT.write_text(json.dumps(out, indent=1))
print(json.dumps(out, indent=1))
