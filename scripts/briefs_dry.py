"""Dry run of collect.briefs on a built version and its tiers: the rows found, by rule and source, and what apply would
change in each folder (write=False), counts only. Also writes the rows found (keys and turns only) for the apply step.
python scripts/briefs_dry.py VERSION_DIR TIER_A TIER_B OUT.json ROWS.jsonl
"""
import json
import sys
import time
from pathlib import Path

from collect import briefs

V, A, B, OUT, ROWS = map(Path, sys.argv[1:6])
t0 = time.time()
got = briefs.find(V)
print(f"find: {len(got['rows']):,} rows in {time.time() - t0:.0f} s; {json.dumps(got['counts'])}", flush=True)
with open(ROWS, "w") as fh:
    for k, t, r in got["rows"]:
        fh.write(json.dumps({"session_key": k, "turn_number": t, "rule": r}) + "\n")
res = {k: v for k, v in got.items() if k != "rows"}
res["rows"] = len(got["rows"])
for name, folder in (("version", V), ("tier A", A), ("tier B", B)):
    res[f"apply {name} (dry)"] = dict(briefs.apply(folder, got["rows"], write=False))
    print(name, res[f"apply {name} (dry)"], flush=True)
json.dump(res, open(OUT, "w"), indent=1)
print("end")
