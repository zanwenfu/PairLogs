"""Read-only: Claude Code sessions where SWE-chat counts more agent replies than we do: what are its extra replies in our
rows? Match SWE-chat's assistant_response texts to our rows of the same session by their first 60 letters."""
import json, sys
import duckdb
V = "/path/to/pairlogs-data/2026-10-11"
SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
NORM = "left(lower(regexp_replace(left({c}, 2000), '[\\s\\p{{P}}\\p{{S}}]+', '', 'g')), 60)"
con = duckdb.connect(); con.execute("SET threads=16; SET memory_limit='56GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
con.execute(f"""create temp table pair as select s.session_id sc_sid, s.duplicate_of our_key
  from read_parquet('{V}/data/swe-chat-v2/*.parquet', union_by_name=true) s
  where s.duplicate_of like 'entire:%' and s.agent = 'Claude Code' group by all""")
con.execute(f"""create temp table t as select p.our_key, s.session_id, {NORM.format(c='s.content')} k, s.model, left(s.content, 120) head
  from read_parquet('{SC}') s join pair p on s.session_id = p.sc_sid
  where s.turn_type = 'assistant_response' and length(trim(coalesce(s.content, ''))) > 0""")
con.execute(f"""create temp table o as select c.session_key our_key, {NORM.format(c='c.content')} k, c.actor, c.kind, c.model
  from read_parquet('{V}/conversations/entire/*.parquet', union_by_name=true) c
  where c.session_key in (select our_key from pair) and c.content is not null and length(trim(c.content)) > 0""")
res = {}
res["their_replies"] = con.execute("select count(*) from t").fetchone()[0]
res["matched_as"] = con.execute("""select coalesce(o.actor || ':' || o.kind, 'not found') as ours, count(*) n
  from t left join (select distinct our_key, k, actor, kind from o) o on t.our_key = o.our_key and t.k = o.k
  group by all order by n desc limit 12""").fetchall()
res["not_agent_text_samples"] = con.execute("""select o.actor || ':' || o.kind, t.model, t.head from t
  join (select distinct our_key, k, actor, kind from o) o on t.our_key = o.our_key and t.k = o.k
  where not (o.actor = 'agent' and o.kind = 'text') order by hash(t.head) limit 25""").fetchall()
res["not_found_samples"] = con.execute("""select t.model, t.head from t where not exists
  (select 1 from o where o.our_key = t.our_key and o.k = t.k) order by hash(t.head) limit 25""").fetchall()
json.dump(res, open(sys.argv[1], "w"), indent=1, default=str); print(json.dumps(res, indent=1, default=str)[:9000])
