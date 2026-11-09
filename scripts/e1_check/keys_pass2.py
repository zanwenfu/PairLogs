"""Read-only on the data: a second pass over keys_1102.py's keys for the cards it did not find (or found only by a text
many rows share). keys_diag.py showed why they were missed: their session is now a copy whose text lives in its kept
session (SWE-chat sessions that Entire now holds), or the session is kept under another key with the same session id
(a renamed repository or file). Each such card is looked for among the user rows of (a) the kept session its key now
points to, else (b) the sessions with the same id under another key; the row whose text is most like the card's
(normalised, at least 0.9 alike) is taken. Cards still not found keep their old labels, marked found false; the
scorers read every card's labels as given. The first pass's keys are kept in OUT/pass1/. No text written.
"""
import collections
import difflib
import json
import os
import re
import shutil

import duckdb

W = "/path/to/pairlogs-work"
V = f"{W}/run2/build/2026-11-02"
K = f"{W}/keys1102"
PB = ("correction", "rejection", "failure_report")
os.makedirs(f"{K}/pass1", exist_ok=True)
for f in ("e1_check_key_2026-11-02.json", "e1_check3_key_2026-11-02.json", "report.json"):
    if not os.path.exists(f"{K}/pass1/{f}"):
        shutil.copy2(f"{K}/{f}", f"{K}/pass1/{f}")
con = duckdb.connect()
con.execute(f"SET threads=8; SET memory_limit='64GB'; SET temp_directory='{W}/tmp'")


def norm(s):
    s = re.sub(r"\[REDACTED\]|<[A-Z][A-Z_]{2,}>", "", s or "")
    return re.sub(r"[\W_]+", "", s.lower())[:400]


def ratio(a, b):
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


k1 = json.load(open(f"{K}/pass1/e1_check_key_2026-11-02.json"))
k3 = json.load(open(f"{K}/pass1/e1_check3_key_2026-11-02.json"))
c1 = {c["id"]: c for c in json.load(open(f"{W}/e1_check_cards.json"))}
c3 = {c["id"]: c for c in json.load(open(f"{W}/e1_check3_cards.json"))}
sess = {r[0]: r[1:] for r in con.execute(
    f"select session_key, source, duplicate_of from read_parquet('{V}/data/*/*.parquet', union_by_name=true)").fetchall()}
by_tail = collections.defaultdict(list)
for sk, (src, dup) in sess.items():
    by_tail[(src, sk.rsplit(":", 1)[-1])].append(sk)

todo = {}
for k, e in k1.items():
    if k.startswith("p2-") and (not e["found_2026_11_02"] or e["how"].startswith("same text")):
        todo[k] = (e["session_key_2026_10_11"], c1[k]["text"])
for k, e in k3.items():
    if not e["found_2026_11_02"]:
        todo[k] = (e["session_key"], c3[k]["text"])
found = {}
for k, (sk, text) in todo.items():
    src = sk.split(":", 1)[0]
    if sk in sess and sess[sk][1]:
        cand, how = [sess[sk][1]], "in the kept session its copy points to"
        while cand[0] in sess and sess[cand[0]][1]:   # follow a chain of copies to its kept end
            cand = [sess[cand[0]][1]]
    else:
        cand, how = [x for x in by_tail.get((src, sk.rsplit(":", 1)[-1]), []) if x != sk], "same session id, other key"
    if not cand:
        continue
    srcs = sorted({sess[x][0] for x in cand if x in sess})
    rows = []
    for s in srcs:
        rows += con.execute(f"""select session_key, turn_number, role, typed_by_developer, actor, kind, copy_of, pushback,
                prompt_pushback, left(content, 1500) from read_parquet('{V}/conversations/{s}/*.parquet')
                where role = 'user' and session_key in ({', '.join("'" + x.replace("'", "''") + "'" for x in cand)})""").fetchall()
    if not rows:
        continue
    best = max(rows, key=lambda r: ratio(text, r[9]))
    r = ratio(text, best[9])
    if r >= 0.9:
        found[k] = (how, round(r, 3), best, sum(ratio(text, x[9]) >= 0.9 for x in rows))
changes = collections.Counter()
for k, (how, r, row, n_alike) in found.items():
    if k.startswith("p2-"):
        e = k1[k]
        e.update({"pipeline": "typed by a person" if row[3] else "not typed", "actor": row[4], "kind": row[5],
                  "session_key": row[0], "turn_number": row[1], "copy_of_2026_11_02": row[6], "role_2026_11_02": row[2],
                  "found_2026_11_02": True, "how": how, "text_alike": r, "rows_alike_in_session": n_alike})
        changes[("who typed", how, e["pipeline_2026_10_11"], e["pipeline"])] += 1
    else:
        e = k3[k]
        ours, sw = row[7], row[8]
        e.update({"ours": bool(ours) if ours is not None else e["ours_2026_10_11"],
                  "swechat": sw if sw is not None else e["swechat_2026_10_11"],
                  "ours_missing_2026_11_02": ours is None, "swechat_missing_2026_11_02": sw is None,
                  "typed_2026_11_02": row[3], "copy_of_2026_11_02": row[6], "session_key": row[0],
                  "turn_number": row[1], "found_2026_11_02": True, "how": how, "text_alike": r,
                  "rows_alike_in_session": n_alike})
        s, o = e["swechat"] in PB, e["ours"]
        e["cell_2026_11_02"] = "both" if s and o else "sc_only" if s else "ours_only" if o else "neither"
        changes[("pushback", how, e["cell"], e["cell_2026_11_02"], e["ours"] != e["ours_2026_10_11"],
                 e["swechat"] != e["swechat_2026_10_11"], ours is None or sw is None)] += 1
for name, obj in (("e1_check_key_2026-11-02.json", k1), ("e1_check3_key_2026-11-02.json", k3)):
    with open(f"{K}/{name}", "w") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)
    os.chmod(f"{K}/{name}", 0o600)
rep = json.load(open(f"{K}/pass1/report.json"))
rep["pass2"] = {"looked_for": len(todo), "found": len(found),
                "changes": [list(c) + [n] for c, n in sorted(changes.items())],
                "still_not_found": sorted(k for k in todo if k not in found),
                "who_typed_changed_now": sum(e["pipeline"] != e["pipeline_2026_10_11"] for e in k1.values()),
                "who_typed_not_found_now": sum(not e["found_2026_11_02"] for e in k1.values()),
                "pushback_not_found_now": sum(not e["found_2026_11_02"] for e in k3.values()),
                "pushback_ours_changed_now": sum(e["ours"] != e["ours_2026_10_11"] for e in k3.values()),
                "pushback_swechat_changed_now": sum(e["swechat"] != e["swechat_2026_10_11"] for e in k3.values()),
                "pushback_cell_changed_now": sum(e["cell_2026_11_02"] != e["cell"] for e in k3.values()),
                "pushback_label_missing_now": sum(bool(e.get("ours_missing_2026_11_02") or e.get("swechat_missing_2026_11_02"))
                                                  for e in k3.values())}
with open(f"{K}/report.json", "w") as f:
    json.dump(rep, f, indent=1, default=str)
print(json.dumps(rep["pass2"], indent=1, default=str))
