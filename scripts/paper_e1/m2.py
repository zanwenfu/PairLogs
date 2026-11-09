"""Read-only: SWE-chat v2's labelled user prompts matched to our rows by text (every agent, with or without entry ids).

A prompt is matched to our user-role rows of the same session whose text starts the same (first 80 characters,
lower-cased, spaces collapsed). Classes: typed by a person (a typed row matches), written by a program (only rows not
typed match), unmatched. The entry-id method of m1 is used as a cross-check where both apply.
Writes JSON to argv[1].
"""
import json
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
OURC = f"read_parquet(['{V}/conversations/entire/*.parquet', '{V}/conversations/swe-chat-v2/*.parquet'], union_by_name=true)"
PB = "('correction', 'rejection', 'failure_report')"
KEY = "left(trim(lower(regexp_replace(left({c}, 600), '\\s+', ' ', 'g'))), 80)"
OUT = sys.argv[1]

con = duckdb.connect()
con.execute("SET threads=8; SET memory_limit='28GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
res = {}


def q(name, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    res[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    print(f"== {name}: {len(res[name])} rows", file=sys.stderr, flush=True)


con.execute(f"""
create temp table sc as
select row_number() over () as id, session_id, source_entry_uuid as u, agent, prompt_pushback as pp,
       (prompt_pushback in {PB}) as is_pb, {KEY.format(c='content')} as key, length(content) as len,
       left(content, 160) as head
from read_parquet('{SC}')
where turn_type = 'user_prompt' and prompt_pushback is not null""")
con.execute(f"""
create temp table ourk as
select session_id, {KEY.format(c='content')} as key, bool_or(typed_by_developer) as typed,
       bool_or(not typed_by_developer) as untyped, string_agg(distinct actor, ',' order by actor) as actors,
       string_agg(distinct kind, ',' order by kind) as kinds
from {OURC}
where role = 'user' and content is not null and length(content) > 0
  and session_id in (select distinct session_id from sc)
group by all""")
con.execute("""
create temp table cls as
select sc.*, o.typed, o.untyped, o.actors, o.kinds,
       case when o.key is null then 'unmatched'
            when o.typed then 'typed by a person'
            else 'written by a program' end as cls
from sc left join ourk o on sc.session_id = o.session_id and sc.key = o.key""")
q("cls_all", f"""select cls, count(*) n, sum(is_pb::int) n_pb, round(100.0 * avg(is_pb::int), 1) pb_pct
                 from cls group by all order by n desc""")
q("cls_by_agent", f"""select agent, cls, count(*) n, round(100.0 * avg(is_pb::int), 1) pb_pct
                      from cls where agent in ('Claude Code', 'Codex', 'OpenCode', 'Pi', 'Cursor', 'Copilot CLI',
                                               'Gemini CLI') group by all order by agent, n desc""")
q("pb_rates", """select agent, count(*) n_all, round(100.0 * avg(is_pb::int), 1) pb_all,
                        count(*) filter (where cls = 'typed by a person') n_typed,
                        round(100.0 * avg(is_pb::int) filter (where cls = 'typed by a person'), 1) pb_typed,
                        count(*) filter (where cls = 'written by a program') n_program,
                        count(*) filter (where cls = 'unmatched') n_unmatched
                 from cls group by rollup(agent) order by n_all desc limit 12""")
q("program_kinds", """select actors, kinds, count(*) n from cls where cls = 'written by a program'
                      group by all order by n desc limit 25""")
for c in ("typed by a person", "written by a program", "unmatched"):
    q(f"sample_{c.replace(' ', '_')}", f"""select agent, pp, len, head from cls where cls = '{c}'
                                          order by hash(id * 7919) limit 80""")

# cross-check against m1's entry-id method, where SWE-chat has an entry id
con.execute(f"""
create temp table byid as
select session_id, source_entry_uuid as u, bool_or(typed_by_developer) as typed
from {OURC} where source_entry_uuid in (select distinct u from sc where u is not null) group by all""")
q("crosscheck_vs_entry_id", """select c.cls as by_text,
        case when b.u is null then 'no entry' when b.typed then 'typed' else 'not typed' end as by_entry_id, count(*) n
        from cls c left join byid b on c.session_id = b.session_id and c.u = b.u
        where c.u is not null group by all order by n desc""")

json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps({k: v for k, v in res.items() if not k.startswith("sample_")}, indent=1, default=str))
