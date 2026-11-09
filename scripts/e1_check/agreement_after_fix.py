"""The who-typed check scored again after the helper-brief fix (collect briefs): the same person's marks, the pipeline's
answers as the fixed version gives them. Part 2: a card whose row (session_key, turn_number) the fix marked as another
agent's becomes "not typed". Part 1: a card's class becomes the fixed version's matching of its SWE-chat prompt.
Scored by agreement.py's rules; prints both, and which cards changed (ids only).

python scripts/e1_check/agreement_after_fix.py KEY.json MARKS.json ROWS.jsonl SWE_FIX.json [--json OUT.json]
  ROWS.jsonl: the rows the fix changed ({"session_key", "turn_number"} a line)
  SWE_FIX.json: {turn_id: class} for Part 1's prompts, from the fixed version's matching
"""
import collections
import copy
import json
import sys

PERSON = {"A": "typed by a person", "B": "person action", "C": "program", "D": "can't tell"}


def score(key: dict, marks: dict) -> dict:
    out = {}
    answer = lambda k: "program" if key[k]["pipeline"] == "not typed" else key[k]["pipeline"]
    for part in ("p1", "p2"):
        ids = [k for k in key if k.startswith(part + "-")]
        table = collections.Counter((key[k]["pipeline"], PERSON[marks[k]["answer"]]) for k in ids)
        decided = [k for k in ids if marks[k]["answer"] != "D" and answer(k) != "unknown"]
        if part == "p2":
            same = sum((answer(k) == "typed by a person") == (marks[k]["answer"] == "A") for k in decided)
        else:
            same = sum(answer(k) == PERSON[marks[k]["answer"]] for k in decided)
        out[part] = {"cards": len(ids), "decided": len(decided), "agree": same,
                     "cant_tell": sum(marks[k]["answer"] == "D" for k in ids),
                     "table": [[p, q, n] for (p, q), n in sorted(table.items())]}
    return out


def main() -> int:
    args = sys.argv[1:]
    out_file = None
    if "--json" in args:
        i = args.index("--json")
        out_file = args[i + 1]
        del args[i:i + 2]
    key = json.load(open(args[0]))
    got = json.load(open(args[1]))
    marks = got.get("marks", got)
    if any(not (marks.get(k) or {}).get("answer") for k in key):
        sys.exit("not every card is marked; nothing reported")
    rows = {(r["session_key"], r["turn_number"]) for r in map(json.loads, open(args[2]))}
    swe = json.load(open(args[3]))
    fixed = copy.deepcopy(key)
    changed = []
    for k, c in fixed.items():
        if k.startswith("p2-") and (c["session_key"], c["turn_number"]) in rows and c["pipeline"] != "not typed":
            c["pipeline"] = "not typed"
            changed.append(k)
        if k.startswith("p1-"):
            if c["turn_id"] not in swe:
                sys.exit(f"{k}: its prompt is not in the fixed matching")
            if swe[c["turn_id"]] != c["pipeline"]:
                c["pipeline"] = swe[c["turn_id"]]
                changed.append(k)
    before, after = score(key, marks), score(fixed, marks)
    for part in ("p1", "p2"):
        b, a = before[part], after[part]
        print(f"Part {part[1]}: agree {b['agree']} of {b['decided']} before the fix, {a['agree']} of {a['decided']} after")
    print("cards whose pipeline answer the fix changed:", len(changed),
          "; the person's marks on them:", dict(collections.Counter(PERSON[marks[k]["answer"]] for k in changed)))
    if out_file:
        json.dump({"before": before, "after": after, "changed": changed,
                   "changed_marks": {k: PERSON[marks[k]["answer"]] for k in changed}}, open(out_file, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
