"""Shapes of tier B's pointer columns (letters -> a, digits -> 9), so the rebuild test is built on what they hold.

python shapes.py POINTERS.parquet
"""
import re
import sys

import duckdb


def shape(v) -> str:
    return re.sub(r"[0-9]", "9", re.sub(r"[A-Za-z]", "a", str(v)))[:90]


c = duckdb.connect()
t = f"read_parquet('{sys.argv[1]}')"
for src in ("entire", "specstory", "aider"):
    print(f"== {src}")
    for col in ("session_key", "session_id", "source_id", "found_url", "found_commit", "checkpoint_pk", "strategy"):
        rows = c.sql(f"select {col} from {t} where source = '{src}' using sample 3 rows (reservoir, 7)").fetchall()
        nulls = c.sql(f"select count(*) from {t} where source = '{src}' and {col} is null").fetchall()[0][0]
        print(f"  {col:14} nulls {nulls:6,}  " + " | ".join(shape(r[0]) for r in rows))
    print("  files per session:", c.sql(f"select min(len(files)), median(len(files)), max(len(files)) from {t} "
                                         f"where source = '{src}'").fetchall()[0])
    print("  also_in non-empty:", c.sql(f"select count(*) from {t} where source = '{src}' and len(also_in) > 0").fetchall()[0][0])
