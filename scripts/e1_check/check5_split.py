"""Check 5, the person's reading split by answer (added 2026-11-09, after the person's marks; the scoring rule of
agreement5.py is unchanged and stays the main figure). Among the rows the release marks typed, weighted to the data as
agreement5.py weights them (each source's typed stratum by its user-role rows, draw5.json): the shares the person reads
as typed (A), a person's action (B) and a program's or another agent's text (C), "can't tell" (D) left out; 95%
intervals from 2,000 draws of each stratum's three shares from Dirichlet(counts + 1/2), seed fixed. Also, as drawn: the
cards the mark calls typed that the person reads as B, and how many of them are a command or a short reply (a slash,
bang or dot command, a <command-name> block, or 12 characters or fewer).

python scripts/e1_check/check5_split.py runs/e1-check/e1_check5_key.json runs/e1-check/draw5.json \
    runs/e1-check/cards5.json <marks.json> OUT.json
"""
import json
import random
import sys
from collections import Counter

KEY, DRAW, CARDS, MARKS, OUT = sys.argv[1:6]
key = json.load(open(KEY))
pop = json.load(open(DRAW))["population"]
cards = {c["id"]: c for c in json.load(open(CARDS))}
got = json.load(open(MARKS))
assert got.get("sample") == "e1-check5b", got.get("sample")   # the person's page only
marks = got["marks"]
assert all((marks.get(k) or {}).get("answer") in ("A", "B", "C", "D") for k in key), "every card marked A-D"
rows = {p["source"]: p["rows"] for p in pop if p["typed"] is True}
by = {s: Counter() for s in rows}
for k, v in key.items():
    if v["pipeline"] == "typed by a person" and marks[k]["answer"] != "D":
        by[v["source"]][marks[k]["answer"]] += 1
assert all(sum(c.values()) for c in by.values()), by   # a typed stratum with no decided card would be left out unseen
W = sum(rows.values())


def shares(pick):
    return {a: sum(rows[s] * pick[s][a] for s in rows) / W for a in "ABC"}


point = shares({s: {a: by[s][a] / sum(by[s].values()) for a in "ABC"} for s in rows})
rng = random.Random("check5-split-2026-11-09")
draws = []
for _ in range(2000):
    pick = {}
    for s in rows:
        g = {a: rng.gammavariate(by[s][a] + 0.5, 1) for a in "ABC"}
        t = sum(g.values())
        pick[s] = {a: g[a] / t for a in "ABC"}
    draws.append(shares(pick))


def iv(a):
    xs = sorted(d[a] for d in draws)
    lo, hi = 100 * xs[50], 100 * xs[1949]
    p = 100 * point[a]
    return [round(min(lo, p), 1), round(max(hi, p), 1)]


short = lambda t: t.lstrip().startswith(("/", "!", ".", "<command-name>")) or len(t.strip()) <= 12
b_cards = [k for k, v in key.items() if v["pipeline"] == "typed by a person" and marks[k]["answer"] == "B"]
out = {"marks_sample": got.get("sample"),
       "corrected_B_to_A": (got.get("corrected") or {}).get("cards_B_to_A", []),
       "marked_typed_by_stratum": {s: dict(c) for s, c in by.items()},
       "weighted_among_marked_typed": {n: {"pct": round(100 * point[a], 1), "ci95": iv(a)}
                                        for a, n in (("A", "typed"), ("B", "action"), ("C", "program"))},
       "marked_typed_read_as_action": len(b_cards),
       "of_them_command_or_short_reply": sum(short(cards[k]["text"]) for k in b_cards),
       "marked_typed_read_as_program": sum(1 for k, v in key.items()
                                           if v["pipeline"] == "typed by a person" and marks[k]["answer"] == "C")}
print(json.dumps(out, indent=1))
json.dump(out, open(OUT, "w"), indent=1)
