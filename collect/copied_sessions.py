"""Session rows for the copied datasets, built at build time from their downloaded files (the agreed schema): SWE-chat
v2 one per transcript, DataClaw one per `conversations.jsonl` record and one per raw Claude Code transcript its
datasets hold, OpenHands feedback one per trajectory. They join the collection's dedup (dedup.assign) and its
conversations tables like the scraped sessions. Their files stay where the copy publishes them (`copied/...`); the
session row names them and holds no file content of its own.

Each row carries, beside its labels, what the build needs and does not publish: `_read` (the files to read, under the
names the readers know, `full.jsonl` for an agent's own transcript), `_reader` (a reader other than the agent's),
`_session` (the agent's session id, SWE-chat's checkpoint and strategy) and `_published` (where its files are
published).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq

from . import facts
from .github import licence_label
from .hfcopy_build import _as_plain, entries
from .sources.entire import namespace
from .model import canonical_agent
from .util import safe_name

AGENT_OF = {"claude": "Claude Code", "claude-code": "Claude Code", "claude_code": "Claude Code", "codex": "Codex",
            "gemini": "Gemini CLI", "opencode": "OpenCode", "cursor": "Cursor", "openhands": "OpenHands"}


def _root(entry: dict) -> Path:
    f = entry["files"][0]
    return Path(f["local"][: len(f["local"]) - len(f["path"])])


def _row(name: str, entry: dict, source_id: str, **kw) -> dict:
    row = {"source": name, "source_id": source_id, "dedup_key": None, "found_url": entry.get("url"),
           "found_commit": entry.get("revision"), "fetched_at": None, "collector_version": None, "content_sha256": None,
           "repo": None, "repo_url": None, "repo_license": None, "repo_language": None, "agent": None,
           "agent_version": None, "models": [], "started_at": None, "ended_at": None, "file_format": "jsonl",
           "n_user_entries": None, "n_user_messages": None, "n_agent_messages": None, "n_tool_calls": None,
           "has_tool_results": None, "has_timestamps": None, "bytes": 0,
           "duplicate_of": None, "also_in": [], "redactions": None, "files": [],
           "extra": {"dataset": entry["id"], "revision": entry.get("revision")}, "_read": [], "_reader": None,
           "_session": {}, "_published": []}
    row.update(kw)
    row["agent"] = canonical_agent(row.get("agent"))
    # The dataset's own licence is not a repository's (repo_license held it before 2026-10-05: odc-by, mit)
    row["extra"].setdefault("dataset_license", entry.get("license"))
    return row


def _published(name: str, entry: dict, rel: str) -> str:
    return f"copied/{name}/{safe_name(entry['id'])}/{rel}"


def _when(value, unit: str | None) -> str | None:
    """A stored time as UTC ISO 8601: an integer in its column's unit, or a string as written."""
    if value is None:
        return None
    if isinstance(value, int) and unit in ("s", "ms", "us", "ns"):
        from datetime import datetime, timezone

        per = {"s": 1, "ms": 1_000, "us": 1_000_000, "ns": 1_000_000_000}[unit]
        return datetime.fromtimestamp(value // per, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _facts_job(args: tuple) -> dict:
    agent, parts = args
    return facts.for_session(agent, [Path(p) for p in parts])


def rows(ledger, pool=None) -> dict[str, list[dict]]:
    """Every copied dataset's sessions, by source."""
    out: dict[str, list[dict]] = defaultdict(list)
    for name, entry in entries(ledger):
        if not entry.get("copied") or not entry.get("files"):
            continue
        root = _root(entry)
        if name == "swe-chat-v2":
            out[name] += _swechat(name, entry, root, pool)
        elif name == "dataclaw":
            out[name] += _dataclaw(name, entry, root)
        elif name == "openhands-feedback":
            out[name] += _openhands(name, entry, root)
    return dict(out)


# -- SWE-chat v2: one session per transcript, read by its agent's own reader --------------------------------------
def _swechat_repos(root: Path) -> dict[str, dict]:
    """Each repository's licence and language as SWE-chat recorded them (the GitHub metadata in its repositories
    table: 663 of 707 with a language), labelled as the collector labels a repository it looks up (licence_label);
    and SWE-chat's own reading of the licence (its `license_type`), which differs from GitHub's for 1,363 of its
    4,553 sessions here (version 2026-10-04: GitHub cannot identify an MPL-2.0 file SWE-chat names, and the like)."""
    if not (root / "repositories.parquet").exists():
        return {}
    out = {}
    for r in pq.read_table(root / "repositories.parquet", columns=["repo_id", "repo_github_metadata", "license_type"]).to_pylist():
        try:
            meta = json.loads(r["repo_github_metadata"]) if isinstance(r["repo_github_metadata"], str) else None
        except ValueError:
            meta = None
        if r["repo_id"] and isinstance(meta, dict):
            lic = meta.get("license") if isinstance(meta.get("license"), dict) else None
            out[r["repo_id"]] = {"repo_license": licence_label({"spdxId": lic.get("spdx_id")} if lic else None),
                                 "repo_language": meta.get("language") if isinstance(meta.get("language"), str) else None,
                                 "license_type": r["license_type"]}
    return out



def _swechat(name: str, entry: dict, root: Path, pool) -> list[dict]:
    if not (root / "sessions.parquet").exists():
        return []
    cols = ["session_id", "agent", "repo_id", "canonical_checkpoint_pk", "strategy", "transcript_path", "created_at"]
    table = pq.read_table(root / "sessions.parquet", columns=cols)
    # Times as the integers they are stored as: a nanosecond time does not read into Python (pyarrow without pandas).
    table = table.set_column(cols.index("created_at"), "created_at", _as_plain(table.column("created_at")))
    unit = getattr(pq.read_schema(root / "sessions.parquet").field("created_at").type, "unit", None)
    sessions = table.to_pylist()
    repos = _swechat_repos(root)
    subs = defaultdict(list)
    if (root / "subagent_tasks.parquet").exists():
        for t in pq.read_table(root / "subagent_tasks.parquet",
                               columns=["session_id", "tool_use_id", "agent_id", "transcript_path"]).to_pylist():
            if t["transcript_path"] and (root / t["transcript_path"]).is_file():
                subs[t["session_id"]].append(t)
    out = []
    for s in sessions:
        path = s["transcript_path"]
        local = root / path if path and (root / path).is_file() else None
        read, published = [], []
        if local:
            read.append(("full.jsonl", str(local)))
            published.append(_published(name, entry, path))
            for t in sorted(subs.get(s["session_id"], []), key=lambda t: t["transcript_path"]):
                read.append((f"subagents/{t['tool_use_id']}/agent-{t['agent_id']}.jsonl", str(root / t["transcript_path"])))
                published.append(_published(name, entry, t["transcript_path"]))
        repo = repos.get(s["repo_id"], {})
        out.append(_row(name, entry, s["session_id"], agent=s["agent"], repo=s["repo_id"],
                        repo_url=f"https://github.com/{s['repo_id']}" if s["repo_id"] else None,
                        repo_license=repo.get("repo_license"), repo_language=repo.get("repo_language"),
                        found_url=f"{entry['url']}/blob/{entry.get('revision')}/{path}" if path else entry.get("url"),
                        dedup_key=f"{namespace(s['agent'])}:{s['session_id']}",
                        file_format="json" if (s["agent"] or "").lower() in ("opencode", "gemini cli") else "jsonl",
                        bytes=sum(Path(p).stat().st_size for _, p in read), _read=read, _published=published,
                        _session={"session_id": s["session_id"], "checkpoint_pk": s["canonical_checkpoint_pk"],
                                  "strategy": s["strategy"]},
                        extra={"dataset": entry["id"], "revision": entry.get("revision"),
                               "swechat_license_type": repo.get("license_type"),
                               "transcript": path if local else None,
                               "why_no_transcript": None if local else ("none recorded" if not path else
                                                                         "not in the dataset's files"),
                               "created_at": _when(s["created_at"], unit)}))
    # A copy taken while the dataset was being uploaded holds sessions whose transcripts are not yet in it (7,819 of
    # 17,819 on 2026-10-03): the build stops rather than publish a partial copy as the dataset.
    named = [r for r in out if r["extra"]["why_no_transcript"] != "none recorded"]
    missing = sum(1 for r in named if r["extra"]["why_no_transcript"] == "not in the dataset's files")
    if named and missing > 0.02 * len(named):
        raise RuntimeError(f"build refused: {missing:,} of {len(named):,} SWE-chat v2 sessions name a transcript its "
                           f"copy (revision {entry.get('revision')}) does not hold: copy it again")
    # Counts from each transcript, by the same parsers as Entire's (the files are Entire's transcripts).
    jobs = [(r["agent"], [p for n, p in r["_read"] if n == "full.jsonl"]) for r in out]
    got = list(pool.map(_facts_job, jobs, chunksize=8)) if pool is not None else list(map(_facts_job, jobs))
    for r, fx in zip(out, got):
        if r["_read"]:
            r.update({k: fx.get(k) for k in facts.COUNTS if k in fx and k != "agent_version"})
            r["agent_version"] = fx.get("agent_version")
            r["extra"]["parse_error"] = fx.get("parse_error")
    return out


# -- DataClaw: one session per conversations.jsonl record; a raw Claude Code transcript, one per file -------------
def _sniff(path: Path) -> str | None:
    """`record` for DataClaw's export (a JSON object with `messages`), `claude code` for Claude Code's own lines."""
    with open(path, "rb") as fh:
        for _, raw in zip(range(50), fh):
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            if isinstance(e, dict) and isinstance(e.get("messages"), list):
                return "record"
            if isinstance(e, dict) and e.get("type") in ("user", "assistant") and isinstance(e.get("message"), dict):
                return "claude code"
    return None


def _dataclaw(name: str, entry: dict, root: Path) -> list[dict]:
    out = []
    for f in entry["files"]:
        path = Path(f["local"])
        if path.suffix != ".jsonl" or not path.is_file():
            continue
        kind = _sniff(path)
        if kind == "claude code":
            sid = None
            with open(path, "rb") as fh:
                for raw in fh:
                    try:
                        sid = json.loads(raw).get("sessionId")
                    except (ValueError, AttributeError):
                        continue
                    if sid:
                        break
            out.append(_row(name, entry, f"{entry['id']}:{f['path']}", agent="Claude Code",
                            dedup_key=f"claude-code:{sid}" if sid else f"sha256:{f['sha256']}", bytes=f["bytes"],
                            _read=[("full.jsonl", str(path))], _published=[_published(name, entry, f["path"])],
                            _session={"session_id": sid}))
        elif kind == "record":
            with open(path, "rb") as fh:
                for n, raw in enumerate(fh, 1):
                    try:
                        rec = json.loads(raw)
                    except ValueError:
                        continue
                    if not isinstance(rec, dict) or not isinstance(rec.get("messages"), list):
                        continue
                    agent = AGENT_OF.get(str(rec.get("agent") or rec.get("source") or "").strip().lower(),
                                         rec.get("agent") or None)
                    sid = rec.get("session_id")
                    msgs = [m for m in rec["messages"] if isinstance(m, dict)]
                    out.append(_row(
                        name, entry, f"{entry['id']}:{f['path']}:{n}", agent=agent,
                        dedup_key=(f"{namespace(agent)}:{sid}" if sid and agent else
                                   "sha256:" + hashlib.sha256(raw.strip()).hexdigest()),
                        bytes=len(raw), models=[rec["model"]] if isinstance(rec.get("model"), str) else [],
                        started_at=facts._iso(rec.get("start_time")), ended_at=facts._iso(rec.get("end_time")),
                        has_timestamps=bool(rec.get("start_time")),
                        n_user_entries=sum(1 for m in msgs if m.get("role") == "user"),
                        n_agent_messages=sum(1 for m in msgs if m.get("role") == "assistant" and str(m.get("content") or "").strip()),
                        n_tool_calls=sum(len(m.get("tool_uses") or []) for m in msgs),
                        has_tool_results=any(isinstance(t, dict) and t.get("output") is not None
                                             for m in msgs for t in m.get("tool_uses") or []),
                        _read=[(f"{f['path']}:{n}", str(path))], _reader="dataclaw",
                        _published=[_published(name, entry, f["path"])], _session={"session_id": sid}))
    return out


# -- OpenHands feedback: one session per trajectory --------------------------------------------------------------
def _openhands(name: str, entry: dict, root: Path) -> list[dict]:
    out = []
    for f in entry["files"]:
        if not f["path"].endswith(".parquet"):
            continue
        path = Path(f["local"])
        t = pq.read_table(path, columns=["trajectory", "feedback", "timestamp", "version"])
        for i, rec in enumerate(t.to_pylist()):
            events = rec.get("trajectory") or []
            digest = hashlib.sha256(json.dumps(events, sort_keys=True, default=str).encode()).hexdigest()
            out.append(_row(name, entry, f"{entry['id']}:{f['path']}:{i}", agent="OpenHands", file_format="parquet",
                            dedup_key=f"sha256:{digest}", bytes=len(json.dumps(events, default=str)),
                            started_at=facts._iso(rec["timestamp"].isoformat() + "Z") if rec.get("timestamp") else None,
                            has_timestamps=rec.get("timestamp") is not None,
                            has_tool_results=any(e.get("observation") for e in events),
                            n_user_entries=sum(1 for e in events if e.get("source") == "user" and e.get("action") == "message"),
                            n_agent_messages=sum(1 for e in events if e.get("source") == "agent" and e.get("action") == "message"),
                            n_tool_calls=sum(1 for e in events if e.get("source") == "agent" and e.get("action")
                                             not in (None, "message", "change_agent_state", "initialize")),
                            _read=[(f"{f['path']}:{i}", str(path))], _reader="openhands",
                            _published=[_published(name, entry, f["path"])],
                            extra={"dataset": entry["id"], "revision": entry.get("revision"), "feedback": rec.get("feedback"),
                                   "openhands_version": rec.get("version")}))
    return out


# -- SWE-chat's labels of prompts, carried over (the agreed point A) -------------------------------------------------
def norm(text: str | None) -> str:
    return " ".join((text or "").split())


# A prompt's text as SWE-chat v2's labels and the rebuilt rows can both give it: spaces normalized, an email and
# SWE-chat's anonymized tokens alike, Cursor's `<user_query>` and `<timestamp>` wrappers and an image's placeholder
# left out. Measured on the full corpus (2026-10-04): Cursor's typed prompts matched SWE-chat's labelled ones 0% by
# plain text and 95% so; Claude Code's 95.6% and 97.7%.
_PRESIDIO = re.compile(r"<PRESIDIO_ANONYMIZED_[A-Z_]+>")
_EMAILISH = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")
_WRAPPERS = re.compile(r"</?user_query>|<timestamp>[^<]{0,80}</timestamp>|\[Image: image/[a-z0-9.+-]+\]")


def label_key(text: str | None) -> str:
    """A prompt's text as both sides compare it: cleaned as a typed row's is (events.unwrap), as SWE-chat keeps the
    blocks a tool inserts and the rows do not (47 of SWE-chat's labels found no row once the rows' text was cleaned,
    2026-10-05); masks, emails and Cursor's wrappers alike."""
    from .events import unwrap

    t = _PRESIDIO.sub("\x00", unwrap(text or ""))
    t = _EMAILISH.sub("\x00", t)
    return norm(_WRAPPERS.sub(" ", t))


# Codex wraps an image the developer attached in tags, as text blocks around it: `<image name=[Image #1]>` (or
# `<image>`), the image, `</image>`. SWE-chat v2 keeps each block as a prompt of its own and labels each, the
# developer's words apart; the rows hold the message whole, the image as `[Image: unknown]` (conv_build._label, pass 7).
# Only a pair holding an image's marker or nothing, so a developer's own markup (`<image href=...></image>`) stays.
IMAGE_TAGS = re.compile(r"<image(?: name=[^<>\n]{0,500})?>\s*(?:\[Image: [^\]\n]{0,100}\]\s*)?</image>")


def without_images(text: str) -> str:
    """A text with Codex's image tags and what they hold taken out, its spaces normalized; else the text as it is."""
    return norm(IMAGE_TAGS.sub(" ", text)) if "</image>" in text else text


def swechat_labels(ledger) -> dict[str, list[tuple]]:
    """SWE-chat v2's labels of its prompts (written by a model: `prompt_intent`, `prompt_pushback`; and `language`), by
    session id: each labelled prompt in SWE-chat's order as (its entry id and block, or None; its text's key; the
    labels). conv_build._label puts them on the rebuilt rows."""
    import pyarrow.compute as pc
    import pyarrow.dataset as ds

    for name, entry in entries(ledger):
        if name != "swe-chat-v2" or not entry.get("copied") or not entry.get("files"):
            continue
        path = _root(entry) / "conversations.parquet"
        if not path.is_file():
            return {}
        has = lambda c: ~pc.field(c).is_null()
        cols = ["session_id", "turn_number", "source_entry_uuid", "content_block_index", "content", "prompt_intent",
                "prompt_pushback", "language"]
        data = ds.dataset(str(path))
        table = data.to_table(   # a column this copy lacks (an older SWE-chat: no entry ids) reads as empty
            columns=[c for c in cols if c in data.schema.names],
            filter=(pc.field("turn_type") == "user_prompt") & (has("prompt_intent") | has("prompt_pushback") | has("language")))
        values = [table.column(c).to_pylist() if c in table.column_names else [None] * table.num_rows for c in cols]
        got: dict[str, list[tuple]] = defaultdict(list)
        for sid, turn, uuid, block, content, intent, pushback, language in zip(*values):
            if sid:
                got[sid].append((turn if turn is not None else -1, block or 0, (uuid, block or 0) if uuid else None,
                                 label_key(content), (intent, pushback, language)))
        return {sid: [x[2:] for x in sorted(v, key=lambda x: (x[0], x[1]))] for sid, v in got.items()}
    return {}
