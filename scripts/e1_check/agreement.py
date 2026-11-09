"""The human check of "who typed it": a person's marks against the pipeline's answers.

python scripts/e1_check/agreement.py runs/e1-check/e1_check_key.json <marks.json> [--json OUT.json]

The rule, fixed before any marking (2026-10-24): report agreement between the person and the pipeline per part and per
pipeline class, whatever it is; "can't tell" (D) is counted and left out of agreement; on the pipeline's "unknown"
class (Part 1) the person's answers are reported as what that class holds. Refuses until every card is marked.
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
missing = [k for k in key if not (marks.get(k) or {}).get("answer")]
if missing:
    sys.exit(f"{len(missing)} of {len(key)} cards are not marked yet; nothing reported")

PERSON = {"A": "typed by a person", "B": "person action", "C": "program", "D": "can't tell"}


def pipeline_answer(k):
    p = key[k]["pipeline"]
    return "program" if p == "not typed" else p   # Part 2: the pipeline says only typed or not


for part in ("p1", "p2"):
    ids = [k for k in key if k.startswith(part + "-")]
    table = collections.Counter((key[k]["pipeline"], PERSON[marks[k]["answer"]]) for k in ids)
    print(f"\nPart {part[1]}: {len(ids)} cards")
    for (pipe, person), n in sorted(table.items()):
        print(f"  pipeline {pipe:18} person {person:18} {n}")
    decided = [k for k in ids if marks[k]["answer"] != "D" and pipeline_answer(k) != "unknown"]
    if part == "p2":   # the pipeline's typed mark is binary: a person's action counts as not typed
        same = sum((pipeline_answer(k) == "typed by a person") == (marks[k]["answer"] == "A") for k in decided)
    else:
        same = sum(pipeline_answer(k) == PERSON[marks[k]["answer"]] for k in decided)
    print(f"  agreement where both decided: {same} of {len(decided)}"
          + (f" ({100 * same / len(decided):.0f}%)" if decided else ""))
    print(f"  can't tell: {sum(marks[k]['answer'] == 'D' for k in ids)}")
    RESULT[part] = {"cards": len(ids), "decided": len(decided), "agree": same,
                    "cant_tell": sum(marks[k]["answer"] == "D" for k in ids),
                    "table": [[pipe, person, n] for (pipe, person), n in sorted(table.items())]}
if JSON_OUT:
    json.dump(RESULT, open(JSON_OUT, "w"), indent=1)
