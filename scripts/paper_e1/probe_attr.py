"""Counts only: SWE-chat v2's turn types in our copy; and which keys Entire's checkpoint metadata.json files hold
(attribution of lines to the agent or a person?), over tier A's Entire sessions."""
import json, collections, glob
import duckdb, pyarrow.parquet as pq
W = "/path/to/pairlogs-work"
V = f"{W}/run2/build-fix2/2026-11-02"
R = f"{W}/release-fix2/2026-11-02/a-names"
con = duckdb.connect(); con.execute("SET threads=8; SET memory_limit='24GB'")
sw = glob.glob(f"{V}/copied/swe-chat-v2/*/conversations.parquet")
print("swe-chat conversations files:", len(sw))
print(con.execute(f"select turn_type, count(*) from read_parquet({sw}) group by 1 order by 2 desc").fetchall()[:12])
keys = collections.Counter(); sub = collections.Counter(); n = files = 0
for f in sorted(glob.glob(f"{R}/data/entire/*.parquet")):
    t = pq.read_table(f, columns=["files"])
    for fl in t["files"].to_pylist():
        n += 1
        for x in fl or []:
            if x["path"].endswith("metadata.json") and x.get("content"):
                files += 1
                try:
                    d = json.loads(x["content"])
                except ValueError:
                    continue
                keys.update(d.keys() if isinstance(d, dict) else [])
                for k, v in (d.items() if isinstance(d, dict) else []):
                    if "attrib" in k.lower() and isinstance(v, dict):
                        sub.update(v.keys())
print("tier A Entire sessions", n, "metadata.json files", files)
print("keys:", keys.most_common(40))
print("attribution sub-keys:", sub.most_common(30))
