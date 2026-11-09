"""Read-only: repository owners of kept sessions in a version (argv[1]) that an owner-type lookup (argv[2]) lacks.
Writes their logins to argv[3]."""
import json
import sys

import duckdb

V, LOOKUP, OUT = sys.argv[1:4]
own = json.load(open(LOOKUP))
con = duckdb.connect()
con.execute("SET threads=4")
b = dict(con.execute(f"select split_part(repo, '/', 1) login, count(*) n from read_parquet('{V}/data/*/*.parquet', "
                     "union_by_name=true) where duplicate_of is null and repo like '%/%' group by 1").fetchall())
new = sorted(l for l in b if l not in own)
print("owners", len(b), "in lookup", len(own), "| not in lookup", len(new), "with", sum(b[l] for l in new), "sessions")
open(OUT, "w").write("\n".join(new) + ("\n" if new else ""))
