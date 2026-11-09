"""Part 3 of the human check with the context test (2026-11-03): our stored pushback label, our labeller run again on
the message alone and with the agent's last reply (scripts/pushback/bedrock_luna_context.py), and SWE-chat's label,
each against a person's marks, under the rule agreement3.py fixed before any marking: strict (pushback = A) and broad
(A or B); "can't tell" (D) left out; each cell weighted by its share of the typed messages both labels cover.

python scripts/e1_check/agreement3_ctx.py KEY.json MARKS.json CONTEXT_OUT.jsonl [both sc_only ours_only neither] [--json OUT]

The four weights default to version 2026-11-02's cells (m10 on 108,121 typed messages); pass them to use others.
Refuses until every card is marked and every card has both context-test labels.
"""
import collections
import json
import sys

JSON_OUT = None
if "--json" in sys.argv:   # the same numbers also written as JSON, for the paper's numbers (paper/numbers.py)
    i = sys.argv.index("--json")
    JSON_OUT = sys.argv[i + 1]
    del sys.argv[i:i + 2]
RESULT = {}

key = json.load(open(sys.argv[1]))
got = json.load(open(sys.argv[2]))
marks = got.get("marks", got)
ctx = collections.defaultdict(dict)
for line in open(sys.argv[3]):
    r = json.loads(line)
    ctx[r["id"]][r["mode"]] = r["label"]
CELLS = ("both", "sc_only", "ours_only", "neither")
WEIGHT = dict(zip(CELLS, map(int, sys.argv[4:8]))) if len(sys.argv) >= 8 else \
    {"both": 27_915, "sc_only": 31_148, "ours_only": 3_389, "neither": 45_669}
missing = [k for k in key if not (marks.get(k) or {}).get("answer")]
if missing:
    sys.exit(f"{len(missing)} of {len(key)} cards are not marked yet; nothing reported")
unlabelled = [k for k in key if ctx[k].get("alone") is None or ctx[k].get("context") is None]
if unlabelled:
    sys.exit(f"{len(unlabelled)} cards lack a context-test label; nothing reported")

print("person's answers by cell (A pushback, B new information or change of plan, C neither, D can't tell):")
for cell in CELLS:
    c = collections.Counter(marks[k]["answer"] for k in key if key[k]["cell"] == cell)
    print(f"  {cell:9}  A {c['A']:2}  B {c['B']:2}  C {c['C']:2}  D {c['D']:2}")
    RESULT.setdefault("answers", {})[cell] = dict(c)

LABELS = (("ours, released", lambda k: bool(key[k]["ours"])),
          ("ours, alone again", lambda k: ctx[k]["alone"] == 1),
          ("ours, with context", lambda k: ctx[k]["context"] == 1),
          ("SWE-chat", lambda k: key[k]["swechat"] in ("correction", "rejection", "failure_report")))
for reading, yes in (("strict (A)", {"A"}), ("broad (A or B)", {"A", "B"})):
    print(f"\n{reading}:")
    for name, says in LABELS:
        tp = fp = fn = tn = 0.0
        for cell, w in WEIGHT.items():
            ids = [k for k in key if key[k]["cell"] == cell and marks[k]["answer"] != "D"]
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
        print(f"  {name:19} precision {prec:.2f}  recall {rec:.2f}  accuracy {(tp + tn) / total:.2f}  "
              f"pushback rate {100 * (tp + fp) / total:.0f}% vs the person's {100 * (tp + fn) / total:.0f}%")
        RESULT.setdefault(reading, {})[name] = {"precision": prec, "recall": rec, "accuracy": (tp + tn) / total,
                                                "rate": 100 * (tp + fp) / total, "person_rate": 100 * (tp + fn) / total}
if JSON_OUT:
    json.dump(RESULT, open(JSON_OUT, "w"), indent=1)
