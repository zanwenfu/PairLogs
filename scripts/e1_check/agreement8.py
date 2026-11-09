"""Score the per-source check of the typed mark (check 8, 2026-11-09): every card is a message the mark calls typed,
drawn per source where logs record no author (SpecStory, Aider, DataClaw); two people mark each card from its text
alone on their own pages (samples e1-check8-person1 and e1-check8-person2).

Precision per source and person: cards read as typed (A) among those decided (A, B or C; "can't tell", D, left out),
with a Wilson 95% interval. Over the three sources, each source weighted by its typed rows (draw8.json), with a normal
interval from the per-source variances. Agreement between the two people, typed or not, both decided, with Cohen's
kappa. Refuses a file from another page, an incomplete file, or one page twice; with --unmarked-as-D, a card left
unmarked counts as "can't tell" (left out) and the count is reported per person.

python agreement8.py KEY8 DRAW8 FIRST.json [SECOND.json] [--json OUT] [--unmarked-as-D]
"""
import collections
import json
import math
import sys

JSON_OUT = None
if "--json" in sys.argv:
    i = sys.argv.index("--json")
    JSON_OUT = sys.argv[i + 1]
    del sys.argv[i:i + 2]
UNMARKED_AS_D = "--unmarked-as-D" in sys.argv
if UNMARKED_AS_D:
    sys.argv.remove("--unmarked-as-D")
key, draw = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
people = [json.load(open(p)) for p in sys.argv[3:5]]
WANT = ["e1-check8-person1", "e1-check8-person2"]
for f, want in zip(people, WANT):
    if f.get("sample") != want:
        sys.exit(f"refused: a file from page {f.get('sample')!r} where {want!r} was expected")
ids = sorted(key)
assert all(v["pipeline"] == "typed by a person" for v in key.values()), "check 8 holds only cards the mark calls typed"
unmarked = {}
for f in people:
    missing = [k for k in ids if (f["marks"].get(k) or {}).get("answer") not in ("A", "B", "C", "D")]
    if missing and not UNMARKED_AS_D:
        sys.exit(f"refused: {f['sample']} leaves {len(missing)} of {len(ids)} cards unmarked")
    assert not set(f["marks"]) - set(ids), f"{f['sample']} marks cards not in the key"
    unmarked[f["sample"]] = missing
    f["marks"] = {k: (f["marks"][k] if k not in missing else {"answer": "D"}) for k in ids}
SOURCES = ["specstory", "aider", "dataclaw"]
weight = {p["source"]: p["typed_rows"] for p in draw["population"]}
assert set(weight) == set(SOURCES), weight


def wilson(k, n, z=1.96):
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca, cb = collections.Counter(a for a, _ in pairs), collections.Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else None


out = {"cards": len(ids), "per_source_cards": dict(collections.Counter(key[k]["source"] for k in ids)), "people": {}}
for f in people:
    m = {k: f["marks"][k]["answer"] for k in ids}
    r = {"answers": dict(collections.Counter(m.values())), "unmarked_as_D": len(unmarked[f["sample"]]),
         "by_source": {}}
    tot_w, est, var = sum(weight.values()), 0.0, 0.0
    for s in SOURCES:
        dec = [k for k in ids if key[k]["source"] == s and m[k] != "D"]
        k_t = sum(m[k] == "A" for k in dec)
        p = k_t / len(dec) if dec else 0.0
        r["by_source"][s] = {"typed": k_t, "of": len(dec), "pct": round(100 * p, 1) if dec else None,
                             "wilson95": wilson(k_t, len(dec)),
                             "not_typed_kinds": dict(collections.Counter(m[k] for k in dec if m[k] != "A"))}
        w = weight[s] / tot_w
        est += w * p
        var += w * w * p * (1 - p) / len(dec) if dec else 0.0
    r["weighted"] = {"pct": round(100 * est, 1), "ci95": [round(100 * (est - 1.96 * math.sqrt(var)), 1),
                                                          round(min(100.0, 100 * (est + 1.96 * math.sqrt(var))), 1)]}
    dec = [k for k in ids if m[k] != "D"]
    r["pooled"] = {"typed": sum(m[k] == "A" for k in dec), "of": len(dec)}
    out["people"][f["sample"]] = r
if len(people) == 2:
    a, b = ({k: f["marks"][k]["answer"] for k in ids} for f in people)
    both = [k for k in ids if "D" not in (a[k], b[k])]
    pairs = [(a[k] == "A", b[k] == "A") for k in both]
    out["two_people"] = {"same": sum(x == y for x, y in pairs), "of": len(pairs),
                         "pct": round(100 * sum(x == y for x, y in pairs) / len(pairs), 1), "kappa": kappa(pairs),
                         "both_typed": sum(x and y for x, y in pairs),
                         "by_source": {s: {"same": sum(1 for k in both if key[k]["source"] == s
                                                       and (a[k] == "A") == (b[k] == "A")),
                                           "of": sum(1 for k in both if key[k]["source"] == s)} for s in SOURCES}}
print(json.dumps(out, indent=1))
if JSON_OUT:
    json.dump(out, open(JSON_OUT, "w"), indent=1)
