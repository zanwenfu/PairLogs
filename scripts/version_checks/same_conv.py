"""Read-only: kept sessions whose whole conversation (every row's role and text, in order) is the same as another kept
session's, in a version: groups by the sources they span, by size, and whether they share a session id. Counts only."""
import sys

import duckdb

V = sys.argv[1]
con = duckdb.connect()
con.execute("SET threads=16; SET memory_limit='60GB'; SET temp_directory='/path/to/pairlogs-work/tmp-same'")
con.execute(f"""create temp table fp as select session_key, count(*) n, count_if(typed_by_developer) nt,
  md5(string_agg(md5(coalesce(role, '') || '|' || coalesce(content, '')), ',' order by turn_number)) f
  from read_parquet('{V}/conversations/*/*.parquet', union_by_name=true) group by 1""")
con.execute(f"""create temp table d as select session_key, source, session_id, repo, split_part(source_id, ':', 1) ds
  from read_parquet('{V}/data/*/*.parquet', union_by_name=true) where duplicate_of is null""")
con.execute("""create temp table g as select f, count(*) k, max(n) n, max(nt) nt,
  string_agg(distinct d.source, '+' order by d.source) sources, count(distinct d.session_id) ids,
  count(distinct coalesce(d.repo, '')) repos, count(distinct d.ds) exports
  from fp join d using (session_key) group by f having count(*) > 1""")
print("groups of identical conversations among kept sessions:", con.execute("select count(*), sum(k) from g").fetchone())
for r in con.execute("""select sources, case when nt = 0 then 'no typed' when n < 4 then '1-3 rows' when n < 20 then
  '4-19 rows' else '20+ rows' end size, ids = 1 same_id, count(*) groups, sum(k) sessions, sum(k - 1) extra
  from g group by all order by extra desc""").fetchall():
    print("  ", r)
print("by exports (dataclaw only, 4+ rows):", con.execute("""select exports, repos, count(*), sum(k - 1) from g
  where sources = 'dataclaw' and n >= 4 group by all order by 3 desc""").fetchall())
print("4+ rows, each group: sources, sessions, rows, typed, ids, repos, exports, agents")
for r in con.execute("""select g.sources, g.k, g.n, g.nt, g.ids, g.repos, g.exports from g where n >= 4 order by sources, n desc""").fetchall():
    print("  ", r)
print("short groups (1-3 rows or no typed): role patterns of the kept rows")
for r in con.execute(f"""with s as (select session_key, string_agg(coalesce(role, '?'), '>' order by turn_number) roles,
    sum(length(coalesce(content, ''))) chars from read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)
    where session_key in (select session_key from fp where f in (select f from g where n < 4 or nt = 0)) group by 1)
  select roles, count(*) sessions, median(chars) med_chars from s group by 1 order by 2 desc limit 12""").fetchall():
    print("  ", r)
