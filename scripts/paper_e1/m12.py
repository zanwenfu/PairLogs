"""Read-only: our parser against SWE-chat's, on the Entire sessions both hold (version 2026-10-11).

Two independent readers of the same transcripts: per session, tool calls, agent text replies and thinking blocks.
SWE-chat's rows are its own (copied files); ours are the kept Entire sessions its copies point to. Writes JSON to
argv[1].
"""
import json
import sys

import duckdb

V = "/path/to/pairlogs-data/2026-10-11"
SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
OUT = sys.argv[1]
con = duckdb.connect()
con.execute("SET threads=16; SET memory_limit='56GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
res = {}


def q(name, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    res[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    print(f"== {name}", file=sys.stderr, flush=True)


# SWE-chat session id -> our kept Entire session (its copy points to it)
con.execute(f"""create temp table pair as
select s.session_id as sc_sid, s.duplicate_of as our_key, any_value(s.agent) as agent
from read_parquet('{V}/data/swe-chat-v2/*.parquet', union_by_name=true) s
where s.duplicate_of like 'entire:%' group by all""")
con.execute(f"""create temp table theirs as
select session_id as sc_sid,
       count(*) filter (where turn_type = 'tool_use') tool_calls,
       count(*) filter (where turn_type = 'assistant_response' and length(trim(coalesce(content, ''))) > 0) agent_texts,
       count(*) filter (where turn_type = 'assistant_thinking') thinking
from read_parquet('{SC}') where session_id in (select sc_sid from pair) group by all""")
con.execute(f"""create temp table ours as
select session_key as our_key,
       count(*) filter (where kind = 'tool_call') tool_calls,
       count(*) filter (where actor = 'agent' and kind = 'text' and length(trim(coalesce(content, ''))) > 0) agent_texts,
       count(*) filter (where kind = 'thinking') thinking
from read_parquet('{V}/conversations/entire/*.parquet', union_by_name=true)
where session_key in (select our_key from pair) group by all""")
con.execute("""create temp table cmp as
select p.agent, t.tool_calls t_tc, o.tool_calls o_tc, t.agent_texts t_at, o.agent_texts o_at, t.thinking t_th, o.thinking o_th
from pair p join theirs t using (sc_sid) join ours o using (our_key)""")
q("overall", """select count(*) sessions,
    sum(t_tc) sc_tool_calls, sum(o_tc) our_tool_calls, round(100.0*avg((t_tc = o_tc)::int),1) same_tool_calls_pct,
    round(100.0*avg((abs(t_tc - o_tc) <= greatest(1, 0.02*t_tc))::int),1) within_2pct_tool_calls,
    sum(t_at) sc_agent_texts, sum(o_at) our_agent_texts, round(100.0*avg((t_at = o_at)::int),1) same_agent_texts_pct,
    sum(t_th) sc_thinking, sum(o_th) our_thinking, round(100.0*avg((t_th = o_th)::int),1) same_thinking_pct
  from cmp""")
q("by_agent", """select agent, count(*) sessions, sum(t_tc) sc_tc, sum(o_tc) our_tc,
    round(100.0*avg((t_tc = o_tc)::int),1) same_tc, sum(t_at) sc_at, sum(o_at) our_at,
    round(100.0*avg((t_at = o_at)::int),1) same_at, sum(t_th) sc_th, sum(o_th) our_th
  from cmp group by all order by sessions desc limit 8""")
q("direction", """select sum((o_tc > t_tc)::int) ours_more_tc, sum((o_tc < t_tc)::int) ours_fewer_tc,
    sum((o_at > t_at)::int) ours_more_at, sum((o_at < t_at)::int) ours_fewer_at from cmp""")
json.dump(res, open(OUT, "w"), indent=1, default=str)
print(json.dumps(res, indent=1, default=str))
