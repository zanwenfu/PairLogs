"""Read-only: why some cards were not found in 2026-11-02 or changed label (keys_1102.py's output). No text printed:
per card, whether its session key is in 2026-11-02's session table (kept or a copy), whether its session id is there
under another key, how many rows that session has and whether the card's turn exists, the best text similarity of
the card to any user row of the session, and for cards found by text the typed marks of every row holding it."""
import collections
import difflib
import json
import re
import sys

import duckdb

W = "/path/to/pairlogs-work"
V = f"{W}/run2/build/2026-11-02"
K = f"{W}/keys1102"
con = duckdb.connect()
con.execute(f"SET threads=8; SET memory_limit='64GB'; SET temp_directory='{W}/tmp'")


def norm(s):
    s = re.sub(r"\[REDACTED\]|<[A-Z][A-Z_]{2,}>", "", s or "")
    return re.sub(r"[\W_]+", "", s.lower())[:400]


k1 = json.load(open(f"{K}/e1_check_key_2026-11-02.json"))
k3 = json.load(open(f"{K}/e1_check3_key_2026-11-02.json"))
c1 = {c["id"]: c for c in json.load(open(f"{W}/e1_check_cards.json"))}
c3 = {c["id"]: c for c in json.load(open(f"{W}/e1_check3_cards.json"))}
todo = {}
for k, e in k1.items():
    if k.startswith("p2-") and (not e["found_2026_11_02"] or e["pipeline"] != e["pipeline_2026_10_11"]
                                or e["how"].startswith("same text")):
        todo[k] = (e["session_key_2026_10_11"], e["turn_number_2026_10_11"], c1[k]["text"], e)
for k, e in k3.items():
    if not e["found_2026_11_02"]:
        todo[k] = (e["session_key"], e["turn_number"], c3[k]["text"], e)
sess = {r[0]: r[1:] for r in con.execute(
    f"select session_key, source, duplicate_of from read_parquet('{V}/data/*/*.parquet', union_by_name=true)").fetchall()}
by_tail = collections.defaultdict(list)
for sk, (src, dup) in sess.items():
    by_tail[(src, sk.rsplit(":", 1)[-1])].append(sk)
out = {}
for k, (sk, tn, text, e) in todo.items():
    src = sk.split(":", 1)[0]
    d = {"source": src, "how": e["how"], "pipeline_old": e.get("pipeline_2026_10_11"), "pipeline_new": e.get("pipeline"),
         "actor_old_new": [e.get("actor_2026_10_11"), e.get("actor")], "kind_old_new": [e.get("kind_2026_10_11"), e.get("kind")],
         "key_in_11_02": sk in sess, "key_is_copy": bool(sk in sess and sess[sk][1])}
    others = [x for x in by_tail.get((src, sk.rsplit(":", 1)[-1]), []) if x != sk]
    d["same_id_other_keys"] = len(others)
    look = [sk] + others[:3]
    rows = con.execute(f"""select session_key, turn_number, role, typed_by_developer, left(content, 1500)
        from read_parquet('{V}/conversations/{src}/*.parquet') where session_key in ({', '.join("'" + x.replace("'", "''") + "'" for x in look)})""").fetchall()
    d["rows_in_session"] = sum(r[0] == sk for r in rows)
    d["turn_exists"] = any(r[0] == sk and r[1] == tn for r in rows)
    user = [r for r in rows if r[2] == "user"]
    best = max(user, key=lambda r: difflib.SequenceMatcher(None, norm(text), norm(r[4])).ratio(), default=None)
    if best:
        d["best_sim"] = round(difflib.SequenceMatcher(None, norm(text), norm(best[4])).ratio(), 3)
        d["best_same_key"] = best[0] == sk
        d["best_turn_delta"] = (best[1] - tn) if best[0] == sk else None
        d["best_typed"] = best[3]
    out[k] = d
# cards found by text: the typed marks of every row holding the same text
for k, e in k1.items():
    if e["how"].startswith("same text"):
        src = e["source"]
        t = re.sub(r"[\W_]+", "", re.sub(r"\[REDACTED\]|<[A-Z][A-Z_]{2,}>", "", c1[k]["text"][:3000]).lower(), flags=re.ASCII)[:200]
        con.execute("create or replace temp table w(k varchar)")
        con.execute("insert into w values (?)", [t])
        n = con.execute(f"""select typed_by_developer, actor, kind, count(*) from read_parquet('{V}/conversations/{src}/*.parquet')
            where role = 'user' and left(lower(regexp_replace(regexp_replace(left(content, 3000), '\\[REDACTED\\]|<[A-Z][A-Z_]{{2,}}>', '', 'g'), '[\\W_]+', '', 'g')), 200) = (select k from w)
            group by all""").fetchall()
        out.setdefault(k, {})["all_rows_with_text"] = [list(r) for r in n]
json.dump(out, open(f"{K}/diag.json", "w"), indent=1, default=str)
print(json.dumps(out, indent=1, default=str))
