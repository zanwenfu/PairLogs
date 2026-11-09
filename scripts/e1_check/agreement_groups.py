"""The who-typed check's Part 2 (our own rows) by source group, with Wilson 95% intervals: formats that record who
wrote a message (Entire's agent logs, SWE-chat v2, OpenHands feedback) against those that record no author, where the
mark errs toward typed (SpecStory, Aider, DataClaw). Also the precision of the typed mark (of the rows the pipeline
marks typed, how many the person read as typed), before the helper-session fix and, given its rescore, after it (the
same cards: not an independent measure).

python scripts/e1_check/agreement_groups.py KEY.json MARKS.json [AFTER_FIX.json] [--json OUT.json]
"""
import json
import math
import sys

JSON_OUT = None
if "--json" in sys.argv:
    i = sys.argv.index("--json")
    JSON_OUT = sys.argv[i + 1]
    del sys.argv[i:i + 2]

GROUPS = {"records the author": ("entire", "swe-chat-v2", "openhands-feedback"),
          "no author recorded": ("specstory", "aider", "dataclaw")}


def wilson(k: int, n: int, z: float = 1.959964) -> list:
    if not n:
        return [None, None]
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


key = json.load(open(sys.argv[1]))
got = json.load(open(sys.argv[2]))
marks = got.get("marks", got)
changed = set(json.load(open(sys.argv[3]))["changed"]) if len(sys.argv) > 3 else set()
ids = [k for k in key if k.startswith("p2-")]
if any(not (marks.get(k) or {}).get("answer") for k in ids):
    sys.exit("not every card is marked")
if not changed <= set(ids):
    sys.exit("the rescore names cards outside Part 2")


def typed(k: str, after: bool) -> bool:
    return key[k]["pipeline"] == "typed by a person" and not (after and k in changed)


out = {}
for when in ("before", "after") if changed else ("before",):
    after = when == "after"
    res = {}
    for g, sources in [("all", None), *GROUPS.items()]:
        cards = [k for k in ids if marks[k]["answer"] != "D" and (sources is None or key[k]["source"] in sources)]
        agree = sum(typed(k, after) == (marks[k]["answer"] == "A") for k in cards)
        marked = [k for k in cards if typed(k, after)]
        right = sum(marks[k]["answer"] == "A" for k in marked)
        res[g] = {"cards": len(cards), "agree": agree, "agree_ci": wilson(agree, len(cards)),
                  "marked_typed": len(marked), "person_typed": right, "precision_ci": wilson(right, len(marked))}
        print(f"{when:6} {g:19} agree {agree} of {len(cards)} {wilson(agree, len(cards))}; "
              f"marked typed {len(marked)}, the person's typed {right} {wilson(right, len(marked))}")
    out[when] = res
if JSON_OUT:
    json.dump(out, open(JSON_OUT, "w"), indent=1)
