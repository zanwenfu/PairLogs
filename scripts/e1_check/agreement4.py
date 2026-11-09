"""Agreement of the person's marks with the model's labels about the work (the human check, sheet 4): per label
(kind of project, domain, kind of task), the share of cards where they agree, Cohen's kappa, and the pairs they
most often disagree on. A card the person left unmarked for a label is left out of that label.

With --also ALSO.json ({card id: {label: {"label", "also"}}}, the released work-label tables' main and other labels
for each card's item), a second reading, chosen after the first was seen (2026-11-04): the person's one label among
the model's main label and its up to two others.

python agreement4.py MARKS.json KEY.json PILOT.jsonl [--also ALSO.json] [--json OUT]
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
ALSO = None
if "--also" in sys.argv:
    i = sys.argv.index("--also")
    ALSO = json.load(open(sys.argv[i + 1]))
    del sys.argv[i:i + 2]

marks = json.load(open(sys.argv[1]))["marks"]
key = json.load(open(sys.argv[2]))["key"]
model = {r["id"]: r.get("answer") or {} for r in map(json.loads, open(sys.argv[3]))}


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca, cb = collections.Counter(a for a, _ in pairs), collections.Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


for field in ("project", "domain", "task"):
    pairs, within = [], 0
    for cid, rid in key.items():
        person = (marks.get(cid) or {}).get(field)
        m = ((model.get(rid) or {}).get(field) or {}).get("label")
        if person and m:
            pairs.append((person.strip().lower(), m.strip().lower()))
            if ALSO is not None:
                got = ALSO[cid][field]
                if (got["label"] or "").strip().lower() != m.strip().lower():
                    sys.exit(f"{cid} {field}: the released main label is not the key's")
                within += person.strip().lower() in {m.strip().lower()} | {a.strip().lower() for a in got["also"]}
    if not pairs:
        continue
    agree = sum(a == b for a, b in pairs)
    off = collections.Counter((a, b) for a, b in pairs if a != b).most_common(6)
    print(f"{field}: {len(pairs)} cards, agree {agree} ({100 * agree / len(pairs):.0f}%), kappa {kappa(pairs):.2f}")
    for (a, b), n in off:
        print(f"   person {a!r} / model {b!r}: {n}")
    RESULT[field] = {"cards": len(pairs), "agree": agree, "kappa": kappa(pairs)}
    if ALSO is not None:
        print(f"   the person's label among the model's main and other labels: {within} ({100 * within / len(pairs):.0f}%)")
        RESULT[field]["within_main_or_other"] = within
if JSON_OUT:
    json.dump(RESULT, open(JSON_OUT, "w"), indent=1)
