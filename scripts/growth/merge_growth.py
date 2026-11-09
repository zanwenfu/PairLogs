"""The growth test's two parts (batch 0, the rest) summed into one record for the paper's numbers.

python merge_growth.py BATCH0_COMPARE.json REST_COMPARE.json REST_GONE.txt FETCH_SECONDS OUT.json

FETCH_SECONDS: the two jobs' fetch times, comma-separated (from their logs), both on 8 cores.
"""
import json
import sys

b0, rest = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
gone_repos = [x for x in open(sys.argv[3]).read().split() if x]
secs = [int(x) for x in sys.argv[4].split(",")]
KEYS = ["fresh", "same", "changed", "new", "new_not_a_copy", "new_with_user_entry", "new_started_after_last_fetch", "new_start_unknown",
        "new_repositories", "version_sessions_in_repos_read", "gone_repo_gave_nothing", "gone_from_a_repo_read"]
out = {"repositories_listed": b0.get("repositories_listed", b0["repositories_read"]) + rest["repositories_listed"],
       "repositories_with_errors": b0.get("repositories_with_errors", 0) + rest["repositories_with_errors"],
       "repositories_read": b0["repositories_read"] + rest["repositories_read"],
       "repositories_with_no_session_now": b0["repos_with_no_session_now"] + rest["repos_with_no_session_now"],
       "repositories_gone_or_private": len(gone_repos),
       "version_last_fetch": rest["version_last_fetch"], "fetch_seconds": secs, "fetch_hours": round(sum(secs) / 3600, 1),
       "by_source": {}, "all": {}}
assert b0["version_last_fetch"] == rest["version_last_fetch"]
for src in ("entire", "specstory", "aider"):
    out["by_source"][src] = {k: b0["by_source"][src][k] + rest["by_source"][src][k] for k in KEYS}
for k in KEYS:
    out["all"][k] = sum(out["by_source"][s][k] for s in out["by_source"])
a = out["all"]
assert a["same"] + a["changed"] + a["new"] == a["fresh"]
out["all"]["same_pct_of_version_sessions_read"] = round(100 * a["same"] / a["version_sessions_in_repos_read"], 1)
json.dump(out, open(sys.argv[5], "w"), indent=1)
print(json.dumps(out, indent=1))
