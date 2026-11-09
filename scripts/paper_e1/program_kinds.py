"""Counts only: SWE-chat's labelled prompts that we find written by programs, against SWE-chat's own flags
(is_continuation; anything else it marks), and their most common openings (first 40 characters, normalised)."""
import glob, json
import duckdb
W = "/path/to/pairlogs-work"
V = f"{W}/run2/build-fix2/2026-11-02"
lab = f"{W}/release-fix2/2026-11-02/b/labels/swe_chat_prompts.parquet"
sw = glob.glob(f"{V}/copied/swe-chat-v2/*/conversations.parquet")[0]
con = duckdb.connect(); con.execute("SET threads=16; SET memory_limit='60GB'")
cols = [r[0] for r in con.execute(f"describe select * from read_parquet('{sw}')").fetchall()]
print("SWE-chat columns with flags:", [c for c in cols if c.startswith("is_") or "flag" in c or "inject" in c])
con.execute(f"""create temp table j as select l.written_by, c.is_continuation, c.prompt_pushback is not null as labelled,
    lower(left(regexp_replace(trim(c.content), '\\s+', ' ', 'g'), 40)) as op
    from read_parquet('{lab}') l join read_parquet('{sw}') c on c.turn_id = l.turn_id""")
print("joined:", con.execute("select written_by, count(*), count(*) filter (where is_continuation) from j group by 1 order by 2 desc").fetchall())
print("SWE-chat user_prompt turns:", con.execute(f"select count(*), count(*) filter (where is_continuation), count(*) filter (where prompt_pushback is not null), count(*) filter (where is_continuation and prompt_pushback is not null) from read_parquet('{sw}') where turn_type = 'user_prompt'").fetchone())
for r in con.execute("select op, count(*) n from j where written_by = 'program' group by 1 order by 2 desc limit 25").fetchall():
    print(f"  {r[1]:6} {r[0]}")
print("program prompts in openings shared by 100+:", con.execute("select sum(n) from (select op, count(*) n from j where written_by='program' group by 1 having n >= 100)").fetchone())
