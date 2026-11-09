"""Read-only: where our pushback label (gpt-6-luna, message alone) and SWE-chat's (Qwen3.5-9B, whole preceding
conversation, temperature 0.7) part ways, on typed messages both labelled (version 2026-10-11).

Writes JSON to argv[1] and a sample of disagreements, with the agent's preceding reply, to argv[2] (parquet).
"""
import json
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
OURC = f"read_parquet(['{V}/conversations/entire/*.parquet', '{V}/conversations/swe-chat-v2/*.parquet'], union_by_name=true)"
PB = "('correction', 'rejection', 'failure_report')"
LEAN = r"\b(undo|revert|wrong|broken|doesn'?t work|does not work|i said|you missed|never mind)\b"
OUT, SAMPLE = sys.argv[1], sys.argv[2]
con = duckdb.connect()
con.execute("SET threads=16; SET memory_limit='56GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
res = {}


def q(name, sql):
    try:
        cur = con.execute(sql)
    except Exception as e:
        res[name] = [{"error": str(e)[:300]}]
        print(f"== {name}: ERROR {e}", file=sys.stderr, flush=True)
        return
    cols = [d[0] for d in cur.description]
    res[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    print(f"== {name}: {len(res[name])} rows", file=sys.stderr, flush=True)


# every row of the sessions that hold a doubly-labelled typed message, in order; the agent's last text before each
con.execute(f"""
create temp table s as
select session_key, turn_number, typed_by_developer, actor, kind, copy_of, content, prompt_pushback, pushback, agent
from {OURC}
where session_key in (select distinct session_key from {OURC}
                      where typed_by_developer and prompt_pushback is not null and pushback is not null)""")
con.execute("""
create temp table w as
select *,
  last_value(case when actor = 'agent' and kind = 'text' and content is not null and length(content) > 0
                  then content end ignore nulls)
    over (partition by session_key order by turn_number rows between unbounded preceding and 1 preceding) as prev_agent,
  sum(case when typed_by_developer and copy_of is null then 1 else 0 end)
    over (partition by session_key order by turn_number rows between unbounded preceding and current row) as typed_n
from s""")
con.execute(f"""
create temp table b as
select session_key, turn_number, agent, content, prompt_pushback as sc4, (prompt_pushback in {PB}) as sc, pushback as ours,
       prev_agent, typed_n, length(content) as len,
       regexp_matches(lower(content), '{LEAN.replace(chr(39), chr(39) * 2)}') as lean
from w where typed_by_developer and copy_of is null and prompt_pushback is not null and pushback is not null""")
q("n", "select count(*) n, round(100*avg(sc::int),1) sc_pb, round(100*avg(ours::int),1) ours_pb, round(100*avg((sc = ours)::int),1) agree from b")
q("by_sc_class", """select sc4, count(*) n, round(100*avg(ours::int),1) ours_pushback_pct from b group by all order by n desc""")
q("first_typed_message", """select (typed_n = 1) as first_typed, count(*) n, round(100*avg(sc::int),1) sc_pb,
                                   round(100*avg(ours::int),1) ours_pb, round(100*avg((prev_agent is null)::int),1) no_agent_reply_before
                            from b group by all""")
q("no_agent_reply_before", """select (prev_agent is null) as nothing_before, count(*) n, round(100*avg(sc::int),1) sc_pb,
                                     round(100*avg(ours::int),1) ours_pb from b group by all""")
q("lean_words", """select lean, count(*) n, round(100*avg(sc::int),1) sc_pb, round(100*avg(ours::int),1) ours_pb,
                          round(100*avg((sc = ours)::int),1) agree from b group by all""")
q("length", """select case when len < 20 then 'a <20' when len < 60 then 'b 20-59' when len < 200 then 'c 60-199'
                           when len < 1000 then 'd 200-999' else 'e 1000+' end as length, count(*) n,
                      round(100*avg(sc::int),1) sc_pb, round(100*avg(ours::int),1) ours_pb,
                      round(100*avg((sc = ours)::int),1) agree from b group by all order by 1""")
q("by_agent", """select agent, count(*) n, round(100*avg(sc::int),1) sc_pb, round(100*avg(ours::int),1) ours_pb,
                        round(100*avg((sc = ours)::int),1) agree from b group by all order by n desc limit 8""")
# identical texts: SWE-chat's label varies with context (and sampling); ours is one label per text by design
con.execute("create temp table t as select lower(trim(content)) as txt, sc, ours from b")
q("identical_texts", """select count(*) texts, sum((n >= 5)::int) texts_5plus,
        sum((n >= 5 and pbs > 0 and pbs < n)::int) mixed_sc_label_5plus
     from (select txt, count(*) n, sum(sc::int) pbs from t group by txt)""")
q("frequent_texts", """select txt, count(*) n, round(100*avg(sc::int),1) sc_pb, bool_or(ours) ours
                       from t group by txt having count(*) >= 15 order by n desc limit 40""")
# a sample of each disagreement cell and of agreement, with the agent's last reply before the message
con.execute(f"""copy (
  select * from (
    select case when sc and not ours then 'sc_only' when ours and not sc then 'ours_only'
                when sc and ours then 'both' else 'neither' end as cell,
           session_key, turn_number, agent, sc4, ours, left(prev_agent, 1200) prev_agent, left(content, 1500) content, len,
           row_number() over (partition by (sc, ours) order by hash(session_key || turn_number::varchar || '#m10')) r
    from b)
  where r <= 60
) to '{SAMPLE}' (format parquet)""")
json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str)[:12000])
