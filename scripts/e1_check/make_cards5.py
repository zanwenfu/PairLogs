"""Cards for a fresh, independent who-typed check of the released mark (a fresh blind set, 2026-11-06).

python make_cards5.py BUILD OLD_KEY OUT_DIR [--per-source 14] [--typed 10] [--seed e1-check5-2026-11-06]

BUILD: the 2026-11-02 build after the helper-session fix (run2/build-fix2/2026-11-02), whose marks the release holds.
OLD_KEY: the first check's key (e1_check_key_2026-11-02.json); its sessions are left out, so no message is seen twice.

The draw mirrors the first check's Part 2: user-role rows of kept sessions (duplicate_of null), not copies (copy_of
null), not empty, from the main conversation tables; per source, `--typed` rows the release marks typed and the rest
of `--per-source` it marks not typed; at most one card per session and per repository; order by md5(seed | turn_id).
Writes cards5.json (what the page shows: id, part, agent, source, the first 1,500 characters, len) and
e1_check5_key.json (the release's answer per card, never shown on the page), and draw5.json (the population counts).
"""
import argparse
import hashlib
import json
import os

import duckdb

ap = argparse.ArgumentParser()
ap.add_argument("build")
ap.add_argument("old_key")
ap.add_argument("out_dir")
ap.add_argument("--per-source", type=int, default=14)
ap.add_argument("--typed", type=int, default=10)
ap.add_argument("--seed", default="e1-check5-2026-11-06")
ap.add_argument("--pool", type=int, default=3000, help="rows per stratum read in hash order before the per-session cut")
args = ap.parse_args()

SOURCES = ["entire", "specstory", "aider", "dataclaw", "openhands-feedback", "swe-chat-v2"]
old = json.load(open(args.old_key))
old_sessions = sorted({v["session_key"] for v in old.values() if v.get("session_key")}
                      | {v["session_key_2026_10_11"] for v in old.values() if v.get("session_key_2026_10_11")})
assert len(old_sessions) >= 74, len(old_sessions)   # Part 2's 84 cards sit in 74 sessions
# Part 1's cards are SWE-chat prompts, keyed by row (turn_id); their sessions are left out too
old_turns = sorted({v["turn_id"] for v in old.values() if v.get("turn_id")})
assert len(old_turns) >= 100, len(old_turns)

con = duckdb.connect()
con.execute("SET threads TO 8")
B = args.build
con.execute(f"""CREATE TABLE kept AS SELECT session_key, coalesce(repo, '') AS repo
                FROM read_parquet('{B}/data/*/*.parquet', union_by_name=true) WHERE duplicate_of IS NULL""")
con.execute("CREATE TABLE old(session_key VARCHAR)")
con.executemany("INSERT INTO old VALUES (?)", [(s,) for s in old_sessions])
con.execute("CREATE TABLE old_turns(turn_id VARCHAR)")
con.executemany("INSERT INTO old_turns VALUES (?)", [(t,) for t in old_turns])
n_old_s = con.execute("SELECT count(*) FROM old").fetchone()[0]
con.execute(f"""INSERT INTO old SELECT DISTINCT session_key FROM read_parquet('{B}/conversations/*/*.parquet', union_by_name=true)
                WHERE turn_id IN (SELECT turn_id FROM old_turns)""")
p1_sessions = con.execute("SELECT count(*) FROM old").fetchone()[0] - n_old_s
assert p1_sessions >= 50, p1_sessions   # Part 1's 100 prompts found again in the build (one per session, mostly)
con.execute(f"""CREATE TABLE cand AS
  SELECT c.source, c.typed_by_developer AS typed, c.session_key, k.repo, c.turn_id, c.turn_number, c.agent,
         c.actor, c.kind, md5('{args.seed}' || '|' || c.turn_id) AS h
  FROM read_parquet('{B}/conversations/*/*.parquet', union_by_name=true) c
  JOIN kept k USING (session_key)
  WHERE c.role = 'user' AND c.copy_of IS NULL AND length(trim(coalesce(c.content, ''))) > 0
    AND c.session_key NOT IN (SELECT session_key FROM old)""")
pop = con.execute("SELECT source, typed, count(*), count(DISTINCT session_key) FROM cand GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
old_hit = con.execute("""SELECT count(*) FROM read_parquet('%s/conversations/*/*.parquet', union_by_name=true)
                         WHERE session_key IN (SELECT session_key FROM old)""" % B).fetchone()[0]
assert old_hit > 0, "none of the first check's sessions found in the build: the exclusion would be a no-op"
assert {r[0] for r in pop} == set(SOURCES), pop

chosen = []
for src in SOURCES:
    for typed, n in ((True, args.typed), (False, args.per_source - args.typed)):
        rows = con.execute("""SELECT session_key, repo, turn_id, turn_number, agent, actor, kind FROM cand
                              WHERE source = ? AND typed = ? ORDER BY h LIMIT ?""", [src, typed, args.pool]).fetchall()
        got, seen_s, seen_r = [], set(), set()
        for s, repo, tid, tn, agent, actor, kind in rows:
            r = repo or s   # sources without a repository: one per session
            if s in seen_s or r in seen_r or any(c["session_key"] == s for c in chosen):
                continue
            seen_s.add(s); seen_r.add(r)
            got.append({"source": src, "typed": typed, "session_key": s, "repo": repo, "turn_id": tid,
                        "turn_number": tn, "agent": agent, "actor": actor, "kind": kind})
            if len(got) == n:
                break
        assert len(got) == n, (src, typed, len(got))
        chosen.extend(got)

con.execute("CREATE TABLE pick(turn_id VARCHAR, session_key VARCHAR)")
con.executemany("INSERT INTO pick VALUES (?, ?)", [(c["turn_id"], c["session_key"]) for c in chosen])
text = {(t, s): x for t, s, x in con.execute(f"""
    SELECT c.turn_id, c.session_key, c.content FROM read_parquet('{B}/conversations/*/*.parquet', union_by_name=true) c
    JOIN pick p ON c.turn_id = p.turn_id AND c.session_key = p.session_key""").fetchall()}
assert len(text) == len(chosen), (len(text), len(chosen))

# the page's order: shuffled by the seed, so source and mark do not run in blocks
chosen.sort(key=lambda c: hashlib.sha256((args.seed + "|order|" + c["turn_id"]).encode()).hexdigest())
cards, key = [], {}
for c in chosen:
    cid = "p2-" + hashlib.sha256((args.seed + c["turn_id"]).encode()).hexdigest()[:12]
    full = text[(c["turn_id"], c["session_key"])]
    cards.append({"id": cid, "part": 2, "agent": c["agent"], "source": c["source"], "text": full[:1500], "len": len(full)})
    key[cid] = {"pipeline": "typed by a person" if c["typed"] else "not typed", "actor": c["actor"], "kind": c["kind"],
                "source": c["source"], "session_key": c["session_key"], "repo": c["repo"], "turn_id": c["turn_id"],
                "turn_number": c["turn_number"]}
assert len(set(key)) == len(chosen)

os.makedirs(args.out_dir, exist_ok=True)
json.dump(cards, open(os.path.join(args.out_dir, "cards5.json"), "w"), indent=1, ensure_ascii=False)
json.dump(key, open(os.path.join(args.out_dir, "e1_check5_key.json"), "w"), indent=1, ensure_ascii=False)
json.dump({"seed": args.seed, "per_source": args.per_source, "typed": args.typed, "old_sessions_left_out": len(old_sessions),
           "part1_sessions_left_out": p1_sessions,
           "old_rows_in_build": old_hit,
           "population": [{"source": s, "typed": t, "rows": n, "sessions": m} for s, t, n, m in pop]},
          open(os.path.join(args.out_dir, "draw5.json"), "w"), indent=1)
print("cards", len(cards), "population", pop)
