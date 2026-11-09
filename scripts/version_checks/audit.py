"""Read-only consistency of a version's tables (NAME=DIR ...): each count printed, and each check a count of rows
that break it (0 = holds). Counts only.
 a. a duplicate's duplicate_of names a kept session (its session_key)
 b. a duplicate has no rows in either conversation table, and empty row counts
 c. a kept session's n_conversation_rows / n_subagent_rows equal its rows in the two tables
 d. every conversation row's session belongs to a kept session
 e. our pushback label only on typed rows that are not copies
 f. session_key unique in the session table"""
import sys

import duckdb

con = duckdb.connect()
con.execute("SET threads=16; SET memory_limit='56GB'; SET temp_directory='/path/to/pairlogs-work/tmp-audit'")
for arg in sys.argv[1:]:
    name, V = arg.split("=", 1)
    con.execute(f"create or replace temp view d as select session_key, duplicate_of, n_conversation_rows nc, "
                f"n_subagent_rows ns from read_parquet('{V}/data/*/*.parquet', union_by_name=true)")
    con.execute(f"create or replace temp table c as select session_key, count(*) n, count_if(pushback is not null) pb, "
                f"count_if(pushback is not null and not (typed_by_developer and copy_of is null)) pb_bad "
                f"from read_parquet('{V}/conversations/*/*.parquet', union_by_name=true) group by 1")
    con.execute(f"create or replace temp table s as select session_key, count(*) n from read_parquet("
                f"'{V}/conversations_subagents/*/*.parquet', union_by_name=true) group by 1")
    q = lambda sql: con.execute(sql).fetchone()[0]
    out = {
        "sessions": q("select count(*) from d"),
        "kept": q("select count(*) from d where duplicate_of is null"),
        "conversation rows": q("select sum(n) from c"), "subagent rows": q("select sum(n) from s"),
        "rows with our label": q("select sum(pb) from c"),
        "a. duplicate_of not a kept session": q("select count(*) from d where duplicate_of is not null and duplicate_of "
                                                 "not in (select session_key from d where duplicate_of is null)"),
        "b. duplicates with rows or counts": q("select count(*) from d where duplicate_of is not null and (nc is not null "
                                               "or ns is not null or session_key in (select session_key from c) or "
                                               "session_key in (select session_key from s))"),
        "c. kept, n_conversation_rows differs": q("select count(*) from d left join c using (session_key) where "
                                                  "d.duplicate_of is null and coalesce(d.nc, 0) <> coalesce(c.n, 0)"),
        "c. kept, n_subagent_rows differs": q("select count(*) from d left join s using (session_key) where "
                                              "d.duplicate_of is null and coalesce(d.ns, 0) <> coalesce(s.n, 0)"),
        "c. kept, no rows and no count": q("select count(*) from d where duplicate_of is null and nc is null"),
        "d. conversation rows of no kept session": q("select coalesce(sum(n), 0) from c where session_key not in "
                                                     "(select session_key from d where duplicate_of is null)"),
        "d. subagent rows of no kept session": q("select coalesce(sum(n), 0) from s where session_key not in "
                                                 "(select session_key from d where duplicate_of is null)"),
        "e. label on a row not typed or a copy": q("select sum(pb_bad) from c"),
        "f. session_key repeated": q("select count(*) - count(distinct session_key) from d"),
    }
    print(f"== {name}")
    for k, v in out.items():
        print(f"   {k}: {v:,}" if isinstance(v, int) else f"   {k}: {v}")
