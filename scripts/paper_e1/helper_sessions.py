"""Read-only, counts only (2026-11-04): how many kept sessions with a typed message are a helper agent's own session,
whose first "user" row is the brief its parent wrote (the person's check found 6 such rows of 57 marked typed).
Markers, by source:
- Entire, OpenCode: the session's raw file names a parent session ("parentID": "ses_...").
- Entire, Claude Code: the raw file's entries say "isSidechain": true.
- DataClaw: no marker in its export; its Claude sessions run on a Haiku model (Claude Code's helper agents use one),
  counted as a candidate, beside brief_leak.py's exact matches.
For each: sessions, their typed rows, and how many hold no other typed row than the first.
python helper_sessions.py VERSION_DIR OUT.json
"""
import json
import sys

import duckdb

V, OUT = sys.argv[1:3]
con = duckdb.connect()
con.execute("SET threads=16; SET memory_limit='96GB'; SET temp_directory='/path/to/pairlogs-work/tmp'")
res = {}


def q(name, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    res[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    print(name, json.dumps(res[name], default=str)[:900], flush=True)


con.execute(f"""create temp table typed as select session_key, turn_number from
    read_parquet('{V}/conversations/*/*.parquet', union_by_name=true) where typed_by_developer and copy_of is null""")
con.execute("create temp table typed_n as select session_key, count(*) as n from typed group by 1")
con.execute(f"""create temp table entire_marked as
    select session_key, agent,
           bool_or(f.content like '%"parentID":"ses\\_%' escape '\\' or f.content like '%"parentID": "ses\\_%' escape '\\') as opencode_child,
           bool_or(left(f.content, 4000) like '%"isSidechain":true%') as claude_sidechain
    from (select session_key, agent, unnest(files) as f from read_parquet('{V}/data/entire/*.parquet')
          where duplicate_of is null)
    group by 1, 2""")
q("entire_markers", """select agent, count(*) filter (where opencode_child) as opencode_child,
        count(*) filter (where claude_sidechain) as claude_sidechain,
        count(*) filter (where (opencode_child or claude_sidechain) and t.n is not null) as marked_with_typed,
        count(*) filter (where (opencode_child or claude_sidechain) and t.n = 1) as marked_one_typed,
        sum(t.n) filter (where opencode_child or claude_sidechain) as typed_rows_in_marked
    from entire_marked e left join typed_n t using (session_key)
    group by 1 having count(*) filter (where opencode_child or claude_sidechain) > 0 order by 2 desc, 3 desc""")
q("dataclaw_models", f"""select case when models::varchar ilike '%haiku%' then 'haiku' else 'other' end as model,
        count(*) as sessions, count(t.n) as with_typed, count(*) filter (where t.n = 1) as one_typed,
        sum(t.n) as typed_rows
    from read_parquet('{V}/data/dataclaw/*.parquet') d left join typed_n t using (session_key)
    where d.duplicate_of is null group by 1""")
json.dump(res, open(OUT, "w"), indent=1, default=str)
print("end")
