"""Check 5: the person's final marks against the model's (Claude Sonnet 5.5, second_rater.py; rater2/check5.filled.json),
typed or not, cards either marked "can't tell" left out; the same agreement and kappa as rater2_report.py gives for
check 1 (as drawn, unweighted).

python scripts/e1_check/person_vs_model5.py <the person's marks, corrected (correct5.py)> runs/e1-check/rater2/check5.filled.json OUT.json
"""
import json
import sys
from collections import Counter

PM, MM, OUT = sys.argv[1:4]
person = json.load(open(PM))
assert person.get("sample") == "e1-check5b" and person.get("corrected"), "the person's final marks"
model = json.load(open(MM))["marks"]
key = json.load(open("runs/e1-check/e1_check5_key.json"))
assert set(person["marks"]) == set(model) == set(key), "the same 84 cards"
pairs = [(person["marks"][k]["answer"], model[k]["answer"]) for k in sorted(key)]
d = [(a == "A", b == "A") for a, b in pairs if "D" not in (a, b)]
n, same = len(d), sum(a == b for a, b in d)
po = same / n
ca, cb = Counter(a for a, _ in d), Counter(b for _, b in d)
pe = sum(ca[x] * cb[x] for x in (True, False)) / (n * n)
out = {"cards": len(pairs), "both_decide": n, "same": same, "pct": round(100 * po, 1),
       "kappa": round((po - pe) / (1 - pe), 3), "four_answers_same": sum(a == b for a, b in pairs if "D" not in (a, b))}
print(json.dumps(out))
json.dump(out, open(OUT, "w"), indent=1)
