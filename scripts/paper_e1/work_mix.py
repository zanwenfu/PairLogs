"""What the sessions are about, by the released work labels of both tiers (2026-11-09). Read-only; counts only.

python work_mix.py RELEASE_DIR OUT.json
RELEASE_DIR: the release as uploaded (work_labels/sessions.parquet for tier A, tier-b/work_labels/sessions.parquet).
Shares of labelled sessions by main label (task, project, domain). The labels are a model's reading of a session's
first typed messages (work_labels/meta.json); use the shares, not single labels.
"""
import json
import sys

import duckdb

R, OUT = sys.argv[1:3]
con = duckdb.connect()
con.execute(f"""CREATE TABLE w AS SELECT * FROM read_parquet(['{R}/work_labels/sessions.parquet',
                '{R}/tier-b/work_labels/sessions.parquet'], union_by_name=true)""")
n, nd = con.execute("SELECT count(*), count(DISTINCT session_key) FROM w").fetchone()
assert n == nd > 0, (n, nd)   # one label row per session
out = {"labelled_sessions": n}
for col in ("task", "project", "domain"):
    rows = con.execute(f"SELECT coalesce({col}, '(none)'), count(*) FROM w GROUP BY 1 ORDER BY 2 DESC").fetchall()
    out[col] = [[k, c, round(100 * c / n, 1)] for k, c in rows]
print(json.dumps(out, indent=1))
json.dump(out, open(OUT, "w"), indent=1)
