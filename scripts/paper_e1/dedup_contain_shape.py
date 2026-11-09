"""Read-only: shapes of the copies whose text the kept session does not hold (dedup_contain.py's output): same
repository or not, time ranges (before, after, overlapping), agents, rows, and how much of the kept is in the copy.
Counts only."""
import json, sys
from collections import Counter
import pyarrow.parquet as pq
from pathlib import Path
V, IN = Path(sys.argv[1]), sys.argv[2]
cols = ["session_key", "repo", "agent", "started_at", "ended_at", "n_conversation_rows", "bytes", "session_id", "source_id"]
t = {}
for f in sorted(V.glob("data/*/*.parquet")):
    for r in pq.read_table(f, columns=cols).to_pylist():
        t[r["session_key"]] = r
d = json.load(open(IN))
def when(c, k):
    a0, a1, b0, b1 = c["started_at"], c["ended_at"], k["started_at"], k["ended_at"]
    if not (a0 and a1 and b0 and b1):
        return "no times"
    if a1 <= b0: return "copy ends before kept starts"
    if b1 <= a0: return "copy starts after kept ends"
    if a0 >= b0 and a1 <= b1: return "copy within kept's span"
    if b0 >= a0 and b1 <= a1: return "kept within copy's span"
    return "overlap"
for key in ("exact swe-chat-v2~entire", "exact specstory~specstory", "exact dataclaw~dataclaw", "near entire~entire", "near entire~swe-chat-v2", "near swe-chat-v2~entire"):
    kind, pair = key.split(" ")
    rs = [p for p in d["pairs"] if p["kind"] == kind and p["pair"] == pair and "unreadable" not in p and p["text_held"] is not None]
    bad = [p for p in rs if p["text_held"] < 0.5]
    part = [p for p in rs if 0.5 <= p["text_held"] < 0.9]
    print(f"== {key}: read {len(rs)}, not held {len(bad)}, partly {len(part)}")
    for name, grp in (("not held", bad), ("partly", part)):
        if not grp: continue
        c = Counter(); sr = Counter(); ag = Counter(); kin = Counter(); rowsr = Counter()
        for p in grp:
            cr, kr = t[p["copy"]], t[p["kept"]]
            c[when(cr, kr)] += 1
            sr["same repo" if cr["repo"] == kr["repo"] else "other repo"] += 1
            ag[cr["agent"] or kr["agent"]] += 1
            kk = p["kept_text_in_copy"]
            kin["kept's text in copy: none" if not kk else "<50%" if kk < 0.5 else "50-90%" if kk < 0.9 else "90%+"] += 1
            a, b = p["rows"]
            rowsr["copy shorter" if a < b else "copy longer" if a > b else "same rows"] += 1
        print("  ", name, dict(c)); print("     ", dict(sr), dict(ag)); print("     ", dict(kin), dict(rowsr))
        print("      copy text items median", sorted(p["text_n"] for p in grp)[len(grp)//2])
