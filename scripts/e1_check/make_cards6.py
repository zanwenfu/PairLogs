"""Cards for the per-agent check of the typed mark on SWE-chat's labelled prompts (check 6,
2026-11-06). The use case rests on the mark's split of Codex's and Claude Code's prompts (programs wrote
73.2% of Codex's labelled prompts, 16.8% of Claude Code's; Codex's mostly in five repositories), so the person reads
prompts of each agent and each mark, and Codex's are drawn inside and outside those five repositories.

python make_cards6.py RELEASE_PROMPTS SWECHAT_DIR OUT_DIR OLD_KEY... [--seed e1-check6-2026-11-06]

RELEASE_PROMPTS: the release's labels/swe_chat_prompts.parquet (turn_id, session_id, turn_number, written_by,
pushback), whose marks the paper reports. SWECHAT_DIR: our pinned copy of SWE-chat v2 (conversations, sessions,
repositories). OLD_KEY: every earlier check's key; their sessions are left out, so no message is seen twice.

Strata (80 cards): Claude Code typed 20, Claude Code program 20; Codex typed and Codex program, each 10 from the five
repositories with the most labelled prompts of the two agents (m16's "top 5") and 10 from the rest. Within a stratum:
order by md5(seed | turn_id), at most one card per session and per repository; inside the top 5, cards
round-robin over its repositories (one card per session).
Writes cards6.json (what the page shows: id, part, agent, source, the first 1,500 characters, len), e1_check6_key.json
(the release's mark and the stratum per card, never shown on the page) and draw6.json (rows and sessions per stratum,
for weighting).
"""
import argparse
import hashlib
import json
import os

import duckdb

ap = argparse.ArgumentParser()
ap.add_argument("prompts")
ap.add_argument("swechat")
ap.add_argument("out_dir")
ap.add_argument("old_keys", nargs="+")
ap.add_argument("--seed", default="e1-check6-2026-11-06")
args = ap.parse_args()

AGENTS = ("Claude Code", "Codex")
MARKS = {"typed": "typed by a person", "program": "program"}
PLAN = [("Claude Code", "typed", "all", 20), ("Claude Code", "program", "all", 20),
        ("Codex", "typed", "top5", 10), ("Codex", "typed", "rest", 10),
        ("Codex", "program", "top5", 10), ("Codex", "program", "rest", 10)]

# every earlier check's sessions: SWE-chat ids from turn ids ("<session>#<turn>"), and our session keys, whose last
# part is the session id for Entire and SWE-chat rows
old_ids, old_keys = set(), set()


def walk(x):
    """Every dict inside a key file, at any depth (the work-label check's key nests its cards)."""
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)


for path in args.old_keys:
    for v in walk(json.load(open(path))):
        t = v.get("turn_id")
        if isinstance(t, str) and "#" in t:
            old_ids.add(t.split("#")[0])
        for k in ("session_key", "session_key_2026_10_11"):
            if isinstance(v.get(k), str):
                old_keys.add(v[k])
                old_ids.add(v[k].split(":")[-1])
assert len(old_ids) >= 150, len(old_ids)

SC = args.swechat.rstrip("/")
con = duckdb.connect()
con.execute("SET threads TO 8")
con.execute(f"""CREATE TABLE p AS
  SELECT r.turn_id, r.session_id, r.written_by, c.agent, c.repo_id, coalesce(repo.name, '') AS repo,
         md5('{args.seed}' || '|' || r.turn_id) AS h
  FROM read_parquet('{args.prompts}') r
  JOIN (SELECT turn_id, any_value(agent) agent, any_value(repo_id) repo_id FROM read_parquet('{SC}/conversations.parquet')
        WHERE turn_type = 'user_prompt' AND prompt_pushback IS NOT NULL GROUP BY turn_id) c USING (turn_id)
  LEFT JOIN read_parquet('{SC}/repositories.parquet') repo ON repo.repo_id = c.repo_id""")
n_rel = con.execute(f"SELECT count(*), count(DISTINCT turn_id) FROM read_parquet('{args.prompts}')").fetchone()
n_p = con.execute("SELECT count(*), count(DISTINCT turn_id) FROM p").fetchone()
assert n_rel[0] == n_rel[1] == n_p[0] == n_p[1], (n_rel, n_p)   # every released prompt found once
top5 = [r[0] for r in con.execute(f"""SELECT repo_id FROM p WHERE agent IN {AGENTS} GROUP BY 1
                                      ORDER BY count(*) DESC, repo_id LIMIT 5""").fetchall()]
con.execute("CREATE TABLE top5(repo_id VARCHAR)")
con.executemany("INSERT INTO top5 VALUES (?)", [(str(r),) for r in top5])
con.execute("""CREATE TABLE cand AS SELECT *, (CAST(repo_id AS VARCHAR) IN (SELECT repo_id FROM top5)) AS in_top5 FROM p
               WHERE agent IN ('Claude Code', 'Codex') AND written_by IN ('typed', 'program')""")
con.execute("CREATE TABLE old(session_id VARCHAR)")
con.executemany("INSERT INTO old VALUES (?)", [(s,) for s in sorted(old_ids)])
left_out = con.execute("SELECT count(DISTINCT session_id) FROM cand WHERE session_id IN (SELECT session_id FROM old)").fetchone()[0]
assert left_out > 0, "no earlier check's session among the candidates: the exclusion would be a no-op"


def where(agent, mark, part):
    w = f"agent = '{agent}' AND written_by = '{mark}'"
    return w + {"all": "", "top5": " AND in_top5", "rest": " AND NOT in_top5"}[part]


pop, chosen = [], []
for agent, mark, part, n in PLAN:
    w = where(agent, mark, part)
    rows_all, sess_all = con.execute(f"SELECT count(*), count(DISTINCT session_id) FROM cand WHERE {w}").fetchone()
    pop.append({"agent": agent, "mark": mark, "part": part, "rows": rows_all, "sessions": sess_all})
    rows = con.execute(f"""SELECT turn_id, session_id, repo_id, repo FROM cand
                           WHERE {w} AND session_id NOT IN (SELECT session_id FROM old) ORDER BY h""").fetchall()
    got = []
    if part == "top5":
        # few repositories: round-robin over them in hash order, one card per session, so the cards spread as widely
        # as the stratum allows
        by_repo = {}
        for r in rows:
            by_repo.setdefault(r[2], []).append(r)
        queues = list(by_repo.values())
        while len(got) < n and any(queues):
            for q_ in queues:
                while q_:
                    tid, sid, repo_id, repo = q_.pop(0)
                    if not any(c["session_id"] == sid for c in chosen + got):
                        got.append({"agent": agent, "mark": mark, "part": part, "turn_id": tid, "session_id": sid,
                                    "repo_id": repo_id, "repo": repo})
                        break
                if len(got) == n:
                    break
    else:
        seen_r = set()
        for tid, sid, repo_id, repo in rows:
            if any(c["session_id"] == sid for c in chosen + got) or repo_id in seen_r:
                continue
            seen_r.add(repo_id)
            got.append({"agent": agent, "mark": mark, "part": part, "turn_id": tid, "session_id": sid,
                        "repo_id": repo_id, "repo": repo})
            if len(got) == n:
                break
    pop[-1]["repos_with_rows"] = len({r[2] for r in rows})
    assert len(got) == n, (agent, mark, part, len(got))
    chosen.extend(got)

con.execute("CREATE TABLE pick(turn_id VARCHAR)")
con.executemany("INSERT INTO pick VALUES (?)", [(c["turn_id"],) for c in chosen])
# a labelled prompt can span several rows (content blocks) of one turn id: joined in block order, as SWE-chat's table
# holds them
text = dict(con.execute(f"""SELECT turn_id, string_agg(content, '' ORDER BY content_block_index) FROM
                            read_parquet('{SC}/conversations.parquet') WHERE turn_id IN (SELECT turn_id FROM pick)
                            AND turn_type = 'user_prompt' AND prompt_pushback IS NOT NULL GROUP BY 1""").fetchall())
assert len(text) == len(chosen) and all((text[c["turn_id"]] or "").strip() for c in chosen), "a card has no text"

chosen.sort(key=lambda c: hashlib.sha256((args.seed + "|order|" + c["turn_id"]).encode()).hexdigest())
cards, key = [], {}
for c in chosen:
    cid = "p6-" + hashlib.sha256((args.seed + c["turn_id"]).encode()).hexdigest()[:12]
    full = text[c["turn_id"]]
    cards.append({"id": cid, "part": 6, "agent": c["agent"], "source": "swe-chat-v2", "text": full[:1500], "len": len(full)})
    key[cid] = {"pipeline": MARKS[c["mark"]], "stratum": f"{c['agent']}|{c['mark']}|{c['part']}", **c}
assert len(key) == len(chosen) == sum(n for *_, n in PLAN)

os.makedirs(args.out_dir, exist_ok=True)
json.dump(cards, open(os.path.join(args.out_dir, "cards6.json"), "w"), indent=1, ensure_ascii=False)
json.dump(key, open(os.path.join(args.out_dir, "e1_check6_key.json"), "w"), indent=1, ensure_ascii=False)
json.dump({"seed": args.seed, "plan": PLAN, "top5_repo_ids": [str(r) for r in top5],
           "top5_repos": [r[0] for r in con.execute("SELECT DISTINCT repo FROM cand WHERE in_top5").fetchall()],
           "earlier_sessions_left_out": left_out, "population": pop,
           "release_prompts": n_rel[0]}, open(os.path.join(args.out_dir, "draw6.json"), "w"), indent=1)
print("cards", len(cards), json.dumps(pop))
