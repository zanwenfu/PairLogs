"""Part 3 of the human check: our pushback label and SWE-chat's, each against a person's marks.

python scripts/e1_check/agreement3.py runs/e1-check/e1_check3_key.json <marks.json>

The rule, fixed before any marking (2026-10-25): each label is scored under two readings of the person's marks,
strict (pushback = A) and broad (pushback = A or B); "can't tell" (D) is counted and left out. The sample holds 20
messages from each cell of the two labels' agreement table, so whole-set figures weight each cell by its share of the
107,723 typed messages both labelled (m10 of 2026-10-24, from SWE-chat's classes and our rate in each): both pushback
27,642; SWE-chat only 30,923; ours only 3,392; neither 45,766. Refuses until every card is marked.
"""
import collections
import json
import sys

WEIGHT = {"both": 27_642, "sc_only": 30_923, "ours_only": 3_392, "neither": 45_766}
key = json.load(open(sys.argv[1]))
got = json.load(open(sys.argv[2]))
marks = got.get("marks", got)
missing = [k for k in key if not (marks.get(k) or {}).get("answer")]
if missing:
    sys.exit(f"{len(missing)} of {len(key)} cards are not marked yet; nothing reported")

print("person's answers by cell (A pushback, B new information or change of plan, C neither, D can't tell):")
for cell in WEIGHT:
    c = collections.Counter(marks[k]["answer"] for k in key if key[k]["cell"] == cell)
    print(f"  {cell:9}  A {c['A']:2}  B {c['B']:2}  C {c['C']:2}  D {c['D']:2}")

for reading, yes in (("strict (A)", {"A"}), ("broad (A or B)", {"A", "B"})):
    print(f"\n{reading}:")
    for name, says in (("ours", lambda k: key[k]["ours"]),
                       ("SWE-chat", lambda k: key[k]["swechat"] in ("correction", "rejection", "failure_report"))):
        tp = fp = fn = tn = 0.0
        for cell, w in WEIGHT.items():
            ids = [k for k in key if key[k]["cell"] == cell and marks[k]["answer"] != "D"]
            if not ids:
                continue
            for k in ids:
                share = w / len(ids)
                truth, label = marks[k]["answer"] in yes, says(k)
                tp += share * (truth and label)
                fp += share * (label and not truth)
                fn += share * (truth and not label)
                tn += share * (not truth and not label)
        total = tp + fp + fn + tn
        prec = tp / (tp + fp) if tp + fp else float("nan")
        rec = tp / (tp + fn) if tp + fn else float("nan")
        print(f"  {name:9} precision {prec:.2f}  recall {rec:.2f}  accuracy {(tp + tn) / total:.2f}  "
              f"pushback rate {100 * (tp + fp) / total:.0f}% vs the person's {100 * (tp + fn) / total:.0f}%")
