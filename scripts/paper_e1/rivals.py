"""Read-only: the overlap table with earlier public data, per source and release tier, on one version (VERSION_DIR).

Per kept session (duplicate_of is null): its release tier (A: the published tier A's kept rows; B: tier B's
pointers); whether SWE-chat v2, DataClaw or OpenHands feedback also holds it (it is their own session, or one of their
rows is a copy of it: duplicate_of); whether the Notre Dame package studied it (SpecStory only: the very file, by git
blob sha of the file's bytes as kept, a floor since masked or changed files differ; and whether its repository is one
they studied, by GitHub id resolved to a current name); and "new", core.py's rule: a collected session (Entire,
SpecStory, Aider) that no copied dataset holds. Notre Dame's SWE-chat sessions are SWE-chat's own, counted there.
No text. argv: MAPPING.json REPO_NAMES.tsv OUT.json
"""
import collections
import hashlib
import json
import os
import sys

import duckdb

W = "/path/to/pairlogs-work"
V = os.environ.get("VERSION_DIR", f"{W}/run2/build/2026-11-02")
R = os.environ.get("RELEASE_DIR", f"{W}/release/2026-11-02")
M, N, OUT = sys.argv[1:4]
con = duckdb.connect()
con.execute(f"SET threads=8; SET memory_limit='64GB'; SET temp_directory='{W}/tmp'")
SESS = f"read_parquet('{V}/data/*/*.parquet', union_by_name=true)"
con.execute(f"create temp table s as select session_key, source, lower(repo) repo, duplicate_of from {SESS}")
kept = {k: (src, repo) for k, src, repo in con.execute(
    "select session_key, source, repo from s where duplicate_of is null").fetchall()}
held = collections.defaultdict(set)
dangling = 0
for k, src in con.execute("select duplicate_of, source from s where duplicate_of is not null").fetchall():
    if k in kept:
        held[k].add(src)
    else:
        dangling += 1
tier_a = {k for (k,) in con.execute(
    f"select session_key from read_parquet('{R}/a-names/data/*/*.parquet', union_by_name=true) where duplicate_of is null").fetchall()}
tier_b = {k for (k,) in con.execute(f"select session_key from read_parquet('{R}/b/pointers.parquet')").fetchall()}

mapping = json.load(open(M))
names = dict(l.rstrip("\n").split("\t") for l in open(N))
nd_repos = {names[str(x["repository_id"])].lower() for x in mapping
            if x["source"] == "specstory" and names.get(str(x["repository_id"]), "GONE") != "GONE"}
nd_sha = {x["session_sha"] for x in mapping if x["source"] == "specstory"}
nd_file = set()
cur = con.execute(f"select session_key, files from read_parquet('{V}/data/specstory/*.parquet') where duplicate_of is null")
while rows := cur.fetchmany(200):   # one batch at a time: the files' bytes are large
    for k, files in rows:
        for f in files or []:
            b = (f.get("content") or "").encode("utf-8", "surrogatepass")
            if hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest() in nd_sha:
                nd_file.add(k)
                break

COPIED = ("swe-chat-v2", "dataclaw", "openhands-feedback")
rows = collections.defaultdict(collections.Counter)
for k, (src, repo) in kept.items():
    tier = "A" if k in tier_a else "B" if k in tier_b else "none"
    c = rows[(src, tier)]
    c["sessions"] += 1
    places = held[k] | {src}
    for d in COPIED:
        c["in_" + d] += d in places
    c["in_nd_file"] += k in nd_file
    c["in_nd_repo"] += src == "specstory" and repo in nd_repos
    new = src in ("entire", "specstory", "aider") and not (held[k] & set(COPIED))
    c["new"] += new
    c["new_not_nd_file"] += new and k not in nd_file
    c["in_any_copied"] += bool(places & set(COPIED))
out = {"version": V, "kept": len(kept), "copies_pointing_nowhere": dangling, "tier_a": len(tier_a), "tier_b": len(tier_b),
       "kept_in_neither_tier": sum(1 for k in kept if k not in tier_a and k not in tier_b),
       "nd_specstory_sessions": len(nd_sha), "nd_specstory_repos_named": len(nd_repos),
       "rows": [{"source": s, "tier": t, **c} for (s, t), c in sorted(rows.items())]}
tot = collections.Counter()
for (s, t), c in rows.items():
    tot.update(c)
out["total"] = dict(tot)
json.dump(out, open(OUT, "w"), indent=1)
print(json.dumps(out, indent=1))
