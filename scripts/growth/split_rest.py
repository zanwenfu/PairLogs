"""Split the growth test's other repositories into K lists of about equal size (the version's session bytes per
repository, largest first onto the lightest list), so no one job holds several of the largest repositories.

python split_rest.py BUILD REST.txt K OUT_PREFIX
"""
import sys

import duckdb

build, rest, k, prefix = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
repos = [x.strip() for x in open(rest) if x.strip()]
con = duckdb.connect()
size = dict(con.execute(f"""SELECT repo, sum(coalesce(bytes, 0)) FROM read_parquet('{build}/data/*/*.parquet', union_by_name=true)
                            WHERE source IN ('entire', 'specstory', 'aider') GROUP BY 1""").fetchall())
lists = [[] for _ in range(k)]
load = [0] * k
for r in sorted(repos, key=lambda r: -(size.get(r) or 0)):
    i = load.index(min(load))
    lists[i].append(r)
    load[i] += size.get(r) or 0
assert sorted(sum(lists, [])) == sorted(repos)
for i, l in enumerate(lists, 1):
    open(f"{prefix}{i}.txt", "w").write("\n".join(sorted(l)) + "\n")
    print(f"list {i}: {len(l)} repositories, {load[i - 1] / 1e9:.1f} GB of session files; largest {max(size.get(r) or 0 for r in l) / 1e9:.2f} GB")
