"""Candidates for the paper's example: SWE-chat sessions with 4-8 labelled prompts, at least one typed by a person and
two written by programs, whose session is also in our tier A (Entire, open licence). Prints each candidate's prompts:
SWE-chat's turn type and pushback label, our class, our actor, and the first 90 characters (tier A's masked text)."""
import glob, random
import duckdb
W = "/path/to/pairlogs-work"
R = f"{W}/release-fix2/2026-11-02"
con = duckdb.connect(); con.execute("SET threads=8; SET memory_limit='32GB'")
lab = f"{R}/b/labels/swe_chat_prompts.parquet"
cand = con.execute(f"""select session_id, count(*) n, count(*) filter (where written_by='typed') t,
    count(*) filter (where written_by='program') p, count(*) filter (where written_by='action') a
    from read_parquet('{lab}') group by 1 having n between 4 and 8 and t >= 1 and p >= 2""").fetchall()
ids = {c[0] for c in cand}
con.execute("create temp table ids(s varchar)"); con.executemany("insert into ids values (?)", [(i,) for i in ids])
inA = con.execute(f"""select distinct session_id, session_key from read_parquet('{R}/a-names/conversations/entire/*.parquet')
    where session_id in (select s from ids)""").fetchall()
print("candidates:", len(cand), "| also in tier A:", len(inA))
random.seed(20261104)
for sid, key in random.sample(inA, min(6, len(inA))):
    print("=====", key)
    labs = {r[0]: r[1:] for r in con.execute(f"select turn_number, written_by, pushback from read_parquet('{lab}') where session_id = ?", [sid]).fetchall()}
    rows = con.execute(f"""select turn_number, role, turn_type, actor, typed_by_developer, left(regexp_replace(content, '\\s+', ' ', 'g'), 90)
        from read_parquet('{R}/a-names/conversations/entire/*.parquet') where session_key = ? and role = 'user' order by turn_number""", [key]).fetchall()
    for r in rows[:14]:
        print(f"  t{r[0]:<4} {r[2] or '':16} actor={r[3] or '':9} typed={str(r[4]):5} | {r[5]}")
