"""The growth test's inputs, from the version as built (2026-11-02 after its fixes): every GitHub repository it holds a
session from (Entire, SpecStory, Aider; kept sessions and copies alike), a seeded sample of them for the first small
batch, and an index of the version's sessions (ids, content hashes, dedup keys; no text) to compare a fresh read with.

python list_repos.py BUILD OUT_DIR [--batch0 50] [--seed growth-2026-11-06]
"""
import argparse
import hashlib
import os

import duckdb

ap = argparse.ArgumentParser()
ap.add_argument("build")
ap.add_argument("out")
ap.add_argument("--batch0", type=int, default=50)
ap.add_argument("--seed", default="growth-2026-11-06")
args = ap.parse_args()
os.makedirs(args.out, exist_ok=True)
con = duckdb.connect()
con.execute(f"""CREATE TABLE idx AS SELECT source, source_id, session_key, repo, dedup_key, content_sha256, duplicate_of,
                fetched_at, started_at FROM read_parquet('{args.build}/data/*/*.parquet', union_by_name=true)""")
con.execute(f"COPY idx TO '{args.out}/version_index.parquet' (FORMAT parquet)")
repos = [r[0] for r in con.execute("""SELECT DISTINCT repo FROM idx WHERE source IN ('entire', 'specstory', 'aider')
                                      AND repo IS NOT NULL AND repo <> '' ORDER BY 1""").fetchall()]
assert all(r.count("/") == 1 and " " not in r for r in repos), [r for r in repos if r.count("/") != 1][:5]
assert len(repos) > 3000, len(repos)
open(f"{args.out}/repos_all.txt", "w").write("\n".join(repos) + "\n")
b0 = sorted(repos, key=lambda r: hashlib.sha256((args.seed + r).encode()).hexdigest())[:args.batch0]
open(f"{args.out}/repos_batch0.txt", "w").write("\n".join(sorted(b0)) + "\n")
rest = sorted(set(repos) - set(b0))
open(f"{args.out}/repos_rest.txt", "w").write("\n".join(rest) + "\n")
print("repositories", len(repos), "batch0", len(b0), "rest", len(rest))
print(con.execute("SELECT source, count(*), count(DISTINCT repo), min(fetched_at), max(fetched_at) FROM idx GROUP BY 1 ORDER BY 1").fetchall())
