"""Build a version: every row as published, redacted, in Parquet, one subset per source; the copied datasets; the card.

    data/build/<version>/
        data/<source>/part-NNN-NNNNN.parquet  one row per session; a duplicate's row has no files
        copied/<dataset>/...                  copied datasets, as they are (secret spans redacted)
        changelog.json
        README.md                             the dataset card

**The published text is what is scanned.** Each file is first written out as it would be published (home-path
names and emails masked), then gitleaks scans that text; whatever it finds is masked and the changed files are
scanned again, until a scan finds nothing. A file still holding a finding after four rounds stops the build.
Scanning the raw files alone is not enough: gitleaks' generic rule depends on the text around a match, and a match
can appear only once nearby text is masked (measured on 2026-10-03: a 32-character key in a transcript that the
raw file's scan did not flag was flagged once four home paths on its line were masked). Paths on the run machine
never reach a published row.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from . import conv_build, copied_sessions, dedup, repos, scan
from .config import Config
from .ledger import Ledger
from .model import LABELS, canonical_agent, coerce
from .facts import _iso
from .util import process_pool

STR = pa.large_string()
SCHEMA = pa.schema([
    ("session_key", pa.string()),
    ("source", pa.string()), ("source_id", pa.string()), ("session_id", pa.string()), ("checkpoint_pk", pa.string()),
    ("strategy", pa.string()), ("dedup_key", pa.string()), ("found_url", pa.string()),
    ("found_commit", pa.string()), ("fetched_at", pa.string()), ("collector_version", pa.string()),
    ("content_sha256", pa.string()), ("repo", pa.string()), ("repo_url", pa.string()), ("repo_license", pa.string()),
    ("repo_language", pa.string()), ("agent", pa.string()), ("agent_version", pa.string()),
    ("models", pa.list_(pa.string())), ("started_at", pa.string()), ("ended_at", pa.string()),
    ("file_format", pa.string()), ("n_user_entries", pa.int64()), ("n_user_messages", pa.int64()),
    ("n_agent_messages", pa.int64()), ("n_tool_calls", pa.int64()), ("has_tool_results", pa.bool_()),
    ("has_timestamps", pa.bool_()), ("n_conversation_rows", pa.int64()), ("n_subagent_rows", pa.int64()),
    ("bytes", pa.int64()), ("duplicate_of", pa.string()),
    ("also_in", pa.list_(pa.struct([("source", pa.string()), ("source_id", pa.string()), ("found_url", pa.string())]))),
    ("redactions", pa.struct([("secrets", pa.int64()), ("emails", pa.int64()), ("home_users", pa.int64())])),
    ("source_facts", STR),
    ("files", pa.list_(pa.struct([("path", pa.string()), ("bytes", pa.int64()), ("sha256", pa.string()),
                                  ("content", STR)]))),
])
SHARD_BYTES = 1 << 30  # a new part file after about 1 GB of content
GROUP_BYTES = 128 << 20  # a new row group after about 128 MB
ROUNDS = 8  # a chain of placeholder private-key headers loses about half its headers a round
DECODE_BATCH_BYTES = 8 << 30  # JSON text decoded and scanned at a time (build._decoded)
SWEEP_MIN = 8  # a secret found this long or longer is masked wherever any staged file holds it (build._sweep)


def version_name(build_root: Path) -> str:
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    name, n = day, 1
    while (build_root / name).exists():
        n += 1
        name = f"{day}.{n}"
    return name


# -- an incremental version: only what is new or changed since the version before -----------------------
# What a session's published row and rows are made from is fingerprinted before any of it is read. A session whose
# fingerprint has not moved since the version before is carried over unread: its rows, its session row, its files.
# With redaction at "most", nothing found in one session's text changes another's files (feed_back runs only when
# STRICT), so a session processed alone gives what a full build gives it. READ_RULES is in every fingerprint: change
# it with any change to the readers, the rules or the masking that would change rows already built, and the next
# version processes every session again (a full build).
READ_RULES = "2026-10-07d"   # d: a swarm coordinator's handoff command, the masker's two fixes, files staged by their own name; c: a turn kept without its text is the developer's (SpecStory); b: legacy Codex forks
INPUTS = "inputs.parquet"   # each session's fingerprint and also_in, kept with the build, never published


def session_key(row: dict) -> str:
    return f"{row['source']}:{row['source_id']}"


def dataset_label(row: dict) -> str:
    """A copied session's dataset, named as the copy names it (hfcopy_build.stage)."""
    from .util import safe_name

    name = row["source"]
    return f"{name}/{safe_name((row.get('extra') or {}).get('dataset') or '')}" if name == "dataclaw" else name


def inputs_fp(row: dict, skip: tuple = ("also_in",)) -> str:
    """A session's inputs, fingerprinted: its stored labels (also_in apart: a copy found elsewhere changes only the
    session's own row, patched in place), its facts, its files' paths and digests, a copy's published paths and the
    names it is read by, and READ_RULES."""
    payload = {"rules": READ_RULES, "labels": {k: row.get(k) for k in LABELS if k not in skip},
               "extra": {k: v for k, v in (row.get("extra") or {}).items() if k not in ("oids", "blob", "facts_version")},
               "files": sorted([f.get("path"), f.get("sha256")] for f in row.get("files") or []),
               "published": row.get("_published") or [], "read": [n for n, _ in row.get("_read") or []],
               "session": row.get("_session") or {}, "reader": row.get("_reader")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _also(row: dict) -> str:
    return json.dumps(row.get("also_in") or [], sort_keys=True, ensure_ascii=False)


def write_inputs(path: Path, rows_by_source: dict[str, list[dict]]) -> None:
    rows = [r for rs in rows_by_source.values() for r in rs]
    pq.write_table(pa.table({"session_key": [session_key(r) for r in rows], "fp": [inputs_fp(r) for r in rows],
                             "also_in": [_also(r) for r in rows]}), str(path))


def read_inputs(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    t = pq.read_table(path).to_pydict()
    return dict(zip(t["session_key"], t["fp"])), dict(zip(t["session_key"], t["also_in"]))


# -- near-copies: each kept session's conversation in digests, kept with the build ----------------------------------
SUMMARIES = "summaries.parquet"   # never published


def content_fp(row: dict) -> str:
    """What a session's conversation is read from: its inputs, less whose copy it is."""
    return inputs_fp(row, ("also_in", "duplicate_of"))


def write_summaries(path: Path, summaries: dict[str, dict]) -> None:
    keys = sorted(summaries)
    pq.write_table(pa.table({"session_key": keys, "fp": [summaries[k]["fp"] for k in keys],
                             "typed": pa.array([summaries[k]["typed"] for k in keys], pa.list_(pa.int64())),
                             "chars": pa.array([summaries[k]["chars"] for k in keys], pa.int64()),
                             "texts": pa.array([summaries[k]["texts"] for k in keys], pa.list_(pa.int64())),
                             "tools": pa.array([summaries[k]["tools"] for k in keys], pa.list_(pa.int64())),
                             "whole": pa.array([summaries[k].get("whole") for k in keys], pa.int64()),
                             "sid": pa.array([summaries[k].get("sid") for k in keys], pa.string())}),
                   str(path), compression="zstd")


def read_summaries(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    t = pq.read_table(path).to_pydict()
    none = [None] * len(t["session_key"])   # a version's summaries from before `whole` and `sid`: read again
    return {k: {"fp": fp, "typed": ty, "chars": ch, "texts": tx, "tools": to, "whole": wh, "sid": si}
            for k, fp, ty, ch, tx, to, wh, si in zip(t["session_key"], t["fp"], t["typed"], t["chars"], t["texts"],
                                                     t["tools"], t.get("whole", none), t.get("sid", none))}


def scope_of(row: dict) -> str:
    """Where a session's near-copies are looked for: its repository; a copied export's session with none, its export."""
    return row.get("repo") or f"{row['source']}:{row['source_id'].split(':', 1)[0]}"


def _raw_files(row: dict) -> list[tuple[str, Path]]:
    """A session's files as fetched, by the names its reader reads them by."""
    if "_read" in row:
        return [(n, Path(p)) for n, p in row["_read"]]
    return [(f["path"], Path(f["local"])) for f in row.get("files") or []]


def near_copy_pass(rows_by_source: dict[str, list[dict]], base: Path | None, pool, procs: int, log=print,
                   places: dict | None = None) -> tuple[dict[str, dict], Counter, int]:
    """Near-copies made duplicates (dedup.near_copies), then copies under one session id (dedup.same_session_copies),
    before any session is fingerprinted: a session that became one, or stopped being one, is then read again as any
    session whose duplicate_of moved. Each kept session's summary is the version before's where its content has not
    moved, else read now from its files as fetched, by the readers its rows are read by. Returns the summaries, the
    near-copies by pair of sources, how many were read, and the copies under one session id by pair of sources."""
    every = [r for rows in rows_by_source.values() for r in rows]
    stored = read_summaries(base / SUMMARIES) if base is not None else {}
    summaries, todo = {}, {}
    for r in every:
        if r.get("duplicate_of"):
            continue
        k, fp = session_key(r), content_fp(r)
        if k in stored and stored[k]["fp"] == fp and stored[k].get("whole") is not None:
            summaries[k] = stored[k]
        else:
            todo[k] = (r, fp)
    jobs = []
    for source in sorted({r["source"] for r, _ in todo.values()}):
        chunk_rows = [r for r, _ in todo.values() if r["source"] == source]
        jobs += [(source, [(r, _raw_files(r)) for r in chunk]) for chunk in _balanced(chunk_rows, procs * 4)]
    t0 = time.time()
    for got in (pool.map(conv_build.summary_job, jobs) if jobs else []):
        for k, s in got.items():
            summaries[k] = {**s, "fp": todo[k][1]}
    pairs = dedup.near_copies(every, summaries, scope_of, places)
    log(f"build: near-copies: {sum(pairs.values()):,} sessions hold a conversation a fuller one of their scope holds, "
        f"now its duplicates; pairs {dict(sorted(pairs.items()))}; conversations summarized {len(todo):,} in "
        f"{time.time() - t0:.0f}s, {len(summaries) - sum(1 for k in todo if k in summaries):,} kept from before")
    same = dedup.same_session_copies(every, summaries, places)
    log(f"build: {sum(same.values()):,} sessions hold the session id and the whole conversation of another kept one, "
        f"now its duplicates; pairs {dict(sorted(same.items()))}")
    return summaries, pairs, len(todo), same


def _write_like(table: pa.Table, original: Path, out: Path) -> None:
    """Written as the original was: its compression and its row groups' size."""
    md = pq.ParquetFile(original).metadata
    codec = md.row_group(0).column(0).compression.lower() if md.num_row_groups else "zstd"
    rows = max(md.row_group(i).num_rows for i in range(md.num_row_groups)) if md.num_row_groups else None
    pq.write_table(table, str(out), compression=codec, row_group_size=rows)


def restage_for(base: Path, entries: list[tuple[str, dict]], rows_by_source: dict[str, list[dict]],
                process: set[str]) -> set[str]:
    """The copied datasets an incremental build stages again (by label): one holding a kept session this build reads
    (its rows are read from the staged copy); and one new since the version before, at another revision, or missing
    from its copied folder: a dataset's files are published as released, whatever became of its sessions."""
    from .util import safe_name

    before = json.loads((base / "changelog.json").read_text()).get("copied") or {}
    out = {dataset_label(r) for rows in rows_by_source.values() for r in rows
           if "_read" in r and not r.get("duplicate_of") and session_key(r) in process}
    for name, entry in entries:
        if not entry.get("copied") or not entry.get("id"):
            continue
        label = f"{name}/{safe_name(entry['id'])}" if name == "dataclaw" else name
        had = before.get(label) or {}
        if (not had.get("copied") or had.get("revision") != entry.get("revision")
                or not (base / "copied" / name / safe_name(entry["id"])).exists()):
            out.add(label)
    return out


def _merge(base: Path, out: Path, drop: set[str], also_of: dict[str, list], copied_keep: set[str]) -> Counter:
    """The version before's files into this one, where this build's own parts are written already: a file holding
    no session in `drop` is the same file (a hard link); one that does is written again without their rows; a
    session row whose also_in moved (`also_of`: key -> its list now) is written again with it. The copied datasets
    in `copied_keep` are linked whole (copied/matches.parquet is computed again)."""
    counts: Counter = Counter()
    for table in ("data", "conversations", "conversations_subagents"):
        for f in sorted((base / table).rglob("*.parquet")) if (base / table).exists() else []:
            rel = f.relative_to(base)
            dest = out / rel
            if dest.exists():
                raise RuntimeError(f"build refused: {rel} is both in this build and in the version before")
            keys = pq.read_table(f, columns=["session_key"])["session_key"].to_pylist()
            if drop.issuperset(keys):   # every session read again: nothing of it to carry, nor to read (a full rebuild)
                counts[f"{table}: rows dropped"] += len(keys)
                counts[f"{table}: files emptied"] += 1
                continue
            hit = not drop.isdisjoint(keys)
            patch = table == "data" and not also_of.keys().isdisjoint(keys)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not hit and not patch:
                os.link(f, dest)
                counts[f"{table}: files linked"] += 1
                continue
            t = pq.read_table(f)
            if hit:
                t = t.filter(pa.array([k not in drop for k in keys]))
                counts[f"{table}: rows dropped"] += len(keys) - t.num_rows
            if patch:
                ks = t["session_key"].to_pylist()
                field = t.schema.field("also_in")
                vals = [also_of[k] if k in also_of else v for k, v in zip(ks, t["also_in"].to_pylist())]
                t = t.set_column(t.schema.get_field_index("also_in"), field, pa.array(vals, field.type))
                counts["data: also_in patched"] += sum(k in also_of for k in ks)
            if t.num_rows:
                _write_like(t, f, dest)
                counts[f"{table}: files written again"] += 1
            else:
                counts[f"{table}: files emptied"] += 1
    copied = base / "copied"
    for name_dir in sorted(p for p in copied.iterdir() if p.is_dir()) if copied.exists() else []:
        for slug_dir in sorted(p for p in name_dir.iterdir() if p.is_dir()):
            if (f"{name_dir.name}/{slug_dir.name}" if name_dir.name == "dataclaw" else name_dir.name) not in copied_keep:
                continue
            for f in sorted(slug_dir.rglob("*")):
                if f.is_file():
                    dest = out / f.relative_to(base)
                    if dest.exists():
                        raise RuntimeError(f"build refused: {dest.relative_to(out)} is both staged again and carried over")
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.link(f, dest)
                    counts["copied: files linked"] += 1
    return counts


def _matches_all(ledger: Ledger, out: Path, copied_labels: set[str]) -> int:
    """copied/matches.parquet over every copied dataset of the version, staged again or carried over: a scraped
    session found this week can be a row of a dataset copied weeks ago."""
    from . import hfcopy_build
    from .util import safe_name

    items = []
    for name, entry in hfcopy_build.entries(ledger):
        if entry.get("id") and (f"{name}/{safe_name(entry['id'])}" if name == "dataclaw" else name) in copied_labels:
            items += [{"name": name, "entry": entry, "file": f} for f in entry.get("files") or []]
    (out / "copied" / "matches.parquet").unlink(missing_ok=True)
    return hfcopy_build.matches_in(items, out)


def _final_counts(out: Path, results: dict) -> tuple[dict, dict]:
    """Per source, the counts the changelog gives, read from the version's own tables: its session rows (and of them
    the duplicates, the published files, what was masked) and its conversation rows."""
    import pyarrow.compute as pc

    scraped, tables = {}, {}
    for src_dir in sorted(p for p in (out / "data").iterdir() if p.is_dir()):
        src = src_dir.name
        n = {"rows": 0, "files": 0, "duplicates": 0, "secrets": 0, "emails": 0, "home_users": 0}
        kept, without = 0, Counter()
        for f in sorted(src_dir.glob("*.parquet")):
            t = pq.read_table(f, columns=["duplicate_of", "redactions", "files", "source_facts"])
            n["rows"] += t.num_rows
            n["duplicates"] += t.num_rows - t["duplicate_of"].null_count
            n["files"] += pc.sum(pc.list_value_length(t["files"])).as_py() or 0
            for k in ("secrets", "emails", "home_users"):
                n[k] += pc.sum(pc.struct_field(t["redactions"], k)).as_py() or 0
            mask = pc.is_null(t["duplicate_of"])
            kept += pc.sum(mask.cast(pa.int64())).as_py() or 0
            for facts in pc.filter(t["source_facts"], mask).to_pylist():
                why = json.loads(facts or "{}").get("conversations_error")
                if why:
                    without[why] += 1
        rows_in = lambda table: sum(pq.ParquetFile(f).metadata.num_rows for f in (out / table / src).glob("*.parquet"))
        scraped[src] = {"rows": n["rows"], "files": n["files"], "duplicates": n["duplicates"],
                        "secrets": {"masked": n["secrets"]} if n["secrets"] else {}, "emails": n["emails"],
                        "home_users": n["home_users"], "new": (results.get(src) or {}).get("new", 0),
                        "changed": (results.get(src) or {}).get("changed", 0)}
        tables[src] = {"rows": rows_in("conversations"), "subagent_rows": rows_in("conversations_subagents"),
                       "sessions": kept, **({"sessions_without_rows": dict(without)} if without else {})}
    return scraped, tables


# -- staging: each file as it would be published ------------------------------------------------------
def _stage(args: tuple) -> tuple[str, dict, bool]:
    """A raw file written as it would be published: (staged path, counts, whether it is UTF-8). Only UTF-8 text is
    published: the scan cannot read other encodings or binary files, so such a file is left out (and listed)."""
    raw, staged, pii, expected = args
    data = Path(raw).read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        # A fetch that stopped partway can leave a row pointing at bytes written for its next version.
        raise RuntimeError(f"build refused: a raw file no longer matches its row's digest ({Path(raw).name}); "
                           f"run the fetch again")
    text, replaced = as_text(data)
    if text is None:
        return staged, {"secrets": {}, "emails": 0, "home_users": 0}, False
    try:
        out, counts = scan.redact(text, [], pii=pii, json_like=scan.json_like(Path(staged).name))
    except RuntimeError as e:
        # A file whose masking cannot keep its JSON valid is left out, with why, as a file that is not text is: one
        # file must not stop a build of seventy thousand (a home-path mask broke an escape in two, 2026-10-04).
        return staged, {"secrets": {}, "emails": 0, "home_users": 0, "left_out": f"masking it: {str(e)[:160]}"}, False
    Path(staged).write_bytes(out.encode("utf-8"))
    return staged, {**counts, "invalid_utf8_replaced": replaced}, True


def _staged_counts(counts: dict) -> dict:
    """A staged file's counts as the build keeps them: what staging masked (emails, home folders, runs after a key's
    prefix) and a Counter the scans add their secrets to. Staging's own secrets, the key runs, were dropped here until
    2026-10-06: the line was written before staging found any."""
    return {**counts, "secrets": Counter(counts.get("secrets") or {})}


def collected(row: dict) -> bool:
    """Whether a stored row is one its source collects now: rows stored before a source's filter narrowed are left
    out of a build (SpecStory's AppleDouble and JSON files, 2026-10-05)."""
    if row.get("source") == "specstory":
        from .sources.specstory import in_history

        return any(in_history(f.get("path") or "") or in_history(f".specstory/history/{f.get('path') or ''}")
                   for f in row.get("files") or [])
    return True


def staged_name(sha: str, name: str) -> str:
    """A file's staged name: its digest, with the kind its own name gives (kind_of). The name, not where the file is
    stored: Entire's files fetched again are stored under a blob digest with no extension (206 JSON files of the
    ledger on 2026-10-07), and staged by that they were masked as plain text, where a mask can cross JSON strings."""
    return f"{sha}{kind_of(name)}.txt"


def stage_jobs(rows_by_source: dict[str, list[dict]], stage: Path) -> tuple[dict[str, str], list[tuple]]:
    """The kept sessions' distinct files, by digest (where each is stored), and their staging jobs (_stage). Staged
    under the digest, with what the file is (`.jsonl`, `.json`, else nothing) kept in the name (staged_name): masks in
    JSON text stay inside strings (scan.json_spans)."""
    raw, named = {}, {}
    for rows in rows_by_source.values():
        for r in rows:
            for f in (r.get("files") or []) if not r.get("duplicate_of") else []:
                raw[f["sha256"]] = f["local"]
                named.setdefault(f["sha256"], f["path"])
    return raw, [(path, str(stage / staged_name(sha, named[sha])), True, sha) for sha, path in raw.items()]


def kind_of(path: str) -> str:
    """`.jsonl` or `.json` for a file of JSON text (scan.json_like), else nothing: kept in a staged file's name."""
    name = Path(path).name.lower()
    if ".jsonl" in name:
        return ".jsonl"
    return ".json" if name.endswith(".json") else ""


def as_text(data: bytes) -> tuple[str | None, int]:
    """A file's bytes as text: (text, invalid sequences replaced), or (None, 0) when it is not text.

    - UTF-8, as it is. NUL bytes are kept: NUL is valid UTF-8, and gitleaks reads past it (tests/check_scanner.py).
      16 of the 9,183 sample files hold some, in runs: a zero-filled tail a crash left in a transcript, or bytes a
      tool printed.
    - UTF-8 but for a few broken sequences (SpecStory cuts long text inside a multi-byte character: 15 sample
      files, one or two sequences each): those replaced by U+FFFD, and counted.
    - UTF-16 with a byte-order mark: decoded (counted as -1, "converted").
    - Anything else is left out, since the scan could not read it: more than one broken sequence per thousand bytes
      (another encoding), or NULs alternating with other bytes (more than 5% of the file in NUL runs of one to three
      bytes: UTF-16 without a mark, whose secrets the scan would not see, or a binary file).
    """
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16"), -1
        except UnicodeDecodeError:
            return None, 0
    if b"\x00" in data:
        short = sum(len(m) for m in re.findall(rb"(?<!\x00)\x00{1,3}(?!\x00)", data))
        if short > len(data) // 20:
            return None, 0
    try:
        return data.decode("utf-8"), 0
    except UnicodeDecodeError:
        text = data.decode("utf-8", errors="replace")
        replaced = text.count("\ufffd") - data.count(b"\xef\xbf\xbd")
        if replaced > max(16, len(data) // 1000):
            return None, 0
        return text, replaced


STREAM_BYTES = 32 << 20  # a JSON-lines file larger than this is masked a line at a time
# Most secrets gone is the bar, not every one (a design decision of 2026-10-04: the sources are public already, and a
# build stopped over one string loses every other session's masking): a secret that cannot be placed is left where it is, counted, and the build goes on.
# ERRATA_REDACTION=strict stops the build there instead, as every build up to rc13 did.
STRICT = os.environ.get("ERRATA_REDACTION") == "strict"


def _mask(args: tuple) -> tuple[str, dict]:
    """A staged file's findings masked in it; the counts, with `left`: the findings that could not be placed, left
    where they are (STRICT raises instead)."""
    staged, findings = args
    path = Path(staged)
    json_like = scan.json_like(path.name)
    try:
        return staged, _masked(path, findings, json_like)
    except RuntimeError as e:   # named, so a stopped build says which file (staged under its content's digest)
        if STRICT:
            raise RuntimeError(f"{e} [in {path.name}]") from e
    # One secret at a time, so the one that cannot be placed is left alone and the rest are masked. A large file is
    # read again for each: with many secrets it is left as it is.
    counts = {"secrets": Counter(), "left": 0}
    by_key: dict[tuple, list[dict]] = {}
    for f in findings:
        by_key.setdefault(scan._key(f), []).append(f)
    if path.stat().st_size > STREAM_BYTES and len(by_key) > 16:
        counts["left"] = len(findings)
        return staged, counts
    for fs in by_key.values():
        try:
            counts["secrets"].update(_masked(path, fs, json_like)["secrets"])
        except RuntimeError:
            counts["left"] += len(fs)
    return staged, counts


def _masked(path: Path, findings: list[dict], json_like: bool) -> dict:
    """The findings masked in the file, written back, and the counts; where one cannot be, raises, the file unchanged."""
    if json_like and path.stat().st_size > STREAM_BYTES and scan.jsonl(path):
        try:
            return scan.redact_lines(path, path, findings)
        finally:
            Path(str(path) + ".part").unlink(missing_ok=True)   # redact_lines' own, left by a raise inside its loop
    text = path.read_bytes().decode("utf-8", errors="surrogateescape")
    out, counts = scan.redact(text, findings, pii=False, json_like=json_like)
    path.write_bytes(out.encode("utf-8", errors="surrogateescape"))
    return counts


def until_clean(paths: list[Path], workdir: Path, pool: ProcessPoolExecutor, procs: int, log=print,
                seen: dict | None = None) -> dict[str, Counter]:
    """Scan the staged files, mask what is found, scan the changed ones again, until nothing is found; secrets masked
    per file (by rule). `seen` collects every round's findings, by file."""
    masked: dict[str, Counter] = {}
    left: dict[str, int] = {}   # findings that could not be placed, by file (not STRICT)
    todo = list(paths)
    for rnd in range(1, ROUNDS + 1):
        found = {p: f for p, f in scan.find_all(todo, workdir, procs, pool).items() if f}
        if seen is not None:
            for p, fs in found.items():
                seen.setdefault(p, []).extend(fs)
        log(f"scan round {rnd}: {len(todo):,} files, {sum(len(f) for f in found.values()):,} findings in {len(found):,}")
        if not found:
            todo = []
            break
        stuck = set()   # nothing masked this round: scanned again, it would give the same
        for staged, counts in pool.map(_mask, list(found.items())):
            masked.setdefault(staged, Counter()).update(counts["secrets"])
            if counts.get("left"):
                left[staged] = counts["left"]
                if not counts["secrets"]:
                    stuck.add(staged)
        todo = [Path(p) for p in found if p not in stuck]
        if not todo:
            break
    else:
        if STRICT:
            raise RuntimeError(f"build refused: {len(todo)} files still hold findings after {ROUNDS} rounds of masking")
    if left or todo:
        log(f"scan: left as they are: {sum(left.values()):,} findings that could not be placed, in {len(left):,} files; "
            f"{len(todo):,} files still giving findings after {ROUNDS} rounds")
    return masked


# -- writing the subsets --------------------------------------------------------------------------------
def _write_part(args: tuple) -> dict:
    """One worker's rows into its own part files, from the staged text; the counts it published."""
    rows, stage_of, utf8_of, counts_of, out_dir, prefix, conv_of = args
    out_dir.mkdir(parents=True, exist_ok=True)
    writer, part, written, batch, batch_bytes = None, 0, 0, [], 0
    stats = {"rows": 0, "files": 0, "secrets": Counter(), "emails": 0, "home_users": 0}

    def flush():
        nonlocal batch, batch_bytes, writer
        if not batch:
            return
        if writer is None:
            writer = pq.ParquetWriter(str(out_dir / f"{prefix}-{part:05d}.parquet"), SCHEMA, compression="zstd")
        writer.write_table(pa.Table.from_pylist(batch, schema=SCHEMA))
        batch, batch_bytes = [], 0

    for row in rows:
        out = coerce({k: row.get(k) for k in LABELS})  # labels stored by a recount bypass Session.row()
        for k in ("started_at", "ended_at"):   # one form, and a stored placeholder (SpecStory's 1970) no time at all
            out[k] = _iso(out[k]) if out[k] else None
        conv = conv_of.get(row["source_id"]) or conv_build.session_fields(row["source"], row, [])
        out.update({k: conv.get(k) for k in ("session_key", "session_id", "checkpoint_pk", "strategy",
                                             "n_conversation_rows", "n_subagent_rows")})
        if conv.get("agent"):   # the transcript's own format's agent, where it contradicts the one recorded
            out["agent"] = conv["agent"]
        if conv_of.get(row["source_id"]) and conv["n_conversation_rows"] is not None:
            out["n_user_messages"] = conv["typed"]  # from the rows: the study's rules for Claude Code (decision 56)
        totals = {"secrets": Counter(), "emails": 0, "home_users": 0}
        files = []
        left_out, replaced = [], {}
        if not row.get("duplicate_of"):
            for f in row.get("files") or []:
                if not utf8_of[f["sha256"]]:
                    left_out.append(f["path"])
                    continue
                data = Path(stage_of[f["sha256"]]).read_bytes()
                files.append({"path": f["path"], "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                              "content": data.decode("utf-8")})
                c = counts_of[f["sha256"]]
                if c.get("invalid_utf8_replaced"):
                    replaced[f["path"]] = c["invalid_utf8_replaced"]
                totals["secrets"].update(c["secrets"])
                totals["emails"] += c["emails"]
                totals["home_users"] += c["home_users"]
        out["files"] = files
        out["redactions"] = {"secrets": sum(totals["secrets"].values()), "emails": totals["emails"],
                             "home_users": totals["home_users"]}
        facts_out = {k: v for k, v in (row.get("extra") or {}).items() if k not in ("oids", "blob", "facts_version")}
        why = {f["path"]: counts_of[f["sha256"]]["left_out"] for f in row.get("files") or []
               if f["path"] in left_out and counts_of.get(f["sha256"], {}).get("left_out")}
        if why:
            facts_out["files_left_out_unmaskable"] = why
            left_out = [p for p in left_out if p not in why]
        if left_out:
            facts_out["files_left_out_not_text"] = left_out
        if conv.get("conversations_error"):
            facts_out["conversations_error"] = conv["conversations_error"]
        if conv.get("typed_fingerprint"):
            facts_out["typed_fingerprint"] = conv["typed_fingerprint"]
        if row.get("_published"):   # a copied dataset's session: its files are published with the copy
            facts_out["published_files"] = row["_published"]
        if replaced:
            facts_out["text_repairs"] = {p: ("converted from UTF-16" if n < 0 else f"{n} invalid UTF-8 sequences replaced")
                                        for p, n in replaced.items()}
        out["also_in"] = row.get("also_in") or []
        out["models"] = row.get("models") or []
        rf = conv.get("row_facts") or {}
        if not rf and row.get("duplicate_of") and all(out.get(k) is None for k in ("n_user_entries", "n_agent_messages",
                                                                                   "n_tool_calls")):
            # A duplicate with no counts stored (an agent facts.py has no parser for) has no rows to fill them from, as a
            # kept session's are: its own files read for them (460 SWE-chat v2 duplicates of version 2026-10-11 had
            # none). Here, after dedup: filled in the stored facts, they made 107 SWE-chat copies outrank Entire's
            # (dedup._rank: has_tool_results first), whose own stored facts have none either.
            rf = conv_build.counts_of(row, _raw_files(row)) or {}
        for k, v in rf.items():   # what the stored facts lack, from the session's rows
            if out.get(k) in (None, []) and v not in (None, []):
                out[k] = v
        # A session's times are its messages' first and last, where they carry times. A recorded start can be a
        # file's own time or a feedback's (SpecStory, OpenHands, DataClaw: 1,585 sessions ended before they started,
        # 2026-10-05). A recorded time that differs is kept in source_facts.
        if rf.get("started_at") and rf.get("ended_at"):
            for k in ("started_at", "ended_at"):
                got = _iso(rf[k])
                if out[k] and out[k] != got:
                    facts_out[f"recorded_{k}"] = out[k]
                out[k] = got
        out["source_facts"] = json.dumps(facts_out, ensure_ascii=False, sort_keys=True)
        size = sum(f["bytes"] for f in files)
        batch.append(out)
        batch_bytes += size
        written += size
        stats["rows"] += 1
        stats["files"] += len(files)
        stats["secrets"].update(totals["secrets"])
        stats["emails"] += totals["emails"]
        stats["home_users"] += totals["home_users"]
        if batch_bytes >= GROUP_BYTES:
            flush()
        if written >= SHARD_BYTES:
            flush()
            writer.close()
            writer, part, written = None, part + 1, 0
    flush()
    if writer is not None:
        writer.close()
    stats["secrets"] = dict(stats["secrets"])
    return stats


def current_rows(cfg: Config, ledger: Ledger, pool) -> tuple[dict[str, list[dict]], Counter, int]:
    """Every session of the version to build, as the build reads them: the collected rows less those removed on
    request, the copied datasets' sessions beside them, one name per agent, and which copy of each session keeps its
    content chosen over all of them (the agreed order: Entire, SWE-chat v2, SpecStory, Aider, DataClaw, OpenHands).
    Returns the rows by source, the duplicates by pair of sources, and how many rows were left out."""
    removed = removals(cfg)
    rows_by_source: dict[str, list[dict]] = {}
    left_out = 0
    for row in ledger.rows():
        if is_removed(row, removed) or not collected(row):
            left_out += 1
            continue
        rows_by_source.setdefault(row["source"], []).append(row)
    for source, rows in copied_sessions.rows(ledger, pool).items():
        kept = [r for r in rows if not is_removed(r, removed)]
        left_out += len(rows) - len(kept)
        rows_by_source[source] = kept
    for rows in rows_by_source.values():   # one name per agent, rows stored before the names were one included
        for r in rows:
            r["agent"] = canonical_agent(r.get("agent"))
    pairs = dedup.assign([r for rows in rows_by_source.values() for r in rows], repos.known(ledger))
    return rows_by_source, pairs, left_out


class _Pool:
    """The build's process pool, started afresh between its stages (renew). A worker keeps the memory of the largest
    task it ran, as Python seldom gives its heap back: the 32 workers that had staged, decoded and read every session
    held so much that writing the sessions after ran a full build out of its 160 GB (2026-10-26). ProcessPoolExecutor's
    own max_tasks_per_child restarts workers too, but a pool with it stalled in a test at Duke (8 tasks a worker;
    1 a worker cost 48 s per 4,000 tasks): a new pool per stage instead."""

    def __init__(self, procs: int):
        self.procs, self._ex = procs, process_pool(procs)

    def renew(self, before: str, log=print) -> None:
        self._ex.shutdown(wait=True)
        self._ex = process_pool(self.procs)
        log(f"build: workers started afresh before {before}; memory: {memory()}")

    def map(self, *args, **kwargs):
        return self._ex.map(*args, **kwargs)

    def submit(self, *args, **kwargs):
        return self._ex.submit(*args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._ex.shutdown(wait=True)


def _rss_kb(pid: int) -> int:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except (OSError, ValueError):
        pass
    return 0


def _descendants(pid: int) -> list[int]:
    out, todo = [], [pid]
    while todo:
        p = todo.pop()
        for task in Path(f"/proc/{p}/task").glob("*") if Path(f"/proc/{p}/task").exists() else []:
            try:
                kids = [int(x) for x in (task / "children").read_text().split()]
            except (OSError, ValueError):
                kids = []
            out += kids
            todo += kids
    return out


def memory() -> str:
    """This process's resident memory and its descendants' (workers, the fork server, gitleaks), from /proc; where
    there is no /proc (macOS), says so."""
    if not Path("/proc/self/status").exists():
        return "not measured here (no /proc)"
    me = os.getpid()
    kids = _descendants(me)
    return (f"this process {_rss_kb(me) / 2**20:.1f} GB, {len(kids)} processes under it "
            f"{sum(_rss_kb(k) for k in kids) / 2**20:.1f} GB")


def version(cfg: Config, ledger: Ledger, results: dict, *, procs: int | None = None, log=print,
            base: Path | None = None, name: str | None = None) -> tuple[Path, dict]:
    """A version of every session; or, with `base` (the version before, built with INPUTS), of the sessions new or
    changed since it, every other carried over from it unread."""
    if base is not None and STRICT:
        # A secret found in one session is swept through every file under strict redaction (_sweep, the feedback):
        # the sessions carried over unread would keep it.
        raise RuntimeError("build refused: an incremental build under strict redaction; build every session")
    procs = procs or os.cpu_count() or 4
    name = name or version_name(cfg.build)
    out = cfg.build / name
    if out.exists() or (base is not None and base.resolve() == out.resolve()):
        raise FileExistsError(f"build refused: {out} exists already")
    tmp = cfg.build / f".{name}.part"
    stage = cfg.data / "tmp" / "stage" / name
    shutil.rmtree(tmp, ignore_errors=True)
    for leftover in (cfg.data / "tmp" / "stage", cfg.data / "tmp" / "scan", cfg.data / "tmp" / "decoded"):  # a crashed build's
        shutil.rmtree(leftover, ignore_errors=True)
    for part in cfg.build.glob(".*.part") if cfg.build.exists() else []:
        shutil.rmtree(part, ignore_errors=True)
    stage.mkdir(parents=True)
    stage = stage.resolve()  # the scan names files by their resolved path
    with _Pool(procs) as pool:
        log(f"build: memory at the start: {memory()}")
        rows_by_source, pairs, left_out = current_rows(cfg, ledger, pool)
        log(f"build: {sum(pairs.values()):,} duplicate rows over every source; pairs {dict(sorted(pairs.items()))}")
        if left_out:
            log(f"build: {left_out:,} rows left out on request (state/removals.txt)")
        summaries, near, summarized, same_session = near_copy_pass(rows_by_source, base, pool, procs, log=log,
                                                                   places=repos.known(ledger))
        process, drop, also_patch, restage = None, set(), {}, None
        if base is not None:   # only the sessions whose inputs moved since the version before
            before_fp, before_also = read_inputs(base / INPUTS)
            now = {session_key(r): inputs_fp(r) for rows in rows_by_source.values() for r in rows}
            process = {k for k, f in now.items() if before_fp.get(k) != f}
            gone = set(before_fp) - set(now)
            drop = process | gone
            also_patch = {session_key(r): r.get("also_in") or [] for rows in rows_by_source.values() for r in rows
                          if session_key(r) not in process and before_also.get(session_key(r)) != _also(r)}
            from . import hfcopy_build as _hf

            restage = restage_for(base, list(_hf.entries(ledger)), rows_by_source, process)
            log(f"build: incremental from {base.name}: {len(process):,} of {len(now):,} sessions new or changed "
                f"({sum(k not in before_fp for k in process):,} new), {len(gone):,} gone, {len(also_patch):,} session "
                f"rows' also_in moved; copied datasets staged again: {sorted(restage) or 'none'}")
        every = rows_by_source
        rows_by_source = every if process is None else {s: [r for r in rows if session_key(r) in process]
                                                        for s, rows in every.items()}
        prefix = "part-" if base is None else f"part-{name}-"   # this build's own parts, beside the carried ones
        raw, jobs = stage_jobs(rows_by_source, stage)
        log(f"build: {len(raw):,} distinct files to publish from {sum(len(v) for v in rows_by_source.values()):,} rows")
        stage_of, utf8_of, counts_of = {}, {}, {}
        for (sha, _), (staged, counts, utf8) in zip(raw.items(), pool.map(_stage, jobs, chunksize=16)):
            stage_of[sha], utf8_of[sha], counts_of[sha] = staged, utf8, _staged_counts(counts)
        unmaskable = {s: c["left_out"] for s, c in counts_of.items() if c.get("left_out")}
        if unmaskable:
            log(f"build: {len(unmaskable):,} files left out: their masking could not keep them valid JSON")
        from . import hfcopy_build

        with process_pool(min(procs, hfcopy_build.COPY_PROCS)) as copy_pool:  # copied files, as they would be
            copied_stage = hfcopy_build.stage(cfg, ledger, stage / "copied", pool=copy_pool, log=log, only=restage)
        seen_raw: dict[str, list[dict]] = {}
        masked = until_clean([Path(stage_of[s]) for s in stage_of if utf8_of[s]] + copied_stage.paths(),
                             cfg.data / "tmp" / "scan", pool, procs, log=log, seen=seen_raw)
        by_staged = {v: k for k, v in stage_of.items()}
        for staged, secrets in masked.items():
            if staged in by_staged:
                counts_of[by_staged[staged]]["secrets"].update(secrets)
        copied_stage.add_masked(masked)
        # The JSON files again, as their text reads decoded, before any row is read from them: the scraped files,
        # the copies' text files, and the copies' Parquet files as their views (a cell a line, as JSON).
        item_of = {str(x): i for i in copied_stage.items
                   for x in ([i["staged"]] if i["kind"] != "parquet" else i["parts"])}
        json_files = [stage_of[s] for s in stage_of if utf8_of[s] and scan.json_like(Path(stage_of[s]).name)]
        json_files += [p for p in item_of if scan.json_like(Path(p).name)]
        originals = {stage_of[s]: raw[s] for s in stage_of}
        originals.update({str(i["staged"]): i["file"]["local"] for i in copied_stage.items if i["kind"] != "parquet"})
        pool.renew("the decoded pass", log)
        decoded_masks, decoded_secrets = _decoded(json_files, cfg.data / "tmp", pool, procs, log, originals)
        every_file = [stage_of[s] for s in stage_of if utf8_of[s]] + list(item_of)
        secrets_in: dict[str, set[str]] = {}
        for found in (_literals(seen_raw), decoded_secrets):
            for p, xs in found.items():
                secrets_in.setdefault(p, set()).update(xs)
        # Every secret found masked wherever any file holds it: STRICT only (most secrets gone is the bar; the sweep
        # is a full read of every staged file; the design decision of 2026-10-04). The texts listed as secrets:
        # always.
        literals = _sweep_literals(secrets_in)
        listed = scan.known_secrets()
        log(f"build: the sweep looks for {len(literals):,} texts; {len(listed):,} listed as secrets, "
            f"{sum(1 for x in listed if len(x) < SWEEP_MIN or chr(10) in x):,} of them too short or on two lines")
        swept_masks, swept = (_sweep(literals, every_file, cfg.data / "tmp", pool, procs, log) if literals
                              else ({}, {}))
        for p, xs in swept.items():
            secrets_in.setdefault(p, set()).update(xs)
        for masks in (decoded_masks, swept_masks):
            for staged, secrets in masks.items():
                if staged in by_staged:
                    counts_of[by_staged[staged]]["secrets"].update(secrets)
                elif staged in item_of:
                    item_of[staged]["secrets"] = item_of[staged].get("secrets", Counter()) + Counter(secrets)
        decoded_secrets = secrets_in   # every secret known in each file, for the rows read from it
        pool.renew("the conversations", log)
        conv_of, conversations = _conversations(ledger, rows_by_source, stage_of, utf8_of, counts_of, by_staged,
                                                copied_stage, stage, tmp, cfg.data / "tmp", pool, procs, log,
                                                decoded_secrets, feed_back=STRICT, prefix=prefix)

        pool.renew("writing the sessions", log)
        per_source = {}
        for source, rows in sorted(rows_by_source.items()):
            chunks = [[] for _ in range(procs)]
            weights = [0] * procs
            for r in sorted(rows, key=lambda r: (-(r.get("bytes") or 0), r["source_id"])):
                i = weights.index(min(weights))
                chunks[i].append(r)
                weights[i] += (r.get("bytes") or 0) if not r.get("duplicate_of") else 1
            jobs = []
            for i, c in enumerate(chunks):
                if not c:
                    continue
                shas = {f["sha256"] for r in c if not r.get("duplicate_of") for f in r.get("files") or []}
                jobs.append((c, {s: stage_of[s] for s in shas}, {s: utf8_of[s] for s in shas},
                             {s: counts_of[s] for s in shas}, tmp / "data" / source, f"{prefix}{i:03d}",
                             {r["source_id"]: conv_of[(source, r["source_id"])] for r in c
                              if (source, r["source_id"]) in conv_of}))
            got = list(pool.map(_write_part, jobs))
            published_files = sum(g["files"] for g in got)
            expected = sum(1 for r in rows if not r.get("duplicate_of") for f in r.get("files") or []
                           if utf8_of[f["sha256"]])
            if published_files != expected:
                raise RuntimeError(f"build refused: {source} published {published_files} files of {expected}")
            secrets = Counter()
            for g in got:
                secrets.update(g["secrets"])
            per_source[source] = {"rows": sum(g["rows"] for g in got), "files": published_files,
                                  "duplicates": sum(1 for r in rows if r.get("duplicate_of")),
                                  "secrets": dict(secrets), "emails": sum(g["emails"] for g in got),
                                  "home_users": sum(g["home_users"] for g in got),
                                  "new": (results.get(source) or {}).get("new", 0),
                                  "changed": (results.get(source) or {}).get("changed", 0)}
            log(f"build: {source}: {per_source[source]['rows']:,} rows, {published_files:,} files; memory: {memory()}")
        with process_pool(min(procs, hfcopy_build.COPY_PROCS)) as copy_pool:
            copied = copied_stage.write(tmp, copy_pool, procs, log=log)
        view_starts = copied_stage.view_starts(tmp)
    if base is not None:
        keep_copied = copied_stage.wanted - restage
        merged = _merge(base, tmp, drop, also_patch, keep_copied)
        log(f"build: carried over from {base.name}: {dict(sorted(merged.items()))}")
        log(f"build: copied/matches.parquet over every copied dataset: {_matches_all(ledger, tmp, copied_stage.wanted):,} rows")
        before = json.loads((base / "changelog.json").read_text())
        copied = dict(sorted({**{k: v for k, v in (before.get("copied") or {}).items() if k in keep_copied},
                              **copied}.items()))
        per_source, tables = _final_counts(tmp, results)
        conversations = {"tables": tables, "this_run": {k: v for k, v in conversations.items() if k != "tables"}}
        view_starts = {**{p: v for p, v in (ledger.get_state("view_starts", base.name) or {}).items()
                          if (tmp / p).exists()}, **view_starts}
    ledger.set_state("view_starts", name, view_starts)   # for the check (view_parts)
    from . import card

    gitleaks_version = scan.rules_digest()
    changelog = {"version": name, "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "licences_looked_up": bool((results.get("_run") or {}).get("licences_looked_up")),
                 "failed_sources": list((results.get("_run") or {}).get("failed") or []),
                 "scraped": per_source, "copied": copied, "conversations": conversations,
                 "scan": {"rules": gitleaks_version}, "redaction": "strict" if STRICT else "most",
                 "duplicate_pairs": _pairs(every), "gaps": gaps(results),
                 "near_copies": {"sessions": sum(near.values()), "pairs": dict(sorted(near.items())),
                                 "summarized": summarized,
                                 "rule": "the same typed messages in order, or the opening of a longer session's, in the "
                                         "same repository or copied export; and the agent's text replies mostly the "
                                         f"same ({dedup.MIN_TEXTS}+ in common, {dedup.MIN_SHARE:.0%}+ of the smaller "
                                         f"side), or {dedup.LONG_MESSAGES}+ typed messages of {dedup.LONG_CHARS}+ "
                                         f"characters with the agents' sides not differing ({dedup.LONG_SHARE:.0%}+ of "
                                         f"the replies alike; with under {dedup.THIN} replies, a reply or "
                                         f"{dedup.LONG_TOOLS}+ tool calls alike)"},
                 "same_session_copies": {"sessions": sum(same_session.values()),
                                         "pairs": dict(sorted(same_session.items())),
                                         "rule": "the same session id and the same whole conversation (every row's "
                                                 "role, text and tool input, in order), in any repository or export: "
                                                 "a source that records no agent keys a session by its raw line"}}
    if base is not None:
        changelog["incremental"] = {"base": base.name, "sessions_read": len(process), "gone": len(drop - process),
                                    "also_in_patched": len(also_patch), "datasets_staged_again": sorted(restage)}
    if (labelled := conv_build.pushback_meta()) is not None:
        changelog["pushback_labels"] = labelled
    (tmp / "changelog.json").write_text(json.dumps(changelog, indent=1, ensure_ascii=False))
    docs = [tmp / "changelog.json"]
    if base is None:   # an incremental build writes no card: the README's one home is the public docs (2026-10-06)
        card.write(tmp, changelog, log=log)
        docs.insert(0, tmp / "README.md")
    # The card and the changelog quote other datasets' cards: they are masked and scanned like every other file.
    for doc in docs:
        doc.write_text(scan.redact(doc.read_text(), [], pii=True)[0])
    with process_pool(2) as small:
        until_clean([d.resolve() for d in docs], cfg.data / "tmp" / "scan", small, 2, log=log)
    write_inputs(tmp / INPUTS, every)
    write_summaries(tmp / SUMMARIES, summaries)
    os.replace(tmp, out)
    keep = {name, ledger.last_published()} | ({base.name} if base is not None else set())
    for old_build in sorted((p for p in cfg.build.iterdir() if p.is_dir() and not p.name.startswith(".")),
                            key=lambda p: (p.name.split(".")[0], int(p.name.split(".")[1]) if "." in p.name else 1))[:-2]:
        if old_build.name not in keep:
            shutil.rmtree(old_build, ignore_errors=True)  # older unpublished builds
    shutil.rmtree(stage, ignore_errors=True)
    ledger.record_version(name, changelog, published=False)
    log(f"build: version {name} at {out}")
    return out, changelog


def _decode_job(args: tuple) -> tuple[str, str | None]:
    """A JSON file's text as it reads decoded (conv_build.decoded_lines) into a view to scan; None if it holds no
    string."""
    src, view = args
    n, unread = 0, []
    with open(view, "w", encoding="utf-8", errors="surrogateescape") as out:
        for line in conv_build.decoded_lines(Path(src), unread):
            out.write(line + "\n")
            n += 1
    if unread:   # lines that read as no JSON: scanned as they are, not decoded
        print(f"decoded JSON: {sum(unread):,} lines of {Path(src).name} read as no JSON", flush=True)
    return src, view if n else None


def _mask_files_job(args: tuple) -> tuple[str, list[dict] | None]:
    """A staged file and the findings its decoded text gave: where the file holds each (conv_build.mask_files)."""
    src, fs = args
    return src, conv_build.mask_files(fs, [src]).get(src)


def _decoded(paths: list[str], work: Path, pool, procs: int, log,
             originals: dict[str, str] | None = None) -> tuple[dict[str, Counter], dict[str, set[str]]]:
    """Every staged JSON file's text as it reads decoded, scanned as the build scans; each secret found there masked
    where the file holds it, escaped or not (conv_build.mask_files), and the files changed scanned again, raw and
    decoded, until neither finds anything. gitleaks' generic rule allows no backslash before a value, so
    `\\"api_key\\": \\"...\\"` in a JSON line is missed: 83 such secrets were left in a sample build's files, in
    text no conversations row carries (OpenCode's diffs, Codex's command output, Claude Code's patches; 2026-10-03).
    A secret under six characters is left to its own match, as everywhere (scan.place).

    The first round reads each file as fetched (`originals`), where one is at hand: a mask the raw scan made can change
    the text around another secret so that the generic rule no longer reads it (2 of 506 secrets in the sample were
    missed that way). A secret found there but already masked in the staged file is passed over. The second round
    reads every staged file as it will be published, and each later one the files the round before changed. Returns
    the masks made, and every secret found, by staged file (conveyed to the rows read from them)."""
    masked: dict[str, Counter] = {}
    found_in: dict[str, set[str]] = {}
    todo = sorted(paths)
    for rnd in range(1, ROUNDS + 1):
        source = lambda p: (originals or {}).get(p, p) if rnd == 1 else p
        hits: dict[str, list[dict]] = {}
        unplaced = 0
        # In batches of about DECODE_BATCH_BYTES of text, each batch's views removed before the next: decoded, the
        # full collection's JSON files would take some 120 GB at once.
        batches, batch, size = [], [], 0
        for p in todo:
            batch.append(p)
            size += Path(source(p)).stat().st_size
            if size >= DECODE_BATCH_BYTES:
                batches.append(batch)
                batch, size = [], 0
        if batch:
            batches.append(batch)
        for bi, b in enumerate(batches):
            # Each batch its own folder: one folder emptied and made again stopped a full build when a file in it was
            # still held open on the network disk, so its removal left the folder (2026-10-26, run2c).
            vdir = work / "decoded" / f"round{rnd}-batch{bi:04d}"
            shutil.rmtree(vdir, ignore_errors=True)
            vdir.mkdir(parents=True, exist_ok=True)
            jobs = [(source(p), str((vdir / f"{i:07d}.txt").resolve())) for i, p in enumerate(b)]
            staged_of = {j[1]: p for j, p in zip(jobs, b)}
            views = [(staged_of[v], v) for _, v in pool.map(_decode_job, jobs, chunksize=4) if v]
            found = scan.find_all([Path(v) for _, v in views], work / "scan", procs, pool)
            look = []
            for src, v in views:
                fs = [{**f, "decoded": True, "gitleaks_decoded": bool(f.get("decoded"))}   # its own decoding kept
                      for f in found.get(v, []) if len(scan.literal_of(f)) >= 6]
                if fs:
                    found_in.setdefault(src, set()).update(scan.literal_of(f) for f in fs)
                    look.append((src, fs))
            # Each file's findings looked for in it in the pool, as files are independent: one file at a time in this
            # process was most of a sample build's decoded pass (the partner's review of rc7).
            for src, held in (pool.map(_mask_files_job, look, chunksize=1) if look else []):
                if not held:
                    if rnd == 1 and originals and src in originals:
                        continue   # found as fetched, and already masked where it is staged
                    if not STRICT:
                        unplaced += 1   # its secrets left where they are (STRICT stops the build here)
                        continue
                    raise RuntimeError(f"build refused: a secret in {Path(src).name}'s decoded text cannot be "
                                       f"placed in it")
                hits[src] = held
            shutil.rmtree(vdir, ignore_errors=True)
        log(f"decoded JSON round {rnd}: {len(todo):,} files, {sum(len(h) for h in hits.values()):,} findings to mask "
            f"in {len(hits):,}" + (f"; {unplaced:,} files' secrets could not be placed, left" if unplaced else ""))
        if not hits and rnd > 1:
            return masked, found_in
        for staged, c in pool.map(_mask, list(hits.items())):
            masked.setdefault(staged, Counter()).update(c["secrets"])
        for staged, secrets in until_clean([Path(p) for p in hits], work / "scan", pool, procs, log=log).items():
            masked.setdefault(staged, Counter()).update(secrets)
        # The second round reads every staged file, decoded as published: a mask can change the text around another
        # secret so that it shows only there (2 left by a sample build whose second round read only the files the
        # first had changed, 2026-10-04). Later rounds read the files the round before changed.
        todo = sorted(paths) if (rnd == 1 and STRICT) else sorted(hits)   # STRICT: a second full read
    if STRICT:
        raise RuntimeError(f"build refused: {len(todo)} files still hold secrets in their decoded text after {ROUNDS} rounds")
    log(f"decoded JSON: {len(todo):,} files still giving findings after {ROUNDS} rounds, left as they are")
    return masked, found_in


def _literals(found: dict[str, list[dict]]) -> dict[str, set[str]]:
    return {p: {scan.literal_of(f) for f in fs if len(scan.literal_of(f)) >= SWEEP_MIN} for p, fs in found.items()}


def _sweep_literals(secrets_in: dict[str, set[str]]) -> set[str]:
    """What the sweep looks for: under strict redaction, every secret found in any file; always, the texts listed as
    secrets (scan.known_secrets), so a session read again keeps the masks a version before was given by hand."""
    found = set().union(*secrets_in.values()) if (STRICT and secrets_in) else set()
    return found | scan.known_secrets()


def _sweep(literals: set[str], paths: list[str], work: Path, pool, procs: int, log) -> tuple[dict, dict]:
    """Every secret found anywhere, masked wherever any staged file holds it, as written, JSON-escaped or more: a secret
    found in one file, where its context let the scan read it, can sit bare in another of the session's files (a
    subagent's transcript) or another session's (the partner session's review, 2026-10-04). One pass of grep's
    fixed-string matcher for every secret; placed with scan.place; the files changed scanned again, raw and decoded.
    Returns the masks made, and the secrets each file held."""
    import subprocess

    lits = sorted({x for x in literals if len(x) >= SWEEP_MIN and "\n" not in x})
    if not lits or not paths:
        return {}, {}
    forms: dict[str, str] = {}
    for x in lits:
        once = json.dumps(x, ensure_ascii=False)[1:-1]
        for form in (x, once, json.dumps(once, ensure_ascii=False)[1:-1]):
            forms.setdefault(form, x)
    pattern = work / "sweep-patterns.txt"   # every secret found, in clear: removed whatever happens
    pattern.parent.mkdir(parents=True, exist_ok=True)
    held: dict[str, set[str]] = {}
    try:
        pattern.write_text("\n".join(forms) + "\n", encoding="utf-8", errors="surrogateescape")
        for i in range(0, len(paths), 1000):
            got = subprocess.run(["grep", "-oFaH", "-f", str(pattern), "--", *paths[i:i + 1000]], capture_output=True)
            if got.returncode not in (0, 1):
                raise RuntimeError(f"build refused: the sweep's grep failed: {got.stderr[:200]!r}")
            for line in got.stdout.decode("utf-8", errors="surrogateescape").splitlines():
                path, _, match = line.partition(":")   # staged paths hold no colon
                if match in forms:
                    held.setdefault(path, set()).add(forms[match])
    finally:
        pattern.unlink(missing_ok=True)
    todo: dict[str, list[dict]] = {}
    # Each file's secrets placed in it in the pool, the largest file first: one file at a time in this process, as
    # the decoded pass placed them before rc8, read a 3.3 GB export at the pace of one worker.
    jobs = sorted(((path, [{"rule": "sweep", "secret": x, "decoded": True} for x in sorted(found)])
                   for path, found in held.items()), key=lambda j: -Path(j[0]).stat().st_size)
    for path, placed in (pool.map(_mask_files_job, jobs, chunksize=1) if jobs else []):
        if placed:
            todo[path] = placed
    masked: dict[str, Counter] = {}
    for staged, c in pool.map(_mask, list(todo.items())):
        masked.setdefault(staged, Counter()).update(c["secrets"])
    for staged, secrets in until_clean([Path(p) for p in todo], work / "scan", pool, procs, log=log).items():
        masked.setdefault(staged, Counter()).update(secrets)
    json_todo = [p for p in todo if scan.json_like(Path(p).name)]
    again, _ = _decoded(json_todo, work, pool, procs, log) if json_todo else ({}, {})
    for staged, secrets in again.items():
        masked.setdefault(staged, Counter()).update(secrets)
    log(f"build: sweep of {len(lits):,} secrets found: held in {len(held):,} files, masked in {len(todo):,}")
    return masked, held


def _secrets_of(r: dict, staged_of_copy: dict, stage_of: dict, decoded_secrets: dict, known: dict) -> list[str]:
    """Every secret known in a session's staged files, for its rows (conv_build._carry): one list, in `known`, for
    every session read from the same files (each of an export's thousands of sessions carries the export's)."""
    paths = tuple([x for _, p in r["_read"] for x in staged_of_copy.get(p, [])] if "_read" in r else
                  [stage_of[f["sha256"]] for f in r.get("files") or [] if f["sha256"] in stage_of])
    if paths not in known:
        known[paths] = sorted({s for p in paths for s in decoded_secrets.get(p, ())})
    return known[paths]


def _by_file(back: dict[str, list[dict]], files_of: dict[str, list[str]]) -> list[tuple[str, list[dict]]]:
    """The findings fed back from each session, gathered by the session's files: a file's every session's, to be
    placed at once (a job of _mask_files_job), the largest file first. A DataClaw export is the file of each of its
    thousands of sessions, an OpenHands Parquet file's view parts every trajectory's: placed a session at a time, a
    3.3 GB export was read again for each of its sessions with a finding, in the main process."""
    by_file: dict[str, list[dict]] = {}
    for key, found in back.items():
        for path in dict.fromkeys(files_of.get(key, [])):
            by_file.setdefault(path, []).extend(found)
    return sorted(by_file.items(), key=lambda j: (-Path(j[0]).stat().st_size, j[0]))


def _balanced(rows: list[dict], n: int) -> list[list[dict]]:
    chunks, weights = [[] for _ in range(n)], [0] * n
    for r in sorted(rows, key=lambda r: (-(r.get("bytes") or 0), r["source_id"])):
        i = weights.index(min(weights))
        chunks[i].append(r)
        weights[i] += r.get("bytes") or 0
    return [c for c in chunks if c]


def _readable(r: dict, stage_of: dict, utf8_of: dict, text_staged: dict) -> list[tuple[str, Path]]:
    """A session's files to read, as staged and masked: a scraped session's; a copied session's text files as the
    copy stages them (what its scan masked is masked in its rows too). A copied Parquet file is read as downloaded,
    its rows masked by their own scan; a copied file left out of the copy (not text) is not read."""
    if "_read" in r:
        if not r["_read"]:
            return []
        out = []
        for n, p in r["_read"]:
            if p in text_staged:
                out.append((n, Path(text_staged[p])))
            elif p.endswith(".parquet"):
                out.append((n, Path(p)))
        return out if any(n == r["_read"][0][0] for n, _ in out) else []
    return [(f["path"], Path(stage_of[f["sha256"]])) for f in r.get("files") or [] if utf8_of[f["sha256"]]]


def _conversations(ledger, rows_by_source, stage_of, utf8_of, counts_of, by_staged, copied_stage, stage: Path,
                   tmp: Path, work: Path, pool, procs: int, log, decoded_secrets: dict | None = None,
                   feed_back: bool = True, prefix: str = "part-") -> tuple[dict, dict]:
    """The conversations tables, written into the version (conv_build): rows from every kept session's masked files,
    their decoded text scanned until clean, and each secret found there masked in the session's own files too, and
    those scanned again, before the sessions table is written from them. Per session, its key, counts, or why it
    has no rows; per source, the tables' counts."""
    raw_dir = work / "conv"
    shutil.rmtree(raw_dir, ignore_errors=True)
    conv_of: dict[tuple[str, str], dict] = {}
    files: list[str] = []
    labels = copied_sessions.swechat_labels(ledger)
    text_staged = {i["file"]["local"]: str(i["staged"]) for i in copied_stage.items if i["kind"] != "parquet"}
    # The secrets the decoded pass found in each session's files, carried into its rows (conv_build._carry): rows
    # read from a downloaded Parquet copy get what the scan of the copy's view found.
    staged_of_copy = {i["file"]["local"]: ([str(i["staged"])] if i["kind"] != "parquet" else [str(x) for x in i["parts"]])
                      for i in copied_stage.items}

    known: dict[tuple, list[str]] = {}
    secrets_of = lambda r: _secrets_of(r, staged_of_copy, stage_of, decoded_secrets or {}, known)
    sid_of = lambda r: (r.get("_session") or {}).get("session_id") or (
        r["source_id"].split(":", 1)[1] if r["source"] == "entire" and ":" in r["source_id"] else None)
    for source, rows in sorted(rows_by_source.items()):
        kept = [r for r in rows if not r.get("duplicate_of")]
        jobs = [(source, [(r, _readable(r, stage_of, utf8_of, text_staged)) for r in chunk], str(raw_dir), f"{prefix}{i:03d}",
                 {sid_of(r): labels[sid_of(r)] for r in chunk if sid_of(r) in labels},
                 {r["source_id"]: secrets_of(r) for r in chunk if secrets_of(r)})
                for i, chunk in enumerate(_balanced(kept, procs))]
        for got in pool.map(conv_build.extract_job, jobs):
            conv_of.update({(source, k): v for k, v in got["per"].items()})
            files += got["files"]
    views, keys = conv_build.stage_views(files, stage / "conversations", pool)
    seen: dict[str, list[dict]] = {}
    masked = until_clean([v["path"] for v in views], work / "scan", pool, procs, log=log, seen=seen)
    masked["(private keys, by their structure, as staged)"] = keys
    back = conv_build.feedback(seen, views) if feed_back else {}
    fed = Counter()
    if not feed_back:
        log("build: the conversations' findings are masked in their rows only, not fed back to the session files")
    if back:
        # A copied session's files are fed back where the copy stages them for publishing (a text file as it is, a
        # Parquet file as its view parts).
        copied_of = {i["file"]["local"]: ([str(i["staged"])] if i["kind"] != "parquet" else [str(x) for x in i["parts"]])
                     for i in copied_stage.items}
        item_of = {str(x): i for i in copied_stage.items
                   for x in ([i["staged"]] if i["kind"] != "parquet" else i["parts"])}
        files_of = {f"{r['source']}:{r['source_id']}": ([x for _, p in r["_read"] for x in copied_of.get(p, [])]
                                                        if "_read" in r else
                                                        [stage_of[f["sha256"]] for f in r.get("files") or []
                                                         if utf8_of[f["sha256"]]])
                    for rows in rows_by_source.values() for r in rows if not r.get("duplicate_of")}
        todo: dict[str, list[dict]] = {}
        lines_per_secret: Counter = Counter()
        for path, held in (pool.map(_mask_files_job, _by_file(back, files_of), chunksize=1) if back else []):
            if held:
                todo[path] = held
                lines_per_secret.update(scan.literal_of(f) for f in held)
        lengths = Counter(len(scan.literal_of(f)) for fs in back.values() for f in fs)
        # Never the secrets themselves: how long they are, and how many lines each masked (a short, ordinary string
        # would mask many).
        log(f"build: secrets fed back from the conversations: {sum(lengths.values()):,}, "
            f"{sum(n for k, n in lengths.items() if k < 12):,} under 12 characters; lines masked per secret: "
            f"{sorted(lines_per_secret.values(), reverse=True)[:20]}")
        def count(staged: str, secrets) -> None:
            if staged in by_staged:
                counts_of[by_staged[staged]]["secrets"].update(secrets)
            elif staged in item_of:
                item_of[staged]["secrets"] = item_of[staged].get("secrets", Counter()) + Counter(secrets)

        for staged, c in pool.map(_mask, list(todo.items())):
            count(staged, c["secrets"])
            fed.update(c["secrets"])
        for staged, secrets in until_clean([Path(p) for p in todo], work / "scan", pool, procs, log=log).items():
            count(staged, secrets)
        # And decoded, as the check reads them: a mask can change what the text around it reads as.
        again, _ = _decoded([p for p in todo if scan.json_like(Path(p).name)], work, pool, procs, log)
        for staged, secrets in again.items():
            count(staged, secrets)
        # And wherever else a staged file holds them.
        every_file = [stage_of[x] for x in stage_of if utf8_of[x]] + [
            str(x) for i in copied_stage.items for x in ([i["staged"]] if i["kind"] != "parquet" else i["parts"])]
        view_paths = [str(v["path"]) for v in views]   # and the views, so the rows: another part, another session
        swept_masks, _ = _sweep({scan.literal_of(f) for fs in back.values() for f in fs}, every_file + view_paths, work,
                                pool, procs, log)
        for staged, secrets in swept_masks.items():
            if staged in view_paths:
                masked.setdefault(staged, Counter()).update(secrets)
            else:
                count(staged, secrets)
        log(f"build: {sum(len(v) for v in back.values()):,} findings in the conversations' text masked again in "
            f"{len(todo):,} session files")
    parts: dict[str, list[Path]] = {}
    for v in views:
        parts.setdefault(v["src"], []).append(v["path"])
    out: dict[str, dict] = {}
    for src in files:
        rel = Path(src).relative_to(raw_dir)  # <table>/<source>/<part>.parquet
        changed = conv_build.rebuild(src, parts.get(src, []), tmp / rel)
        table, source = rel.parts[0], rel.parts[1]
        info = out.setdefault(source, {"rows": 0, "subagent_rows": 0, "rows_masked": 0})
        info["subagent_rows" if table == "conversations_subagents" else "rows"] += pq.ParquetFile(tmp / rel).metadata.num_rows
        info["rows_masked"] += changed
    secrets = Counter()
    for c in masked.values():
        secrets.update(c)
    agent_of = {(source, r["source_id"]): r.get("agent") or "unknown" for source, rows in rows_by_source.items()
                for r in rows}
    carried: dict[str, Counter] = {}   # SWE-chat's labelled prompts, by agent: where they landed, by which rule
    for key, got in conv_of.items():
        source = key[0]
        info = out.setdefault(source, {"rows": 0, "subagent_rows": 0, "rows_masked": 0})
        info["sessions"] = info.get("sessions", 0) + 1
        if got.get("conversations_error"):
            why = info.setdefault("sessions_without_rows", {})
            why[got["conversations_error"]] = why.get(got["conversations_error"], 0) + 1
        if got.get("labels"):
            carried.setdefault(agent_of.get(key, "unknown"), Counter()).update(got["labels"])
    shutil.rmtree(raw_dir, ignore_errors=True)
    log(f"build: conversations: {sum(i['rows'] for i in out.values()):,} rows, "
        f"{sum(i['subagent_rows'] for i in out.values()):,} subagent rows; secrets masked in their text {dict(secrets)}")
    for agent, c in sorted(carried.items(), key=lambda kv: -sum(kv[1][w] for w in ("typed", "other", "none"))):
        log(f"build: SWE-chat's labels, {agent}: {dict(sorted(c.items()))}")
    return conv_of, {"tables": out, "secrets_masked": dict(secrets), "secrets_fed_back_to_files": dict(fed),
                     "swechat_labels": {a: dict(sorted(c.items())) for a, c in sorted(carried.items())}}


STANDING_GAPS = [
    "GitHub's searches read default branches only: a session committed only to another branch is not found.",
    "Entire: a trailer commit whose checkpoints were never pushed, or live in a private repository, gives no session "
    "(4 of 14 repositories sampled on 2026-10-03 had a trailer but no checkpoint data on GitHub).",
    "Aider: code search does not index files over 384 KB, so a repository whose only Aider history is that large is "
    "not found (8% of the histories sampled on 2026-10-03 were over it); in a repository that is found, every "
    "history is read.",
    "SpecStory: a repository is found only if one of its history files is under 384 KB; every file of a repository "
    "found is read.",
    "Counts come from a cheap parse of each format and are left empty where the format does not say; see Session "
    "table columns in full, under Reference.",
]


def gaps(results: dict) -> list[str]:
    """What this run could not read whole, from the sources' own reports, then the gaps every run has."""
    out = []
    e = results.get("entire") or {}
    if e.get("slices_incomplete") or e.get("slices_capped"):
        out.append(f"Entire: {e.get('slices_incomplete', 0)} discovery slices GitHub marked incomplete and "
                   f"{e.get('slices_capped', 0)} it capped at 1,000 results; they are read again next run.")
    if e.get("unreachable"):
        out.append(f"Entire: {e['unreachable']} repositories holding trailer commits could not be read (gone, or "
                   f"private since).")
    for name in ("entire", "specstory", "aider", "swe-chat-v2", "dataclaw", "openhands-feedback"):
        r = results.get(name) or {}
        if r.get("errors"):
            out.append(f"{name}: {r['errors']} repositories or datasets failed to fetch this run; they are tried "
                       f"again next run.")
        if r.get("error"):
            out.append(f"{name}: the source failed this run ({r['error'][:120]}).")
        search = r.get("search") or {}
        if search.get("capped") or search.get("incomplete"):
            out.append(f"{name}: {search.get('capped', 0)} code-search slices held more than 1,000 files of one "
                       f"size ({search.get('missed_by_cap', 0)} results not listed) and {search.get('incomplete', 0)} "
                       f"came back incomplete; repositories found are read whole, so only a repository found by "
                       f"none of its files is missed.")
    return out + STANDING_GAPS


def removals(cfg: Config) -> set[str]:
    """The removal list (state/removals.txt): one entry per line, `repo:<owner/name>`, `row:<source>:<source_id>`,
    `session:<dedup key>` or `dataset:<owner/name>`; `#` starts a comment."""
    path = cfg.state / "removals.txt"
    if not path.exists():
        return set()
    return {line.split("#", 1)[0].strip() for line in path.read_text().splitlines() if line.split("#", 1)[0].strip()}


def is_removed(row: dict, removed: set[str]) -> bool:
    return bool(removed) and (f"repo:{row.get('repo')}" in removed or f"row:{row['source']}:{row['source_id']}" in removed
                              or f"session:{row.get('dedup_key')}" in removed)


def _pairs(rows_by_source: dict[str, list[dict]]) -> dict:
    index = {f"{r['source']}:{r['source_id']}": r["source"] for rows in rows_by_source.values() for r in rows}
    pairs = Counter()
    for rows in rows_by_source.values():
        for r in rows:
            if r.get("duplicate_of"):
                pairs[f"{index.get(r['duplicate_of'], '?')}~{r['source']}"] += 1
    return dict(sorted(pairs.items()))
