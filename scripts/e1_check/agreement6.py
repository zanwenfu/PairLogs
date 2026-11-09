"""The per-agent who-typed check (check 6): a person's marks on SWE-chat's labelled prompts of Claude Code and Codex,
against the release's mark, as drawn and weighted, per agent.

python scripts/e1_check/agreement6.py runs/e1-check/e1_check6_key.json runs/e1-check/draw6.json <marks.json> [--json OUT]

The rule, fixed 2026-11-06 before any card was marked: A is typed; B (a person's
action) and C (a program) are not typed; D ("can't tell") is counted and left out. Reported per agent:
- as drawn: agreement on typed or not where the person decided, the typed mark's precision (A among cards marked
  typed), the program mark's precision (C among cards marked program; B counted apart), Cohen's kappa, Wilson 95%
  intervals; Codex inside and outside the five repositories with the most labelled prompts;
- weighted to the prompts the mark calls typed or program (each stratum by its rows, draw6.json): the share a person
  reads as a program's (C) and as typed (A), beside the mark's own program share on the same rows, and the gap between
  the agents in the person's program share; 95% intervals from 2,000 draws of each stratum's shares of the three
  answers from their Jeffreys posterior, Dirichlet(counts + 1/2), extended to the point when a share is 0 or 1 (changed
  2026-11-07, before the person marked any card, after an independent review: resampling 10 cards a stratum gave
  intervals of width 0 where every card agreed).
  The cards were drawn one per repository (in the five repositories with the most labelled prompts, in turn;
  three of them hold Codex's), and weighted
  here by the stratum's rows as if drawn at random: a repository's size counts only through which repositories were
  drawn, so the weighted shares lean toward smaller repositories. The base is the prompts the mark calls typed or
  program; those it calls a person's action are left out.
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
bad = {marks[k]["answer"] for k in key} - set("ABCD")
if bad:
    sys.exit(f"unknown answers {bad}")

AGENTS = ("Claude Code", "Codex")
pop = {(p["agent"], p["mark"], p["part"]): p["rows"] for p in draw["population"]}
cards = [{"stratum": tuple(v["stratum"].split("|")), "agent": v["agent"], "typed": v["mark"] == "typed",
          "part": v["part"], "answer": marks[k]["answer"]} for k, v in key.items()]
assert {c["stratum"] for c in cards} == set(pop), "strata in the key and the draw differ"
decided = [c for c in cards if c["answer"] != "D"]


def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(100 * (c - h), 1), round(100 * (c + h), 1)]


def share(sel, ans):
    n = len(sel)
    k = sum(c["answer"] == ans for c in sel)
    return {"k": k, "of": n, "pct": round(100 * k / n, 1) if n else None, "wilson95": wilson(k, n)}


def kappa(sel):
    n = len(sel)
    if not n:
        return None
    a = sum(c["typed"] == (c["answer"] == "A") for c in sel) / n
    pm = sum(c["typed"] for c in sel) / n
    pp = sum(c["answer"] == "A" for c in sel) / n
    e = pm * pp + (1 - pm) * (1 - pp)
    return round((a - e) / (1 - e), 3) if e < 1 else None


def weighted(shares, agent):
    """Over the agent's prompts the mark calls typed or program: each stratum's shares of the answers times its rows.
    shares: {stratum: {"A": a, "B": b, "C": c}}."""
    rows = {s: n for s, n in pop.items() if s[0] == agent}
    tot = sum(rows.values())
    out = {"mark_program": 100 * sum(n for s, n in rows.items() if s[1] == "program") / tot}
    if any(s not in shares for s in rows):
        return None   # a stratum with no decided card: no weighted figure
    for name, ans in (("person_program", "C"), ("person_typed", "A"), ("person_action", "B")):
        out[name] = 100 * sum(n * shares[s][ans] for s, n in rows.items()) / tot
    return out


by_stratum = {}
for c in decided:
    by_stratum.setdefault(c["stratum"], []).append(c)
counts = {s: {a: sum(c["answer"] == a for c in cs) for a in "ABC"} for s, cs in sorted(by_stratum.items())}
point = {a: weighted({s: {x: v[x] / sum(v.values()) for x in v} for s, v in counts.items()}, a) for a in AGENTS}
rng = random.Random("e1-check6-2026-11-07-jeffreys")
draws = []
for _ in range(2000):
    sh = {}
    for s, v in counts.items():
        g = {x: rng.gammavariate(v[x] + 0.5, 1.0) for x in "ABC"}
        t = sum(g.values())
        sh[s] = {x: g[x] / t for x in g}
    draws.append({a: weighted(sh, a) for a in AGENTS})


def ci(get, at=None):
    xs = sorted(x for x in (get(d) for d in draws) if x is not None)
    if not xs:
        return None
    lo, hi = xs[50], xs[1949]
    if at is not None:   # the point itself when a share is 0 or 1 (no draw reaches it)
        lo, hi = min(lo, at), max(hi, at)
    return [round(lo, 1), round(hi, 1)]


out = {"cards": len(cards), "cant_tell": len(cards) - len(decided), "decided": len(decided),
       "answers": dict(Counter(c["answer"] for c in cards)), "by_agent": {}}
for a in AGENTS:
    d = [c for c in decided if c["agent"] == a]
    r = {"as_drawn": {
        "agreement_typed_or_not": {"same": sum(c["typed"] == (c["answer"] == "A") for c in d), "of": len(d),
                                   "wilson95": wilson(sum(c["typed"] == (c["answer"] == "A") for c in d), len(d))},
        "kappa": kappa(d),
        "typed_mark_precision": share([c for c in d if c["typed"]], "A"),
        "program_mark_precision": share([c for c in d if not c["typed"]], "C"),
        "program_mark_read_as_action": share([c for c in d if not c["typed"]], "B"),
        "answers_by_stratum": {"|".join(s): dict(Counter(c["answer"] for c in cards if c["stratum"] == s))
                               for s in sorted(pop) if s[0] == a}}}
    if a == "Codex":
        for part in ("top5", "rest"):
            dp = [c for c in d if c["part"] == part]
            r["as_drawn"][f"agreement_{part}"] = {"same": sum(c["typed"] == (c["answer"] == "A") for c in dp),
                                                  "of": len(dp)}
    if point[a]:
        r["weighted"] = {m: {"pct": round(v, 1), "ci95": ci(lambda x, a=a, m=m: x[a][m] if x[a] else None, at=v)}
                         for m, v in point[a].items()}
    out["by_agent"][a] = r
if point["Codex"] and point["Claude Code"]:
    g = point["Codex"]["person_program"] - point["Claude Code"]["person_program"]
    out["gap_person_program_codex_minus_cc"] = {
        "pct_points": round(g, 1),
        "ci95": ci(lambda x: x["Codex"]["person_program"] - x["Claude Code"]["person_program"]
                   if x["Codex"] and x["Claude Code"] else None, at=g),
        "mark": round(point["Codex"]["mark_program"] - point["Claude Code"]["mark_program"], 1)}
print(json.dumps(out, indent=1))
if JSON_OUT:
    json.dump(out, open(JSON_OUT, "w"), indent=1)
