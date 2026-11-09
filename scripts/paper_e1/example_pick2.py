"""Codex sessions in SWE-chat whose labelled prompts mix typed ones with program text, also in our tier A: per
prompt, SWE-chat's turn type and pushback label, our class, and the first 100 characters."""
import glob, random
import duckdb
W = "/path/to/pairlogs-work"
V = f"{W}/run2/build-fix2/2026-11-02"
R = f"{W}/release-fix2/2026-11-02"
lab = f"{R}/b/labels/swe_chat_prompts.parquet"
sw = glob.glob(f"{V}/copied/swe-chat-v2/*/conversations.parquet")[0]
con = duckdb.connect(); con.execute("SET threads=16; SET memory_limit='60GB'")
con.execute(f"""create temp table j as select l.session_id, l.turn_number, l.written_by, c.prompt_pushback, c.agent,
    left(regexp_replace(trim(c.content), '\\s+', ' ', 'g'), 100) as op
    from read_parquet('{lab}') l join read_parquet('{sw}') c on c.turn_id = l.turn_id""")
cand = con.execute("""select session_id, count(*) n, count(*) filter (where written_by='typed') t,
    count(*) filter (where written_by='program') p, count(distinct left(lower(op), 12)) filter (where written_by='program') k
    from j where agent ilike '%codex%' group by 1 having n between 4 and 9 and t between 1 and 3 and p >= 3 and k >= 2""").fetchall()
ids = [c[0] for c in cand]
con.execute("create temp table ids(s varchar)"); con.executemany("insert into ids values (?)", [(i,) for i in ids])
inA = {r[0] for r in con.execute(f"""select distinct session_id from read_parquet('{R}/a-names/conversations/entire/*.parquet')
    where session_id in (select s from ids)""").fetchall()}
print("Codex candidates:", len(cand), "| in tier A:", len(inA))
random.seed(20261104)
for sid in random.sample(sorted(inA), min(5, len(inA))):
    print("=====", sid)
    for r in con.execute("select turn_number, written_by, prompt_pushback, op from j where session_id = ? order by turn_number", [sid]).fetchall():
        print(f"  t{r[0]:<4} ours={r[1]:8} swe-pushback={str(r[2]):14} | {r[3]}")
