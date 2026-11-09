"""What SpecStory and Aider share: finding files with GitHub's code search, and reading repositories with git.

Code search returns at most 1,000 results a query and indexes only files under 384 KB on default branches, so a
query is split by file size until each slice holds no more than the cap (bisecting the size range), and every
repository it finds is then read whole with git: files over 384 KB, which search cannot see, are read too.
"""

from __future__ import annotations

import hashlib
import shutil
import time
from pathlib import Path

from ..gitio import GitError, bare_promisor, blob_ids, fetch_trees, ls_remote, prefetch, write_blobs
from ..util import safe_name
from . import Context

CAP, PER_PAGE = 1000, 100
MAX_SIZE = 384_000  # code search indexes files under 384 KB
NAME_MAX = 255      # bytes in one file or folder name, on Linux and macOS disks


def fits(path: str) -> str:
    """A repository path as a local one each part of which fits NAME_MAX: git allows any length, and a name made on
    Windows can reach 765 bytes (255 characters of 3 bytes). A longer part is cut, given a hash of the whole name so
    two never meet, and keeps its extension. The session keeps the repository's path; only the local file differs."""
    out = []
    for part in path.split("/"):
        raw = part.encode()
        if len(raw) > NAME_MAX:
            stem, dot, ext = part.rpartition(".")
            ext = dot + ext if stem and len(ext) <= 16 else ""
            tag = "~" + hashlib.sha256(raw).hexdigest()[:16]
            room = NAME_MAX - len(ext.encode()) - len(tag)
            part = (stem if ext else part).encode()[:room].decode(errors="ignore") + tag + ext
        out.append(part)
    return "/".join(out)


def search_files(ctx: Context, source: str, query: str) -> list[dict]:
    """Every (repository, path) code search finds for `query`, by size slices under the cap; cached per slice."""
    found: dict[tuple[str, str], dict] = {}

    def read(lo: int, hi: int) -> None:
        key = f"slice:{query}:{lo}..{hi}"
        seen = ctx.ledger.get_state(source, key)
        # Code search has no date to read only what is new, so every refresh reads every slice again; a slice read
        # in the last day is reused, so a stopped run resumes where it stopped.
        fresh = seen and time.time() - float(seen.get("read_at") or 0) < 86400
        if fresh and seen.get("complete") and not seen.get("split"):
            for item in seen.get("items") or []:
                found[(item["repo"], item["path"])] = item
            return
        first = ctx.github.search("code", f"{query} size:{lo}..{hi}", page=1, per_page=PER_PAGE)
        total = int(first.get("total_count") or 0)
        if total > CAP and hi > lo:
            ctx.ledger.set_state(source, key, {"total": total, "split": True, "read_at": time.time()})
            mid = (lo + hi) // 2
            read(lo, mid)
            read(mid + 1, hi)
            return
        items, pages, incomplete = list(first.get("items") or []), 1, bool(first.get("incomplete_results"))
        while len(items) < min(total, CAP) and pages < CAP // PER_PAGE:
            pages += 1
            more = ctx.github.search("code", f"{query} size:{lo}..{hi}", page=pages, per_page=PER_PAGE)
            incomplete |= bool(more.get("incomplete_results"))
            got = more.get("items") or []
            if not got:
                break
            items += got
        rows = [{"repo": (i.get("repository") or {}).get("full_name"), "path": i.get("path"),
                 "url": i.get("html_url"), "fork": (i.get("repository") or {}).get("fork")} for i in items]
        rows = [r for r in rows if r["repo"] and r["path"]]
        for r in rows:
            found[(r["repo"], r["path"])] = r
        ctx.ledger.set_state(source, key, {"total": total, "read": len(items), "incomplete": incomplete,
                                           "complete": not incomplete and total <= CAP, "capped": total > CAP,
                                           "items": rows, "read_at": time.time()})

    read(0, MAX_SIZE)
    return sorted(found.values(), key=lambda r: (r["repo"], r["path"]))


def seed_file(ctx: Context, source: str) -> Path:
    return ctx.config.state / source / "seeds.txt"


def known_repos(ctx: Context, source: str) -> dict[str, set[str]]:
    """Repositories read beside today's code search, by route: every one a past run of this ledger read, and a seed
    list (state/<source>/seeds.txt, one a line: a past version's repositories, for a fresh ledger). Code search does
    not return the same repositories each time: a fresh run's missed 30 of the 1,407 SpecStory repositories a version
    two weeks older held, still public with their history at head (2026-10-25). One deleted since fails as an error;
    one emptied since reads nothing."""
    out = {"ledger": {sid.split(":", 1)[0] for sid in ctx.ledger.source_ids(source)}, "seeds": set()}
    f = seed_file(ctx, source)
    if f.exists():
        out["seeds"] = {x.strip() for x in f.read_text().splitlines()
                        if x.strip() and not x.lstrip().startswith("#") and x.strip().count("/") == 1}
    return out


def with_known(ctx: Context, source: str, repos: list[str]) -> list[str]:
    """`repos` (code search's) and the known ones, logged by route."""
    known = known_repos(ctx, source)
    found = set(repos)
    ledger, seeds = known["ledger"] - found, known["seeds"] - found - known["ledger"]
    ctx.log(f"{source}: beside the search, {len(ledger):,} repositories a past run read and {len(seeds):,} from the "
            f"seed list")
    return sorted(found | ledger | seeds)


def search_gaps(ctx: Context, source: str, query: str) -> dict:
    """Slices code search could not read whole: more than 1,000 files of one size (capped), or results GitHub
    marked incomplete. Reported in the run's stats and the card's known gaps."""
    slices = ctx.ledger.state_keys(source, f"slice:{query}:")
    leaf = [v for v in slices.values() if isinstance(v, dict) and not v.get("split")]
    return {"slices": len(leaf), "capped": sum(1 for v in leaf if v.get("capped")),
            "incomplete": sum(1 for v in leaf if v.get("incomplete")),
            "missed_by_cap": sum(max(0, (v.get("total") or 0) - (v.get("read") or 0)) for v in leaf if v.get("capped"))}


def read_repo(ctx: Context, source: str, repo: str, wanted) -> tuple[str, dict[str, tuple[str, Path]]]:
    """A repository's default branch, read anonymously: its head commit, and every file `wanted(path)` accepts,
    written under raw/<source>/<repo>/ (path -> (blob id, local file)). A file whose blob id is unchanged since the
    last run is not fetched again."""
    url = f"https://github.com/{repo}"
    refs = ls_remote(url)
    head = refs.get("HEAD")
    if not head:
        raise GitError(f"{repo}: no HEAD")
    git_dir = ctx.config.data / "tmp" / f"{source}-git" / f"{safe_name(repo)}.git"
    shutil.rmtree(git_dir, ignore_errors=True)
    dest = ctx.config.raw / source / safe_name(repo)
    out: dict[str, tuple[str, Path]] = {}
    try:
        bare_promisor(git_dir, url)
        fetch_trees(git_dir, ["+HEAD:refs/crawl/head"])
        blobs = {p: o for p, o in blob_ids(git_dir, "refs/crawl/head").items() if wanted(p)}
        todo = []
        for path, oid in sorted(blobs.items()):
            local = dest / fits(path)
            out[path] = (oid, local)
            old = ctx.ledger.get(source, f"{repo}:{path}")
            if not (old and (old.get("extra") or {}).get("blob") == oid and local.exists()):
                todo.append((oid, local))
        if todo:
            prefetch(git_dir, [o for o, _ in todo])
            write_blobs(git_dir, todo)
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)
    return head, out


def politely(fn, *args, tries: int = 3, **kw):
    for attempt in range(tries):
        try:
            return fn(*args, **kw)
        except GitError:
            if attempt == tries - 1:
                raise
            time.sleep(5 * (attempt + 1))
