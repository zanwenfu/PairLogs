"""Read-only: who wrote what agents' logs file as the user's, and what that does to common measures, by agent, across
all six sources of a version (VERSION_DIR). The use case on PairLogs' own data (2026-11-06; it
shows the new data, beside the re-reading of SWE-chat).

Unit: an entry (rows sharing a transcript entry; queued messages' delivered copies skipped), in kept sessions. Classes
as m9: typed by a person; a person's action (developer actor, not typed: interrupts, plan approvals, commands); program
(harness, agent, tool, peer). Pushback: our released label (gpt-6-luna), on typed entries only.
Per agent family (m9's grouping of the names logs use), with 95% intervals from 2,000 draws that resample clusters
(a repository; a DataClaw upload; an OpenHands feedback session):
- the share of user entries a program wrote, a person typed, a person's action;
- user entries per session, all and typed only; sessions with no typed message (in any role);
- pushback on typed entries (and with the typed messages logs keep outside the user role: queued messages, answers to
  the agent's questions), and the rate a study gets if it divides the same pushback by every user entry
  ("naive"), with each agent's rank under both;
- the same agent's program share by the logger that recorded it (its source), and the agents compared inside one
  logger (Entire's sessions only);
- all of it again on the core (sessions with a typed entry, outside the scripted repositories: core.py's rule) and
  without each agent's five largest clusters.
argv: OUT.json
"""
import json
import os
import random
import sys
from collections import Counter

import duckdb

V = os.environ["VERSION_DIR"]
SESS = f"read_parquet('{V}/data/*/*.parquet', union_by_name=true)"
CONV = f"read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)"   # main conversations: helpers' rows are no user's
SAMPLE = int(os.environ.get("M17_SAMPLE", "0"))   # a dry run: 1 in SAMPLE sessions
OUT = sys.argv[1]
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp-m17'")
con.execute("SET preserve_insertion_order=false")
PRODUCT = """case
  when agent in ('Codex', 'Codex (scoped transcript export)') then 'Codex'
  when agent in ('Cursor', 'Cursor CLI') then 'Cursor'
  when agent in ('VS Code Copilot', 'copilotide', 'Copilot CLI') then 'GitHub Copilot'
  when agent in ('Pi', 'Oh My Pi') then 'Pi'
  when agent in ('Antigravity', 'Antigravity CLI') then 'Antigravity'
  when agent is null then '(not named)' else agent end"""
CLUSTER = """case when repo is not null then repo
  when source = 'dataclaw' then split_part(session_key, ':', 2) || ':' ||
       coalesce(nullif(regexp_extract(split_part(session_key, ':', 3), '^(raw/[^/]+|[^/]+/)', 1), ''), '')
  else session_key end"""
res = {}

# kept sessions; the session's agent, as its session row names it (one per session); the core flag as core.py (a typed
# message in any role, outside the scripted repositories)
con.execute(f"""create temp table s as
select session_key, source, repo, {PRODUCT} as product, {CLUSTER} as cluster from {SESS} where duplicate_of is null
{f"and hash(session_key) % {SAMPLE} = 0" if SAMPLE else ""}""")
con.execute(f"""create temp table e as
select c.session_key,
  case when bool_or(c.typed_by_developer) then 'typed'
       when bool_or(c.actor = 'developer') then 'action' else 'program' end as cls,
  bool_or(c.pushback) filter (where c.typed_by_developer) as pb,
  bool_or(c.typed_by_developer and c.pushback is not null) as labelled,
  bool_or(c.typed_by_developer and c.kind = 'command') as cmd   -- a slash command a person typed (typed for every agent)
from {CONV} c
where c.role = 'user' and c.copy_of is null
  and c.session_key in (select session_key from s)
group by c.session_key, coalesce(c.source_entry_uuid, c.turn_number::varchar)""")
# typed text outside the user role: a message typed while the agent worked (queued; its delivered copy is skipped above)
# and an answer to the agent's own question (recorded as a tool result). Not what the log files as the user's, so not
# in the shares above; counted for typed messages and their pushback (the paper's 446,014 typed messages hold them).
con.execute(f"""create temp table xt as
select c.session_key, bool_or(c.pushback) as pb, bool_or(c.pushback is not null) as labelled
from {CONV} c
where c.role <> 'user' and c.typed_by_developer and c.copy_of is null and c.content is not null
  and c.session_key in (select session_key from s)
group by c.session_key, coalesce(c.source_entry_uuid, c.turn_number::varchar)""")
con.execute(f"""create temp table firsts as
select session_key, first(content order by turn_number) first_typed from {CONV}
where typed_by_developer and copy_of is null and content is not null
group by 1""")
con.execute("""create temp table scripted as
select s.repo from s join firsts f using (session_key) where s.repo is not null
group by 1 having count(*) >= 20 and count(distinct left(f.first_typed, 60)) <= 0.2 * count(*)""")
con.execute("""create temp table ss as
select s.*, coalesce(x.n, 0) n, coalesce(x.typed, 0) typed, coalesce(x.program, 0) program, coalesce(x.action, 0) action,
       coalesce(x.pb, 0) pb, coalesce(x.labelled, 0) labelled,
       coalesce(y.typed_x, 0) typed_x, coalesce(y.pb_x, 0) pb_x, coalesce(y.labelled_x, 0) labelled_x,
       coalesce(x.typed_cmd, 0) typed_cmd, coalesce(x.pb_nc, 0) pb_nc, coalesce(x.labelled_nc, 0) labelled_nc,
       coalesce(x.typed, 0) + coalesce(y.typed_x, 0) > 0
         and (s.repo is null or s.repo not in (select repo from scripted)) as core
from s left join (select session_key, count(*) n, count(*) filter (where cls = 'typed') typed,
                         count(*) filter (where cls = 'program') program, count(*) filter (where cls = 'action') action,
                         count(*) filter (where pb) pb, count(*) filter (where labelled) labelled,
                         count(*) filter (where cls = 'typed' and cmd) typed_cmd,
                         count(*) filter (where pb and not cmd) pb_nc, count(*) filter (where labelled and not cmd) labelled_nc
                  from e group by 1) x using (session_key)
       left join (select session_key, count(*) typed_x, count(*) filter (where pb) pb_x,
                         count(*) filter (where labelled) labelled_x from xt group by 1) y using (session_key)""")
res["checks"] = dict(zip(["kept", "entries", "typed", "program", "action", "pb", "labelled", "core", "scripted_repos",
                          "typed_outside_user_role", "with_typed_any_role"],
                         con.execute("""select count(*), sum(n), sum(typed), sum(program), sum(action), sum(pb), sum(labelled),
                                               count(*) filter (where core), (select count(*) from scripted), sum(typed_x),
                                               count(*) filter (where typed + typed_x > 0) from ss""").fetchone()))
assert res["checks"]["entries"] == res["checks"]["typed"] + res["checks"]["program"] + res["checks"]["action"]
# what falls into "program" without a recorded author, and sessions with no user entry at all (counted as holding no
# typed message): both counted, not assumed
res["checks"]["user_rows_no_actor"] = con.execute(f"""select count(*) from {CONV} c where c.role = 'user'
    and c.copy_of is null and c.actor is null and c.session_key in (select session_key from s)""").fetchone()[0]
res["checks"]["sessions_no_user_entry_by_agent"] = dict(con.execute(
    "select product, count(*) filter (where n = 0) from ss group by 1 order by 1").fetchall())

FIELDS = ["sessions", "n", "typed", "program", "action", "no_typed", "pb", "labelled", "typed_x", "pb_x", "labelled_x",
          "typed_cmd", "pb_nc", "labelled_nc"]


def per_cluster(where):
    rows = con.execute(f"""select product, cluster, count(*), sum(n), sum(typed), sum(program), sum(action),
                                  count(*) filter (where typed + typed_x = 0), sum(pb), sum(labelled), sum(typed_x),
                                  sum(pb_x), sum(labelled_x), sum(typed_cmd), sum(pb_nc), sum(labelled_nc)
                           from ss where {where} group by 1, 2""").fetchall()
    out = {}
    for p, c, *v in rows:
        out.setdefault(p, {})[c] = [int(x or 0) for x in v]
    return out


def measures(t):
    sessions, n, typed, program, action, no_typed, pb, labelled, typed_x, pb_x, labelled_x, typed_cmd, pb_nc, labelled_nc = t
    if not sessions or not n:
        return None
    return {"sessions": sessions, "entries": n, "program_pct": 100 * program / n, "typed_pct": 100 * typed / n,
            "action_pct": 100 * action / n, "entries_per_session": n / sessions, "typed_per_session": typed / sessions,
            "typed_any_role_per_session": (typed + typed_x) / sessions,
            "no_typed_pct": 100 * no_typed / sessions,   # no typed message in any role
            "pb_typed_pct": 100 * pb / labelled if labelled else None,
            "pb_typed_any_role_pct": 100 * (pb + pb_x) / (labelled + labelled_x) if labelled + labelled_x else None,
            "pb_naive_pct": 100 * pb / n if labelled else None,
            # typed slash commands: their share of typed entries, their pushback, and the typed rate without them (11-08)
            "cmd_share_pct": 100 * typed_cmd / typed if typed else None,
            "pb_cmd_pct": 100 * (pb - pb_nc) / (labelled - labelled_nc) if labelled > labelled_nc else None,
            "pb_typed_no_cmd_pct": 100 * pb_nc / labelled_nc if labelled_nc else None}


def total(clusters, keys):
    t = [0] * len(FIELDS)
    for k in keys:
        t = [a + b for a, b in zip(t, clusters[k])]
    return t


def wtotal(clusters, cnt):
    t = [0] * len(FIELDS)
    for c, v in clusters.items():
        k = cnt.get(c)
        if k:
            t = [a + k * b for a, b in zip(t, v)]
    return t


def study(where, min_sessions=200, label=""):
    pc = per_cluster(where)
    # sorted, so the draws do not depend on the order the query returns agents in (a rerun gives the same intervals)
    prods = sorted(p for p in pc if sum(v[0] for v in pc[p].values()) >= min_sessions)
    point = {p: measures(total(pc[p], pc[p])) for p in prods}
    rng = random.Random("m17-2026-11-07-joint")
    keys = {p: sorted(pc[p]) for p in prods}
    # one draw of clusters for every agent at once (a repository holds several agents' sessions, so the agents' rates
    # are not independent): each draw resamples all clusters, and each agent sums the clusters drawn that hold it (as
    # m16b: separate draws per agent misstate the gaps' intervals)
    allc = sorted({c for p in prods for c in pc[p]})
    draws = []
    for _ in range(2000):
        cnt = Counter(rng.choice(allc) for _ in allc)
        draws.append({p: measures(wtotal(pc[p], cnt)) for p in prods})

    def ci(get):
        xs = sorted(x for x in (get(d) for d in draws) if x is not None)
        return [round(xs[int(0.025 * len(xs))], 1), round(xs[int(0.975 * len(xs)) - 1], 1)] if xs else None
    out = {}
    for p in sorted(prods, key=lambda p: -point[p]["sessions"]):
        m = point[p]
        out[p] = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in m.items()}
        out[p]["clusters"] = len(keys[p])
        for k in ("program_pct", "entries_per_session", "typed_per_session", "typed_any_role_per_session", "no_typed_pct",
                  "pb_typed_pct", "pb_typed_any_role_pct", "pb_naive_pct", "pb_typed_no_cmd_pct"):
            out[p][k + "_ci"] = ci(lambda d, p=p, k=k: d[p][k] if d[p] else None)
        top5 = sorted(keys[p], key=lambda c: -pc[p][c][1])[:5]
        w5 = measures(total(pc[p], [c for c in keys[p] if c not in top5]))
        out[p]["top5_share_of_entries"] = round(100 * sum(pc[p][c][1] for c in top5) / m["entries"], 1)
        out[p]["without_top5"] = {k: round(w5[k], 2) for k in ("program_pct", "pb_typed_pct", "pb_typed_any_role_pct",
                                                                 "pb_naive_pct", "no_typed_pct")
                                  if w5 and w5[k] is not None}
    ranked = [p for p in out if out[p]["pb_typed_pct"] is not None and p != "(not named)"]
    for k in ("pb_typed_pct", "pb_typed_any_role_pct", "pb_naive_pct"):
        order = sorted(ranked, key=lambda p: -out[p][k])
        for i, p in enumerate(order):
            out[p][k.replace("_pct", "_rank")] = i + 1
    # the gap between two agents under both rates, with its interval (every pair among the five largest named agents)
    big = [p for p in sorted(ranked, key=lambda p: -out[p]["sessions"])][:5]
    gaps = {}
    for i, a in enumerate(big):
        for b in big[i + 1:]:
            g = {}
            for k in ("pb_typed_pct", "pb_typed_any_role_pct", "pb_naive_pct", "program_pct", "pb_typed_no_cmd_pct"):
                if point[a][k] is None or point[b][k] is None:
                    continue
                g[k] = round(point[a][k] - point[b][k], 1)
                g[k + "_ci"] = ci(lambda d, a=a, b=b, k=k: d[a][k] - d[b][k] if d[a] and d[b] and d[a][k] is not None
                                  and d[b][k] is not None else None)
            gaps[f"{a} - {b}"] = g
    print(f"== {label}: {len(out)} agents", file=sys.stderr, flush=True)
    return {"by_agent": out, "gaps": gaps}


MIN = 200 // SAMPLE if SAMPLE else 200
res["all_kept"] = study("true", MIN, label="all kept")
res["core"] = study("core", MIN, label="core")
# one logger for every agent (Entire records Claude Code, Codex, Cursor, OpenCode, Copilot, Gemini CLI, Pi...): the
# agents compared with the logging tool held fixed
res["entire_only"] = study("source = 'entire'", MIN // 2 if MIN > 1 else 1, label="entire only")

# the same agent by the logger that recorded it (source), all kept sessions
rows = con.execute(f"""select product, source, count(*) sessions, sum(n) n, sum(program) program, sum(typed) typed,
                             count(distinct cluster) clusters from ss group by 1, 2 having count(*) >= {100 // SAMPLE if SAMPLE else 100}
                      order by product, sessions desc""").fetchall()
pcs = {}
for p, s_, c, *v in con.execute("""select product, source, cluster, count(*), sum(n), sum(program) from ss
                                   group by 1, 2, 3""").fetchall():
    pcs.setdefault((p, s_), {})[c] = [int(x or 0) for x in v]
rng = random.Random("m17-by-logger")
by_logger = []
for p, s_, sessions, n, program, typed, clusters in rows:
    ks = sorted(pcs[(p, s_)])
    xs = []
    for _ in range(2000):
        pick = [rng.choice(ks) for _ in ks]
        nn = sum(pcs[(p, s_)][k][1] for k in pick)
        xs.append(100 * sum(pcs[(p, s_)][k][2] for k in pick) / nn if nn else None)
    xs = sorted(x for x in xs if x is not None)
    by_logger.append({"agent": p, "source": s_, "sessions": sessions, "entries": int(n or 0), "clusters": clusters,
                      "program_pct": round(100 * program / n, 1) if n else None,
                      "program_pct_ci": [round(xs[int(0.025 * len(xs))], 1), round(xs[int(0.975 * len(xs)) - 1], 1)] if xs else None})
res["by_logger"] = by_logger
json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
