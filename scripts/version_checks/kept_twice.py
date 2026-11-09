"""Read-only: kept sessions (duplicate_of empty) that share their agent and session id with another kept session, in
each version given (NAME=DIR ...), by which sources the group spans and whether its repositories differ. Counts only."""
import sys

import duckdb

con = duckdb.connect()
con.execute("SET threads=8")
for arg in sys.argv[1:]:
    name, V = arg.split("=", 1)
    r = con.execute(f"""
      with k as (select agent, session_id, source, lower(repo) repo from read_parquet('{V}/data/*/*.parquet',
                 union_by_name=true) where duplicate_of is null and session_id is not null),
           g as (select agent, session_id, count(*) n, string_agg(distinct source, '+' order by source) sources,
                        count(distinct repo) repos from k group by 1, 2 having count(*) > 1)
      select sources, repos > 1 repos_differ, count(*) groups, sum(n) sessions from g group by all order by sessions desc
    """).fetchall()
    kept = con.execute(f"select count(*) from read_parquet('{V}/data/*/*.parquet', union_by_name=true) where duplicate_of is null").fetchone()[0]
    print(name, "kept", kept)
    for x in r:
        print("  ", x)
