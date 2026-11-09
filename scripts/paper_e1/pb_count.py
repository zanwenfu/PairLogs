"""Typed rows and pushback labels in the version before and after the helper-session fix (counts only)."""
import duckdb
W = "/path/to/pairlogs-work"
con = duckdb.connect(); con.execute("SET threads=8; SET memory_limit='24GB'")
for name in ("run2/build", "run2/build-fix2"):
    V = f"{W}/{name}/2026-11-02"
    r = con.execute(f"""select count(*) filter (where typed_by_developer),
        count(*) filter (where typed_by_developer and pushback),
        count(*) filter (where pushback is not null and not coalesce(typed_by_developer, false))
        from read_parquet(['{V}/conversations/*/*.parquet', '{V}/conversations_subagents/*/*.parquet'],
                          union_by_name=true)""").fetchone()
    print(name, "| typed rows", r[0], "| pushback among them", r[1], "| a label on an untyped row", r[2], flush=True)
