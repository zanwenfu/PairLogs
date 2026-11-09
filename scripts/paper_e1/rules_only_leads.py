"""Program text the text rules miss, found in the formats with no author: the openings Entire's records mark as program
text but the text rules call typed (opening = first 40 characters, whitespace squeezed; kept only when it is markup or a
fixed program line: starts with '<', '[Image: source:', or occurs in 20 or more Entire rows from 10 or more sessions
and never as a typed row there), then counted among the typed rows of DataClaw, SpecStory and Aider. Counts only.
Also (2026-11-08), per agent in Entire: the rows the records call program and the rules alone call typed, and how many
of them open with one of these openings (the openings' coverage of what the rules miss, so a count elsewhere can be
read against it); and each source's typed rows by agent."""
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path
import duckdb
from collect import events as E
V, OUT = sys.argv[1:3]
con = duckdb.connect()
con.execute(f"""CREATE TABLE k AS SELECT session_key, agent FROM read_parquet('{V}/data/entire/*.parquet', union_by_name=true)
                WHERE duplicate_of IS NULL""")
op = lambda t: " ".join((t or "")[:80].split())[:40]
prog, prog_sess, typed_open = Counter(), defaultdict(set), Counter()
miss_open, rec_typed = defaultdict(Counter), Counter()
for f in sorted(Path(V).glob("conversations/entire/*.parquet")):
    for key, ref, content, ag in con.execute(f"""SELECT c.session_key, c.typed_by_developer, c.content, k.agent
            FROM read_parquet('{f}') c JOIN k USING (session_key) WHERE c.role = 'user' AND c.copy_of IS NULL""").fetchall():
        o = op(content)
        if ref:
            typed_open[o] += 1
            rec_typed[ag] += 1
        elif E.tell(content or "")[1]:
            prog[o] += 1
            prog_sess[o].add(key)
            miss_open[ag][o] += 1
leads = {o for o, n in prog.items() if o and typed_open[o] == 0 and
         (o.startswith("<") or o.startswith("[Image: source:") or (n >= 20 and len(prog_sess[o]) >= 10))}
out = {"program_openings": len(leads), "program_rows_in_entire": sum(prog[o] for o in leads), "by_source": {},
       "entire_missed_by_agent": {str(a): {"records_typed": rec_typed[a], "missed": sum(c.values()),
                                           "covered_by_openings": sum(n for o, n in c.items() if o in leads)}
                                  for a, c in sorted(miss_open.items(), key=lambda x: -sum(x[1].values())) if sum(c.values()) >= 50}}
for src in ("dataclaw", "specstory", "aider", "swe-chat-v2"):
    kept = {k for (k,) in con.execute(f"SELECT session_key FROM read_parquet('{V}/data/{src}/*.parquet', union_by_name=true) WHERE duplicate_of IS NULL").fetchall()}
    con.execute("CREATE OR REPLACE TABLE s(session_key VARCHAR)")
    con.executemany("INSERT INTO s VALUES (?)", [(x,) for x in kept])
    typed = hit = 0
    sess_hit = set()
    agents, typed_by_agent = Counter(), Counter()
    for key, content, ag in con.execute(f"""SELECT c.session_key, c.content, c.agent FROM read_parquet('{V}/conversations/{src}/*.parquet', union_by_name=true) c
            JOIN s USING (session_key) WHERE c.typed_by_developer AND c.copy_of IS NULL""").fetchall():
        typed += 1
        typed_by_agent[ag] += 1
        if op(content) in leads:
            hit += 1
            sess_hit.add(key)
            agents[ag] += 1
    out["by_source"][src] = {"typed_rows": typed, "typed_rows_opening_like_program_text": hit,
                             "pct": round(100 * hit / typed, 2) if typed else None, "sessions": len(sess_hit),
                             "by_agent": dict(agents.most_common(6)),
                             "typed_rows_by_agent": {str(a): n for a, n in typed_by_agent.most_common(8)}}
Path(OUT).write_text(json.dumps(out, indent=1) + "\n")
print(json.dumps(out, indent=1))
