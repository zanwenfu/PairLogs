"""Counts only: SWE-chat v2's repositories against the Entire repositories of the fixed 2026-11-02 (kept sessions)."""
import glob, collections
import duckdb, pyarrow.parquet as pq
W = "/path/to/pairlogs-work"
V = f"{W}/run2/build-fix2/2026-11-02"
con = duckdb.connect(); con.execute("SET threads=8; SET memory_limit='24GB'")
rp = glob.glob(f"{V}/copied/swe-chat-v2/*/repositories.parquet")
cols = pq.read_schema(rp[0]).names
print("swe-chat repositories table columns:", cols[:12])
name_col = next(c for c in ("repo_full_name", "full_name", "repo_name", "repo_id", "name") if c in cols)
sw = {r[0].lower() for r in con.execute(f"select distinct {name_col} from read_parquet({rp})").fetchall() if r[0]}
ours_kept = {r[0].lower() for r in con.execute(f"select distinct repo from read_parquet('{V}/data/entire/*.parquet', union_by_name=true) where duplicate_of is null and repo is not null").fetchall()}
ours_any = {r[0].lower() for r in con.execute(f"select distinct repo from read_parquet('{V}/data/*/*.parquet', union_by_name=true) where repo is not null").fetchall()}
swe_rows = {r[0].lower() for r in con.execute(f"select distinct repo from read_parquet('{V}/data/swe-chat-v2/*.parquet') where repo is not null").fetchall()}
print("name column:", name_col, "| SWE-chat repositories:", len(sw))
print("our Entire repositories with kept sessions:", len(ours_kept))
print("both:", len(sw & ours_kept), "| only SWE-chat:", len(sw - ours_kept), "| only ours:", len(ours_kept - sw))
print("of SWE-chat's not in our Entire kept: in our data at all (any source, kept or copy):", len((sw - ours_kept) & ours_any),
      "| named by our swe-chat-v2 rows:", len((sw - ours_kept) & swe_rows))
import json, sys
json.dump({"swechat_repositories": len(sw), "our_entire_repositories_kept": len(ours_kept), "both": len(sw & ours_kept),
           "only_swechat": len(sw - ours_kept), "only_ours": len(ours_kept - sw),
           "only_swechat_in_our_swechat_rows": len((sw - ours_kept) & swe_rows),
           "version": "2026-11-02 after the helper-session fix", "name_column": name_col},
          open(sys.argv[1], "w"), indent=1)
