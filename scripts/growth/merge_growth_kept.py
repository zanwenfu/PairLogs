"""The update test on kept sessions, its parts added up: compare_growth_kept.py ran once per part of the re-read (batch
0, then the rest), each writing its counts by source; this adds them over parts and sources into the totals the paper
reads (growth_kept_11-06.json). Counts only.

python merge_growth_kept.py PART1.json PART2.json [...] OUT.json
"""
import json
import sys

*parts, out = sys.argv[1:]
t = {}
for p in parts:
    d = json.load(open(p))
    for v in d["by_source"].values():
        for k, x in v.items():
            t[k] = t.get(k, 0) + x
t["repositories_read"] = sum(json.load(open(p))["repositories_read"] for p in parts)
assert t["same"] + t["changed"] + t["gone"] == t["kept_in_repos_read"], t
print(t)
print("same pct", round(100 * t["same"] / t["kept_in_repos_read"], 1),
      "gone pct", round(100 * t["gone"] / t["kept_in_repos_read"], 1))
json.dump(t, open(out, "w"), indent=1)
