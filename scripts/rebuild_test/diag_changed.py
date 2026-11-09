"""Why a rebuilt session's files differ from its pointer's hashes: for each such session, its raw files (as fetched,
before masking) in the released build's data and in the rebuild's, compared by sha256 at the same path. A raw file
that differs means the session changed at its source since the release's fetch; raw files that are the same while
the masked file differs would mean the pipeline does not reproduce itself. Per repository, all raw files compared too.
Counts and keys only, never text.

python diag_changed.py SAMPLE.parquet REBUILT_VERSION_DIR REBUILT_DATA_RAW RELEASED_DATA_RAW OUT.json
"""
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq

SAMPLE, REBUILT, NEW_RAW, OLD_RAW, OUT = map(Path, sys.argv[1:6])


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        while b := fh.read(8 << 20):
            h.update(b)
    return h.hexdigest()


def tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p for p in root.rglob("*") if p.is_file()} if root.exists() else {}


sample = pq.read_table(SAMPLE).to_pylist()
rebuilt = {}
for f in sorted(REBUILT.glob("data/*/*.parquet")):
    for r in pq.read_table(f, columns=["session_key", "duplicate_of", "files"]).to_pylist():
        rebuilt.setdefault(r["session_key"], []).append(r)

out = {"repositories": {}, "changed_sessions": []}
trees = {}
for repo in sorted({(p["source"], p["repo"]) for p in sample}):
    src, name = repo
    d = name.replace("/", "__")
    old, new = tree(OLD_RAW / src / d), tree(NEW_RAW / src / d)
    trees[repo] = (old, new)
    c = Counter()
    for rel in set(old) | set(new):
        if rel not in new:
            c["only in the release's fetch"] += 1
        elif rel not in old:
            c["only in the rebuild's fetch"] += 1
        else:
            c["same" if sha(old[rel]) == sha(new[rel]) else "differ"] += 1
    out["repositories"][f"{src}:{name}"] = dict(c)

why = Counter()
for p in sample:
    got = rebuilt.get(p["session_key"])
    if not got:
        continue
    g = next((x for x in got if not x.get("duplicate_of")), got[0])
    oldf = {x["path"]: x["sha256"] for x in p["files"] or []}
    newf = {x["path"]: x["sha256"] for x in g.get("files") or []}
    if oldf == newf:
        continue
    old, new = trees[(p["source"], p["repo"])]
    sid = p["session_key"].rsplit(":", 1)[-1]
    raw = Counter()
    for path in sorted(set(oldf) | set(newf)):
        if oldf.get(path) == newf.get(path):
            continue
        rels = [r for r in set(old) | set(new) if (r.endswith("/" + path) or r == path) and (sid in r or p["source"] != "entire")]
        for rel in rels:
            if rel in old and rel in new:
                raw["raw same, masked differs" if sha(old[rel]) == sha(new[rel]) else "raw differs at the source"] += 1
            else:
                raw["raw file only in one fetch"] += 1
        if not rels:
            raw["raw file not found"] += 1
    kind = ("changed at the source" if raw["raw differs at the source"] or raw["raw file only in one fetch"]
            else "pipeline difference" if raw["raw same, masked differs"] else "unexplained")
    why[kind] += 1
    out["changed_sessions"].append({"session_key": p["session_key"], "files_old": len(oldf), "files_new": len(newf),
                                    "raw": dict(raw), "why": kind})
out["why"] = dict(why)
OUT.write_text(json.dumps(out, indent=1))
print(json.dumps({"repositories": out["repositories"], "why": out["why"]}, indent=1))
