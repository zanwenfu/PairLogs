"""Read-only, from m16's per-prompt file (keys and labels, no text): the use case again (1) with the repository
SWE-chat's own analysis code leaves out as machine-generated (`_EXCLUDED_REPOS = ("dayhaysoos/nimbus",)` in
SALT-NLP/SWE-chat's swechat.py, read 2026-11-06), so the comparison is made on SWE-chat's own analysis population, and
(2) for every agent in SWE-chat's labelled prompts, not only Claude Code and Codex: the program share, SWE-chat's
pushback rate on every prompt and on typed prompts, our label's on typed prompts, each agent's rank under each rate,
with 95% intervals from 2,000 draws of repositories.

python m16b.py PROMPTS.parquet OUT.json
"""
import json
import random
import sys
from collections import defaultdict

import duckdb

PROMPTS, OUT = sys.argv[1:3]
EXCLUDED = ("dayhaysoos/nimbus",)
TYPED = "cls = 'typed by a person'"
con = duckdb.connect()
con.execute("SET threads=4")
res = {"excluded_repos": list(EXCLUDED)}
res["excluded_prompts"] = dict(con.execute(f"""select agent, count(*) from read_parquet('{PROMPTS}')
                                               where repo_id in {EXCLUDED + ('',)} group by 1""").fetchall())
assert sum(res["excluded_prompts"].values()) > 0, "the excluded repository holds no labelled prompt: check its name"


def per_repo(where, agents):
    rows = con.execute(f"""select repo_id, agent, count(*), sum(is_pb::int), count(*) filter (where {TYPED}),
                                  sum(is_pb::int) filter (where {TYPED}),
                                  count(*) filter (where {TYPED} and our_pb is not null),
                                  sum(our_pb::int) filter (where {TYPED} and our_pb is not null),
                                  count(*) filter (where cls = 'program')
                           from read_parquet('{PROMPTS}') where {where} and agent in {tuple(agents) + ('',)}
                           group by 1, 2""").fetchall()
    out = defaultdict(dict)
    for repo, agent, *v in rows:
        out[repo][agent] = [int(x or 0) for x in v]
    return out


def rates(rows, repos, agents):
    tot = {a: [0] * 7 for a in agents}
    for r in repos:
        for a, v in rows[r].items():
            if a in tot:
                tot[a] = [x + y for x, y in zip(tot[a], v)]
    got = {}
    for a, (n, pb, nt, pbt, no, ob, npg) in tot.items():
        got[a] = {"pb_all": 100 * pb / n if n else None, "pb_typed": 100 * pbt / nt if nt else None,
                  "ours_typed": 100 * ob / no if no else None, "program": 100 * npg / n if n else None,
                  "n": n, "n_typed": nt}
    return got


def study(where, agents, pairs, seed):
    rows = per_repo(where, agents)   # repositories with a prompt of these agents, as m16 resamples them
    repos = sorted(rows, key=str)
    point = rates(rows, repos, agents)
    rng = random.Random(seed)
    boot = [rates(rows, [rng.choice(repos) for _ in repos], agents) for _ in range(2000)]

    def ci(get):
        xs = sorted(v for v in (get(b) for b in boot) if v is not None)
        return [round(xs[int(0.025 * len(xs))], 1), round(xs[int(0.975 * len(xs)) - 1], 1)] if xs else None
    out = {"repos": len(repos), "by_agent": {}, "gaps": {}}
    for a in agents:
        r = {k: (round(v, 1) if isinstance(v, float) else v) for k, v in point[a].items()}
        for k in ("pb_all", "pb_typed", "ours_typed", "program"):
            r[k + "_ci"] = ci(lambda b, a=a, k=k: b[a][k])
        r["repos_with_prompts"] = sum(1 for x in repos if a in rows[x])
        out["by_agent"][a] = r
    for k in ("pb_all", "pb_typed", "ours_typed"):
        ranked = sorted([a for a in agents if point[a][k] is not None], key=lambda a: -point[a][k])
        for i, a in enumerate(ranked):
            out["by_agent"][a][k + "_rank"] = i + 1
    for a, b in pairs:
        g = {}
        for k in ("pb_all", "pb_typed", "ours_typed", "program"):
            x, y = point[a][k], point[b][k]
            g[k] = None if x is None or y is None else round(x - y, 1)
            g[k + "_ci"] = ci(lambda bt, a=a, b=b, k=k: None if bt[a][k] is None or bt[b][k] is None
                              else bt[a][k] - bt[b][k])
        out["gaps"][f"{a} - {b}"] = g
    return out


EX = f"repo_id not in {EXCLUDED + ('',)}"
two = ("Claude Code", "Codex")
res["two_agents_swechat_population"] = study(EX, two, [two], 20261102)
res["two_agents_all"] = study("true", two, [two], 20261102)   # m16's own population: must give m16's numbers
counts = con.execute(f"select agent, count(*) from read_parquet('{PROMPTS}') where {EX} group by 1 order by 2 desc").fetchall()
res["prompts_by_agent"] = dict(counts)
agents = tuple(a for a, n in counts if a and n >= 1000)
res["every_agent_swechat_population"] = study(EX, agents, [("Claude Code", a) for a in agents if a != "Claude Code"],
                                              20261106)
json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
