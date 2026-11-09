"""Read-only: are our extra tool calls duplicates? Per agent, tool-call rows vs distinct call ids in the Entire sessions
SWE-chat also holds; and, for sessions where ours reads more, what the extra rows are."""
import json, sys
import duckdb
V = "/path/to/pairlogs-data/2026-10-11"
SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
con = duckdb.connect(); con.execute("SET threads=16; SET memory_limit='56GB'")
con.execute(f"""create temp table pair as select s.session_id sc_sid, s.duplicate_of our_key, any_value(s.agent) agent
  from read_parquet('{V}/data/swe-chat-v2/*.parquet', union_by_name=true) s where s.duplicate_of like 'entire:%' group by all""")
con.execute(f"""create temp table o as select c.session_key, p.agent, c.tool_call_id, c.tool_name, c.raw_line
  from read_parquet('{V}/conversations/entire/*.parquet', union_by_name=true) c join pair p on c.session_key = p.our_key
  where c.kind = 'tool_call'""")
con.execute(f"""create temp table t as select s.session_id, p.agent, s.tool_call_id, s.tool_name from read_parquet('{SC}') s
  join pair p on s.session_id = p.sc_sid where s.turn_type = 'tool_use'""")
res = {}
res["ours_rows_vs_ids"] = con.execute("""select agent, count(*) rows, count(distinct session_key || '#' || coalesce(tool_call_id, raw_line::varchar)) distinct_ids,
   count(*) filter (where tool_call_id is null) no_id from o group by all order by rows desc limit 8""").fetchall()
res["theirs_rows_vs_ids"] = con.execute("""select agent, count(*) rows, count(distinct session_id || '#' || coalesce(tool_call_id, '')) distinct_ids,
   count(*) filter (where tool_call_id is null) no_id from t group by all order by rows desc limit 8""").fetchall()
# ids only we have (by agent), and the tool names of those calls
res["ids_only_ours"] = con.execute("""select o.agent, count(*) n from o
   where o.tool_call_id is not null and not exists (select 1 from t where t.tool_call_id = o.tool_call_id)
   group by all order by n desc limit 8""").fetchall()
res["only_ours_tool_names"] = con.execute("""select o.agent, o.tool_name, count(*) n from o
   where o.tool_call_id is not null and not exists (select 1 from t where t.tool_call_id = o.tool_call_id)
   group by all order by n desc limit 25""").fetchall()
json.dump(res, open(sys.argv[1], "w"), indent=1, default=str); print(json.dumps(res, indent=1, default=str))
