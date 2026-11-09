"""The fresh who-typed check (check 5): a person's marks against the release's typed mark, as drawn and weighted.

python scripts/e1_check/agreement5.py runs/e1-check/e1_check5_key.json runs/e1-check/draw5.json <marks.json> [--json OUT]

The rule, fixed 2026-11-06 before any card was marked: A is typed; B and C are not
typed; D ("can't tell") is counted and left out. Reported:
- as drawn (84 cards, 14 per source, 10 the release marks typed and 4 not typed): agreement where the person decided,
  the precision of the typed mark (A among cards marked typed), the typed share among cards marked not typed, Cohen's
  kappa, Wilson 95% intervals; and by format group (the format records the author: Entire, SWE-chat v2, OpenHands
  feedback; it does not: SpecStory, Aider, DataClaw);
- weighted to the data: each stratum (source x mark) weighted by its user-role rows in the version (draw5.json), so the
  rates are those of the data's user-role rows: precision, recall (the share of typed rows the mark finds) and
  agreement, with 95% intervals from 2,000 draws of each stratum's typed share from its Jeffreys posterior,
  Beta(k + 1/2, n - k + 1/2) (seed fixed). Changed 2026-11-07, before the person marked any card, after an independent
  review: resampling the cards inside a stratum cannot produce a miss in a stratum of 4 cards that holds none, so
  recall came out 100 [100, 100].
Refuses until every card is marked.
"""
import json
import math
import random
import sys
from collections import Counter

JSON_OUT = None
if "--json" in sys.argv:
    i = sys.argv.index("--json")
    JSON_OUT = sys.argv[i + 1]
    del sys.argv[i:i + 2]
key = json.load(open(sys.argv[1]))
draw = json.load(open(sys.argv[2]))
got = json.load(open(sys.argv[3]))
marks = got.get("marks", got)
missing = [k for k in key if not (marks.get(k) or {}).get("answer")]
if missing:
    sys.exit(f"{len(missing)} of {len(key)} cards are not marked yet; nothing reported")
extra = set(marks) - set(key)
if extra:
    sys.exit(f"{len(extra)} marks are for cards not in this key: the wrong file?")

AUTHOR_RECORDED = {"entire", "swe-chat-v2", "openhands-feedback"}
pop = {(p["source"], p["typed"]): p["rows"] for p in draw["population"]}
cards = [{"source": v["source"], "marked": v["pipeline"] == "typed by a person", "answer": marks[k]["answer"]}
         for k, v in key.items()]
decided = [c for c in cards if c["answer"] != "D"]


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


def rate(sel):
    n = len(sel)
    k = sum(c["answer"] == "A" for c in sel)
    return {"typed": k, "of": n, "pct": round(100 * k / n, 1) if n else None, "wilson95": wilson(k, n)}


def kappa(sel):
    n = len(sel)
    a = sum(c["marked"] == (c["answer"] == "A") for c in sel) / n
    pm = sum(c["marked"] for c in sel) / n
    pp = sum(c["answer"] == "A" for c in sel) / n
    e = pm * pp + (1 - pm) * (1 - pp)
    return round((a - e) / (1 - e), 3) if e < 1 else None


def weighted(typed_share):
    """Precision, recall and agreement over the data's user-role rows, each stratum (its typed share) weighted by its
    rows."""
    w_typed = sum(pop[s] * typed_share[s] for s in typed_share if s[1])           # marked typed, truly typed
    w_marked = sum(pop[s] for s in typed_share if s[1])
    w_missed = sum(pop[s] * typed_share[s] for s in typed_share if not s[1])      # marked not typed, truly typed
    w_not = sum(pop[s] for s in typed_share if not s[1])
    return {"precision": w_typed / w_marked, "recall": w_typed / (w_typed + w_missed),
            "agreement": (w_typed + (w_not - w_missed)) / (w_marked + w_not)}


empty = sorted(set(pop) - {(c["source"], c["marked"]) for c in decided})
assert not empty or all(k in pop for k in empty)
by_stratum = {}
for c in decided:
    by_stratum.setdefault((c["source"], c["marked"]), []).append(c)
kn = {s: (sum(c["answer"] == "A" for c in cs), len(cs)) for s, cs in by_stratum.items()}
# over the strata with a decided card; any without one is named in the output
point = weighted({s: k / n for s, (k, n) in kn.items()})
rng = random.Random("e1-check5-2026-11-07-jeffreys")
draws = [weighted({s: rng.betavariate(k + 0.5, n - k + 0.5) for s, (k, n) in sorted(kn.items())}) for _ in range(2000)]
# a stratum whose cards all agree has a share of 0 or 1, which no posterior draw reaches: the interval is extended to
# the point (the boundary rule of the Jeffreys interval: 0 of n has lower end 0, n of n upper end 1)
ci = {m: [round(100 * min(point[m], sorted(d[m] for d in draws)[50]), 1),
          round(100 * max(point[m], sorted(d[m] for d in draws)[1949]), 1)] for m in point}

out = {
    "marks_sample": got.get("sample"),   # 2026-11-09: e1-check5 was a model's (Gemini Argon); e1-check5b the person's
    "corrected_B_to_A": (got.get("corrected") or {}).get("cards_B_to_A", []),   # the person's final marks (correct5.py)
    "cards": len(cards), "cant_tell": len(cards) - len(decided), "decided": len(decided),
    "answers": dict(Counter(c["answer"] for c in cards)),
    "as_drawn": {
        "agreement": {"same": sum(c["marked"] == (c["answer"] == "A") for c in decided), "of": len(decided)},
        "precision_marked_typed": rate([c for c in decided if c["marked"]]),
        "typed_among_marked_not_typed": rate([c for c in decided if not c["marked"]]),
        "kappa": kappa(decided),
        "precision_author_recorded": rate([c for c in decided if c["marked"] and c["source"] in AUTHOR_RECORDED]),
        "precision_author_not_recorded": rate([c for c in decided if c["marked"] and c["source"] not in AUTHOR_RECORDED]),
        "precision_by_source": {s: rate([c for c in decided if c["marked"] and c["source"] == s])
                                for s in sorted({c["source"] for c in cards})},
    },
    "strata_without_a_decided_card": [list(e) for e in empty],
    "weighted": {m: {"pct": round(100 * point[m], 1), "ci95": ci[m]} for m in point},
}
print(json.dumps(out, indent=1))
if JSON_OUT:
    json.dump(out, open(JSON_OUT, "w"), indent=1)
