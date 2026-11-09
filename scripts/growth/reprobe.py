"""After the growth test's fetch: every listed repository with a session of the version that did not come back is
asked once more, one at a time and slowly (`git ls-remote`, anonymous), whether GitHub answers for it. A refusal
(HTTP 403/429) or a timeout goes to ERRORS (left out of the counts: it says nothing about the repository), a missing
repository to GONE, an answer to OK (the repository is there; its sessions did not come back).

python reprobe.py VERSION_INDEX.parquet REPOS.txt LEDGER.sqlite ERRORS.txt GONE.txt [--pause 1.0]
"""
import json
import os
import sqlite3
import subprocess
import sys
import time

import duckdb

IDX, REPOS, LEDGER, ERRORS, GONE = sys.argv[1:6]
PAUSE = float(sys.argv[sys.argv.index("--pause") + 1]) if "--pause" in sys.argv else 1.0
repos = {x.strip() for x in open(REPOS) if x.strip()}
back = {(s, i) for s, i in sqlite3.connect(f"file:{LEDGER}?mode=ro", uri=True).execute("SELECT source, source_id FROM items")}
con = duckdb.connect()
rows = con.execute(f"""SELECT source, source_id, repo FROM read_parquet('{IDX}')
                       WHERE source IN ('entire', 'specstory', 'aider')""").fetchall()
todo = sorted({r for s, i, r in rows if r in repos and (s, i) not in back})
env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "/bin/echo"}
err, gone, ok = [], [], []
for n, r in enumerate(todo, 1):
    try:
        p = subprocess.run(["git", "-c", "credential.helper=", "ls-remote", "--heads", f"https://github.com/{r}"],
                           capture_output=True, text=True, timeout=60, env=env)
        e = p.stderr.lower()
        if p.returncode == 0:
            ok.append(r)
        elif "403" in e or "429" in e or "rate" in e:
            err.append(r)
        elif "not found" in e or "404" in e or "could not read username" in e or "authentication" in e:
            gone.append(r)   # anonymous git asks for a login for a repository that is missing or private
        else:
            err.append(r)
    except subprocess.TimeoutExpired:
        err.append(r)
    if len(err) > 25 and len(err) > n // 2:
        sys.exit(f"stopped after {n}: GitHub refuses ({len(err)} errors); nothing written")
    time.sleep(PAUSE)
open(ERRORS, "w").write("".join(x + "\n" for x in err))
open(GONE, "w").write("".join(x + "\n" for x in gone))
print(json.dumps({"asked": len(todo), "answered": len(ok), "gone_or_private": len(gone), "refused_or_timeout": len(err)}))
