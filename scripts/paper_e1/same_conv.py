"""Read-only: kept sessions whose whole conversation (every row's role and text, in order) is the same as another kept
session's, in a version: groups by the sources they span, by size, and whether they share one session id. A blank
session id counts as its own (count(distinct) skips NULLs, which once made two sessions, one with no id, read as one
id: 11-01). Counts only.

python same_conv.py VERSION_DIR TMP_DIR"""
import sys

import duckdb

V, TMP = sys.argv[1], sys.argv[2]
con = duckdb.connect()
con.execute(f"SET threads=8; SET memory_limit='60GB'; SET temp_directory='{TMP}'")
con.execute(f"""create temp table fp as select session_key, count(*) n, count_if(typed_by_developer) nt,
  md5(string_agg(md5(coalesce(role, '') || '|' || coalesce(content, '')), ',' order by turn_number)) f
  from read_parquet('{V}/conversations/*/*.parquet', union_by_name=true) group by 1""")
con.execute(f"""create temp table d as select session_key, source, session_id, repo, split_part(source_id, ':', 1) ds
  from read_parquet('{V}/data/*/*.parquet', union_by_name=true) where duplicate_of is null""")
con.execute("""create temp table g as select f, count(*) k, max(n) n, max(nt) nt,
  string_agg(distinct d.source, '+' order by d.source) sources,
  count(distinct coalesce(d.session_id, 'blank:' || d.session_key)) ids, count_if(d.session_id is null) blank,
  count(distinct coalesce(d.repo, '')) repos, count(distinct d.ds) exports
  from fp join d using (session_key) group by f having count(*) > 1""")
print("kept sessions with a conversation:", con.execute("select count(*) from fp join d using (session_key)").fetchone())
print("groups of identical conversations among kept sessions (groups, sessions, extra):",
      con.execute("select count(*), sum(k), sum(k - 1) from g").fetchone())
print("by size (groups, sessions, extra):")
for r in con.execute("""select case when nt = 0 then 'no typed' when n < 4 then '1-3 rows' when n < 20 then '4-19 rows'
  else '20+ rows' end size, count(*), sum(k), sum(k - 1) from g group by 1 order by 4 desc""").fetchall():
    print("  ", r)
print("one session id in a group (ids = 1, blank ids counted apart):",
      con.execute("select count(*), sum(k) from g where ids = 1").fetchone())
print("groups where some member has a blank id (groups, sessions, blank):",
      con.execute("select count(*), sum(k), sum(blank) from g where blank > 0").fetchone())
print("one repository (or one export) per group, 4+ rows with a typed message (groups, extra):",
      con.execute("select count(*), sum(k - 1) from g where n >= 4 and nt > 0 and repos = 1 and exports = 1").fetchone())
print("sources, size, one id, groups, sessions, extra:")
for r in con.execute("""select sources, case when nt = 0 then 'no typed' when n < 4 then '1-3 rows' when n < 20 then
  '4-19 rows' else '20+ rows' end size, ids = 1 one_id, count(*) groups, sum(k) sessions, sum(k - 1) extra
  from g group by all order by extra desc""").fetchall():
    print("  ", r)
print("4+ rows with a typed message, each group: sources, sessions, rows, typed, ids, blank, repos, exports")
for r in con.execute("""select sources, k, n, nt, ids, blank, repos, exports from g where n >= 4 and nt > 0
  order by sources, n desc""").fetchall():
    print("  ", r)
