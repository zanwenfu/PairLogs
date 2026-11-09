"""Read-only: every column of every table, its filled rows in version A and in version B (env A, B; names NA, NB), so nothing a later step
added to 10-11 (as the pushback labels were) is missing from the rebuild unnoticed. Prints a line per column whose
share of filled rows moved by more than 1 point, or which one version lacks."""
import duckdb

import os
A, B = os.environ["A"], os.environ["B"]
NA, NB = os.environ.get("NA", "old"), os.environ.get("NB", "new")
TABLES = {"data": "data/*/*.parquet", "conversations": "conversations/*/*.parquet",
          "conversations_subagents": "conversations_subagents/*/*.parquet", "inputs": "inputs.parquet",
          "summaries": "summaries.parquet"}
con = duckdb.connect()
con.execute("SET threads=16; SET memory_limit='56GB'; SET temp_directory='/path/to/pairlogs-work/tmp-fill3'")


def fill(V, glob):
    try:
        cols = [r[0] for r in con.execute(f"describe select * from read_parquet('{V}/{glob}', union_by_name=true)").fetchall()]
    except Exception as e:
        return None, {}
    sel = ", ".join(f'count("{c}")' for c in cols)
    got = con.execute(f"select count(*), {sel} from read_parquet('{V}/{glob}', union_by_name=true)").fetchone()
    return got[0], dict(zip(cols, got[1:]))


for t, glob in TABLES.items():
    na, fa = fill(A, glob)
    nb, fb = fill(B, glob)
    print(f"== {t}: rows {NA} {na}, {NB} {nb}", flush=True)
    if na is None or nb is None:
        continue
    for c in sorted(set(fa) | set(fb)):
        if c not in fa or c not in fb:
            print(f"   {c}: only in {NA if c in fa else NB} ({fa.get(c, fb.get(c)):,} filled)")
            continue
        sa, sb = fa[c] / max(1, na), fb[c] / max(1, nb)
        if abs(sa - sb) > float(os.environ.get("THRESH", "0.01")):
            print(f"   {c}: {fa[c]:,} ({100 * sa:.1f}%) -> {fb[c]:,} ({100 * sb:.1f}%)")
