"""Why a sampled pointer was not rebuilt as released: its repository's state on GitHub now (anonymous git ls-remote),
and, for a session rebuilt as a copy, what it is a copy of in the rebuild and in the released build. Ids only.

python diag.py SAMPLE.parquet REBUILT_VERSION_DIR RELEASED_BUILD_DIR
"""
import subprocess
import sys
from pathlib import Path

import pyarrow.compute as pc
import pyarrow as pa
import pyarrow.parquet as pq

SAMPLE, REBUILT, RELEASED = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sample = pq.read_table(SAMPLE).to_pylist()


def sessions(root: Path, keys=None, repos=None):
    out = []
    for f in sorted(root.glob("data/*/*.parquet")):
        if f.parent.name not in ("entire", "specstory", "aider"):
            continue
        t = pq.read_table(f, columns=["session_key", "source", "repo", "duplicate_of"])
        if keys is not None:
            t = t.filter(pc.is_in(t["session_key"], value_set=pa.array(sorted(keys))))
        if repos is not None:
            t = t.filter(pc.is_in(t["repo"], value_set=pa.array(sorted(repos))))
        out += t.to_pylist()
    return out


rebuilt = sessions(REBUILT)
repos_read = {(r["source"], r["repo"]) for r in rebuilt}
by_key = {}
for r in rebuilt:
    by_key.setdefault(r["session_key"], []).append(r)
for p in sample:
    if (p["source"], p["repo"]) not in repos_read:
        env = {"GIT_TERMINAL_PROMPT": "0", "PATH": "/usr/bin:/bin"}
        r = subprocess.run(["git", "ls-remote", "--heads", f"https://github.com/{p['repo']}"], capture_output=True,
                           text=True, timeout=60, env=env)
        state = "reachable" if r.returncode == 0 else ("not found or private" if "not found" in r.stderr.lower()
                                                       or "could not read" in r.stderr.lower() else r.stderr.strip()[:80])
        print(f"not rebuilt: {p['source']} {p['repo']}: ls-remote {state}")
for p in sample:
    got = by_key.get(p["session_key"]) or []
    if got and all(g.get("duplicate_of") for g in got):
        of = got[0]["duplicate_of"]
        print(f"rebuilt as a copy: {p['source']} {p['repo']}: of {of}")
        rel = sessions(RELEASED, keys={p["session_key"], of})
        for r in rel:
            print(f"  released build: {r['session_key'] == p['session_key'] and 'this' or 'its original'}: "
                  f"duplicate_of {r['duplicate_of']}")
