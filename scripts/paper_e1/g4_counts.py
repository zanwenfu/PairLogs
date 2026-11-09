"""Read-only, version 2026-10-11: kept sessions, typed messages and objection moments per agent, programming language
and owner account type, under both objection labels (ours, gpt-6-luna on the message alone; SWE-chat's, its
correction, rejection or failure report, on the messages it labelled); and the sessions behind errata-bench's 146
tasks, found or not. argv: owner types JSON, errata-bench CSV, output JSON."""
import csv
import json
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
PB = "('correction', 'rejection', 'failure_report')"
types, tasks_csv, out = sys.argv[1], sys.argv[2], sys.argv[3]
con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
owner = json.load(open(types))
con.execute("create temp table owner(login varchar, kind varchar)")
con.executemany("insert into owner values (?, ?)", list(owner.items()))
con.execute(f"""create temp table s as
select session_key, session_id, source, agent, repo, coalesce(repo_language, 'none recorded') lang,
       case when repo is null or repo not like '%/%' then 'no repository (copied export)'
            else coalesce((select kind from owner where login = split_part(repo, '/', 1)), 'unknown') end account
from read_parquet('{V}/data/*/*.parquet', union_by_name=true) where duplicate_of is null""")
con.execute(f"""create temp table t as
select session_key, pushback, prompt_pushback from read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)
where typed_by_developer and copy_of is null""")
con.execute("""create temp table per as
select s.*, count(t.session_key) typed, count(t.pushback) ours_labelled, count(*) filter (where t.pushback) ours_obj,
       count(t.prompt_pushback) sc_labelled, count(*) filter (where t.prompt_pushback in """ + PB + """) sc_obj
from s left join t using (session_key) group by all""")
top_agents = [r[0] for r in con.execute("select agent, count(*) n from per group by 1 order by n desc limit 9").fetchall()]
top_langs = [r[0] for r in con.execute("select lang, count(*) n from per group by 1 order by n desc limit 13").fetchall()]
METRICS = """count(*) sessions, count(*) filter (where typed > 0) sessions_typed, sum(typed) typed_messages,
  sum(ours_obj) ours_objections, round(100.0 * sum(ours_obj) / nullif(sum(ours_labelled), 0), 1) ours_pct,
  count(*) filter (where ours_obj > 0) sessions_with_ours_objection,
  sum(sc_labelled) sc_labelled, sum(sc_obj) sc_objections, round(100.0 * sum(sc_obj) / nullif(sum(sc_labelled), 0), 1) sc_pct,
  count(*) filter (where sc_obj > 0) sessions_with_sc_objection"""
res = {}


def q(name, sql, params=()):
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    res[name] = [dict(zip(cols, r)) for r in cur.fetchall()]


q("all", f"select {METRICS} from per")
q("by_agent", f"select case when agent in ? then agent else 'other' end agent, {METRICS} from per group by 1 order by sessions desc", [top_agents])
q("by_language", f"select case when lang in ? then lang else 'other' end language, {METRICS} from per group by 1 order by sessions desc", [top_langs])
q("by_account", f"select account, {METRICS} from per group by 1 order by sessions desc")
q("claude_codex_by_account", f"select agent, account, {METRICS} from per where agent in ('Claude Code', 'Codex') group by all order by agent, sessions desc")
q("claude_codex_by_language", f"select agent, case when lang in ? then lang else 'other' end language, {METRICS} from per where agent in ('Claude Code', 'Codex') group by all order by agent, sessions desc", [top_langs])
tasks = list(csv.DictReader(open(tasks_csv)))
con.execute("create temp table eb(task_id varchar, agent varchar, session_id varchar, repo varchar, fair varchar, forge varchar)")
con.executemany("insert into eb values (?, ?, ?, ?, ?, ?)", [(t["task_id"], t["agent"], t["session_id"], t["repo"], t["fair"], t["in_forge_34"]) for t in tasks])
q("eb_found", """select count(*) tasks, count(distinct eb.session_id) sessions,
  count(*) filter (where p.session_key is not null) tasks_found, count(distinct p.session_key) sessions_found
  from eb left join per p on p.session_id = eb.session_id and p.agent = eb.agent""")
q("eb_by_account_language", """select p.account, p.lang language, count(*) tasks, count(*) filter (where eb.fair = 'True') fair,
  count(*) filter (where eb.forge = 'True') in_forge_34
  from eb join per p on p.session_id = eb.session_id and p.agent = eb.agent group by all order by tasks desc""")
q("eb_missing", """select eb.agent, eb.session_id, eb.repo from eb left join per p on p.session_id = eb.session_id and p.agent = eb.agent
  where p.session_key is null limit 20""")
json.dump(res, open(out, "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in res.items() if k in ("all", "by_account", "eb_found")}, indent=1, default=str))
