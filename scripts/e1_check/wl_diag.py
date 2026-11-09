"""Read-only: why 10 work-label cards have no label in the final run (keys1102/e1_check4_key_2026-11-02.json). Per
card, no text: for a session, whether its key is in 2026-11-02 (kept or a copy, and the kept end's labels), whether its
id is kept under another key, and how many typed rows it has there; for a repository, whether any 2026-11-02 session
names it, whether GitHub's lookup of 11-01 had it (wl/data/repos.jsonl), and whether the final run has it under
another letter case."""
import collections
import json

import duckdb

W = "/path/to/pairlogs-work"
V = f"{W}/run2/build/2026-11-02"
K = f"{W}/keys1102"
con = duckdb.connect()
con.execute(f"SET threads=8; SET memory_limit='32GB'; SET temp_directory='{W}/tmp'")
k4 = json.load(open(f"{K}/e1_check4_key_2026-11-02.json"))["matched_2026_11_02"]
fin_s = {json.loads(l)["session_key"] for l in open(f"{W}/wl/data/final/sessions.jsonl")}
fin_r = {json.loads(l)["repo"].lower() for l in open(f"{W}/wl/data/final/repos.jsonl")}
looked = {json.loads(l).get("repo", "").lower() for l in open(f"{W}/wl/data/repos.jsonl")}
sess = {r[0]: r[1:] for r in con.execute(
    f"select session_key, source, duplicate_of, lower(repo) from read_parquet('{V}/data/*/*.parquet', union_by_name=true)").fetchall()}
by_tail = collections.defaultdict(list)
for sk, (src, dup, repo) in sess.items():
    by_tail[(src, sk.rsplit(":", 1)[-1])].append(sk)
repos = collections.Counter(v[2] for v in sess.values() if v[2])
out = {}
for cid, e in k4.items():
    if e["final_item"]:
        continue
    kind, item = e["item_2026_10_26"].split(":", 1)
    if kind == "repo":
        out[cid] = {"kind": kind, "sessions_naming_it_11_02": repos.get(item.lower(), 0),
                    "in_github_lookup_11_01": item.lower() in looked, "in_final_any_case": item.lower() in fin_r}
    else:
        src = item.split(":", 1)[0]
        d = {"kind": kind, "source": src, "key_in_11_02": item in sess, "is_copy": bool(item in sess and sess[item][1])}
        end = item
        while end in sess and sess[end][1]:
            end = sess[end][1]
        d["kept_end_has_final_label"] = end in fin_s if end != item else None
        others = [x for x in by_tail.get((src, item.rsplit(":", 1)[-1]), []) if x != item]
        d["same_id_other_keys"] = len(others)
        d["other_key_has_final_label"] = any(x in fin_s for x in others)
        if item in sess:
            d["typed_rows_11_02"] = con.execute(f"""select count(*) from read_parquet('{V}/conversations/{src}/*.parquet')
                where session_key = ? and typed_by_developer""", [item]).fetchone()[0]
        out[cid] = d
json.dump(out, open(f"{K}/wl_diag.json", "w"), indent=1)
print(json.dumps(out, indent=1))
