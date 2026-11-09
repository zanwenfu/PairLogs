"""The context test's numbers (2026-11-03), counts only: our labeller's pushback rate and its agreement with SWE-chat's
label, for the released label, the message alone again, and the message with the agent's last reply; each cell of
agreement weighted by its share of the typed messages both labels cover (m10 on version 2026-11-02).

python scripts/pushback/ctx_summary.py SAMPLE.jsonl OUT.jsonl SUMMARY.json
"""
import collections
import json
import sys

WEIGHT = {"both": 27_915, "sc_only": 31_148, "ours_only": 3_389, "neither": 45_669}
SC_PUSHBACK = {"correction", "rejection", "failure_report"}

rows = {}
for line in open(sys.argv[1], encoding="utf-8", errors="surrogateescape"):
    r = json.loads(line)
    rows[r["id"]] = {"cell": r["cell"], "ours": bool(r["ours"]), "sc": r["sc4"] in SC_PUSHBACK}
got = collections.defaultdict(dict)
for line in open(sys.argv[2]):
    r = json.loads(line)
    got[r["id"]][r["mode"]] = r["label"]
missing = [k for k in rows if got[k].get("alone") is None or got[k].get("context") is None]
by_cell = collections.Counter(r["cell"] for r in rows.values())


def weighted(says) -> tuple[float, float]:
    rate = agree = 0.0
    for cell, w in WEIGHT.items():
        ids = [k for k in rows if rows[k]["cell"] == cell and k not in missing]
        for k in ids:
            share = w / len(ids)
            rate += share * says(k)
            agree += share * (says(k) == rows[k]["sc"])
    total = sum(WEIGHT.values())
    return 100 * rate / total, 100 * agree / total


out = {"n": len(rows), "by_cell": dict(by_cell), "without_both_labels": len(missing), "weights": WEIGHT}
for name, says in (("released", lambda k: rows[k]["ours"]), ("alone", lambda k: got[k]["alone"] == 1),
                   ("context", lambda k: got[k]["context"] == 1)):
    rate, agree = weighted(says)
    out[name] = {"pushback_pct": round(rate, 1), "agree_swechat_pct": round(agree, 1)}
# weighted as the rates are (the sample draws 250 from each cell, whatever its size)
out["alone_same_as_released_pct"] = round(weighted(lambda k: (got[k]["alone"] == 1) == rows[k]["ours"])[0], 1)
out["context_up_pct"] = round(weighted(lambda k: got[k]["alone"] == 0 and got[k]["context"] == 1)[0], 1)
out["context_down_pct"] = round(weighted(lambda k: got[k]["alone"] == 1 and got[k]["context"] == 0)[0], 1)
json.dump(out, open(sys.argv[3], "w"), indent=1)
print(json.dumps(out))
