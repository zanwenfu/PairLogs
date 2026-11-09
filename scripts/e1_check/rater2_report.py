"""The second reader (a model, second_rater.py) against the person and against the pipeline, on the person's checks.

python scripts/e1_check/rater2_report.py OUT.json

Reads runs/e1-check/rater2/check{1,3,5,6}.json (the model's marks), the person's exports in ~/Downloads (checks 1 and
3; checks 5 and 6 once marked), and each check's key. A card the model gave no answer (its reply did not open with a
letter in three asks) counts as "can't tell" (D). The rules are the person's checks' own: D is counted and left out.
- person vs model: agreement on the four answers where both decided, Cohen's kappa; and collapsed as each check is
  reported: who typed it, typed (A) or not (B, C); pushback, A or not (narrow) and A or B against C (broad);
- model vs pipeline: each check's own scorer run with the model's marks in the person's place (filled files written
  beside the model's marks, *.filled.json).
"""
import glob
import json
import math
import os
import re
import subprocess
import sys
from collections import Counter

OUT = sys.argv[1]
K = "runs/e1-check"
R = f"{K}/rater2"
CARDS = {1: f"{K}/e1_check_cards.json", 3: f"{K}/e1_check3_cards.json", 5: f"{K}/cards5.json", 6: f"{K}/cards6.json"}
PERSON = {1: "e1-check-marks-a22cc1d8928c9fe3", 3: "e1-check3-marks-54761e8375102fae", 5: "e1-check-marks-e1-check5",
          6: "e1-check-marks-e1-check6"}


def newest(prefix):
    xs = sorted(glob.glob(os.path.expanduser(f"~/Downloads/{prefix}*.json")), key=os.path.getmtime)
    return xs[-1] if xs else None


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[x] * cb[x] for x in set(ca) | set(cb)) / (n * n)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


def agree(pairs, f=lambda x: x):
    d = [(f(a), f(b)) for a, b in pairs if "D" not in (a, b)]
    k = sum(a == b for a, b in d)
    return {"same": k, "of": len(d), "pct": round(100 * k / len(d), 1) if d else None, "wilson95": wilson(k, len(d)),
            "kappa": kappa(d)}


res = {"model": None, "checks": {}}
for n, cpath in CARDS.items():
    cards = json.load(open(cpath))
    got = json.load(open(f"{R}/check{n}.json"))
    res["model"] = got.get("model")
    model = {c["id"]: (got["marks"].get(c["id"]) or {}).get("answer") or "D" for c in cards}
    # a stored answer whose reply went on to name a second letter ("A or C? ...") was a hedge, not an answer: "can't
    # tell" (the parser took the first letter until 2026-11-07; second_rater.py now asks again instead)
    hedged = [k for k, v in got["marks"].items() if re.match(r"^\s*\**\s*(?:or|/|,)\s*\**\s*[ABCD]\b", v.get("note") or "")]
    for k in hedged:
        model[k] = "D"
    no_answer = sum(1 for c in cards if c["id"] not in got["marks"])
    filled = f"{R}/check{n}.filled.json"
    json.dump({"sample": got.get("sample"), "marks": {k: {"answer": v} for k, v in model.items()}}, open(filled, "w"))
    r = {"cards": len(cards), "model_no_answer": no_answer, "model_hedged": len(hedged),
         "model_answers": dict(Counter(model.values()))}
    pf = newest(PERSON[n])
    if pf:
        person = json.load(open(pf)).get("marks", {})
        if all((person.get(c["id"]) or {}).get("answer") for c in cards):
            pairs = [(person[c["id"]]["answer"], model[c["id"]]) for c in cards]
            r["person_vs_model"] = {"four_answers": agree(pairs)}
            if n == 3:
                r["person_vs_model"]["pushback_narrow"] = agree(pairs, lambda x: x == "A")
                r["person_vs_model"]["pushback_broad"] = agree(pairs, lambda x: x in "AB")
            else:
                r["person_vs_model"]["typed_or_not"] = agree(pairs, lambda x: x == "A")
            if n == 1:
                part = {c["id"]: c["part"] for c in cards}
                for p in (1, 2):
                    pp = [(person[k]["answer"], model[k]) for k in part if part[k] == p]
                    r["person_vs_model"][f"part{p}_typed_or_not"] = agree(pp, lambda x: x == "A")
                    r["person_vs_model"][f"part{p}_four_answers"] = agree(pp)
        else:
            r["person_vs_model"] = "the person's marks are not complete yet"
    else:
        r["person_vs_model"] = "the person has not marked this check yet"
    # the model in the person's place, through the check's own scorer
    cmd = {1: ["scripts/e1_check/agreement.py", f"{K}/e1_check_key_2026-11-02.json", filled],
           # the person's own scorer and weights (2026-11-02's cells; agreement3.py holds 10-24's: the review of 11-07)
           3: ["scripts/e1_check/agreement3_ctx.py", f"{K}/e1_check3_key_2026-11-02.json", filled,
               "runs/e1-results/ctx_test_2026-11-03/cards80_out.jsonl"],
           5: ["scripts/e1_check/agreement5.py", f"{K}/e1_check5_key.json", f"{K}/draw5.json", filled],
           6: ["scripts/e1_check/agreement6.py", f"{K}/e1_check6_key.json", f"{K}/draw6.json", filled]}[n]
    side = f"{R}/check{n}.model_vs_pipeline.json"
    if n in (1, 5, 6):
        p = subprocess.run([sys.executable, "-I", *cmd, "--json", side], capture_output=True, text=True)
        r["model_vs_pipeline"] = json.load(open(side)) if p.returncode == 0 and os.path.exists(side) else p.stderr[-500:]
    else:
        p = subprocess.run([sys.executable, "-I", *cmd], capture_output=True, text=True)
        open(side.replace(".json", ".txt"), "w").write(p.stdout + p.stderr)
        r["model_vs_pipeline"] = f"text report in {side.replace('.json', '.txt')}"
    res["checks"][n] = r
json.dump(res, open(OUT, "w"), indent=1)
print(json.dumps(res, indent=1)[:6000])
