"""Read-only: the three human checks' answer keys matched to version 2026-11-02, written beside the old keys.

The cards and pages are not touched (the person's marks are tied to them). Each card is found again in 2026-11-02 and
its labels re-read there; a card that cannot be found keeps its old labels and says so (`found_2026_11_02` false).
- who typed it (184): Part 1 cards are SWE-chat v2 prompts numbered as m5 numbered them (row_number() over SWE-chat's
  labelled user prompts); each is found again by that number in 2026-11-02's SWE-chat copy and checked by session,
  agent and its first 1,500 characters against m5's sample, then classed by the release's
  labels/swe_chat_prompts.parquet (by SWE-chat's turn_id) and, as a cross-check, by m16_prompts_11-02.parquet.
  Part 2 cards are our rows, found by session key and turn number, checked by their first 1,500 characters against
  m7's sample, else found by those characters among the same source's user rows.
- pushback (80): found as Part 2 is (the card's text against the row's), then our label (`pushback`) and SWE-chat's
  (`prompt_pushback`) read from the row. `cell` stays the sampling stratum the card was drawn from (the scorer weights
  by it); the cell the 2026-11-02 labels would give is kept as `cell_2026_11_02`.
- labels about the work (100): each card's item matched to the final run's labels (wl/data/final) by session key or
  repository, else by the session's own id within its source; the run's test labels of the same 100 items (the
  check's own texts) written as a second labels file.
No text is written: ids, labels and counts only. argv: OUT_DIR. Env VERSION_DIR (default run2/build/2026-11-02).
"""
import collections
import difflib
import json
import os
import re
import sys

import duckdb

W = "/path/to/pairlogs-work"
V = os.environ.get("VERSION_DIR", f"{W}/run2/build/2026-11-02")
OUT = sys.argv[1]
SC = f"{V}/copied/swe-chat-v2/SALT-NLP__SWE-chat/conversations.parquet"
PB = ("correction", "rejection", "failure_report")
os.makedirs(OUT, exist_ok=True)
os.chmod(OUT, 0o700)
con = duckdb.connect()
con.execute(f"SET threads=8; SET memory_limit='96GB'; SET temp_directory='{W}/tmp'")
report = {"version": V}


def norm(s, n=400):
    """Unicode-aware, for comparing two texts here."""
    s = re.sub(r"\[REDACTED\]|<[A-Z][A-Z_]{2,}>", "", s or "")
    return re.sub(r"[\W_]+", "", s.lower())[:n]


def norm_ascii(s, n=200):
    """As the SQL macro nrm reads it (RE2's \\W is ASCII-only): the first 3,000 characters."""
    s = re.sub(r"\[REDACTED\]|<[A-Z][A-Z_]{2,}>", "", (s or "")[:3000])
    return re.sub(r"[\W_]+", "", s.lower(), flags=re.ASCII)[:n]


def sim(a, b):
    return round(difflib.SequenceMatcher(None, norm(a), norm(b)).ratio(), 3)


def write(name, obj):
    p = f"{OUT}/{name}"
    with open(p, "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.chmod(p, 0o600)


def sql_list(xs):
    return ", ".join("'" + x.replace("'", "''") + "'" for x in xs)


# ---------- rows of ours, found by session key and turn number, else by their text ----------
def find_rows(want):
    """want: {card: (source, session_key, turn_number, text)} -> {card: dict}. text = what the card showed."""
    got = {}
    by_src = collections.defaultdict(list)
    for cid, (src, sk, tn, _) in want.items():
        by_src[src].append((cid, sk, tn))
    for src, items in by_src.items():
        keys = sorted({sk for _, sk, _ in items})
        rows = con.execute(f"""
            select session_key, turn_number, role, typed_by_developer, actor, kind, copy_of, pushback, prompt_pushback,
                   left(content, 1500) head
            from read_parquet('{V}/conversations/{src}/*.parquet') where session_key in ({sql_list(keys)})""").fetchall()
        idx = collections.defaultdict(list)
        for r in rows:
            idx[(r[0], r[1])].append(r)
        for cid, sk, tn in items:
            cand = idx.get((sk, tn), [])
            text = want[cid][3]
            hit = [r for r in cand if norm(r[9]) and norm(text) and (norm(r[9]).startswith(norm(text)[:200])
                                                                     or norm(text).startswith(norm(r[9])[:200]))]
            if hit:
                got[cid] = {"how": "same key and turn", "row": hit[0], "n_rows_same_key_turn": len(cand)}
            elif cand:
                got[cid] = {"how": "same key and turn, text differs", "row": cand[0], "n_rows_same_key_turn": len(cand),
                            "sim": max(sim(text, r[9]) for r in cand)}
    # by text among the same source's user rows, for cards not found by key and turn (or whose text differs)
    left = {cid: w for cid, w in want.items() if cid not in got or got[cid]["how"] != "same key and turn"}
    by_src = collections.defaultdict(list)
    for cid, (src, sk, tn, text) in left.items():
        by_src[src].append(cid)
    for src, cids in by_src.items():
        con.execute("create or replace temp table w(cid varchar, k varchar)")
        con.executemany("insert into w values (?, ?)", [(c, norm_ascii(want[c][3])) for c in cids])
        con.execute("""create or replace macro nrm(s) as left(lower(regexp_replace(regexp_replace(left(s, 3000),
            '\\[REDACTED\\]|<[A-Z][A-Z_]{2,}>', '', 'g'), '[\\W_]+', '', 'g')), 200)""")
        rows = con.execute(f"""
            select w.cid, c.session_key, c.turn_number, c.role, c.typed_by_developer, c.actor, c.kind, c.copy_of,
                   c.pushback, c.prompt_pushback
            from read_parquet('{V}/conversations/{src}/*.parquet') c join w on nrm(c.content) = w.k
            where c.role = 'user' and length(w.k) >= 40""").fetchall()
        hits = collections.defaultdict(list)
        for r in rows:
            hits[r[0]].append(r[1:])
        for cid in cids:
            h = hits.get(cid, [])
            if not h:
                got.setdefault(cid, {"how": "not found", "row": None})
                if got[cid]["how"] == "same key and turn, text differs":
                    got[cid]["how"] = "same key and turn, text differs; text found nowhere else"
                continue
            _, sk, tn, _ = want[cid]
            same_session = [r for r in h if r[0] == sk]
            pick = (same_session or h)[0]
            row = (pick[0], pick[1], pick[2], pick[3], pick[4], pick[5], pick[6], pick[7], pick[8], None)
            got[cid] = {"how": "same text, " + ("same session, turn moved" if same_session else
                                                ("another session" if len(h) == 1 else f"{len(h)} rows hold it")),
                        "row": row, "n_text_hits": len(h)}
    return got


# ---------- 1. who typed it ----------
key1 = json.load(open(f"{W}/e1_check_key.json"))
cards1 = {c["id"]: c for c in json.load(open(f"{W}/e1_check_cards.json"))}
m5 = {r[0]: r[1:] for r in con.execute(
    f"select id, session_id, agent, cls, head from read_parquet('{W}/m5_sample.parquet')").fetchall()}
con.execute(f"""create temp table sc as
select row_number() over () as id, turn_id, session_id, agent, left(content, 1500) head
from read_parquet('{SC}') where turn_type = 'user_prompt' and prompt_pushback is not null""")
p1_ids = [int(k.split("-")[1]) for k in key1 if k.startswith("p1-")]
sc = {r[0]: r[1:] for r in con.execute(f"select * from sc where id in ({', '.join(map(str, p1_ids))})").fetchall()}
by_head = collections.defaultdict(list)
for r in con.execute(f"""select session_id, head, turn_id from sc where session_id in
        ({sql_list(sorted({m5[i][0] for i in p1_ids if i in m5}))})""").fetchall():
    by_head[(r[0], r[1])].append(r[2])
rel = dict(con.execute(f"select turn_id, written_by from read_parquet('{W}/release/2026-11-02/b/labels/swe_chat_prompts.parquet')").fetchall())
m16 = dict(con.execute(f"select id, cls from read_parquet('{W}/m16_prompts_11-02.parquet')").fetchall())
NAME = {"typed": "typed by a person", "action": "person action", "program": "program", "unknown": "unknown"}
new1, why1 = {}, collections.Counter()
want2 = {}
for k, v in key1.items():
    if k.startswith("p1-"):
        i = int(k.split("-")[1])
        s, a, cls_old, head = m5[i]
        card_ok = norm(cards1[k]["text"])[:200] == norm(head)[:200]
        r = sc.get(i)
        same = bool(r) and r[1] == s and r[2] == a and r[3] == head
        tids = by_head.get((s, head), [])
        tid = r[0] if same else (tids[0] if len(tids) == 1 else None)
        how = ("same number, session, agent and text" if same else
               "found by session and text" if tid else "not found")
        cls_new = NAME.get(rel.get(tid)) if tid else None
        cross = m16.get(i) if same else None
        e = {"pipeline": cls_new or v["pipeline"], "what": v["what"] if cls_new == v["pipeline"] else None,
             "pipeline_2026_10_11": v["pipeline"], "found_2026_11_02": bool(cls_new), "how": how, "turn_id": tid,
             "card_matches_sample": card_ok, "m16_agrees": (cross == cls_new) if cross else None,
             "turn_ids_with_this_text": len(tids)}
        new1[k] = e
        why1[(how, v["pipeline"], e["pipeline"])] += 1
    else:
        want2[k] = (v["source"], v["session_key"], v["turn_number"], cards1[k]["text"])
got2 = find_rows(want2)
for k, (src, sk, tn, _) in want2.items():
    v = key1[k]
    g = got2.get(k, {"how": "not found", "row": None})
    row = g["row"]
    if row is not None:
        typed, actor, kind, copy_of = row[3], row[4], row[5], row[6]
        e = {"pipeline": "typed by a person" if typed else "not typed", "actor": actor, "kind": kind,
             "source": src, "session_key": row[0], "turn_number": row[1], "copy_of_2026_11_02": copy_of,
             "role_2026_11_02": row[2]}
    else:
        e = {k2: v[k2] for k2 in ("pipeline", "actor", "kind", "source", "session_key", "turn_number")}
    e.update({"pipeline_2026_10_11": v["pipeline"], "actor_2026_10_11": v["actor"], "kind_2026_10_11": v["kind"],
              "session_key_2026_10_11": sk, "turn_number_2026_10_11": tn,
              "found_2026_11_02": row is not None, "how": g["how"]})
    new1[k] = e
    why1[(g["how"], v["pipeline"], e["pipeline"])] += 1
write("e1_check_key_2026-11-02.json", new1)
report["who_typed"] = {
    "cards": len(new1),
    "by_how_old_new": [{"how": h, "old": o, "new": n, "cards": c} for (h, o, n), c in sorted(why1.items())],
    "changed": sum(e["pipeline"] != e["pipeline_2026_10_11"] for e in new1.values()),
    "not_found": sum(not e["found_2026_11_02"] for e in new1.values()),
    "p1_card_text_matches_m5_sample": sum(bool(e.get("card_matches_sample")) for k, e in new1.items() if k.startswith("p1-")),
    "p1_m16_agrees": collections.Counter(str(e.get("m16_agrees")) for k, e in new1.items() if k.startswith("p1-")),
    "p2_actor_kind_changed": sum((e["actor"], e["kind"]) != (e["actor_2026_10_11"], e["kind_2026_10_11"])
                                 for k, e in new1.items() if k.startswith("p2-")),
    "p2_now_copy": sum(bool(e.get("copy_of_2026_11_02")) for k, e in new1.items() if k.startswith("p2-")),
}
print(json.dumps(report["who_typed"], default=str), flush=True)

# ---------- 3. pushback ----------
key3 = json.load(open(f"{W}/e1_check3_key.json"))
cards3 = {c["id"]: c for c in json.load(open(f"{W}/e1_check3_cards.json"))}
want3 = {k: (v["session_key"].split(":", 1)[0], v["session_key"], v["turn_number"], cards3[k]["text"])
         for k, v in key3.items()}
got3 = find_rows(want3)
new3, why3 = {}, collections.Counter()


def cell_of(sw, ours):
    s = sw in PB
    return "both" if s and ours else "sc_only" if s else "ours_only" if ours else "neither"


for k, v in key3.items():
    g = got3.get(k, {"how": "not found", "row": None})
    row = g["row"]
    e = dict(v)
    e.update({"ours_2026_10_11": v["ours"], "swechat_2026_10_11": v["swechat"], "how": g["how"],
              "found_2026_11_02": row is not None})
    if row is not None:
        ours, sw = row[7], row[8]
        e.update({"ours": bool(ours) if ours is not None else v["ours"],
                  "swechat": sw if sw is not None else v["swechat"],
                  "ours_missing_2026_11_02": ours is None, "swechat_missing_2026_11_02": sw is None,
                  "typed_2026_11_02": row[3], "copy_of_2026_11_02": row[6],
                  "session_key": row[0], "turn_number": row[1]})
    e["cell_2026_11_02"] = cell_of(e["swechat"], e["ours"])
    new3[k] = e
    why3[(g["how"], v["cell"], e["cell_2026_11_02"], e["ours"] != v["ours"], e["swechat"] != v["swechat"])] += 1
write("e1_check3_key_2026-11-02.json", new3)
report["pushback"] = {
    "cards": len(new3),
    "by_how_cell_old_new_ours_changed_swechat_changed": [
        {"how": h, "cell": c, "cell_now": n, "ours_changed": o, "swechat_changed": s, "cards": x}
        for (h, c, n, o, s), x in sorted(why3.items())],
    "ours_changed": sum(e["ours"] != e["ours_2026_10_11"] for e in new3.values()),
    "swechat_changed": sum(e["swechat"] != e["swechat_2026_10_11"] for e in new3.values()),
    "cell_changed": sum(e["cell_2026_11_02"] != e["cell"] for e in new3.values()),
    "not_found": sum(not e["found_2026_11_02"] for e in new3.values()),
    "label_missing": sum(bool(e.get("ours_missing_2026_11_02") or e.get("swechat_missing_2026_11_02"))
                         for e in new3.values()),
    "not_typed_now": sum(e.get("typed_2026_11_02") is False for e in new3.values()),
}
print(json.dumps(report["pushback"], default=str), flush=True)

# ---------- 4. labels about the work ----------
key4 = json.load(open(f"{W}/e1_check4_key.json"))
fin_s = {r["session_key"]: r for r in map(json.loads, open(f"{W}/wl/data/final/sessions.jsonl"))}
fin_r = {r["repo"]: r for r in map(json.loads, open(f"{W}/wl/data/final/repos.jsonl"))}
fin_r_lower = collections.defaultdict(list)
for repo in fin_r:
    fin_r_lower[repo.lower()].append(repo)
by_sid = collections.defaultdict(list)
for sk in fin_s:
    src, rest = sk.split(":", 1)
    by_sid[(src, rest.rsplit(":", 1)[-1] if src in ("entire",) else rest)].append(sk)
rel_s = set()
for t in ("a-names", "b"):
    p = f"{W}/release/2026-11-02/{t}/work_labels/sessions.parquet"
    rel_s |= {r[0] for r in con.execute(f"select session_key from read_parquet('{p}')").fetchall()}
rel_r = set()
for t in ("a-names", "b"):
    p = f"{W}/release/2026-11-02/{t}/work_labels/repositories.parquet"
    rel_r |= {r[0] for r in con.execute(f"select repo from read_parquet('{p}')").fetchall()}


def labels(r, fields):
    return {f: {"label": (r.get(f) or {}).get("label")} for f in fields if r.get(f)}


new4, final_rows, why4 = {}, [], collections.Counter()
for cid, rid in key4["key"].items():
    kind, item = rid.split(":", 1)
    if kind == "repo":
        if item in fin_r:
            r, how, now = fin_r[item], "same repository", item
        elif len(fin_r_lower.get(item.lower(), [])) == 1:
            now = fin_r_lower[item.lower()][0]
            r, how = fin_r[now], "same repository, other letter case"
        else:
            r, how, now = None, "not in the final run", None
        ans = labels(r, ("project", "domain")) if r else {}
        in_rel = now in rel_r if now else False
    else:
        if item in fin_s:
            r, how, now = fin_s[item], "same session key", item
        else:
            src, rest = item.split(":", 1)
            c = by_sid.get((src, rest.rsplit(":", 1)[-1] if src in ("entire",) else rest), [])
            if len(c) == 1:
                now, how = c[0], "same session id, key changed"
                r = fin_s[now]
            else:
                r, now, how = None, None, "not in the final run" if not c else f"{len(c)} sessions share its id"
        ans = labels(r, ("project", "domain", "task")) if r else {}
        in_rel = now in rel_s if now else False
    new4[cid] = {"item_2026_10_26": rid, "final_item": (kind + ":" + now) if now else None, "how": how,
                 "in_release_2026_11_02": in_rel}
    final_rows.append({"id": rid, "answer": ans, "final_item": (kind + ":" + now) if now else None})
    why4[(kind, how, in_rel)] += 1
write("e1_check4_key_2026-11-02.json", {"sample": key4["sample"], "seed": key4["seed"], "key": key4["key"],
                                        "matched_2026_11_02": new4})
with open(f"{OUT}/work_labels_final.jsonl", "w") as f:
    for r in final_rows:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
os.chmod(f"{OUT}/work_labels_final.jsonl", 0o600)
# the run's test labels of the same 100 items (the check's own texts), ids only
cid_of = dict(l.rstrip("\n").split("\t") for l in open(f"{W}/wl/data/check-ids.tsv"))
test = {}
for r in map(json.loads, open(f"{W}/wl/data/test/result.jsonl")):
    if r.get("request_id") in cid_of and r.get("status") == 200 and r.get("answer"):
        test[r["id"]] = {f: {"label": (v or {}).get("label")} for f, v in r["answer"].items()
                         if f in ("project", "domain", "task")}
with open(f"{OUT}/work_labels_test100.jsonl", "w") as f:
    for rid in key4["key"].values():
        f.write(json.dumps({"id": rid, "answer": test.get(rid, {})}, ensure_ascii=False) + "\n")
os.chmod(f"{OUT}/work_labels_test100.jsonl", 0o600)
report["work_labels"] = {
    "cards": len(new4),
    "by_kind_how_in_release": [{"kind": k, "how": h, "in_release": i, "cards": c} for (k, h, i), c in sorted(why4.items())],
    "with_final_labels": sum(bool(r["answer"]) for r in final_rows),
    "test_run_items_matched": sum(1 for rid in key4["key"].values() if test.get(rid)),
}
print(json.dumps(report["work_labels"], default=str), flush=True)
write("report.json", report)
print("done", flush=True)
