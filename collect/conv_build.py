"""The conversations tables of a version: rows extracted from each kept session's staged (masked) files, their text
scanned again as it reads once decoded, and every secret found there masked in the session's files too.

A secret can hide from the scan of a file and show once its JSON is decoded: written with escapes (`\\/`, `\\u0026`),
or in code whose quotes JSON escaped: gitleaks' generic rule allows no backslash between `=` and the value, so
`api_key = \\"...\\"` in a JSON line is never found (measured with gitleaks on 2026-10-03: 3 of 4 quoted cases missed
in a JSON line, all 4 found decoded). So the tables' free text (SCANNED) is staged as views showing it decoded: a cell
a line, with only a backslash, a line break and a carriage return escaped (`\\\\`, `\\n`, `\\r`), quotes as they are;
PART_ROWS rows a part, so the check splits a published file the same way and the split never depends on what masking
changed. Each part is masked for private keys by their structure as it is staged (scan.pem_spans, as every file is),
scanned until clean, and fed back: each secret found is masked wherever the session's own files hold it, escaped or
not (scan.place), and those files are scanned again before the sessions table is written.

Ids and fixed vocabularies are not in the views: they come from the files, scanned where they were written, and
cells side by side in one line read to gitleaks as assignments (`"...token", "toolu_01..."`), which masked 3,474
session keys in a first build of the sample (2026-10-03).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import events, formats, scan

STR, BIG = pa.string(), pa.large_string()
TYPES = {"turn_number": pa.int64(), "conversation_turn_number": pa.int64(), "content_block_index": pa.int64(),
         "is_conversational": pa.bool_(), "content": BIG, "timestamp": pa.timestamp("us", tz="UTC"),
         "input_tokens": pa.int64(), "output_tokens": pa.int64(), "cache_creation_input_tokens": pa.int64(),
         "cache_read_input_tokens": pa.int64(), "is_continuation": pa.bool_(), "is_first_turn": pa.bool_(),
         "word_count": pa.int64(), "char_count": pa.int64(), "tool_input_json": BIG, "typed_by_developer": pa.bool_(),
         "is_error": pa.bool_(), "copy_of": pa.int64(), "content_truncated": pa.bool_(), "raw_line": pa.int64(),
         "pushback": pa.bool_()}
SCHEMA = pa.schema([(c, TYPES.get(c, STR)) for c in events.COLUMNS])
# A subagent's thread queues no messages (the main thread's do) and so copies none: those two columns, empty in every
# subagent row, are left out of its table (2026-10-05), as SWE-chat's own subagent table has neither.
SUB_LEFT_OUT = ("queue_op_subtype", "copy_of", "pushback")   # a subagent queues nothing; nobody types to it
SUB_SCHEMA = pa.schema([(c, STR) for c in events.SUBAGENT_KEYS]
                       + [(c, TYPES.get(c, STR)) for c in events.COLUMNS if c not in SUB_LEFT_OUT])
TABLES = {"conversations": SCHEMA, "conversations_subagents": SUB_SCHEMA}
SCANNED = ("content", "tool_input_json", "command", "file_path", "pattern")  # the free text, scanned again
PART_ROWS = 512  # rows a view part: the build scans in these parts, and the check splits a published file the same
GROUP_ROWS = 50_000  # rows a Parquet row group


def session_fields(source: str, row: dict, files: list[tuple[str, Path]]) -> dict:
    """The session columns its rows carry: the key, the agent's own id, and for Entire its checkpoint and strategy
    (`{repo}#{checkpoint_id}`, as SWE-chat's `checkpoint_pk`), read from the session's metadata.json."""
    meta = {}
    for name, local in files:
        if Path(name).name == "metadata.json":
            try:
                got = json.loads(local.read_bytes())
                meta = got if isinstance(got, dict) else {}
            except ValueError:
                meta = {}
    if row.get("_session") is not None:   # a copied dataset's session: what the dataset records
        own = row["_session"]
        return {"source": source, "session_key": f"{source}:{row['source_id']}", "session_id": own.get("session_id"),
                "checkpoint_pk": own.get("checkpoint_pk"), "strategy": own.get("strategy"), "repo_id": row.get("repo"),
                "agent": row.get("agent")}
    extra = row.get("extra") or {}
    checkpoint = extra.get("checkpoint_id") or meta.get("checkpoint_id")
    session_id = meta.get("session_id") or (row["source_id"].split(":", 1)[1] if source == "entire" and ":" in row["source_id"] else None)
    if source == "specstory" and files:   # its header: `<!-- Claude Code Session <id> (<time>) -->`
        from .sources.specstory import SESSION

        head = SESSION.search(files[0][1].read_text(encoding="utf-8", errors="replace")[:4000])
        session_id = head[2] if head else None
    return {"source": source, "session_key": f"{source}:{row['source_id']}", "session_id": session_id,
            "checkpoint_pk": f"{row['repo']}#{checkpoint}" if checkpoint and row.get("repo") else None,
            "strategy": meta.get("strategy") if isinstance(meta.get("strategy"), str) else None,
            "repo_id": row.get("repo"), "agent": row.get("agent")}


def _time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


LONE = re.compile("[\ud800-\udfff]")


def _clean(v):
    """A string as UTF-8 can hold it: a lone surrogate (JSON's `\\ud83d`, half an emoji cut off) becomes U+FFFD."""
    if isinstance(v, str) and LONE.search(v):
        return v.encode("utf-16", "surrogatepass").decode("utf-16", "replace")
    return v


def _table(rows: list[dict], schema: pa.Schema) -> pa.Table:
    return pa.Table.from_pylist([{**{k: _clean(v) for k, v in r.items()}, "timestamp": _time(r.get("timestamp"))}
                                 for r in rows], schema=schema)


class _Writer:
    def __init__(self, path: Path, schema: pa.Schema):
        self.path, self.schema, self.writer, self.batch, self.rows = path, schema, None, [], 0

    def add(self, rows: list[dict]) -> None:
        self.batch += rows
        if len(self.batch) >= GROUP_ROWS:
            self.flush()

    def flush(self) -> None:
        if not self.batch:
            return
        if self.writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.writer = pq.ParquetWriter(str(self.path), self.schema, compression="zstd")
        self.writer.write_table(_table(self.batch, self.schema))
        self.rows += len(self.batch)
        self.batch = []

    def close(self) -> Path | None:
        self.flush()
        if self.writer is not None:
            self.writer.close()
            return self.path
        return None


# An escape escape_tolerant reads for a letter, a digit, `_.-` (the characters of _PLAIN_RUN) or a backslash (before
# which a bare `uXXXX` is read): `\u`, or such a `%XX` at any depth. A text with none holds a secret only if it
# holds the secret's longest run of those characters as written.
_RUN_ESCAPE = re.compile(r"\\u|%(?:25){0,4}(?:3[0-9]|[46][1-9a-fA-F]|[57][0-9aA]|2[dDeE]|5[cCfF])")
GRAM = 8   # the least run (_PLAIN_RUN): runs are looked up by their first GRAM characters
MANY = 256   # secrets in a session from which each place of a text is looked up, not each run looked for


def _plan(secrets: tuple[str, ...]) -> tuple[list, dict, list]:
    """A session's secrets (six characters or more), each with its longest run; the runs by their first GRAM
    characters; and those always looked for: with no run, or one inside the mask (a mask can make it)."""
    probes, index, always = [], {}, []
    for s in secrets:
        if len(s) < 6:
            continue
        runs = _PLAIN_RUN.findall(s)
        run = max(runs, key=len) if runs else None
        if run is None or run in scan.MASK:
            always.append(len(probes))
        else:
            index.setdefault(run[:GRAM], []).append(len(probes))
        probes.append((s, run))
    return probes, index, always


def _carry(rows: list[dict], secrets: list[str], cache: dict | None = None) -> None:
    """The secrets the decoded pass found in a session's files, masked in its rows wherever they hold them, escaped or
    not: a copy read as downloaded (a Parquet file's rows) gets what the scan of the copy found.

    A session read from a copied export carries every secret the scan of the export found: the largest, 3.3 GB of
    4,018 sessions, holds 174,979 masks. So each secret is tried only on a value that can hold it (one with no
    _RUN_ESCAPE holds its run as written; a run is found from each place of the text when the secrets are MANY),
    in the order given, as each was tried on every value before; and `cache` (one per worker job) keeps each
    session's plan and each pattern, compiled once, not once per session (re's own cache holds 512)."""
    cache = {"plans": {}, "patterns": {}} if cache is None else cache
    key = tuple(secrets)
    plan = cache["plans"].get(key)
    if plan is None:
        plan = cache["plans"][key] = _plan(key)
    probes, index, always = plan
    if not probes:
        return
    patterns = cache["patterns"]
    for r in rows:
        changed = False
        for c in SCANNED:
            v = r.get(c)
            if not v:
                continue
            if _RUN_ESCAPE.search(v):
                ids = range(len(probes))   # an escape can hide any run
            elif len(probes) < MANY:
                ids = [i for i, (_, run) in enumerate(probes) if run is None or run in scan.MASK or run in v]
            else:
                seen = set(always)
                for k in range(len(v) - GRAM + 1):
                    got = index.get(v[k:k + GRAM])
                    if got:
                        seen.update(got)
                ids = sorted(i for i in seen if probes[i][1] is None or probes[i][1] in scan.MASK or probes[i][1] in v)
            for i in ids:
                s = probes[i][0]
                rx = patterns.get(s)
                if rx is None:
                    rx = patterns[s] = scan.escape_tolerant(s)
                v, n = rx.subn(scan.MASK, v)
                changed |= bool(n)
            r[c] = v
        if changed:
            text = r.get("content") or ""
            r["word_count"], r["char_count"] = len(text.split()), len(text)


COPIED = ("swe-chat-v2", "dataclaw", "openhands-feedback")   # their text is masked for secrets only (COPIED_PII)


def _personal(rows: list[dict]) -> None:
    """Emails and home-path names masked in rows' text, as in the files they come from."""
    for r in rows:
        changed = False
        for c in SCANNED:
            v = r.get(c)
            if v and ("@" in v or "%40" in v or "Users" in v or "users" in v or "home" in v):
                out, counts = scan.redact(v, [], pii=True)
                if out != v:
                    r[c], changed = out, True
        if changed:
            text = r.get("content") or ""
            r["word_count"], r["char_count"] = len(text.split()), len(text)


PUSHBACK = ("correction", "failure_report", "rejection")   # SWE-chat's prompt_pushback values that are pushback
LABEL_PREFIX = 200
QUEUED = events.QUEUE_SUBTYPE["enqueue"]   # a queue_operation row of a message queued


def _masked_agrees(ours: str, theirs: str) -> bool:
    """Whether `ours` is `theirs` with stretches masked: each mask in `ours` stands for one character or more of
    `theirs`, and everything else is the same. A scraped copy of a session is masked for personal data and SWE-chat's
    is not: a name in a path (`/Users/[REDACTED]/`) kept 467 of SWE-chat's labels off the Entire copies of version
    2026-10-11, whose rows were SWE-chat's own, entry id for entry id. The pieces between masks are found left to
    right, each at its first place: no regular expression, as a prompt can hold dozens of masks."""
    pieces = ours.split(scan.MASK)
    if len(pieces) < 2 or len(theirs) < sum(map(len, pieces)) + len(pieces) - 1:
        return False
    if not theirs.startswith(pieces[0]) or not theirs.endswith(pieces[-1]):
        return False
    at, end = len(pieces[0]), len(theirs) - len(pieces[-1])   # theirs[at:end] holds the masks and the middle pieces
    for p in pieces[1:-1]:
        i = theirs.find(p, at + 1, end - 1) if p else at + 1   # a mask stands for one character at least
        if i < 0 or i > end - 1 - len(p):
            return False
        at = i + len(p)
    return end - at >= 1


def _label(rows: list[dict], labelled: list[tuple], examples: list | None = None) -> dict:
    """SWE-chat v2's labels (copied_sessions.swechat_labels) on the rows of the prompts they were given to (user_prompt
    rows, and Claude Code's system_injected rows and rows of a queued message), read before any mask (a masked row no
    longer reads as SWE-chat's text). Each of SWE-chat's labelled prompts goes to one row at most, in passes over the
    rows:

    1. by SWE-chat's entry id and block, when the two texts agree (one is the other or begins it): SWE-chat dropped
       blocks the rows keep (an IDE's context, images), so the same block number can be another block;
    2. by the text's key (copied_sessions.label_key): SWE-chat's first prompt with it that no row has taken, in its
       turn order; a repeat SWE-chat gave no label of its own gets none;
    3. by the key's first LABEL_PREFIX characters, when one row and one labelled prompt of the session alone begin
       so, and that prompt is not taken: templated prompts share their start ("Implement the following plan:");
    4. a row whose text holds a mask: SWE-chat's first prompt no row has taken whose text it is, masks aside
       (_masked_agrees). Pass 1 takes such a text too, where the entry ids agree;
    5. a row whose first LABEL_PREFIX characters hold a mask: pass 3, masks aside (one Project Runner label of version
       2026-10-11 had landed by its start a week before, and the Entire copy masked a name at character 93);
    6. a row filed `system_injected` (an entry Claude Code marks as its own, such as a prompt the agent scheduled for
       itself, delivered), as pass 1: SWE-chat v2 files these as prompts and labels them;
    7. a row holding an image Codex wraps in tags (copied_sessions.IMAGE_TAGS), by its text without them: SWE-chat
       keeps the tags as prompts of their own and labels each, the developer's words apart, and the row, whole, had
       taken no label (188 of Codex's labels in version 2026-10-11, 2026-10-12);
    8. a message the developer queued while the agent worked (Claude Code's enqueued row), by its text, where no other
       row holds it: a message removed from the queue, or queued when the session ended, has no row but this one.

    Passes 6 to 8 (2026-10-12) come last, so no label an earlier pass gives moves. Passes 7 and 8 give a label only
    where it is plain which (later): no prompt or system_injected row holds the text, and the rows holding it and
    SWE-chat's untaken labels of it are as many. A text said again (`continue`), a queued message delivered (its
    delivery holds the label), or a Codex message whose words have a row of their own (the app's record of them, where
    the label already is) takes none: the independent review of the change found labels given out of turn order where
    repeated texts were taken one by one, and a measure found most of SWE-chat's labels of queued messages name the
    entry of their delivery.

    The counts of SWE-chat's labelled prompts: landed on a row marked typed, on another row, or on none; by the rule
    that landed each; pushback ones apart. `examples` collects prefix matches and disagreeing ids (a measurement's,
    never written). The partner's review of rc4: a label taken by id was given again by text."""
    from .copied_sessions import IMAGE_TAGS, label_key, without_images

    by_id: dict[tuple, int] = {}
    by_key: dict[str, list[int]] = {}
    their_prefix = Counter(k[:LABEL_PREFIX] for _, k, _ in labelled if len(k) >= LABEL_PREFIX)
    by_prefix: dict[str, int] = {}
    for i, (eid, key, _) in enumerate(labelled):
        if eid is not None:
            by_id.setdefault(eid, i)
        by_key.setdefault(key, []).append(i)
        if len(key) >= LABEL_PREFIX and their_prefix[key[:LABEL_PREFIX]] == 1:
            by_prefix[key[:LABEL_PREFIX]] = i
    prompts = [r for r in rows if r["turn_type"] == "user_prompt"]
    keys = [label_key(r["content"]) for r in prompts]
    our_prefix = Counter(k[:LABEL_PREFIX] for k in keys if len(k) >= LABEL_PREFIX)
    took: dict[int, tuple[int, str]] = {}   # a prompt's place among the rows: SWE-chat's prompt it took, by which rule
    used: set[int] = set()
    counts: Counter = Counter()

    def agreeing(key: str):
        """SWE-chat's prompts no row has taken whose text a masked key is, masks aside, in turn order (lazily)."""
        shown = len(key) - key.count(scan.MASK) * len(scan.MASK)   # without an entry id, the text must say most:
        return (j for j, (_, theirs, _) in enumerate(labelled)     # a row wholly masked agrees with any text
                if j not in used and theirs and shown >= max(8, len(theirs) / 2) and _masked_agrees(key, theirs))

    for n, (r, key) in enumerate(zip(prompts, keys)):
        j = by_id.get((r["source_entry_uuid"], r.get("content_block_index") or 0)) if r.get("source_entry_uuid") else None
        if j is None or j in used:
            continue
        theirs = labelled[j][1]
        if key and theirs and (theirs == key or key.startswith(theirs) or theirs.startswith(key)):
            took[n] = (j, "id")
            used.add(j)
        elif key and theirs and _masked_agrees(key, theirs):
            took[n] = (j, "id_masked")
            used.add(j)
        else:
            counts["id_text_disagrees"] += 1
            if examples is not None:
                examples.append(("id, text disagrees", theirs[:90], key[:90]))
    for n, key in enumerate(keys):
        if n not in took:
            free = next((j for j in by_key.get(key, ()) if j not in used), None)
            if free is not None:
                took[n] = (free, "text")
                used.add(free)
    for n, key in enumerate(keys):
        if n in took or len(key) < LABEL_PREFIX or our_prefix[key[:LABEL_PREFIX]] != 1:
            continue
        j = by_prefix.get(key[:LABEL_PREFIX])
        if j is not None and j not in used:
            took[n] = (j, "prefix")
            used.add(j)
            if examples is not None:
                examples.append(("prefix", labelled[j][1][:90], key[:90]))
    for n, key in enumerate(keys):
        if n in took or scan.MASK not in key:
            continue
        j = next(agreeing(key), None)
        if j is not None:
            took[n] = (j, "text_masked")
            used.add(j)
    for n, key in enumerate(keys):
        at = key.find(scan.MASK)
        if n in took or len(key) < LABEL_PREFIX or not 0 <= at < LABEL_PREFIX or our_prefix[key[:LABEL_PREFIX]] != 1:
            continue
        split = next((s for s in range(LABEL_PREFIX - len(scan.MASK) + 1, LABEL_PREFIX)
                      if key.startswith(scan.MASK, s)), None)   # a mask the cut would split: the start ends before it
        head = key[:LABEL_PREFIX if split is None else split]
        if len(head) - head.count(scan.MASK) * len(scan.MASK) < LABEL_PREFIX // 2:
            continue   # the start must say most of itself
        hits = [j for j, (_, theirs, _) in enumerate(labelled)
                if len(theirs) >= LABEL_PREFIX and _masked_agrees(head + scan.MASK, theirs)]
        if len(hits) == 1 and hits[0] not in used:
            took[n] = (hits[0], "prefix_masked")
            used.add(hits[0])
    # 6. a row filed system_injected (an entry Claude Code marks as its own, such as a command's expansion or the
    # delivery of a prompt the agent scheduled) whose entry SWE-chat labelled, as pass 1: SWE-chat v2 labels some such
    # entries as prompts.
    held = Counter(keys)   # the texts of the prompt rows and of these: a text one holds is theirs, not a queued row's
    injected_from = len(prompts)
    for r in rows:
        if r["turn_type"] == "system_injected" and r.get("content"):
            prompts.append(r)
            keys.append(label_key(r["content"]))
            held[keys[-1]] += 1
    for n in range(injected_from, len(prompts)):
        r, key = prompts[n], keys[n]
        j = by_id.get((r["source_entry_uuid"], r.get("content_block_index") or 0)) if r.get("source_entry_uuid") else None
        theirs = labelled[j][1] if j is not None and j not in used else ""
        if key and theirs and (theirs == key or key.startswith(theirs) or theirs.startswith(key)
                               or _masked_agrees(key, theirs)):
            took[n] = (j, "id_injected")
            used.add(j)

    def later(cands: dict[int, str], index: dict[str, list[int]], how: str) -> None:
        """Passes 7 and 8: rows (their place, their key) given labels only where it is plain which: no prompt or
        system_injected row holds the text, and as many of these rows hold it as SWE-chat has labelled prompts with it
        that no row took, given in turn order. A text repeated in a session (`continue`, a queued message's delivery)
        takes none."""
        groups: dict[str, list[int]] = defaultdict(list)
        for n, key in cands.items():
            if key and not held[key]:
                groups[key].append(n)
        for key, ns in groups.items():
            free = [j for j in index.get(key, ()) if j not in used]
            if not free and scan.MASK in key:
                free = list(agreeing(key))
            if len(free) == len(ns):
                for n, j in zip(ns, free):
                    took[n] = (j, how)
                    used.add(j)

    # the tags off the content, then its key: tags before an IDE's header kept unwrap from taking the header off (a
    # row the tags' removal leaves as it was holds its own text, so later() passes it by)
    bare = {n: label_key(IMAGE_TAGS.sub(" ", prompts[n]["content"])) for n in range(injected_from)
            if n not in took and "</image>" in (prompts[n]["content"] or "")}
    if bare:
        by_bare: dict[str, list[int]] = defaultdict(list)
        for i, (_, key, _) in enumerate(labelled):
            by_bare[without_images(key)].append(i)
        later(bare, by_bare, "image")
    queued_from = len(prompts)
    for r in rows:
        if r["turn_type"] == "queue_operation" and r.get("queue_op_subtype") == QUEUED and r.get("content"):
            prompts.append(r)
            keys.append(label_key(r["content"]))
    later({n: keys[n] for n in range(queued_from, len(prompts))}, by_key, "queued")
    landed: dict[int, tuple[bool, str]] = {}
    for n, (j, how) in took.items():
        r = prompts[n]
        r["prompt_intent"], r["prompt_pushback"], r["language"] = labelled[j][2]
        landed[j] = (bool(r["typed_by_developer"]), how)
    for j, (_, _, (_, pushback, _)) in enumerate(labelled):
        typed, how = landed.get(j, (False, None))
        where = "typed" if typed else ("other" if j in landed else "none")
        for pre in ("", "pushback_") if pushback in PUSHBACK else ("",):
            counts[f"{pre}{where}"] += 1
            if how:
                counts[f"{pre}by_{how}"] += 1
    return dict(counts)


def row_facts(rows: list[dict]) -> dict:
    """A session's labels as its rows give them, for a session whose stored facts lack them (a format whose reader
    came after its facts, or none): user entries, agent messages, tool calls, models, first and last times."""
    times = sorted(str(r["timestamp"]) for r in rows if r.get("timestamp") is not None)
    first = min((r["timestamp"] for r in rows if r.get("timestamp") is not None), default=None)
    last = max((r["timestamp"] for r in rows if r.get("timestamp") is not None), default=None)
    iso = lambda t: t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if hasattr(t, "strftime") else (str(t) if t else None)
    return {"n_user_entries": sum(1 for r in rows if r["role"] == "user"),
            "n_agent_messages": sum(1 for r in rows if r["turn_type"] == "assistant_response"),
            "n_tool_calls": sum(1 for r in rows if r["turn_type"] == "tool_use"),
            "models": sorted({r["model"] for r in rows if r.get("model")}),
            "started_at": iso(first), "ended_at": iso(last),
            "has_tool_results": any(r["turn_type"] == "tool_result" for r in rows),
            "has_timestamps": bool(times)}


def counts_of(row: dict, files: list[tuple[str, Path]]) -> dict | None:
    """A session's counts from its own files, read as extract_job reads a kept session's (the same reader, typed counted
    the same way), for a duplicate whose stored facts have none: it has no rows to fill them from. None where the files
    give no rows."""
    if not files:
        return None
    session = session_fields(row["source"], row, files)
    try:
        reader = formats.READERS.get(row.get("_reader") or "") or formats.BY_SOURCE.get(row["source"])
        got = reader(files, session) if reader else events.for_session(row.get("agent"), files, session)
    except Exception:   # as extract_job: no rows
        return None
    rows = (got or ([], []))[0]
    if not rows:
        return None
    native = not (row.get("_reader") or row["source"] in formats.BY_SOURCE)
    return {**row_facts(rows), "n_user_messages": events.count_typed(row.get("agent") if native else None, rows,
                                                                     per_turn=bool(row.get("_reader")))}


_PUSHBACK: dict | None = None


def pushback_labels() -> dict:
    """A model's reading of each typed message, pushback or not, by (session_key, turn_number): the file
    ERRATA_PUSHBACK_LABELS names (session_key, turn_number, pushback), read once a process; none without it."""
    global _PUSHBACK
    if _PUSHBACK is None:
        import os

        path = os.environ.get("ERRATA_PUSHBACK_LABELS")
        _PUSHBACK = {}
        if path and Path(path).exists():
            t = pq.read_table(path, columns=["session_key", "turn_number", "pushback"]).to_pydict()
            _PUSHBACK = {(k, n): p for k, n, p in zip(t["session_key"], t["turn_number"], t["pushback"])}
    return _PUSHBACK


def pushback_meta() -> dict | None:
    """The labelling run's own record, meta.json beside the labels file ERRATA_PUSHBACK_LABELS names (the model, the
    prompt as sent, the dates, counts and cost): the changelog's `pushback_labels`. None without it."""
    import os

    path = os.environ.get("ERRATA_PUSHBACK_LABELS")
    meta = Path(path).with_name("meta.json") if path else None
    return json.loads(meta.read_text()) if meta and meta.exists() else None


def _pushback(rows: list[dict]) -> None:
    """Each typed row's pushback label, where the labels hold its session and turn; others none."""
    labels = pushback_labels()
    if labels:
        for r in rows:
            if r["typed_by_developer"]:
                r["pushback"] = labels.get((r["session_key"], r["turn_number"]))


def extract_job(args: tuple) -> dict:
    """One worker's kept sessions: their rows into its own part files (not yet scanned); per session, its counts or
    why it has no rows."""
    source, sessions, out_dir, prefix, *rest = args
    labels = rest[0] if rest else {}   # SWE-chat's labels of these sessions' prompts (copied_sessions.swechat_labels)
    secrets = rest[1] if len(rest) > 1 else {}   # the secrets the decoded pass found in each session's files
    carried = {"plans": {}, "patterns": {}}   # _carry's, for this job's sessions
    main = _Writer(Path(out_dir) / "conversations" / source / f"{prefix}.parquet", SCHEMA)
    subs = _Writer(Path(out_dir) / "conversations_subagents" / source / f"{prefix}.parquet", SUB_SCHEMA)
    per: dict[str, dict] = {}
    for row, files in sessions:
        session = session_fields(source, row, files)
        got = {k: session[k] for k in ("session_key", "session_id", "checkpoint_pk", "strategy")}
        try:
            # A copied dataset's own export (DataClaw, OpenHands), or SpecStory's and Aider's markdown whatever the
            # agent; else the agent's own transcript.
            reader = formats.READERS.get(row.get("_reader") or "") or formats.BY_SOURCE.get(source)
            if not files:
                out = None
                got["conversations_error"] = "no file to read"
            else:
                out = reader(files, session) if reader else events.for_session(row.get("agent"), files, session)
        except Exception as e:  # recorded, never a crash: the session keeps its files and no rows
            out = None
            got["conversations_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        if out is None:
            got.setdefault("conversations_error", f"no reader for agent {row.get('agent')!r}")
            got.update(n_conversation_rows=None, n_subagent_rows=None, typed=None)
        else:
            rows, sub_rows = out
            if not rows and not sub_rows:   # a reason, as for a session with no reader (none recorded before 2026-10-05)
                # a copied dataset's record: its export's format says nothing of one record (#17's review)
                got["conversations_error"] = ("the record holds no messages" if row.get("_reader")
                                              else events.no_rows_reason(files))
            known = labels.get(session.get("session_id") or "")
            if known:   # before any mask, on the rows and the subagents' rows (SWE-chat labels prompts agents wrote)
                got["labels"] = _label(rows + sub_rows, known)
            _carry(rows + sub_rows, secrets.get(row["source_id"]) or [], carried)
            if source not in COPIED:   # personal data, as the files are masked: a decoded string can show what an escape hid
                _personal(rows + sub_rows)
            _pushback(rows)
            main.add(rows)
            subs.add(sub_rows)
            typed_texts = [" ".join((r["content"] or "").split()) for r in rows
                           if r["typed_by_developer"] and r["copy_of"] is None]
            if len(typed_texts) >= 2:   # what the developer typed, in order: sessions copied with a few bytes changed share it
                got["typed_fingerprint"] = hashlib.sha256("\x1f".join(typed_texts).encode("utf-8", "surrogatepass")).hexdigest()
            native = not (row.get("_reader") or source in formats.BY_SOURCE)   # the agent's own transcript
            got.update(n_conversation_rows=len(rows), n_subagent_rows=len(sub_rows),
                       typed=events.count_typed(session.get("agent") if native else None, rows,
                                                per_turn=bool(row.get("_reader"))))   # an export: a session a line
        got["agent"] = session.get("agent")   # the transcript's own format's, where it contradicts the recorded one
        if out is not None:
            got["row_facts"] = row_facts(rows)
        per[row["source_id"]] = got
    return {"per": per, "files": [str(p) for p in (main.close(), subs.close()) if p]}


# -- a session's conversation in digests, for near-copies (dedup.near_copies) ----------------------------------
def _digest(text: str) -> int:
    return int.from_bytes(hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=8).digest(), "big",
                          signed=True)


def summarize(rows: list[dict]) -> dict:
    """What near-copies are told by: the session's typed messages in order and their characters (a command by its name
    and arguments, not its markup), its agent's text replies and its tool calls (by tool and input), each as a digest of
    its text with spaces collapsed; and the whole conversation, every row's role, text and tool input in order, as one
    digest (dedup.same_session_copies)."""
    typed, chars, texts, tools = [], 0, set(), set()
    whole = hashlib.blake2b(digest_size=8)
    for r in rows:
        whole.update(f"{r.get('role') or ''}\x1f{r.get('content') or ''}\x1f{r.get('tool_input_json') or ''}\x1e"
                     .encode("utf-8", "surrogatepass"))
        if r["typed_by_developer"] and r["copy_of"] is None:
            t = " ".join(((r["command"] if r["kind"] == "command" and r.get("command") else r["content"]) or "").split())
            if t:
                typed.append(_digest(t))
                chars += len(t)
        elif r["actor"] == "agent" and r["turn_type"] == "assistant_response":
            t = " ".join((r["content"] or "").split())
            if t:
                texts.add(_digest(t))
        elif r["actor"] == "agent" and r["turn_type"] == "tool_use":
            tools.add(_digest(f"{r['tool_name'] or ''}\x1f{r['tool_input_json'] or r['content'] or ''}"))
    return {"typed": typed, "chars": chars, "texts": sorted(texts), "tools": sorted(tools),
            "whole": int.from_bytes(whole.digest(), "big", signed=True)}


def summary_job(args: tuple) -> dict:
    """One worker's sessions read by the readers their rows are read by, from their files as fetched, summarized
    (summarize) with their session id (`sid`): by session key; a session with no file, no reader or no rows has
    none."""
    source, sessions = args
    out = {}
    for row, files in sessions:
        if not files:
            continue
        session = session_fields(source, row, files)
        try:
            reader = formats.READERS.get(row.get("_reader") or "") or formats.BY_SOURCE.get(source)
            got = reader(files, session) if reader else events.for_session(row.get("agent"), files, session)
        except Exception:   # as extract_job records it, the session has no rows
            continue
        if got and got[0]:
            out[f"{source}:{row['source_id']}"] = {**summarize(got[0]), "sid": session.get("session_id")}
    return out


# -- views: what the scan reads -------------------------------------------------------------------------------
def string_columns(schema: pa.Schema) -> list[str]:
    return [f.name for f in schema if pa.types.is_string(f.type) or pa.types.is_large_string(f.type)]


UNESCAPE = re.compile(r"\\(\\|n|r)")


def encode(v: str | None) -> str:
    """A cell as a line of its view: empty for None, `\\e` for an empty string, else its text with only a backslash, a
    line break and a carriage return escaped."""
    if v is None:
        return ""
    if v == "":
        return "\\e"
    return v.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")


def decode(line: str) -> str | None:
    if line == "":
        return None
    if line == "\\e":
        return ""
    return UNESCAPE.sub(lambda m: {"\\": "\\", "n": "\n", "r": "\r"}[m[1]], line)


def view_parts(path: str):
    """A conversations file's free text as it reads decoded, a cell a line (encode), PART_ROWS rows a part: the build
    stages these and the check reads a published file the same way, so both scan the same text."""
    pf = pq.ParquetFile(path)
    for batch in pf.iter_batches(columns=list(SCANNED), batch_size=PART_ROWS):
        values = [batch.column(c).to_pylist() for c in SCANNED]
        yield [encode(v) for cells in zip(*values) for v in cells]


NESTED = 3  # how deep a string that is itself JSON is read as JSON again


def json_strings(value, depth: int = 0):
    """Every string value of a JSON value (not its keys), in order. A string that is itself a JSON object or list (a
    tool's arguments stored as text: Codex's `arguments`, OpenHands' `extras`) is given whole, then as its own
    strings, to a depth of NESTED: inside it, quotes are escaped once more, and a scan would miss what they quote."""
    if isinstance(value, str):
        yield value
        t = value.lstrip()
        if depth < NESTED and t[:1] in ("{", "[") and len(t) > 1:
            try:
                inner = json.loads(t)
            except ValueError:
                return
            if isinstance(inner, (dict, list)):
                yield from json_strings(inner, depth + 1)
    elif isinstance(value, dict):
        for v in value.values():
            yield from json_strings(v, depth)
    elif isinstance(value, list):
        for v in value:
            yield from json_strings(v, depth)


WHOLE_BYTES = 512 << 20   # a JSON document read whole up to this size; a larger one a line at a time
_TOKEN = re.compile(rb'\\.|"')   # an escape pair, or a quote


def _literals(line: bytes):
    """A line's JSON string literals, in one pass: a quote outside an escape pair opens one or closes it, and one the
    line leaves open is dropped. A pattern for whole literals tried again from each later quote of a line that ends
    inside a string took quadratic time: 13.5 s on a 368 KB line of escaped quotes (the partner's review of rc10)."""
    start = None
    for m in _TOKEN.finditer(line):
        if m.group(0) == b'"':
            if start is None:
                start = m.start()
            else:
                yield line[start:m.end()]
                start = None


def decoded_lines(source, unread: list | None = None):
    """A JSON text's strings as they read decoded, a string a line (encode). `source` is a path or the text itself.
    One JSON document is read whole (leniently, as facts reads Gemini CLI's) up to WHOLE_BYTES; anything else a line
    at a time, each line leniently. A line that is no JSON value gives its string literals, each read as JSON, so one
    bad line (or a bad first line) loses at most a string cut by its end. Lines that are no JSON value, blank ones
    aside, are counted in `unread`."""
    from .facts import _lenient

    def by_line(lines):
        missed = 0
        for line in lines:
            if not line.strip():
                continue
            try:
                value = _lenient(line)
            except ValueError:
                # No JSON value on its own (a record over several lines, a pretty-printed document's line): each string
                # literal on it read as JSON, so its text still reads decoded. A 760 MB export of 775 lines, 766 of
                # them no value, stopped the full build of 2026-10-04 when such a file was refused instead.
                missed += 1
                for literal in _literals(line):
                    try:
                        text = json.loads(literal)
                    except ValueError:
                        continue
                    yield from (encode(_clean(s)) for s in json_strings(text) if s)
                continue
            yield from (encode(_clean(s)) for s in json_strings(value) if s)
        if unread is not None and missed:
            unread.append(missed)

    if isinstance(source, Path):
        # JSON lines (its first line is JSON), or anything over WHOLE_BYTES, a line at a time: a copy's export is
        # 3.3 GB.
        with open(source, "rb") as fh:
            first = fh.readline()
        try:
            json.loads(first)
            lines = True
        except ValueError:
            lines = source.stat().st_size > WHOLE_BYTES
        if lines:
            with open(source, "rb") as fh:
                yield from by_line(fh)
            return
        raw = source.read_bytes()
    else:
        raw = source.encode("utf-8", errors="surrogateescape")
    try:
        value = _lenient(raw)
    except ValueError:
        value = None
    if value is not None:
        yield from (encode(_clean(s)) for s in json_strings(value) if s)
        return
    yield from by_line(raw.split(b"\n"))


def strings(path: str):
    """Every string cell of a file, for the check's look for values naming the run machine."""
    pf = pq.ParquetFile(path)
    cols = string_columns(pf.schema_arrow)
    for batch in pf.iter_batches(columns=cols, batch_size=PART_ROWS):
        for c in cols:
            yield from (v for v in batch.column(c).to_pylist() if v)


def _stage_one(job: tuple) -> tuple[list[dict], Counter]:
    """One file's view parts as plain text files to scan, each masked for private keys by their structure as it is
    staged; the keys masked."""
    i, src, stage_dir = job
    staged, keys = [], Counter()
    for k, lines in enumerate(view_parts(src)):
        p = Path(stage_dir) / f"{i:04d}-{k:06d}.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        out = []
        for line in lines:   # a cell at a time, as hfcopy_build._write_view: a key's header in one cell and an END in
            if scan.PEM_HEADER.search(line) or scan.PEM_END.search(line):   # another made one mask over several cells
                line, counts = scan.redact(line, [], pii=False)
                keys.update(counts["secrets"])
            out.append(line)
        p.write_text("\n".join(out) + "\n", encoding="utf-8", errors="surrogateescape")
        staged.append({"path": p, "src": src, "start": k * PART_ROWS})
    return staged, keys


def stage_views(files: list[str], stage_dir: Path, pool=None) -> tuple[list[dict], Counter]:
    """Every file's view parts, staged (_stage_one); and the private keys masked as they were staged."""
    jobs = [(i, src, str(stage_dir)) for i, src in enumerate(sorted(files))]
    staged, keys = [], Counter()
    for got, k in (pool.map(_stage_one, jobs) if pool is not None else map(_stage_one, jobs)):
        staged += got
        keys.update(k)
    return staged, keys


def _keys_in(src: str, start: int, n: int) -> list[str]:
    t = pq.read_table(src, columns=["session_key"])
    return t.column("session_key").slice(start, n).to_pylist()


def feedback(found: dict[str, list[dict]], staged: list[dict]) -> dict[str, list[dict]]:
    """The findings of the views' scan, by the session whose row held them: secrets of six characters or more (a
    shorter one is placed only inside its own match, which a session's files may not hold the same way)."""
    by_path = {str(s["path"].resolve()): s for s in staged}
    out: dict[str, list[dict]] = defaultdict(list)
    for path, findings in found.items():
        s = by_path.get(path)
        if s is None:
            continue
        keys = _keys_in(s["src"], s["start"], PART_ROWS)
        for f in findings:
            row = ((f.get("line") or 0) - 1) // len(SCANNED)
            if len(scan.literal_of(f)) >= 6 and 0 <= row < len(keys):
                out[keys[row]].append({**f, "decoded": True, "gitleaks_decoded": bool(f.get("decoded"))})
    return out


# A run of letters, digits and `_.-` in a secret: each such character has only the forms escape_tolerant gives it, as
# written, `\\u00XX` or `%XX`, so a line with neither `%` nor `\\u` holds the secret only if it holds the run as written.
_PLAIN_RUN = re.compile(r"[A-Za-z0-9_.-]{8,}")


def mask_files(findings: list[dict], paths: list[str]) -> dict[str, list[dict]]:
    """Of a session's staged files, those holding a finding's secret, as written or with escapes (`\\/`, `\\uXXXX`,
    `%XX`); each with the findings it holds, one copy per line holding it, so a large file masked a line at a time
    (scan.redact_lines) places each where it is. A secret over several lines is left to the scan of the files
    themselves (pem_spans finds keys by their structure). Findings of one secret are looked for once (scan._key, as
    redact() places them): gitleaks reports every occurrence, and a secret found 400 times took 36 s on a 20,000-line
    file looked for once per finding, 0.1 s looked for once (review of rc7, 2026-10-04)."""
    one: dict[tuple, dict] = {}
    encoded: set[tuple] = set()   # a secret any of whose findings gitleaks decoded is looked for in encoded runs too
    for f in findings:
        k = scan._key(f)
        one.setdefault(k, f)
        if f.get("gitleaks_decoded"):
            encoded.add(k)
    probes = []
    for k, f in one.items():
        lit = scan.literal_of(f)
        if "\n" in lit:
            continue
        runs = _PLAIN_RUN.findall(lit)
        probes.append((f, scan.escape_tolerant(lit), lit, max(runs, key=len) if runs else None, k in encoded))
    out = {}
    for p in paths:
        hits = []
        with open(p, "rb") as fh:
            for n, raw in enumerate(fh, 1):
                line = raw.decode("utf-8", errors="surrogateescape")
                plain = "%" not in line and "\\u" not in line   # no escape that can hide a letter or digit
                for f, rx, lit, run, encoded in probes:
                    if encoded and scan._encoded_spans(line, lit):
                        pass   # gitleaks decoded it from a base64 or hex run this line holds
                    elif plain and run is not None and run not in line:
                        continue   # the line cannot hold the secret in any form escape_tolerant reads
                    elif not rx.search(line):
                        continue
                    if scan.place(line, f):
                        hits.append({**f, "line": n, "end_line": n})
        if hits:
            out[p] = hits
    return out


def rebuild(src: str, parts: list[Path], dest: Path) -> int:
    """The file with each cell whose view line changed read back from the masked line (then its row's word and
    character counts); the cells changed. A file with no change is moved as it is."""
    changed: dict[tuple[int, str], str | None] = {}
    n = 0
    width = len(SCANNED)
    for lines, part in zip(view_parts(src), parts):
        masked = part.read_text(encoding="utf-8", errors="surrogateescape").split("\n")
        if len(masked) != len(lines) + 1 or masked[-1] != "":   # a mask never adds or removes a line
            raise RuntimeError(f"build refused: a masked part of {Path(src).name} has {len(masked) - 1} lines, not {len(lines)}")
        for i, (old, new) in enumerate(zip(lines, masked)):
            if new != old:
                changed[((n + i) // width, SCANNED[(n + i) % width])] = decode(new)
        n += len(lines)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not changed:
        Path(src).replace(dest)
        return 0
    table = pq.read_table(src)
    values = {c: table.column(c).to_pylist() for c in list(SCANNED) + ["word_count", "char_count"]}
    for (r, c), v in changed.items():
        values[c][r] = v
    for r in {r for r, _ in changed}:
        text = values["content"][r] or ""
        values["word_count"][r], values["char_count"][r] = len(text.split()), len(text)
    for c, v in values.items():
        i = table.schema.get_field_index(c)
        table = table.set_column(i, table.schema.field(i), pa.array(v, type=table.schema.field(i).type))
    pq.write_table(table, str(dest), compression="zstd", row_group_size=GROUP_ROWS)
    Path(src).unlink()
    return len(changed)


def counts(paths: list[Path]) -> Counter:
    c = Counter()
    for p in paths:
        c["rows"] += pq.ParquetFile(p).metadata.num_rows
    return c
