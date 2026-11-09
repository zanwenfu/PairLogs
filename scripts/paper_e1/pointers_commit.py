"""How many tier-B pointers record the commit their session was found at (2026-11-08: not every
pointer holds a commit). Counts only.

python pointers_commit.py TIER_B_POINTERS.parquet OUT.json
"""
import json
import sys

import duckdb

P, OUT = sys.argv[1:3]
rows = duckdb.sql(f"""SELECT source, count(*) AS n, count(*) FILTER (WHERE found_commit IS NULL) AS no_commit
                      FROM read_parquet('{P}') GROUP BY ALL ORDER BY n DESC""").fetchall()
by = {s: {"pointers": n, "no_commit": z} for s, n, z in rows}
gh = [s for s in by if s != "swe-chat-v2"]   # SWE-chat's sessions come from its own release, not from GitHub
out = {"by_source": by,
       "github_pointers": sum(by[s]["pointers"] for s in gh),
       "github_no_commit": sum(by[s]["no_commit"] for s in gh)}
assert out["github_pointers"] > 0
print(json.dumps(out, indent=1))
json.dump(out, open(OUT, "w"), indent=1)
