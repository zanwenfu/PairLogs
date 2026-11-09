"""Read-only: where our tool-call count differs from SWE-chat's, did the session grow after SWE-chat's snapshot?"""
import json, sys
import duckdb
V = "/path/to/pairlogs-data/2026-10-11"
SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
con = duckdb.connect(); con.execute("SET threads=16; SET memory_limit='56GB'")
con.execute(f"""create temp table pair as select s.session_id sc_sid, s.duplicate_of our_key, any_value(s.agent) agent
  from read_parquet('{V}/data/swe-chat-v2/*.parquet', union_by_name=true) s where s.duplicate_of like 'entire:%' group by all""")
con.execute(f"""create temp table o as select c.session_key our_key, count(*) filter (where kind = 'tool_call') tc,
  max(timestamp) last_ts, min(timestamp) first_ts
  from read_parquet('{V}/conversations/entire/*.parquet', union_by_name=true) c where c.session_key in (select our_key from pair) group by all""")
con.execute(f"""create temp table t as select session_id sc_sid, count(*) filter (where turn_type = 'tool_use') tc, max(timestamp) last_ts
  from read_parquet('{SC}') where session_id in (select sc_sid from pair) group by all""")
con.execute("""create temp table cmp as select p.agent, o.tc o_tc, t.tc t_tc, o.last_ts o_last, t.last_ts t_last
  from pair p join o using (our_key) join t using (sc_sid)""")
res = {}
res["by_agent"] = con.execute("""select agent,
   count(*) filter (where o_tc > t_tc) ours_more,
   count(*) filter (where o_tc > t_tc and o_last > t_last + interval 1 minute) ours_more_and_later,
   sum(o_tc - t_tc) filter (where o_tc > t_tc) extra_calls,
   sum(o_tc - t_tc) filter (where o_tc > t_tc and o_last > t_last + interval 1 minute) extra_calls_in_later,
   count(*) filter (where o_tc < t_tc) ours_fewer, sum(t_tc - o_tc) filter (where o_tc < t_tc) missing_calls
  from cmp group by all order by extra_calls desc nulls last limit 10""").fetchall()
json.dump(res, open(sys.argv[1], "w"), indent=1, default=str); print(json.dumps(res, indent=1, default=str))
