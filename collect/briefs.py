"""Helper agents' sessions, filed as sessions of their own: every "user" message in them is another agent's text.

The person's check of the typed mark (2026-11-04) found them: 6 of 57 sampled rows marked typed were briefs one agent
wrote for a helper ("Explore the ... I need to understand: 1. ..."). A helper's session saved as a session of its own
starts with the prompt its parent sent, and its later "user" messages are the parent's too (follow-ups, review
rounds: read on 2026-11-04, 20 of 20 in the sessions the first rules found, 19 of 20 in DataClaw's Haiku-only ones);
nothing in most formats says a program wrote them. A session is a helper's by any of three rules:

- **OpenCode helper sessions:** the session's raw file names a parent session (`"parentID": "ses_..."`).
- **Matched briefs, any source:** the session's first typed row starts (`brief_key`: the first 300 characters, spaces
  collapsed, lower case; 40 or more) as the prompt of an orchestration tool call (`category` = "Orchestration": Task,
  runSubagent, spawn_subagent, ...) made in another session. A call in the same session, or in one of its copies,
  does not count: an agent often hands the person's own words on to a helper.
- **DataClaw's Haiku-only Claude Code sessions:** every model the session names is a Claude Haiku model, the model
  Claude Code runs its helper agents on; DataClaw's uploads hold such helpers' sessions without their parents. Read on
  2026-11-04: 30 of 30 such sessions drawn at random open with an agent's brief; 1 of 20 later typed rows read as a
  person's (someone who chose Haiku as the main model), the rule's known cost.

Every typed row of such a session (and any delivered copy of one, `copy_of`; not a person's message a forked thread
inherited from its parent's conversation, `copy_of` -1, which stays the person's) becomes another agent's text:
`typed_by_developer` false, `actor` "peer", its `pushback` label and (in a tier's label tables) its `text_id` empty; its
session's `n_user_messages` (typed rows, copies left out) counts it no more. `find` reads a built version and returns
the rows; `apply` rewrites them wherever a folder holds them (the version, a tier's tables, a tier's label tables),
only the files that change, each written beside and moved over. `fingerprints` then gives each changed session's
`source_facts.typed_fingerprint` again from its typed rows as the build makes it (conv_build: two or more typed rows,
copies left out, spaces collapsed, in order; none for fewer), and `set_fingerprints` writes it into every session table
that has `source_facts`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

KEY_CHARS, MIN_CHARS = 300, 40
PROMPT_FIELDS = ("prompt", "message", "task", "instructions")
PARENT = re.compile(r'"parentID"\s*:\s*"ses_')
RULES = ("opencode helper session", "matches another session's brief", "dataclaw haiku-only session")
TABLES = ("conversations", "conversations_subagents")


def brief_key(text: str | None) -> str | None:
    """The start of a text as briefs are matched: whitespace collapsed, lower case, the first 300 characters; None
    for a text too short to match safely."""
    k = re.sub(r"\s+", " ", (text or "").strip()).lower()[:KEY_CHARS]
    return k if len(k) >= MIN_CHARS else None


def _prompt(tool_input_json: str | None) -> str | None:
    try:
        args = json.loads(tool_input_json or "")
    except ValueError:
        return None
    if not isinstance(args, dict):
        return None
    return next((args[f] for f in PROMPT_FIELDS if isinstance(args.get(f), str) and args[f].strip()), None)


def _parquets(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.parquet")) if folder.exists() else []


def haiku_only(models) -> bool:
    return bool(models) and all("haiku" in (m or "").lower() for m in models)


def _canonical(version_dir: Path) -> tuple[dict[str, str], set[str], set[str]]:
    """Each session's kept session (itself when kept), the OpenCode sessions whose raw file names a parent, and
    DataClaw's Claude Code sessions that name only Haiku models."""
    kept, children, haiku = {}, set(), set()
    for path in _parquets(version_dir / "data"):
        names = pq.ParquetFile(path).schema_arrow.names
        t = pq.read_table(path, columns=["session_key", "duplicate_of", "agent"] + (["models"] if "models" in names else []))
        for key, dup in zip(t["session_key"].to_pylist(), t["duplicate_of"].to_pylist()):
            kept[key] = dup or key
        if path.parent.name == "dataclaw" and "models" in names:
            haiku |= {k for k, a, m in zip(t["session_key"].to_pylist(), t["agent"].to_pylist(), t["models"].to_pylist())
                      if a == "Claude Code" and haiku_only(m)}
        opencode = [i for i, a in enumerate(t["agent"].to_pylist()) if a == "OpenCode"]
        if not opencode:
            continue
        files = pq.read_table(path, columns=["session_key", "files"]).take(opencode)
        for key, fs in zip(files["session_key"].to_pylist(), files["files"].to_pylist()):
            if any(PARENT.search((f or {}).get("content") or "") for f in fs or []):
                children.add(key)
    return kept, children, haiku


def find(version_dir: Path) -> dict:
    """Every typed row of a built version's helper sessions: {"rows": [(session_key, turn_number, rule)], "counts":
    sessions by rule and source, "row_counts": rows by rule and source, ...}."""
    kept, children, haiku = _canonical(version_dir)
    issuers: dict[str, set[str]] = defaultdict(set)   # brief key -> the kept sessions that sent it
    for table in TABLES:
        for path in _parquets(version_dir / table):
            t = ds.dataset(path).to_table(columns=["session_key", "tool_input_json"],
                                          filter=(ds.field("kind") == "tool_call")
                                          & (ds.field("category") == "Orchestration"))
            for key, args in zip(t["session_key"].to_pylist(), t["tool_input_json"].to_pylist()):
                k = brief_key(_prompt(args))
                if k:
                    issuers[k].add(kept.get(key, key))
    first: dict[str, tuple[int, str | None, str]] = {}   # session -> (turn, brief key, source), copies left out
    typed: dict[str, list[int]] = defaultdict(list)       # session -> every typed row's turn, copies included
    for path in _parquets(version_dir / "conversations"):
        t = ds.dataset(path).to_table(columns=["session_key", "turn_number", "content", "source", "copy_of"],
                                      filter=ds.field("typed_by_developer") == True)  # noqa: E712
        for key, turn, text, src, cp in zip(t["session_key"].to_pylist(), t["turn_number"].to_pylist(),
                                            t["content"].to_pylist(), t["source"].to_pylist(), t["copy_of"].to_pylist()):
            if cp != -1:   # a developer's message a forked thread (Codex) inherited from its parent stays the person's
                typed[key].append(turn)
            if cp is None and (key not in first or turn < first[key][0]):
                first[key] = (turn, brief_key(text), src)
    rows, counts, row_counts = [], Counter(), Counter()
    for key, (turn, k, src) in first.items():
        if key in children:
            rule = RULES[0]
        elif k and issuers.get(k, set()) - {kept.get(key, key)}:
            rule = RULES[1]
        elif key in haiku:
            rule = RULES[2]
        else:
            continue
        rows += [(key, n, rule) for n in sorted(typed[key])]
        counts[f"{rule} / {src}"] += 1
        row_counts[f"{rule} / {src}"] += len(typed[key])
    return {"rows": sorted(rows), "counts": dict(counts), "row_counts": dict(row_counts), "brief_keys": len(issuers),
            "opencode_helper_sessions": len(children), "dataclaw_haiku_only_sessions": len(haiku),
            "sessions_with_typed": len(first)}


def _write_like(table: pa.Table, original: Path) -> None:
    md = pq.ParquetFile(original).metadata
    codec = md.row_group(0).column(0).compression.lower() if md.num_row_groups else "zstd"
    size = max(md.row_group(i).num_rows for i in range(md.num_row_groups)) if md.num_row_groups else None
    tmp = original.with_name(original.name + ".briefs-tmp")
    pq.write_table(table, tmp, compression=codec, row_group_size=size)
    os.replace(tmp, original)


def _set(table: pa.Table, name: str, values: list) -> pa.Table:
    i = table.schema.get_field_index(name)
    return table.set_column(i, table.schema.field(i), pa.array(values, table.schema.field(i).type))


def apply(folder: Path, rows: list, *, write: bool = True) -> Counter:
    """The rows (session_key, turn_number[, rule]) marked as another agent's text in every message table under
    `folder` that holds them (`conversations/`, `labels/conversations/`, either tier's), and their sessions'
    `n_user_messages` lowered in every session table that has the column (`data/`, `pointers.parquet`). A row
    already not typed is left. With `write` false, only counts."""
    target = {(k, t) for k, t, *_ in rows}
    per_session = Counter(k for k, _ in target)
    counts = Counter()
    message_files = [p for sub in ("conversations", "labels/conversations") for p in _parquets(folder / sub)]
    lowered = Counter()   # sessions whose typed row was changed, by how many
    for path in message_files:
        cols = pq.ParquetFile(path).schema_arrow.names
        t = pq.read_table(path, columns=["session_key", "turn_number", "typed_by_developer"]
                          + (["copy_of"] if "copy_of" in cols else []))
        keys = t["session_key"].to_pylist()
        copy_of = t["copy_of"].to_pylist() if "copy_of" in cols else [None] * len(keys)
        # a brief's row, or a delivered copy of it (copy_of: the turn it was queued at)
        hits = [i for i, (k, n, typed, c) in enumerate(zip(keys, t["turn_number"].to_pylist(),
                                                           t["typed_by_developer"].to_pylist(), copy_of))
                if typed and ((k, n) in target or (c is not None and (k, c) in target))]
        if not hits:
            continue
        counts["rows marked another agent's"] += len(hits)
        for i in hits:
            if copy_of[i] is None:             # n_user_messages leaves copies out
                lowered[keys[i]] += 1
        if not write:
            continue
        whole = pq.read_table(path)
        hit = set(hits)
        for name, value in (("typed_by_developer", False), ("actor", "peer"), ("pushback", None), ("text_id", None)):
            if name in cols:
                old = whole[name].to_pylist()
                whole = _set(whole, name, [value if i in hit else v for i, v in enumerate(old)])
        _write_like(whole, path)
        counts["message files written"] += 1
    session_files = _parquets(folder / "data") + ([folder / "pointers.parquet"]
                                                   if (folder / "pointers.parquet").exists() else [])
    for path in session_files:
        if "n_user_messages" not in pq.ParquetFile(path).schema_arrow.names:
            continue
        t = pq.read_table(path, columns=["session_key", "n_user_messages"])
        keys, n = t["session_key"].to_pylist(), t["n_user_messages"].to_pylist()
        # a session table lowers its count only where this folder's message rows changed (a tier without the rows,
        # such as tier B's pointers beside its label tables, takes the count from the label tables' change)
        new = [max(0, (c or 0) - lowered[k]) if lowered.get(k) and c is not None else c for k, c in zip(keys, n)]
        if new == n:
            continue
        counts["sessions' n_user_messages lowered"] += sum(1 for a, b in zip(n, new) if a != b)
        if write:
            _write_like(_set(pq.read_table(path), "n_user_messages", new), path)
            counts["session files written"] += 1
    counts["sessions asked"] = len(per_session)
    return counts


def fingerprints(version_dir: Path, sessions: set[str]) -> dict[str, str | None]:
    """Each session's typed_fingerprint from its typed rows in `version_dir` now, as conv_build makes it; None where
    fewer than two typed rows are left."""
    texts: dict[str, list] = defaultdict(list)
    keys = pa.array(sorted(sessions), pa.string())
    for path in _parquets(version_dir / "conversations"):
        t = ds.dataset(path).to_table(columns=["session_key", "turn_number", "content"],
                                      filter=(ds.field("typed_by_developer") == True)  # noqa: E712
                                      & ds.field("copy_of").is_null() & ds.field("session_key").isin(keys))
        for k, n, c in zip(t["session_key"].to_pylist(), t["turn_number"].to_pylist(), t["content"].to_pylist()):
            texts[k].append((n, " ".join((c or "").split())))
    out = {}
    for k in sessions:
        typed = [c for _, c in sorted(texts.get(k, []))]
        out[k] = (hashlib.sha256("\x1f".join(typed).encode("utf-8", "surrogatepass")).hexdigest()
                  if len(typed) >= 2 else None)
    return out


def set_fingerprints(folder: Path, fps: dict[str, str | None], *, write: bool = True) -> Counter:
    """`source_facts.typed_fingerprint` set to `fps`' value (removed where None) for its sessions, in every session
    table under `folder/data` that has `source_facts`; the JSON written as the build writes it (sorted keys)."""
    counts = Counter()
    for path in _parquets(folder / "data"):
        if "source_facts" not in pq.ParquetFile(path).schema_arrow.names:
            continue
        t = pq.read_table(path, columns=["session_key", "source_facts"])
        old = t["source_facts"].to_pylist()
        new = list(old)
        for i, k in enumerate(t["session_key"].to_pylist()):
            if k not in fps:
                continue
            facts = json.loads(old[i] or "{}")
            if facts.get("typed_fingerprint") == fps[k]:
                continue   # already right, or none to remove (a session with no source_facts keeps none)
            if fps[k] is None:
                facts.pop("typed_fingerprint")
            else:
                facts["typed_fingerprint"] = fps[k]
            new[i] = json.dumps(facts, ensure_ascii=False, sort_keys=True)
            counts["fingerprints removed" if fps[k] is None else "fingerprints changed"] += 1
        if new == old:
            continue
        if write:
            _write_like(_set(pq.read_table(path), "source_facts", new), path)
            counts["session files written"] += 1
    return counts
