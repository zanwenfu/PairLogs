"""Read-only, counts only (2026-11-04, after the person's who-typed check): typed rows that are a brief one agent wrote
for a helper agent. The person called 6 of 57 sampled typed rows such briefs ("Explore the ... I need to understand:
1. ..."): the helper's own session was saved as a session, its first "user" row the parent's prompt. Exact test: a
typed row whose start (first 300 characters, spaces collapsed, lower case) equals the `prompt` of an orchestration
tool call (a Task/Agent-style call) in any session of the version. Also the share of the kept sessions with a typed
message that have no other typed row.
python brief_leak.py VERSION_DIR OUT.json
"""
import json
import sys

import duckdb

V, OUT = sys.argv[1:3]
con = duckdb.connect()
con.execute("SET threads=16; SET memory_limit='96GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
CONV = (f"read_parquet(['{V}/conversations/*/*.parquet', '{V}/conversations_subagents/*/*.parquet'], "
        "union_by_name=true, filename=true)")
NORM = "left(lower(regexp_replace(trim({c}), '\\s+', ' ', 'g')), 300)"
con.execute(f"""create temp table briefs as
    select distinct {NORM.format(c="coalesce(json_extract_string(tool_input_json, '$.prompt'),"
                                   " json_extract_string(tool_input_json, '$.message'),"
                                   " json_extract_string(tool_input_json, '$.task'))")} as k, tool_name
    from {CONV} where kind = 'tool_call' and category = 'Orchestration' and tool_input_json is not null""")
con.execute("delete from briefs where k is null or length(k) < 40")
con.execute(f"""create temp table typed as
    select source, session_key, turn_number, {NORM.format(c='content')} as k, (filename like '%subagents%') as sub
    from {CONV} where typed_by_developer and copy_of is null and content is not null""")
res = {}


def q(name, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    res[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    print(name, json.dumps(res[name], default=str)[:900], flush=True)


q("briefs", "select tool_name, count(*) as briefs from briefs group by 1 order by 2 desc limit 15")
q("typed_rows", "select source, sub, count(*) as typed_rows, count(distinct session_key) as sessions from typed group by 1, 2 order by 1, 2")
con.execute("create temp table hit as select t.* from typed t where t.k in (select k from briefs)")
q("typed_rows_that_are_briefs", """select source, sub, count(*) as rows, count(distinct session_key) as sessions
    from hit group by 1, 2 order by 1, 2""")
q("sessions_left_without_typed", """select h.source, count(distinct h.session_key) as sessions_with_a_brief,
        count(distinct h.session_key) filter (where not exists (select 1 from typed t where t.session_key = h.session_key
            and t.k not in (select k from briefs))) as left_with_no_typed
    from hit h where not h.sub group by 1 order by 1""")
json.dump(res, open(OUT, "w"), indent=1, default=str)
print("end")
