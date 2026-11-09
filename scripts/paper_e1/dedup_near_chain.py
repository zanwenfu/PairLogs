"""Read-only: for near-copies whose text the kept session does not hold, whether the copy reached the kept through an
exact copy (same dedup key) that is the near-copy, and in which repository. Counts only."""
import json, sys
from collections import Counter, defaultdict
import pyarrow.parquet as pq
from pathlib import Path
V, IN = Path(sys.argv[1]), sys.argv[2]
cols = ["session_key", "repo", "dedup_key", "duplicate_of", "session_id", "agent"]
t, by_key = {}, defaultdict(list)
for f in sorted(V.glob("data/*/*.parquet")):
    for r in pq.read_table(f, columns=cols).to_pylist():
        t[r["session_key"]] = r
        if r["dedup_key"]:
            by_key[r["dedup_key"]].append(r)
d = json.load(open(IN))
for key in ("near entire~entire", "near entire~swe-chat-v2", "near swe-chat-v2~entire"):
    kind, pair = key.split(" ")
    bad = [p for p in d["pairs"] if p["kind"] == kind and p["pair"] == pair and "unreadable" not in p
           and p["text_held"] is not None and p["text_held"] < 0.5]
    c = Counter()
    for p in bad:
        x, z = t[p["copy"]], t[p["kept"]]
        mates = [r for r in by_key.get(x["dedup_key"], []) if r["session_key"] != x["session_key"]]
        in_kept_repo = [r for r in mates if r["repo"] == z["repo"]]
        c[("same id as kept" if x["session_id"] and x["session_id"] == z["session_id"] else "other id",
           f"key mates {len(mates)}", f"mates in kept's repo {len(in_kept_repo)}",
           "copy repo = kept repo" if x["repo"] == z["repo"] else "copy in other repo")] += 1
    print(key, len(bad))
    for k, v in c.most_common():
        print("   ", v, k)
