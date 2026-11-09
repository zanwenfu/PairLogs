"""Read-only on the data: a second pass for the work-label cards with no final label (wl_diag.py: SWE-chat sessions
now copies of an Entire session, whose kept end the final run labelled; a SpecStory file kept under another key with
the same id). Each is matched to its kept end, else to the one other key with its id; the final labels written for
the card's own item id, so agreement4.py reads them unchanged. The first pass's files are kept in keys1102/pass1/."""
import collections
import json
import os
import shutil

import duckdb

W = "/path/to/pairlogs-work"
V = f"{W}/run2/build/2026-11-02"
K = f"{W}/keys1102"
for f in ("e1_check4_key_2026-11-02.json", "work_labels_final.jsonl"):
    if not os.path.exists(f"{K}/pass1/{f}"):
        shutil.copy2(f"{K}/{f}", f"{K}/pass1/{f}")
k4 = json.load(open(f"{K}/pass1/e1_check4_key_2026-11-02.json"))
rows = [json.loads(l) for l in open(f"{K}/pass1/work_labels_final.jsonl")]
fin_s = {r["session_key"]: r for r in map(json.loads, open(f"{W}/wl/data/final/sessions.jsonl"))}
rel_s = set()
for t in ("a-names", "b"):
    rel_s |= {r[0] for r in duckdb.sql(f"select session_key from read_parquet('{W}/release/2026-11-02/{t}/work_labels/sessions.parquet')").fetchall()}
con = duckdb.connect()
sess = {r[0]: r[1:] for r in con.execute(
    f"select session_key, source, duplicate_of from read_parquet('{V}/data/*/*.parquet', union_by_name=true)").fetchall()}
by_tail = collections.defaultdict(list)
for sk, (src, dup) in sess.items():
    by_tail[(src, sk.rsplit(":", 1)[-1])].append(sk)
n = collections.Counter()
for r in rows:
    cid = next(c for c, rid in k4["key"].items() if rid == r["id"])
    e = k4["matched_2026_11_02"][cid]
    if e["final_item"] or not r["id"].startswith("session:"):
        continue
    item = r["id"].split(":", 1)[1]
    end, how = item, None
    while end in sess and sess[end][1]:
        end = sess[end][1]
    if end != item and end in fin_s:
        how = "the kept session its copy points to"
    else:
        src = item.split(":", 1)[0]
        others = [x for x in by_tail.get((src, item.rsplit(":", 1)[-1]), []) if x != item and x in fin_s]
        if len(others) == 1:
            end, how = others[0], "same session id, other key"
    if not how:
        continue
    f = fin_s[end]
    r["answer"] = {x: {"label": (f.get(x) or {}).get("label")} for x in ("project", "domain", "task") if f.get(x)}
    r["final_item"] = "session:" + end
    e.update({"final_item": "session:" + end, "how": how, "in_release_2026_11_02": end in rel_s})
    n[how] += 1
with open(f"{K}/e1_check4_key_2026-11-02.json", "w") as fh:
    json.dump(k4, fh, indent=1, ensure_ascii=False)
with open(f"{K}/work_labels_final.jsonl", "w") as fh:
    for r in rows:
        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
for p in ("e1_check4_key_2026-11-02.json", "work_labels_final.jsonl"):
    os.chmod(f"{K}/{p}", 0o600)
rep = json.load(open(f"{K}/report.json"))
rep["work_labels_pass2"] = {"matched": dict(n), "with_final_labels_now": sum(bool(r["answer"]) for r in rows),
                            "in_release_now": sum(e["in_release_2026_11_02"] for e in k4["matched_2026_11_02"].values()),
                            "without": sorted(c for c, e in k4["matched_2026_11_02"].items() if not e["final_item"])}
json.dump(rep, open(f"{K}/report.json", "w"), indent=1, default=str)
print(json.dumps(rep["work_labels_pass2"], indent=1))
