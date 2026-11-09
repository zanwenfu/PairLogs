"""A first look at tier A, once its archives are unzipped into one folder: sessions by source and agent, who wrote
what the logs file as the user's, the tools agents call, and one session's flow. Prints counts and labels only.

python examples/quickstart.py TIER_A_FOLDER
Needs duckdb (pip install duckdb).
"""
import sys

import duckdb

A = sys.argv[1].rstrip("/")
con = duckdb.connect()
con.execute(f"create view sessions as select * from read_parquet('{A}/data/*/*.parquet', union_by_name=true)")
con.execute(f"create view rows as select * from read_parquet('{A}/conversations/*/*.parquet', union_by_name=true)")


def show(title, sql):
    print(f"\n{title}")
    con.sql(sql).show(max_rows=20)


show("Distinct sessions by source (copies point to their kept session through duplicate_of)", """
    select source, count(*) as sessions, count(distinct repo) as repositories
    from sessions where duplicate_of is null group by 1 order by 2 desc""")
show("Sessions by agent", """
    select coalesce(agent, '(not named)') as agent, count(*) as sessions
    from sessions where duplicate_of is null group by 1 order by 2 desc limit 10""")
show("Who wrote what the logs file as the user's (rows of kept sessions)", """
    select actor, kind, count(*) as rows, round(100 * avg(typed_by_developer::int), 1) as pct_typed
    from rows where role = 'user' and copy_of is null group by 1, 2 order by 3 desc limit 12""")
show("What developers typed, by agent", """
    select coalesce(agent, '(not named)') as agent, count(*) as typed_messages,
           round(100 * avg(pushback::int), 1) as pct_pushback_our_label
    from rows where typed_by_developer and copy_of is null group by 1 order by 2 desc limit 10""")
show("The tools agents call most", """
    select coalesce(tool_name, '(none)') as tool, category, count(*) as calls
    from rows where kind = 'tool_call' and copy_of is null group by 1, 2 order by 3 desc limit 10""")
show("Tool results that report an error", """
    select count(*) as tool_results, round(100 * avg(coalesce(is_error, false)::int), 1) as pct_error
    from rows where kind = 'tool_result' and copy_of is null""")
key = con.execute("""select session_key from sessions where duplicate_of is null and n_user_messages between 3 and 6
                     and n_tool_calls between 5 and 30 and source = 'entire' order by session_key
                     limit 1""").fetchall()[0][0]
show(f"One session's flow ({key}): who made each row and what it is", f"""
    select turn_number, actor, kind, tool_name, typed_by_developer as typed
    from rows where session_key = '{key}' order by turn_number limit 40""")
