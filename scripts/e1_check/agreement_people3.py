"""Agreement between two people's marks on the same 80 pushback cards (check 3: the first person's page of 2026-11-04
and a second person's page with the same cards and guide, sample e1-check3-person2). Pushback by our definition
(A) and by SWE-chat's broader one (A or B: new information and changes of plan count too), cards either person marked
"can't tell" (D) left out, with Cohen's kappa; also all four answers. Unweighted, as drawn. Refuses two files with
different cards, an incomplete file, or one page twice.

python agreement_people3.py FIRST.json SECOND.json [--json OUT]
"""
import collections
import json
import sys

JSON_OUT = None
if "--json" in sys.argv:
    i = sys.argv.index("--json")
    JSON_OUT = sys.argv[i + 1]
    del sys.argv[i:i + 2]
first, second = (json.load(open(p)) for p in sys.argv[1:3])
if first.get("sample") == second.get("sample"):
    sys.exit("refused: both files come from the same page (one person's marks twice?)")
ids = [c["id"] for c in json.load(open("runs/e1-check/e1_check3_cards.json"))]
for name, f in (("first", first), ("second", second)):
    missing = [k for k in ids if (f["marks"].get(k) or {}).get("answer") not in ("A", "B", "C", "D")]
    if missing:
        sys.exit(f"refused: the {name} file leaves {len(missing)} of {len(ids)} cards unmarked")


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca, cb = collections.Counter(a for a, _ in pairs), collections.Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


def agree(f):
    d = [(f(first["marks"][k]["answer"]), f(second["marks"][k]["answer"])) for k in ids
         if "D" not in (first["marks"][k]["answer"], second["marks"][k]["answer"])]
    same = sum(a == b for a, b in d)
    return {"same": same, "of": len(d), "pct": round(100 * same / len(d), 1) if d else None,
            "kappa": kappa(d) if d else None}


out = {"first_sample": first.get("sample"), "second_sample": second.get("sample"), "cards": len(ids),
       "pushback_ours": agree(lambda x: x == "A"), "pushback_swechat": agree(lambda x: x in "AB"),
       "four_answers": agree(lambda x: x),
       "second_answers": dict(collections.Counter(second["marks"][k]["answer"] for k in ids))}
print(json.dumps(out, indent=1))
if JSON_OUT:
    json.dump(out, open(JSON_OUT, "w"), indent=1)
