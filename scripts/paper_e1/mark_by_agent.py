"""Read-only (2026-11-08): what program text may still sit among each agent's TYPED user entries, by agent and logger.
The mark's precision is checked by source, not by agent, and agents other than
Claude Code and Codex record less about who wrote an entry, so their program shares may be low. Two signs of text a
program wrote, among entries the mark calls typed:
- the entry opens with a markup tag (<name ...>), as harness blocks do: counted by tag name;
- a long entry (200+ characters) whose opening (first 80 characters, case and spacing and digits folded) recurs in 10
  or more sessions of 3 or more clusters (repositories): a template. A person seldom types the same long opening in
  ten sessions of three repositories; scripts and harnesses do.
Neither is proof (people paste markup; teams share prompts); together they bound what the mark may miss in a form a
program repeats. Also, toward a second question (why Claude Code's user role holds less program text in
SpecStory's exports than in Entire's logs): the program entries by kind, per agent and logger.
And a robustness test of the use case: typed slash commands (kind = 'command', marked typed for every agent: a
person typed them) are seldom pushback, and agents' logs record them unevenly; the pushback rate over typed entries
with and without them, inside Entire's logs, with the Claude Code - other agent gaps and 95% intervals from 2,000 joint
draws of clusters (m17j's method).
Unit: an entry, as m17 (rows sharing a transcript entry; queued messages' delivered copies skipped; kept sessions).
Prints counts, tag names and, for templates only, the first 60 characters of the recurring opening.
VERSION_DIR=... python mark_by_agent.py OUT.json   (MBA_SAMPLE=20: a dry run on 1 in 20 sessions)
"""
import json
import os
import sys

import duckdb

V = os.environ["VERSION_DIR"]
SAMPLE = int(os.environ.get("MBA_SAMPLE", "0"))
OUT = sys.argv[1]
SESS = f"read_parquet('{V}/data/*/*.parquet', union_by_name=true)"
CONV = f"read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)"
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp-mba'")
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
con.execute(f"""create temp table s as
select session_key, source, {PRODUCT} as product, {CLUSTER} as cluster from {SESS} where duplicate_of is null
{f"and hash(session_key) % {SAMPLE} = 0" if SAMPLE else ""}""")
con.execute(f"""create temp table e as
select c.session_key,
  case when bool_or(c.typed_by_developer) then 'typed'
       when bool_or(c.actor = 'developer') then 'action' else 'program' end as cls,
  coalesce(first(c.kind order by c.turn_number) filter (where not coalesce(c.typed_by_developer, false)), '') as kind,
  bool_or(c.typed_by_developer and c.kind = 'command') as is_cmd,
  bool_or(c.pushback) filter (where c.typed_by_developer) as pb,
  bool_or(c.typed_by_developer and c.pushback is not null) as labelled,
  string_agg(c.content, ' ' order by c.turn_number) filter (where c.typed_by_developer) as typed_text
from {CONV} c
where c.role = 'user' and c.copy_of is null and c.session_key in (select session_key from s)
group by c.session_key, coalesce(c.source_entry_uuid, c.turn_number::varchar)""")
con.execute("""create temp table t as
select s.source, s.product, s.cluster, e.session_key, e.typed_text as x,
  regexp_extract(e.typed_text, '^\\s*<([A-Za-z_][A-Za-z0-9_:-]*)', 1) as tag,
  length(e.typed_text) as len,
  regexp_replace(regexp_replace(lower(left(ltrim(e.typed_text), 80)), '\\s+', ' ', 'g'), '[0-9]', '0', 'g') as opening
from e join s using (session_key) where e.cls = 'typed' and e.typed_text is not null""")
n_s = con.execute("select count(*) from s").fetchone()[0]
n_t = con.execute("select count(*) from t").fetchone()[0]
assert n_s > 0 and n_t > 0, (n_s, n_t)
groups = con.execute("""select source, product, count(distinct session_key) from s join e using (session_key)
                        group by all having count(distinct session_key) >= 500 / greatest(1, ?) order by 3 desc""",
                     [SAMPLE or 1]).fetchall()
res = {"version": V, "sample_1_in": SAMPLE or 1, "sessions": n_s, "typed_entries": n_t, "groups": []}
templates = con.execute("""select opening, count(distinct session_key) ns, count(distinct cluster) nc, count(*) n,
                                  list(distinct source) srcs, list(distinct product) prods
                           from t where len >= 200 group by opening
                           having count(distinct session_key) >= 10 and count(distinct cluster) >= 3""").fetchall()
tmpl = {o: (ns, nc, n) for o, ns, nc, n, _, _ in templates}
con.execute("create temp table tmpl(opening varchar)")
if tmpl:
    con.executemany("insert into tmpl values (?)", [(o,) for o in tmpl])
for src, prod, ns in groups:
    g = {"source": src, "product": prod, "sessions": ns}
    cls = dict(con.execute("""select cls, count(*) from e join s using (session_key) where source = ? and product = ?
                              group by cls""", [src, prod]).fetchall())
    g["entries"] = cls
    typed = cls.get("typed", 0)
    tags = con.execute("""select tag, count(*) from t where source = ? and product = ? and tag <> '' group by tag
                          order by 2 desc""", [src, prod]).fetchall()
    g["typed_opening_with_a_tag"] = sum(n for _, n in tags)
    g["typed_opening_with_a_tag_pct"] = round(100 * g["typed_opening_with_a_tag"] / typed, 2) if typed else None
    g["tags_top"] = tags[:10]
    tm = con.execute("""select opening, count(*) from t where source = ? and product = ? and len >= 200
                        and opening in (select opening from tmpl) group by opening order by 2 desc""",
                     [src, prod]).fetchall()
    g["typed_in_templates"] = sum(n for _, n in tm)
    g["typed_in_templates_pct"] = round(100 * g["typed_in_templates"] / typed, 2) if typed else None
    g["templates_top"] = [{"opening": o[:60], "entries_here": n, "sessions_all": tmpl[o][0], "clusters_all": tmpl[o][1]}
                          for o, n in tm[:12]]
    g["program_by_kind"] = dict(con.execute("""select kind, count(*) from e join s using (session_key)
                                               where source = ? and product = ? and cls = 'program' group by kind
                                               order by 2 desc""", [src, prod]).fetchall()[:12])
    cmd = con.execute("""select count(*) filter (where is_cmd),
                                 count(*) filter (where labelled), count(*) filter (where labelled and pb),
                                 count(*) filter (where labelled and not is_cmd), count(*) filter (where labelled and pb and not is_cmd)
                          from e join s using (session_key) where source = ? and product = ? and cls = 'typed'""",
                       [src, prod]).fetchone()
    g["typed_commands"] = cmd[0]
    g["pb_typed_pct"] = round(100 * cmd[2] / cmd[1], 2) if cmd[1] else None
    g["pb_typed_no_commands_pct"] = round(100 * cmd[4] / cmd[3], 2) if cmd[3] else None
    res["groups"].append(g)

# the robustness test inside Entire: per cluster, labelled typed entries and their pushback, with and without commands
import random
AG = ["Claude Code", "Codex", "OpenCode", "Cursor", "Pi", "GitHub Copilot"]
rows = con.execute("""select cluster, product, count(*) filter (where labelled), count(*) filter (where labelled and pb),
                               count(*) filter (where labelled and not is_cmd), count(*) filter (where labelled and pb and not is_cmd)
                        from e join s using (session_key) where source = 'entire' and cls = 'typed' group by all""").fetchall()
pc = {}
for cl, prod, n, k, n2, k2 in rows:
    if prod in AG:
        pc.setdefault(cl, {})[prod] = (n, k, n2, k2)
allc = sorted(pc)
def rates(cnt):
    t = {a: [0, 0, 0, 0] for a in AG}
    for cl, m in cnt.items():
        for a, v in pc[cl].items():
            for i in range(4):
                t[a][i] += v[i] * m
    return {a: (100 * v[1] / v[0] if v[0] else None, 100 * v[3] / v[2] if v[2] else None) for a, v in t.items()}
from collections import Counter
point = rates(Counter({c: 1 for c in allc}))
rng = random.Random("mark-by-agent-2026-11-08-joint")
draws = [rates(Counter(rng.choice(allc) for _ in allc)) for _ in range(2000)]
def iv(xs):
    xs = sorted(x for x in xs if x is not None)
    return [round(xs[50], 1), round(xs[1949], 1)] if len(xs) >= 1950 else None
rob = {"clusters": len(allc), "by_agent": {}, "gaps_vs_claude_code": {}}
for a in AG:
    rob["by_agent"][a] = {"pb_typed": round(point[a][0], 2) if point[a][0] is not None else None,
                          "pb_typed_no_commands": round(point[a][1], 2) if point[a][1] is not None else None}
for a in AG[1:]:
    g = {}
    for j, tag in ((0, "typed"), (1, "typed_no_commands")):
        if point["Claude Code"][j] is None or point[a][j] is None:
            continue
        g[tag] = round(point["Claude Code"][j] - point[a][j], 1)
        g[tag + "_ci"] = iv([d["Claude Code"][j] - d[a][j] if d["Claude Code"][j] is not None and d[a][j] is not None else None for d in draws])
    rob["gaps_vs_claude_code"][a] = g
res["entire_commands_robustness"] = rob
print(json.dumps(res, indent=1, ensure_ascii=False)[:20000])
json.dump(res, open(OUT, "w"), indent=1, ensure_ascii=False)
