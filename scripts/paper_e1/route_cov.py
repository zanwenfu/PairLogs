"""Read-only: Entire discovery coverage by route on a version. Each repository with kept Entire sessions is put under
the routes that found it: trailer commits (state/entire/commits.jsonl, any holder), the seed list (SWE-chat's
repositories, state/entire/seeds.txt), else the settings-file code search (the only other route). And a two-sample
(Chapman) estimate of how many public repositories hold Entire checkpoints: our trailer search and SWE-chat's own
list as two captures of the repositories that hold checkpoints now (SWE-chat's listed ones that do = those we probed
from the seed list and kept sessions of). Names compared without case; a repository renamed since a route saw it can
be missed (counted under settings). argv: VERSION_DIR STATE_DIR SWECHAT_REPOSITORIES.parquet OUT.json"""
import collections
import json
import math
import sys

import duckdb

V, S, SC, OUT = sys.argv[1:5]
trailer = set()
for line in open(f"{S}/entire/commits.jsonl"):
    trailer.add(json.loads(line)["repo"].lower())
seeds = {x.strip().lower() for x in open(f"{S}/entire/seeds.txt") if x.strip() and not x.startswith("#") and x.count("/") == 1}
con = duckdb.connect()
kept = dict(con.execute(f"""select lower(repo), count(*) from read_parquet('{V}/data/entire/*.parquet', union_by_name=true)
                            where duplicate_of is null and repo is not null group by 1""").fetchall())
swechat = {r[0].lower() for r in con.execute(f"""select coalesce(regexp_extract(url, 'github.com/([^/]+/[^/?#]+)', 1), name)
                                                 from read_parquet('{SC}')""").fetchall() if r[0]}
route = collections.Counter()
sess = collections.Counter()
for r, n in kept.items():
    k = ("trailer" if r in trailer else "") + ("+seed" if r in seeds else "")
    k = k.lstrip("+") or "settings search only"
    route[k] += 1
    sess[k] += n
A = {r for r in kept if r in trailer}
B = {r for r in kept if r in swechat}
m = len(A & B)
n_hat = (len(A) + 1) * (len(B) + 1) / (m + 1) - 1
var = (len(A) + 1) * (len(B) + 1) * (len(A) - m) * (len(B) - m) / ((m + 1) ** 2 * (m + 2))
out = {"repos_with_kept_sessions": len(kept), "sessions": sum(kept.values()),
       "by_route_repos": dict(route), "by_route_sessions": dict(sess),
       "trailer_set": len(trailer), "seed_list": len(seeds), "swechat_listed": len(swechat),
       "capture": {"ours_by_trailer": len(A), "swechat_listed_and_holding": len(B), "both": m,
                   "chapman_estimate": round(n_hat), "ci95": [round(n_hat - 1.96 * math.sqrt(var)),
                                                              round(n_hat + 1.96 * math.sqrt(var))]}}
json.dump(out, open(OUT, "w"), indent=1)
print(json.dumps(out, indent=1))
