"""The name check (check 7): how many private people's names the released tier A still holds, by who wrote the text.

python scripts/e1_check/agreement7.py runs/e1-check/e1_check7_key.json runs/e1-check/names7/draw7.json \
    runs/e1-check/names7/candidates.jsonl <marks.json> [--json OUT]

The rule, fixed 2026-11-06 before any card was marked: A is a private person's name;
B a public figure's; C not a real person's; D ("can't tell") counted and left out. Per stratum of who wrote the text:
the share of carded phrases a person reads as private names (Wilson 95%), and from it the private names left per
1,000 released rows (the stratum's phrases per drawn row times that share); then summed over tier A's rows. 95%
intervals from 2,000 draws of both stages: the drawn rows resampled (so the phrases per row vary), and the private
share drawn from its Jeffreys posterior, Beta(k + 1/2, n - k + 1/2), over the cards decided; extended to the point
when the share is 0 or 1. A stratum whose cards are all "can't tell" has no point estimate: it is named, left out of
the total's point, and drawn from the prior in the intervals. (Changed 2026-11-07, before any card was marked, after an
independent review: resampling only the cards treated the phrases per row as exact, and an all-"can't tell" stratum
counted as no names.) A lower bound: the finder (the masking's own) sees only phrases shaped like a full name, so a
name it does not see is not counted.
Refuses until every card is marked.
--count person (2026-11-08, the owner's call after the marks: a labeller cannot tell a private person from a public
one, so the measure is whether a phrase is a person's name at all): A or B counts, C does not, D is left out; the
output's keys then say "person" where they say "private".
--three (2026-11-08, after the owner said the first marks were made by a model, Gemini Argon, not a person): the
marks come from the three-choice page (build_sheet7.py --three, sample e1-check7b): A a real person's name, B not a
person's name, C can't tell. Read as A -> A, B -> C, C -> D, and counted as --count person.
"""
import json
import math
import random
import sys
from collections import Counter

JSON_OUT = None
POS, LABEL = {"A"}, "private"
THREE = "--three" in sys.argv
if THREE:
    sys.argv.remove("--three")
    POS, LABEL = {"A", "B"}, "person"
if "--count" in sys.argv:
    i = sys.argv.index("--count")
    mode = sys.argv[i + 1]
    del sys.argv[i:i + 2]
    if THREE and mode != "person":
        sys.exit("--three counts a person's name: --count person only")
    if mode == "person":
        POS, LABEL = {"A", "B"}, "person"
    elif mode != "private":
        sys.exit(f"--count private or person, not {mode}")
if "--json" in sys.argv:
    i = sys.argv.index("--json")
    JSON_OUT = sys.argv[i + 1]
    del sys.argv[i:i + 2]
key = json.load(open(sys.argv[1]))
draw = json.load(open(sys.argv[2]))
cands = [json.loads(line) for line in open(sys.argv[3])]
got = json.load(open(sys.argv[4]))
marks = got.get("marks", got)
if THREE:
    if got.get("sample") != "e1-check7b":
        sys.exit(f"--three reads the three-choice page's marks (sample e1-check7b), not {got.get('sample')!r}")
    bad3 = {(m or {}).get("answer") for m in marks.values()} - {"A", "B", "C", None}
    if bad3:
        sys.exit(f"unknown answers {bad3} for the three-choice page")
    marks = {k: {**(m or {}), "answer": {"A": "A", "B": "C", "C": "D"}.get((m or {}).get("answer"))} for k, m in marks.items()}
elif got.get("sample") == "e1-check7b":
    sys.exit("these marks are from the three-choice page: add --three")
missing = [k for k in key if not (marks.get(k) or {}).get("answer")]
if missing:
    sys.exit(f"{len(missing)} of {len(key)} cards are not marked yet; nothing reported")
extra = set(marks) - set(key)
if extra:
    sys.exit(f"{len(extra)} marks are for cards not in this key: the wrong file?")
bad = {marks[k]["answer"] for k in key} - set("ABCD")
if bad:
    sys.exit(f"unknown answers {bad}")


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


strata = draw["strata"]
n_cand = Counter(c["stratum"] for c in cands)
# phrases per drawn row with a phrase, by stratum (a row: one message row of one table)
row_counts = {s: sorted(Counter((c["session_key"], c["turn_id"], c["table"]) for c in cands
                                if c["stratum"] == s).values()) for s in strata}
for s, v in strata.items():
    assert len(row_counts[s]) == v["rows_with_candidate"] and sum(row_counts[s]) == n_cand[s], s
by = {s: [marks[k]["answer"] for k, v in key.items() if v["stratum"] == s] for s in strata}
kn = {s: (sum(x in POS for x in by[s]), sum(x != "D" for x in by[s])) for s in strata}

rng = random.Random("e1-check7-2026-11-07-two-stage")
B = 2000
boots = {s: [] for s in strata}
for _ in range(B):
    for s, v in strata.items():
        n_rows = v["drawn"]
        hits = Counter(i for i in rng.choices(range(n_rows), k=n_rows) if i < len(row_counts[s]))
        phrases = sum(row_counts[s][i] * m for i, m in hits.items())
        k, n = kn[s]
        share = rng.betavariate(k + 0.5, n - k + 0.5)
        boots[s].append(1000 * phrases / n_rows * share)


def interval(xs, at):
    xs = sorted(xs)
    lo, hi = xs[50], xs[1949]
    if at is not None:
        lo, hi = min(lo, at), max(hi, at)
    return [round(lo, 2), round(hi, 2)]


out = {"marks_sample": got.get("sample"), "cards": len(key), "answers": dict(Counter(m["answer"] for k, m in marks.items() if k in key)), "by_stratum": {},
       "lower_bound_note": "the masking's own finder: phrases shaped like a full name only"}
tier_total, no_point = 0.0, []
for s, v in strata.items():
    k, n = kn[s]
    pk = 1000 * n_cand[s] / v["drawn"] * k / n if n else None
    if pk is None:
        no_point.append(s)
    out["by_stratum"][s] = {
        "rows_in_tier": v["rows_in_tier"], "rows_drawn": v["drawn"], "rows_with_phrase": len(row_counts[s]),
        "phrases_found": n_cand[s], "cards": len(by[s]), "answers": dict(Counter(by[s])),
        "counted": LABEL, f"{LABEL}_share": {"k": k, "of": n, "wilson95": wilson(k, n)},
        f"{LABEL}_per_1000_rows": round(pk, 2) if pk is not None else None,
        f"{LABEL}_per_1000_rows_ci95": interval(boots[s], pk),
        "masked_on_purpose_here": s == "typed"}
    tier_total += (pk or 0.0) * v["rows_in_tier"] / 1000
tot = [sum(boots[s][i] * v["rows_in_tier"] / 1000 for s, v in strata.items()) for i in range(B)]
out[f"tier_a_{LABEL}_names_left"] = {"estimate": round(tier_total), "ci95": [round(x) for x in interval(tot, tier_total)],
                                    "strata_without_a_decided_card": no_point,
                                    "rows_in_strata": sum(v["rows_in_tier"] for v in strata.values())}
print(json.dumps(out, indent=1))
if JSON_OUT:
    json.dump(out, open(JSON_OUT, "w"), indent=1)
