"""Aider: chat histories (`.aider.chat.history.md`) committed to public repositories. One row per file, kept whole: a
file holds every session started in its folder, each opening `# aider chat started at <local time>`.

Found by code search for the file name; each repository found is read from its default branch, so every such file
in it is kept, also those over code search's 384 KB limit. A file in a repository that holds no file under that
limit cannot be found (8% of files measured were over it on 2026-10-03).

Aider writes the user's input as `#### ` lines and its own notices as `> ` lines (model, version, warnings); the
rest is the model's replies (which can hold `#### ` headings too, so typed messages are not counted), with edits as SEARCH/REPLACE blocks rather than tool calls. Session times are local
times with no zone, so they are kept as written in `source_facts`, not as `started_at`.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..facts import FACTS_VERSION
from ..model import RawFile, Session
from ..util import append_jsonl
from ..repos import by_current, current, once
from . import Context
from .github_files import politely, read_repo, search_files, search_gaps, with_known

NAME = "aider"
QUERY = "filename:.aider.chat.history.md"
STARTED = re.compile(r"^# aider chat started at (.+)$", re.M)
MODEL = re.compile(r"^> (?:Main model|Model|Weak model|Editor model):\s*(\S+)", re.M)
VERSION = re.compile(r"^> Aider v(\d[\w.\-+]*)", re.M)


def wanted(path: str) -> bool:
    return path.rsplit("/", 1)[-1] == ".aider.chat.history.md"


def facts(text: str) -> tuple[dict, dict]:
    users = agents = edits = 0
    state = None  # "user", "agent" or None, for the block a line continues
    for line in text.splitlines():
        if line.startswith("#### "):
            if state != "user":
                users += 1
            state = "user"
        elif line.startswith("# aider chat started at") or line.startswith("> "):
            state = None
        elif line.strip():
            if state != "agent":
                agents += 1
            state = "agent"
            edits += line.startswith("<<<<<<< SEARCH")
    started = STARTED.findall(text)
    versions = VERSION.findall(text)
    # `####` opens the user's input, but a model's reply can hold a level-4 heading too, so the blocks are counted
    # as user entries and not as typed messages (review, 2026-10-03: 3 counted for 1 real in a hand-made file).
    return ({"n_user_entries": users, "n_user_messages": None, "n_agent_messages": agents, "n_tool_calls": None,
             "has_tool_results": None, "has_timestamps": bool(started), "started_at": None, "ended_at": None,
             "models": sorted(set(MODEL.findall(text))), "agent_version": versions[-1] if versions else None},
            {"sessions_in_file": len(started), "chat_started_local": [started[0], started[-1]] if started else [],
             "search_replace_edits": edits})


def row_facts(row: dict) -> dict:
    """A stored row's counts from its file (for counting again with a newer parser); runs in the process pool."""
    from pathlib import Path

    local = Path(row["files"][0]["local"])
    if not local.exists():
        return {"skip": "its file is gone; counted again once fetched again"}
    got, extra = facts(local.read_text(errors="replace"))
    return {**got, "extra_update": extra}


def run(ctx: Context) -> dict:
    if ctx.only:
        repos, found_urls = sorted(set(ctx.only)), {}
    elif ctx.github is None:
        ctx.log("aider: no GitHub token and no --only: nothing to read")
        return {"repos": 0, "sessions": 0, "new": 0, "changed": 0, "same": 0}
    else:
        items = search_files(ctx, NAME, QUERY)
        repos = sorted({i["repo"] for i in items})
        found_urls = {(i["repo"], i["path"]): i["url"] for i in items}
        ctx.log(f"aider: code search found {len(items):,} files in {len(repos):,} repositories")
        repos = with_known(ctx, NAME, repos)
    if ctx.limit:
        repos = repos[:ctx.limit]
    if ctx.dry_run:
        return {"repos": len(repos)}
    meta = by_current(ctx.github.repo_meta(repos)) if ctx.github else {}
    repos = once(meta, repos)   # a renamed repository answers to its old name too: read once, under its current one
    found_urls = {(current(meta, r), path): u for (r, path), u in found_urls.items()}
    stats = {"repos": len(repos), "sessions": 0, "new": 0, "changed": 0, "same": 0, "errors": 0, "beyond_search": 0,
             "search": search_gaps(ctx, NAME, QUERY) if not ctx.only else None}

    def one(repo: str) -> list[dict]:
        head, files = politely(read_repo, ctx, NAME, repo, wanted)
        rows = []
        for path, (oid, local) in files.items():
            if not local.exists():
                continue
            got, extra = facts(local.read_text(errors="replace"))
            m = meta.get(repo) or {}
            session = Session(
                source=NAME, source_id=f"{repo}:{path}", files=[RawFile.at(path, local)],
                found_url=found_urls.get((repo, path)) or f"https://github.com/{repo}/blob/{head}/{path}",
                found_commit=head, repo=repo, repo_url=m.get("repo_url") or f"https://github.com/{repo}",
                repo_license=m.get("repo_license"), repo_language=m.get("repo_language"), agent="Aider",
                file_format="markdown", facts=got,
                extra={"blob": oid, "found_by_search": (repo, path) in found_urls, "facts_version": FACTS_VERSION, **extra})
            rows.append(session.row())
        return rows

    with ThreadPoolExecutor(ctx.workers) as pool:
        futures = {pool.submit(one, r): r for r in repos}
        for fut in as_completed(futures):
            repo = futures[fut]
            try:
                rows = fut.result()
            except Exception as e:
                stats["errors"] += 1
                append_jsonl(ctx.config.state / NAME / "errors.jsonl", {"repo": repo, "error": f"{type(e).__name__}: {str(e)[-200:]}"})
                continue
            for row in rows:
                stats[ctx.ledger.upsert(row)] += 1
                stats["sessions"] += 1
                stats["beyond_search"] += not row["extra"]["found_by_search"]
    from .. import relabel

    stats["recounted"] = relabel.recount(ctx, NAME, row_facts)
    stats["relabeled"] = relabel.repos(ctx, NAME)
    return stats
