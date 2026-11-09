"""Labels brought up to date without fetching again.

- **Counts**: a row counted by an older parser (`facts.FACTS_VERSION`) is counted again from its stored files. A
  row fetched once and unchanged since would otherwise keep a wrong count forever.
- **Repository labels**: licence and language are looked up again for every row's repository on each run with a
  token: a repository can add or change its licence, and rows fetched without a token have none.

Either change makes the run's data new, so the next build includes it.
"""

from __future__ import annotations

from . import facts, repos as repo_names
from .sources import Context


def recount(ctx: Context, source: str, compute) -> int:
    """Count again every row of `source` counted by an older parser; `compute(row)` runs in the process pool."""
    stale = [r for r in ctx.ledger.rows(source)
             if (r.get("extra") or {}).get("facts_version") != facts.FACTS_VERSION]
    if not stale:
        return 0
    done = 0
    for i in range(0, len(stale), 256):  # a bounded number in flight: each holds a transcript in memory
        chunk = stale[i:i + 256]
        for row, got in zip(chunk, ctx.cpu.map(compute, chunk)):
            done += _apply(ctx, source, row, got)
    ctx.log(f"{source}: {done:,} rows counted again with parser version {facts.FACTS_VERSION}")
    return done


def _apply(ctx: Context, source: str, row: dict, got: dict) -> int:
    """Store a row's new counts; a row whose files are gone (`skip`) keeps its old ones until it is fetched again."""
    if got.get("skip"):
        return 0
    labels = {k: got.get(k) for k in facts.COUNTS if k != "agent_version"}
    if got.get("agent_version"):
        labels["agent_version"] = got["agent_version"]
    extra = {**(row.get("extra") or {}), **(got.get("extra_update") or {}), "facts_version": facts.FACTS_VERSION,
             "parse_error": got.get("parse_error")}
    ctx.ledger.update_labels(source, row["source_id"], {**labels, "extra": extra})
    return 1


def repos(ctx: Context, source: str) -> int:
    """Refresh every row's licence and language from GitHub; the rows whose labels changed."""
    if ctx.github is None:
        return 0
    rows = list(ctx.ledger.rows(source))
    meta = ctx.github.repo_meta(sorted({r["repo"] for r in rows if r.get("repo")}))
    repo_names.remember(ctx.ledger, meta)   # what de-duplication reads: current name, fork, age (repos.rank)
    changed = 0
    for r in rows:
        m = meta.get(r.get("repo"))
        if not m:
            continue
        want = {"repo_license": m["repo_license"], "repo_language": m["repo_language"], "repo_url": m["repo_url"]}
        if any(r.get(k) != v for k, v in want.items()):
            ctx.ledger.update_labels(source, r["source_id"], want)
            changed += 1
    if changed:
        ctx.log(f"{source}: repository labels updated on {changed:,} rows")
    return changed
