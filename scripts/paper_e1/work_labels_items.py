"""Read-only, a version: what the work-label run reads of every kept session with a typed message, as the pilot read
its sample (work_labels_sample.py): the session's key, source, agent and repository, and its first typed messages in
order, up to ten, 600 characters each, adding none once 4,000 are taken. A copy's typed rows are its kept session's.
Also the repositories those sessions name (owner/name), one per line, for work_labels_repos.py. Writes real text:
keep the output where the data is.

python work_labels_items.py VERSION_DIR ITEMS.jsonl REPOS.txt TMP_DIR
"""
import json
import os
import sys

import duckdb


def main() -> None:
    V, out, repos_out, tmp = sys.argv[1:5]
    con = duckdb.connect()
    con.execute(f"SET threads=16; SET memory_limit='60GB'; SET temp_directory='{tmp}'")
    con.execute(f"""create temp table k as select session_key, source, agent, repo from read_parquet('{V}/data/*/*.parquet',
      union_by_name=true) where duplicate_of is null""")
    con.execute(f"""create temp table t as select session_key, turn_number, content
      from read_parquet('{V}/conversations/*/*.parquet', union_by_name=true)
      where typed_by_developer and copy_of is null and content is not null and length(content) > 0
        and session_key in (select session_key from k)""")
    meta = {r[0]: r[1:] for r in con.execute("select session_key, source, agent, repo from k").fetchall()}
    typed: dict[str, list[str]] = {}
    for key, content in con.execute("select session_key, content from t order by session_key, turn_number").fetchall():
        lst = typed.setdefault(key, [])
        if len(lst) < 10 and sum(len(x) for x in lst) < 4000:
            lst.append(content[:600])
    keys = sorted(typed)
    old = os.umask(0o077)
    with open(out, "w", encoding="utf-8") as f:
        for i, key in enumerate(keys):
            src, agent, repo = meta[key]
            f.write(json.dumps({"item": f"s{i:05d}", "session_key": key, "source": src, "agent": agent, "repo": repo,
                                "typed": typed[key]}, ensure_ascii=False) + "\n")
    os.umask(old)
    named = {}   # GitHub's names ignore case: one spelling per repository
    for k in keys:
        if meta[k][2] and "/" in meta[k][2]:
            named.setdefault(meta[k][2].lower(), meta[k][2])
    repos = sorted(named.values())
    with open(repos_out, "w", encoding="utf-8") as f:
        f.write("".join(r + "\n" for r in repos))
    by = {}
    for k in keys:
        by[meta[k][0]] = by.get(meta[k][0], 0) + 1
    print(json.dumps({"sessions": len(keys), "by_source": by, "repos": len(repos),
                      "no_repo": sum(1 for k in keys if not (meta[k][2] and "/" in meta[k][2])),
                      "chars": sum(sum(len(x) for x in typed[k]) for k in keys)}, indent=1))


if __name__ == "__main__":
    main()
