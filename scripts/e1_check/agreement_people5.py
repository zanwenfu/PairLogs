"""Agreement between two people's marks on the same 84 cards of the fresh weighted check (check 5: the first person's
final marks, correct5.py's output, and a second person's page with the same cards and guide, sample
e1-check5-person2): typed (A) or not (B, C), cards either person marked "can't tell" (D) left out, with Cohen's kappa;
also all four answers. Unweighted, as drawn. Refuses two files with different cards, an incomplete file, or one page
twice.

python agreement_people5.py FIRST.json SECOND.json [--json OUT]
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
cards = json.load(open("runs/e1-check/cards5.json"))
ids = [c["id"] for c in cards]
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


def agree(keys, f):
    d = [(f(first["marks"][k]["answer"]), f(second["marks"][k]["answer"])) for k in keys
         if "D" not in (first["marks"][k]["answer"], second["marks"][k]["answer"])]
    same = sum(a == b for a, b in d)
    return {"same": same, "of": len(d), "pct": round(100 * same / len(d), 1) if d else None,
            "kappa": kappa(d) if d else None}


typed = lambda x: x == "A"
out = {"first_sample": first.get("sample"), "second_sample": second.get("sample"), "cards": len(ids),
       "typed_or_not": agree(ids, typed),
       "four_answers": agree(ids, lambda x: x),
       "second_answers": dict(collections.Counter(second["marks"][k]["answer"] for k in ids))}
print(json.dumps(out, indent=1))
if JSON_OUT:
    json.dump(out, open(JSON_OUT, "w"), indent=1)
