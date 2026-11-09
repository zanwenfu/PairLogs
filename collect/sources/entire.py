"""Entire: agent sessions recorded with the Entire CLI (github.com/entireio/cli) in public GitHub repositories.

Ported from errata-bench's crawler (src/errata_bench/crawl/: discover.py, entire.py, fetch.py), with five changes:
every agent and every repository, not one policy's; every part of a long transcript, not the first 50 MB; imported
sessions kept; each place that holds checkpoints fetched once, not once per repository naming it (errata-bench G-80);
and only what changed fetched again.

**Finding them.** A commit made during an Entire session carries an `Entire-Checkpoint: <id>` trailer. GitHub's
commit search finds those commits on default branches, at most 1,000 results a query, so the date range is halved
until a slice holds no more. A commit pushed to several repositories is found in each; one *owner* per commit (not a
fork, earliest created) decides which repositories are read, so a fork is read only for commits of its own.

**Where the sessions are.** Entire keeps checkpoints in one of these places (read from the CLI's source and docs):
- `refs/heads/entire/checkpoints/v1`: one branch, each checkpoint a subtree at `<id[:2]>/<id[2:]>/`, 12-hex ids;
- `refs/entire/checkpoints/<shard>/<id>`: one ref per checkpoint (the default since v0.9.0), ULID or hex ids, the
  shard being the id's last two characters; invisible to a clone, listed by `git ls-remote`;
- a separate repository named by `strategy_options.checkpoint_remote` in `.entire/settings.json`, either layout.

Each checkpoint holds the whole session so far, one folder per session (`0/`, `1/`, ...) with its `metadata.json`
and its transcript in the agent's own format (`full.jsonl`, and `full.jsonl.001`, ... past 50 MB), and the
subagents' transcripts under `tasks/<tool call id>/`. A session is read from its latest checkpoint.

**What is kept per session:** its `metadata.json`, every transcript part, and the subagent files its tool calls
name, byte for byte as Entire stored them (Entire's own secret redaction already applied). Entire's derived
`transcript.jsonl` (a compacted copy) and `prompt.txt` are not kept.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import facts
from ..repos import by_current, current, once
from ..gitio import GitError, bare_promisor, blob_ids, fetch_trees, ls_remote, prefetch, read_small, write_blobs
from ..model import RawFile, Session
from ..util import append_jsonl, read_jsonl, safe_name
from . import Context

NAME = "entire"

# -- Entire's layouts ------------------------------------------------------------------------
V1_BRANCH = "refs/heads/entire/checkpoints/v1"
REF_PREFIX = "refs/entire/checkpoints/"
_ID = r"(?:[0-9a-f]{12}|[0-9A-HJKMNP-TV-Z]{26})"
PER_CHECKPOINT_REF = re.compile(rf"^refs/entire/checkpoints/([0-9A-Za-z]{{2}})/({_ID})$")
V1_CHECKPOINT = re.compile(r"^([0-9a-f]{2})/([0-9a-f]{10})/metadata\.json$")
TRAILER = re.compile(rf"^Entire-Checkpoint:[ \t]*({_ID})[ \t]*$", re.M)
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PART = re.compile(r"^full\.jsonl(?:\.(\d{3,}))?$")  # agent.ChunkFileName: full.jsonl, full.jsonl.001, ...

# The agent's own session id, namespaced by agent, is the key that finds the same session in another source.
NAMESPACE = {"claude code": "claude-code", "codex": "codex", "opencode": "opencode", "pi": "pi",
             "oh my pi": "oh-my-pi", "gemini cli": "gemini-cli", "cursor": "cursor", "copilot cli": "copilot-cli",
             "factory droid": "factory-droid", "factoryai droid": "factory-droid", "factory ai droid": "factory-droid",
             "devin": "devin"}
JSON_DOCUMENT = {"opencode", "gemini cli"}  # agents whose transcript is one JSON document, not JSONL


# A source's own word for an agent not recorded. SWE-chat writes "unknown" where Entire records none: the two keys
# differed, and 48 sessions found in both were kept twice in version 2026-10-27 (18 in 2026-10-11).
NOT_RECORDED = {"", "unknown", "none", "null"}


def namespace(agent: str | None) -> str:
    a = (agent or "").strip().lower()
    if a in NOT_RECORDED:
        return "unknown-agent"
    return NAMESPACE.get(a) or re.sub(r"[^a-z0-9]+", "-", a).strip("-") or "unknown-agent"


def per_checkpoint_id(ref: str) -> str | None:
    m = PER_CHECKPOINT_REF.match(ref)
    return m[2] if m and m[1] == m[2][-2:] else None


@dataclass(frozen=True)
class Layout:
    v1_branch: bool
    per_checkpoint: tuple[str, ...]

    @property
    def has_checkpoints(self) -> bool:
        return self.v1_branch or bool(self.per_checkpoint)


def layout_of(refs: dict[str, str]) -> Layout:
    ids = tuple(sorted(i for i in (per_checkpoint_id(r) for r in refs) if i))
    return Layout(v1_branch=V1_BRANCH in refs, per_checkpoint=ids)


def refs_digest(refs: dict[str, str]) -> str:
    """A digest of Entire's refs alone: unchanged means no new or rewritten checkpoint since the last fetch."""
    mine = sorted((r, s) for r, s in refs.items() if r == V1_BRANCH or per_checkpoint_id(r))
    return hashlib.sha256(json.dumps(mine).encode()).hexdigest()


def trailer_ids(message: str) -> list[str]:
    seen: dict[str, None] = {}
    for cid in TRAILER.findall(message or ""):
        seen.setdefault(cid, None)
    return list(seen)


def checkpoint_remote(settings_json: str | None) -> str | None:
    """The public GitHub checkpoint repository a settings file names, as owner/name, or None."""
    if not settings_json:
        return None
    try:
        settings = json.loads(settings_json)
    except ValueError:
        return None
    value = ((settings.get("strategy_options") or {}) if isinstance(settings, dict) else {}).get("checkpoint_remote")
    if isinstance(value, dict) and str(value.get("provider", "")).lower() == "github":
        repo = str(value.get("repo") or "")
        if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            return repo
    return None


def _created(value) -> datetime:
    if isinstance(value, str) and value:
        try:
            t = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


# -- discovery ------------------------------------------------------------------------------------
QUERY = '"Entire-Checkpoint"'
CAP, PER_PAGE = 1000, 100
EPOCH = datetime(2025, 1, 1, tzinfo=timezone.utc)  # before Entire's first release; an empty month costs one query
RECHECK_DAYS = 30  # slices this recent are read again each run: commits pushed late carry earlier dates


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _commits_file(ctx: Context) -> Path:
    return ctx.config.state / NAME / "commits.jsonl"


def _search_page(ctx: Context, start: datetime, end: datetime, page: int) -> dict:
    return ctx.github.search("commits", f"{QUERY} committer-date:{_iso(start)}..{_iso(end)}", page=page,
                             per_page=PER_PAGE, sort="committer-date", order="asc")


def _read_slice(ctx: Context, start: datetime, end: datetime, recheck: bool) -> None:
    key = f"slice:{_iso(start)}..{_iso(end)}"
    seen = ctx.ledger.get_state(NAME, key)
    if seen and seen.get("complete") and not recheck and not seen.get("split"):
        return
    if seen and seen.get("split") and not recheck:
        total = seen["total"]
        first = None
    else:
        first = _search_page(ctx, start, end, 1)
        total = int(first.get("total_count") or 0)
    if total > CAP and end - start > timedelta(seconds=1):
        ctx.ledger.set_state(NAME, key, {"total": total, "split": True})
        mid = (start + (end - start) / 2).replace(microsecond=0)
        _read_slice(ctx, start, mid, recheck)
        _read_slice(ctx, mid + timedelta(seconds=1), end, recheck)
        return
    if first is None:
        first = _search_page(ctx, start, end, 1)
    items, pages, incomplete = list(first.get("items") or []), 1, bool(first.get("incomplete_results"))
    while len(items) < min(total, CAP) and pages < CAP // PER_PAGE:
        pages += 1
        more = _search_page(ctx, start, end, pages)
        incomplete |= bool(more.get("incomplete_results"))
        got = more.get("items") or []
        if not got:
            break
        items += got
    rows = []
    for item in items:
        commit = item.get("commit") or {}
        ids = trailer_ids(commit.get("message") or "")
        if not ids:
            continue  # the words in prose, not a trailer
        repo = item.get("repository") or {}
        rows.append({"repo": repo.get("full_name"), "fork": repo.get("fork"), "sha": item.get("sha"),
                     "committer_date": (commit.get("committer") or {}).get("date"), "checkpoint_ids": ids})
    for row in rows:
        append_jsonl(_commits_file(ctx), row)
    # A slice GitHub marked incomplete is read again on the next run; its count is reported meanwhile.
    ctx.ledger.set_state(NAME, key, {"total": total, "read": len(items), "with_trailer": len(rows),
                                     "incomplete": incomplete, "complete": not incomplete, "capped": total > CAP})


def discover(ctx: Context) -> dict:
    """Read every month since EPOCH, the last RECHECK_DAYS again; the commits and repositories known so far."""
    if ctx.github is None:
        ctx.log("entire: no GitHub token, so no discovery; the repositories already known are read")
    else:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        recheck_from = now - timedelta(days=RECHECK_DAYS)
        t = EPOCH
        while t < now:
            nxt = (t.replace(day=1) + timedelta(days=32)).replace(day=1)
            # The month in progress ends at its last second, not now, so its key is the same on every run (a key
            # ending now left a new incomplete-looking slice behind each time); it is read again each run anyway.
            _read_slice(ctx, t, nxt - timedelta(seconds=1), recheck=nxt - timedelta(seconds=1) >= recheck_from)
            t = nxt
    commits = {(r["repo"], r["sha"]): r for r in read_jsonl(_commits_file(ctx)) if r.get("repo") and r.get("sha")}
    if _commits_file(ctx).exists():  # slices read again append their commits again: written back once each
        tmp = _commits_file(ctx).with_suffix(".part")
        with open(tmp, "w") as fh:
            for r in commits.values():
                fh.write(json.dumps(r) + "\n")
        tmp.replace(_commits_file(ctx))
    slices = ctx.ledger.state_keys(NAME, "slice:")
    return {"commits": list(commits.values()),
            "slices_incomplete": sum(1 for v in slices.values() if isinstance(v, dict) and v.get("incomplete")),
            "slices_capped": sum(1 for v in slices.values() if isinstance(v, dict) and v.get("capped")
                                 and not v.get("split"))}


# Two more routes to repositories, beside trailer commits (2026-10-25): commit search does not index every
# repository's commits (of SWE-chat's 705 repositories, 268 were not found by trailers, 167 of them still holding
# checkpoints), so code search for Entire's settings file and a seed list (repositories other datasets name) add
# what it misses. Every repository found is probed the same way; duplicates are set aside at build time.
SETTINGS_QUERY = "filename:settings.json path:.entire"


def seed_file(ctx: Context) -> Path:
    return ctx.config.state / NAME / "seeds.txt"


def more_repos(ctx: Context) -> dict[str, set[str]]:
    """Repositories found by the settings-file search and by the seed list, by route."""
    out: dict[str, set[str]] = {"settings": set(), "seeds": set()}
    if ctx.github is not None:
        from .github_files import search_files

        out["settings"] = {r["repo"] for r in search_files(ctx, NAME, SETTINGS_QUERY)
                           if r.get("repo") and not r.get("fork")}
    f = seed_file(ctx)
    if f.exists():
        out["seeds"] = {x.strip() for x in f.read_text().splitlines()
                        if x.strip() and not x.lstrip().startswith("#") and x.count("/") == 1}
    return out


def owners(commits: list[dict], meta: dict[str, dict | None]) -> set[str]:
    """The repositories that own at least one trailer commit: per commit, its holder that is not a fork, earliest
    created, then by name."""
    def rank(repo: str) -> tuple:
        m = meta.get(repo) or {}
        return (bool(m.get("fork")), m.get("created_at") or "9999", repo)

    holders: dict[str, set[str]] = {}
    for c in commits:
        holders.setdefault(c["sha"], set()).add(c["repo"])
    return {min(repos, key=rank) for repos in holders.values()}


# -- sources of checkpoints -------------------------------------------------------------------------
def _settings(repo: str, timeout: int = 30) -> str | None:
    url = f"https://raw.githubusercontent.com/{repo}/HEAD/.entire/settings.json"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read(1 << 20).decode(errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    return None


def probe(repo: str) -> dict:
    """Where one repository's sessions are: itself, and the checkpoint repository its settings name."""
    out = {"repo": repo, "sources": {}, "error": None}
    try:
        refs = ls_remote(f"https://github.com/{repo}")
    except GitError as e:
        out["error"] = f"unreachable: {str(e)[-120:]}"
        return out
    layout = layout_of(refs)
    if layout.has_checkpoints:
        out["sources"][repo] = refs_digest(refs)
    try:
        remote = checkpoint_remote(_settings(repo))
    except Exception as e:  # a settings file that cannot be read leaves the repository's own checkpoints
        remote, out["error"] = None, f"settings: {type(e).__name__}"
    if remote and remote.lower() != repo.lower():
        try:
            rrefs = ls_remote(f"https://github.com/{remote}")
        except GitError:
            out["remote_unreachable"] = remote
        else:
            if layout_of(rrefs).has_checkpoints:
                out["sources"][remote] = refs_digest(rrefs)
    return out


# -- fetching one source -------------------------------------------------------------------------------
def _json(data: bytes | None) -> dict | None:
    if not data:
        return None
    try:
        value = json.loads(data)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


@dataclass
class Copy:
    checkpoint_id: str
    created_at: datetime
    metadata: dict
    metadata_oid: str
    parts: list[tuple[str, str]]  # (file name, blob id), in order
    tasks: dict[str, str]  # tasks/<call>/<name> -> blob id, at the checkpoint's root


def _copies(git_dir: Path, layout: Layout) -> tuple[list[Copy], int]:
    """Every session copy in a source's checkpoints, and the number of checkpoints."""
    folders = []  # (checkpoint id, {path within the checkpoint: blob id})
    if layout.v1_branch:
        ids = blob_ids(git_dir, "refs/crawl/v1")
        grouped: dict[str, dict[str, str]] = {}
        for path, oid in ids.items():
            parts = path.split("/", 2)
            if len(parts) == 3:
                grouped.setdefault(f"{parts[0]}/{parts[1]}/", {})[parts[2]] = oid
        for path in ids:
            m = V1_CHECKPOINT.match(path)
            if m:
                folders.append((m[1] + m[2], grouped.get(f"{m[1]}/{m[2]}/", {})))
    for cid in layout.per_checkpoint:
        folders.append((cid, blob_ids(git_dir, f"refs/crawl/cp/{cid[-2:]}/{cid}")))
    wanted = [o for _, files in folders for p, o in files.items()
              if p.count("/") == 1 and p.endswith("/metadata.json") and p.split("/")[0].isdigit()]
    prefetch(git_dir, wanted)
    blobs = read_small(git_dir, sorted(set(wanted)))
    copies = []
    for cid, files in folders:
        tasks = {p: o for p, o in files.items() if p.startswith("tasks/") and p.count("/") == 2}
        indexes = sorted({p.split("/")[0] for p in files if p.split("/")[0].isdigit()}, key=int)
        for n in indexes:
            meta_oid = files.get(f"{n}/metadata.json")
            meta = _json(blobs.get(meta_oid or ""))
            if meta is None:
                continue
            parts = []
            for p, o in files.items():
                head, _, name = p.partition("/")
                if head == n and "/" not in name:
                    m = PART.match(name)
                    if m:
                        parts.append((int(m[1] or 0), name, o))
            parts.sort()
            copies.append(Copy(checkpoint_id=cid, created_at=_created(meta.get("created_at")), metadata=meta,
                               metadata_oid=meta_oid, parts=[(name, o) for _, name, o in parts], tasks=tasks))
    return copies, len(folders)


def _call_ids(paths: list[Path]) -> set[str]:
    """Every tool call id in a Claude Code transcript, read a line at a time."""
    ids = set()
    for path in paths:
        with open(path, "rb") as f:
            for line in f:
                if b'"tool_use"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                content = ((entry.get("message") or {}).get("content") if isinstance(entry, dict) else None) or []
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id"):
                        ids.add(str(block["id"]))
    return ids


def fetch_source(ctx: Context, source_repo: str, meta: dict | None, found: dict[str, dict]) -> dict:
    """Fetch one place's sessions into raw/entire/<source>/<session>/; store a row for each new or changed one."""
    stats = {"source": source_repo, "sessions": 0, "new": 0, "changed": 0, "same": 0, "skipped": {}}
    refs = ls_remote(f"https://github.com/{source_repo}")
    layout = layout_of(refs)
    if not layout.has_checkpoints:
        stats["skipped"]["no_checkpoints"] = 1
        return stats
    git_dir = ctx.config.data / "tmp" / "entire-git" / f"{safe_name(source_repo)}.git"
    shutil.rmtree(git_dir, ignore_errors=True)
    try:
        bare_promisor(git_dir, f"https://github.com/{source_repo}")
        specs = ([f"+{V1_BRANCH}:refs/crawl/v1"] if layout.v1_branch else []) + \
                [f"+{REF_PREFIX}{c[-2:]}/{c}:refs/crawl/cp/{c[-2:]}/{c}" for c in layout.per_checkpoint]
        for i in range(0, len(specs), 500):
            fetch_trees(git_dir, specs[i:i + 500])
        copies, n_checkpoints = _copies(git_dir, layout)
        stats["checkpoints"] = n_checkpoints

        by_session: dict[str, list[Copy]] = {}
        for c in copies:
            sid = str(c.metadata.get("session_id") or "")
            by_session.setdefault(sid, []).append(c)
        dest_root = ctx.config.raw / NAME / safe_name(source_repo)
        pending = []
        for sid, cs in sorted(by_session.items()):
            cs.sort(key=lambda c: (c.created_at, c.checkpoint_id))
            latest = cs[-1]
            if not SAFE_ID.match(sid):
                stats["skipped"]["unsafe_session_id"] = stats["skipped"].get("unsafe_session_id", 0) + 1
                continue
            if not latest.parts:
                stats["skipped"]["no_transcript"] = stats["skipped"].get("no_transcript", 0) + 1
                continue
            source_id = f"{source_repo}:{sid}"
            oids = {"metadata.json": latest.metadata_oid, **{name: o for name, o in latest.parts}}
            old = ctx.ledger.get(NAME, source_id)
            if old and (old.get("extra") or {}).get("oids") == oids and all(Path(f["local"]).exists()
                                                                          for f in old.get("files") or []):
                stats["same"] += 1
                continue
            dest = dest_root / sid
            targets = [(latest.metadata_oid, dest / "metadata.json")] + [(o, dest / name) for name, o in latest.parts]
            prefetch(git_dir, [o for o, _ in targets])
            write_blobs(git_dir, targets)
            files = [RawFile.at("metadata.json", dest / "metadata.json")] + \
                    [RawFile.at(name, dest / name) for name, _ in latest.parts]
            # Subagent transcripts: Entire writes a task's files once, at the checkpoint after it ends, so every
            # checkpoint the session was in may hold some. Kept where the session's own tool calls name them.
            calls = _call_ids([dest / name for name, _ in latest.parts]) if (latest.metadata.get("agent") or "").lower() == "claude code" else set()
            task_targets, placed = [], set()
            for c in cs:
                for path, oid in sorted(c.tasks.items()):
                    _, call, name = path.split("/")
                    if call in calls and SAFE_ID.match(call) and SAFE_ID.match(name) and (call, name) not in placed:
                        placed.add((call, name))
                        task_targets.append((oid, dest / "subagents" / call / name))
            if task_targets:
                prefetch(git_dir, [o for o, _ in task_targets])
                write_blobs(git_dir, task_targets)
                files += [RawFile.at(f"subagents/{t.parent.name}/{t.name}", t) for _, t in task_targets]
            pending.append((sid, source_id, latest, cs, files, oids, len({c for c, _ in placed})))
    finally:
        shutil.rmtree(git_dir, ignore_errors=True)

    # Facts in the process pool: parsing a transcript is CPU work, and some run to hundreds of megabytes.
    futures = {}
    for sid, source_id, latest, cs, files, oids, n_tasks in pending:
        parts = [f.local for f in files if PART.match(f.path)]
        futures[source_id] = ctx.cpu.submit(facts.for_session, latest.metadata.get("agent"), parts)
    for sid, source_id, latest, cs, files, oids, n_tasks in pending:
        got = futures[source_id].result()
        agent = latest.metadata.get("agent")
        commit = _found_commit(found, [c.checkpoint_id for c in reversed(cs)])
        code_repo = commit["repo"] if commit else None
        session = Session(
            source=NAME, source_id=source_id, files=files,
            found_url=f"https://github.com/{code_repo}/commit/{commit['sha']}" if commit else f"https://github.com/{source_repo}",
            found_commit=commit["sha"] if commit else None,
            repo=source_repo, repo_url=(meta or {}).get("repo_url") or f"https://github.com/{source_repo}",
            repo_license=(meta or {}).get("repo_license"), repo_language=(meta or {}).get("repo_language"),
            agent=agent, file_format="json" if (agent or "").lower() in JSON_DOCUMENT else "jsonl",
            dedup_key=f"{namespace(agent)}:{sid}",
            facts={k: v for k, v in got.items() if k in facts.COUNTS},
            extra={"oids": oids, "checkpoint_id": latest.checkpoint_id, "checkpoint_ids": [c.checkpoint_id for c in cs],
                   "code_repo": code_repo, "entire_kind": latest.metadata.get("kind"),
                   "entire_cli_version": latest.metadata.get("cli_version"), "model_recorded": latest.metadata.get("model"),
                   "transcript_parts": sum(1 for f in files if PART.match(f.path)), "subagent_tasks": n_tasks,
                   "parse_error": got.get("parse_error"), "facts_version": facts.FACTS_VERSION},
        )
        # No started_at from a checkpoint's own time: a checkpoint is written at a commit, often near a session's
        # end (review, 2026-10-03). It is kept in source_facts as what it is.
        session.extra["first_checkpoint_at"] = facts._iso(cs[0].metadata.get("created_at"))
        if not session.facts.get("models") and latest.metadata.get("model"):
            session.facts["models"] = [str(latest.metadata["model"])]
        result = ctx.ledger.upsert(session.row())
        stats[result] += 1
        stats["sessions"] += 1
    return stats


def row_facts(row: dict) -> dict:
    """A stored row's counts from its files (for counting again with a newer parser); runs in the process pool."""
    parts = sorted((f for f in row.get("files") or [] if PART.match(f["path"])),
                   key=lambda f: int(PART.match(f["path"])[1] or 0))
    if not all(Path(f["local"]).exists() for f in parts):
        return {"skip": "its files are gone; counted again once fetched again"}
    got = facts.for_session(row.get("agent"), [Path(f["local"]) for f in parts])
    meta_file = next((f for f in row.get("files") or [] if f["path"] == "metadata.json"), None)
    if meta_file and not got.get("models"):
        try:
            meta = json.loads(Path(meta_file["local"]).read_bytes())
        except (ValueError, OSError):
            meta = {}
        if meta.get("model"):
            got["models"] = [str(meta["model"])]
    return got


def _found_commit(found: dict[str, dict], checkpoint_ids: list[str]) -> dict | None:
    """The trailer commit naming the session's latest checkpoint that has one."""
    for cid in checkpoint_ids:
        if cid in found:
            return found[cid]
    return None


# -- the run -------------------------------------------------------------------------------------------
def run(ctx: Context) -> dict:
    t0 = time.time()
    disc = discover(ctx)
    commits = disc["commits"]
    holders = sorted({c["repo"] for c in commits})
    meta = ctx.github.repo_meta(holders) if ctx.github else {}
    repos = once(meta, owners(commits, meta)) if commits else []
    if not ctx.only:   # a named sample reads only what it names
        extra = more_repos(ctx)
        named = extra["settings"] | extra["seeds"]
        if ctx.github:
            meta.update(ctx.github.repo_meta(sorted(named - set(meta))))
        settings, seeds = set(once(meta, extra["settings"])), set(once(meta, extra["seeds"]))
        old_names = sum(1 for r in named if current(meta, r) != r)
        ctx.log(f"entire: {len(repos):,} repositories by trailer commits; the settings-file search adds "
                f"{len(settings - set(repos)):,}, the seed list {len(seeds - set(repos) - settings):,} "
                f"({old_names:,} of their names a repository's old one, read under its current name)")
        repos = sorted(set(repos) | settings | seeds)
    if ctx.only:
        repos = [r for r in repos if r in ctx.only] or list(ctx.only)
    if ctx.limit:
        repos = repos[:ctx.limit]
    found: dict[str, dict] = {}
    for c in sorted(commits, key=lambda c: c.get("committer_date") or ""):
        for cid in c.get("checkpoint_ids") or []:
            found.setdefault(cid, {"repo": c["repo"], "sha": c["sha"]})
    ctx.log(f"entire: {len(commits):,} trailer commits in {len(holders):,} repositories; {len(repos):,} to probe")

    with ThreadPoolExecutor(ctx.workers) as pool:
        probes = list(pool.map(probe, repos))
    sources: dict[str, str] = {}
    for p in probes:
        sources.update(p["sources"])
    unreachable = sum(1 for p in probes if p["error"] and p["error"].startswith("unreachable"))
    # A checkpoint repository a settings file names can be an old name too: each place once, under its current name.
    if ctx.github:
        meta.update(ctx.github.repo_meta([s for s in sources if s not in meta]))
    meta = by_current(meta)
    named_sources, sources = sources, {}
    for s, digest in sorted(named_sources.items()):
        sources.setdefault(current(meta, s), digest)
    todo = [s for s, digest in sorted(sources.items())
            if ctx.ledger.get_state(NAME, f"refs:{s}") != digest]
    ctx.log(f"entire: {len(sources):,} places hold checkpoints ({unreachable} repositories unreachable); "
            f"{len(todo):,} new or changed since the last run")
    if ctx.dry_run:
        return {"commits": len(commits), "repos": len(repos), "sources": len(sources), "to_fetch": len(todo),
                "unreachable": unreachable, **{k: v for k, v in disc.items() if k != "commits"}}

    totals = {"sessions": 0, "new": 0, "changed": 0, "same": 0, "skipped": {}, "errors": 0}
    with ThreadPoolExecutor(max(1, ctx.workers // 2)) as pool:
        futures = {pool.submit(fetch_source, ctx, s, meta.get(s), found): s for s in todo}
        for i, fut in enumerate(as_completed(futures), 1):
            s = futures[fut]
            try:
                st = fut.result()
            except Exception as e:  # recorded and tried again next run; never silently dropped
                totals["errors"] += 1
                append_jsonl(ctx.config.state / NAME / "errors.jsonl", {"source": s, "error": f"{type(e).__name__}: {str(e)[-300:]}"})
                ctx.log(f"entire: [{i}/{len(todo)}] {s}: ERROR {type(e).__name__}: {str(e)[-120:]}")
                continue
            ctx.ledger.set_state(NAME, f"refs:{s}", sources[s])
            for k in ("sessions", "new", "changed", "same"):
                totals[k] += st.get(k, 0)
            for k, v in st["skipped"].items():
                totals["skipped"][k] = totals["skipped"].get(k, 0) + v
            if i % 50 == 0 or i == len(todo):
                ctx.log(f"entire: [{i}/{len(todo)}] fetched; sessions so far {totals['sessions']:,} "
                        f"(new {totals['new']:,}, changed {totals['changed']:,})")
    from .. import relabel

    totals["recounted"] = relabel.recount(ctx, NAME, row_facts)
    totals["relabeled"] = relabel.repos(ctx, NAME)
    return {"commits": len(commits), "repos": len(repos), "sources": len(sources), "fetched": len(todo),
            "unreachable": unreachable, "seconds": round(time.time() - t0),
            **{k: v for k, v in disc.items() if k != "commits"}, **totals}
