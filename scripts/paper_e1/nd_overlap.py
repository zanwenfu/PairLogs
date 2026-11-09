"""Read-only: overlap of a version's sessions with the Notre Dame studies' (their package's data/core/
session_mapping.json). SpecStory: their GitHub repository ids, resolved to current names beforehand (REPO_NAMES.tsv),
against our SpecStory repositories; and by session, their 40-character sha against each kept session's found commit
and its file's git blob sha (from the file's bytes as kept: a masked file's differs). SWE-chat: their ids are
SWE-chat's own repository ids, read in SWE-chat's repositories table. argv: VERSION_DIR MAPPING.json REPO_NAMES.tsv
SWECHAT_REPOSITORIES.parquet OUT.json"""
import hashlib
import json
import sys

import duckdb

V, M, N, SCR, OUT = sys.argv[1:6]
mapping = json.load(open(M))
names = dict(l.rstrip("\n").split("\t") for l in open(N))
con = duckdb.connect()
sc_names = {str(i): (n or "").lower() for i, n in con.execute(
    f"select repo_id, regexp_extract(url, 'github.com/([^/]+/[^/?#]+)', 1) from read_parquet('{SCR}')").fetchall()}
nd_spec_repos = {names[str(x["repository_id"])].lower() for x in mapping
                 if x["source"] == "specstory" and names.get(str(x["repository_id"]), "GONE") != "GONE"}
nd_swe_repos = {sc_names.get(str(x["repository_id"]), "") for x in mapping if x["source"] == "swe"} - {""}
nd_sha = {x["session_sha"] for x in mapping if x["source"] == "specstory"}
our_repos = {}
for src, repo in con.execute(f"""select distinct source, lower(repo) from read_parquet('{V}/data/*/*.parquet', union_by_name=true)
                                 where duplicate_of is null and source in ('swe-chat-v2', 'entire') and repo is not null""").fetchall():
    our_repos.setdefault(src, set()).add(repo)
by_commit = by_blob = n_spec = in_nd_repo = 0
cur = con.execute(f"""select lower(repo), found_commit, files from read_parquet('{V}/data/specstory/*.parquet')
                      where duplicate_of is null""")   # one batch at a time: the files' bytes are large
while rows := cur.fetchmany(200):
    for repo, commit, files in rows:
        our_repos.setdefault("specstory", set()).add(repo)
        n_spec += 1
        in_nd_repo += repo in nd_spec_repos
        if commit in nd_sha:
            by_commit += 1
        for f in files or []:
            b = (f.get("content") or "").encode("utf-8", "surrogatepass")
            if hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest() in nd_sha:
                by_blob += 1
                break
spec = our_repos.get("specstory", set())
out = {"nd_sessions": len(mapping), "nd_specstory_sessions": len(nd_sha),
       "nd_specstory_repos_named": len(nd_spec_repos), "nd_swe_repos_named": len(nd_swe_repos),
       "our_specstory_sessions": n_spec, "our_specstory_repos": len(spec),
       "specstory_repos_both": len(nd_spec_repos & spec), "nd_specstory_repos_not_ours": len(nd_spec_repos - spec),
       "our_specstory_repos_not_nd": len(spec - nd_spec_repos),
       "our_specstory_sessions_in_a_nd_repo": in_nd_repo,
       "sessions_matched_by_commit": by_commit, "sessions_matched_by_blob": by_blob,
       "nd_swe_repos_in_our_entire_or_swechat": len(nd_swe_repos & (our_repos.get("entire", set()) | our_repos.get("swe-chat-v2", set())))}
json.dump(out, open(OUT, "w"), indent=1)
print(json.dumps(out, indent=1))
