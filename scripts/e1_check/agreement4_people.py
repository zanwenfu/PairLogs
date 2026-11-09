"""Agreement between two people's marks on the same work-label cards (the human check, sheet 4, done twice): per label
(kind of project, domain, kind of task), the share of cards where they agree, Cohen's kappa, and the pairs they most
often disagree on. A card either person left unmarked for a label is left out of that label; "can't tell" is a label
like the others, as in agreement4.py. Refuses two files of different card sets, or the same file twice.

python agreement4_people.py FIRST.json SECOND.json [--json OUT]
"""
import collections
import json
import sys

JSON_OUT = None
if "--json" in sys.argv:   # the same numbers also written as JSON, for the paper's numbers (paper/numbers.py)
    i = sys.argv.index("--json")
    JSON_OUT = sys.argv[i + 1]
    del sys.argv[i:i + 2]

first, second = (json.load(open(p)) for p in sys.argv[1:3])
if first.get("sample") != second.get("sample"):
    sys.exit("refused: the two files are marks on different card sets")
if first.get("rater") == second.get("rater"):
    sys.exit("refused: both files are one person's marks")
a_marks, b_marks = first["marks"], second["marks"]


def kappa(pairs):
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca, cb = collections.Counter(a for a, _ in pairs), collections.Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


RESULT = {}
for field in ("project", "domain", "task"):
    pairs = []
    for cid in sorted(set(a_marks) & set(b_marks)):
        a, b = (a_marks[cid] or {}).get(field), (b_marks[cid] or {}).get(field)
        if a and b:
            pairs.append((a.strip().lower(), b.strip().lower()))
    if not pairs:
        continue
    agree = sum(a == b for a, b in pairs)
    print(f"{field}: {len(pairs)} cards, agree {agree} ({100 * agree / len(pairs):.0f}%), kappa {kappa(pairs):.2f}")
    for (a, b), n in collections.Counter((a, b) for a, b in pairs if a != b).most_common(6):
        print(f"   first {a!r} / second {b!r}: {n}")
    RESULT[field] = {"cards": len(pairs), "agree": agree, "kappa": kappa(pairs)}
if JSON_OUT:
    json.dump(RESULT, open(JSON_OUT, "w"), indent=1)
