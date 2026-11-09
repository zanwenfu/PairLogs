"""Cards for a per-source check of the typed mark where logs record no author (2026-11-09): SpecStory, Aider, DataClaw,
where the mark rests on text rules alone. Three blind reviews asked for the mark's precision per source, read by
people other than the author, on these sources.

python make_cards8.py BUILD KEY1 KEY5 WITHDRAWN_TSV OUT_DIR [--n 30] [--seed e1-check8-2026-11-09]

BUILD: the 2026-11-02 build after the helper-session fix (the release's marks), as check 5.
KEY1, KEY5: the first and the fresh check's keys; their sessions are left out, so no message is seen twice.
WITHDRAWN_TSV: repositories withdrawn from the release (status, repo, counts); their sessions are left out.

Per source, `--n` user-role rows the release marks typed, drawn at random among that source's typed rows of kept
sessions (duplicate_of null), not copies (copy_of null), not empty, from the main conversation tables: order by
md5(seed | turn_id), at most one card per session (no cap per repository, so the draw is uniform over messages up to
the session cap and estimates each source's precision over its typed messages). Every card is one the mark calls
typed; the page never says so. Writes cards8.json (id, part, agent, source, the first 1,500 characters, len),
e1_check8_key.json (never shown on the page) and draw8.json (population counts).
"""
import argparse
import hashlib
import json
import os

import duckdb

ap = argparse.ArgumentParser()
ap.add_argument("build")
ap.add_argument("key1")
ap.add_argument("key5")
ap.add_argument("withdrawn")
ap.add_argument("out_dir")
ap.add_argument("--n", type=int, default=30)
ap.add_argument("--seed", default="e1-check8-2026-11-09")
ap.add_argument("--pool", type=int, default=20000, help="rows per source read in hash order before the per-session cut")
args = ap.parse_args()

SOURCES = ["specstory", "aider", "dataclaw"]
k1, k5 = json.load(open(args.key1)), json.load(open(args.key5))
old_sessions = sorted({v["session_key"] for k in (k1, k5) for v in k.values() if v.get("session_key")}
                      | {v["session_key_2026_10_11"] for v in k1.values() if v.get("session_key_2026_10_11")})
assert len(old_sessions) >= 74 + 84 - 5, len(old_sessions)   # check 1's 74 sessions of ours, check 5's 84
old_turns = sorted({v["turn_id"] for v in k1.values() if v.get("turn_id")})
assert len(old_turns) >= 100, len(old_turns)
withdrawn = sorted({line.split("\t")[1].strip() for line in open(args.withdrawn) if line.strip()})
assert len(withdrawn) == 50, len(withdrawn)

con = duckdb.connect()
con.execute("SET threads TO 8")
B = args.build
con.execute(f"""CREATE TABLE kept AS SELECT session_key, coalesce(repo, '') AS repo
                FROM read_parquet('{B}/data/*/*.parquet', union_by_name=true) WHERE duplicate_of IS NULL""")
con.execute("CREATE TABLE old(session_key VARCHAR)")
con.executemany("INSERT INTO old VALUES (?)", [(s,) for s in old_sessions])
con.execute("CREATE TABLE old_turns(turn_id VARCHAR)")
con.executemany("INSERT INTO old_turns VALUES (?)", [(t,) for t in old_turns])
con.execute(f"""INSERT INTO old SELECT DISTINCT session_key FROM read_parquet('{B}/conversations/*/*.parquet', union_by_name=true)
                WHERE turn_id IN (SELECT turn_id FROM old_turns)""")
con.execute("CREATE TABLE wd(repo VARCHAR)")
con.executemany("INSERT INTO wd VALUES (?)", [(r,) for r in withdrawn])
con.execute(f"""CREATE TABLE cand AS
  SELECT c.source, c.session_key, k.repo, c.turn_id, c.turn_number, c.agent, c.actor, c.kind,
         md5('{args.seed}' || '|' || c.turn_id) AS h
  FROM read_parquet('{B}/conversations/*/*.parquet', union_by_name=true) c
  JOIN kept k USING (session_key)
  WHERE c.role = 'user' AND c.copy_of IS NULL AND c.typed_by_developer
    AND length(trim(coalesce(c.content, ''))) > 0 AND c.source IN ('specstory', 'aider', 'dataclaw')
    AND c.session_key NOT IN (SELECT session_key FROM old) AND k.repo NOT IN (SELECT repo FROM wd)""")
pop = con.execute("""SELECT source, count(*), count(DISTINCT session_key), count(DISTINCT nullif(repo, ''))
                     FROM cand GROUP BY 1 ORDER BY 1""").fetchall()
assert {r[0] for r in pop} == set(SOURCES), pop
left_out = con.execute(f"""SELECT count(*) FROM read_parquet('{B}/conversations/*/*.parquet', union_by_name=true)
                           WHERE session_key IN (SELECT session_key FROM old)""").fetchone()[0]
assert left_out > 0, "none of the earlier checks' sessions found in the build: the exclusion would be a no-op"

chosen = []
for src in SOURCES:
    rows = con.execute("""SELECT session_key, repo, turn_id, turn_number, agent, actor, kind FROM cand
                          WHERE source = ? ORDER BY h LIMIT ?""", [src, args.pool]).fetchall()
    got, seen = [], set()
    for s, repo, tid, tn, agent, actor, kind in rows:
        if s in seen:
            continue
        seen.add(s)
        got.append({"source": src, "session_key": s, "repo": repo, "turn_id": tid, "turn_number": tn,
                    "agent": agent, "actor": actor, "kind": kind})
        if len(got) == args.n:
            break
    assert len(got) == args.n, (src, len(got))
    chosen.extend(got)

con.execute("CREATE TABLE pick(turn_id VARCHAR, session_key VARCHAR)")
con.executemany("INSERT INTO pick VALUES (?, ?)", [(c["turn_id"], c["session_key"]) for c in chosen])
text = {(t, s): x for t, s, x in con.execute(f"""
    SELECT c.turn_id, c.session_key, c.content FROM read_parquet('{B}/conversations/*/*.parquet', union_by_name=true) c
    JOIN pick p ON c.turn_id = p.turn_id AND c.session_key = p.session_key""").fetchall()}
assert len(text) == len(chosen), (len(text), len(chosen))

chosen.sort(key=lambda c: hashlib.sha256((args.seed + "|order|" + c["turn_id"]).encode()).hexdigest())
cards, key = [], {}
for c in chosen:
    cid = "p8-" + hashlib.sha256((args.seed + c["turn_id"]).encode()).hexdigest()[:12]
    full = text[(c["turn_id"], c["session_key"])]
    cards.append({"id": cid, "part": 2, "agent": c["agent"], "source": c["source"], "text": full[:1500], "len": len(full)})
    key[cid] = {"pipeline": "typed by a person", "actor": c["actor"], "kind": c["kind"], "source": c["source"],
                "session_key": c["session_key"], "repo": c["repo"], "turn_id": c["turn_id"], "turn_number": c["turn_number"]}
assert len(key) == len(chosen) == args.n * len(SOURCES)

os.makedirs(args.out_dir, exist_ok=True)
json.dump(cards, open(os.path.join(args.out_dir, "cards8.json"), "w"), indent=1, ensure_ascii=False)
json.dump(key, open(os.path.join(args.out_dir, "e1_check8_key.json"), "w"), indent=1, ensure_ascii=False)
json.dump({"seed": args.seed, "n_per_source": args.n, "old_sessions_left_out": len(old_sessions),
           "old_rows_in_build": left_out, "withdrawn_repositories": len(withdrawn),
           "population": [{"source": s, "typed_rows": n, "sessions": m, "repositories": r} for s, n, m, r in pop]},
          open(os.path.join(args.out_dir, "draw8.json"), "w"), indent=1)
print("cards", len(cards), "population", pop)
