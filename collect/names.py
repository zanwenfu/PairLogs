"""Masking people's full names in a release tier, the way secrets are masked: found in what people typed, then masked
wherever the same text occurs in that session (its rows, its helper agents' rows, its raw files).

Calibrated on tier A of 2026-10-11 (2026-10-24):
- Presidio's PERSON finder (spaCy en_core_web_lg) run on everything found 979 "names" in 3,000 rows, about one a
  private person's; the rest were code (`dict[str]`, `WeakMap`), tool names and non-English text. Not used that way.
- Run on typed messages only, keeping spans shaped like a full name (two or more capitalised words, letters only,
  score >= 0.85): 94 spans in 47 of 6,000 messages, about two in three real names and one in three a private person
  (a pasted chat naming a colleague, a family friend, a client). That is this stage.

Phone numbers are not looked for: in the same sample every candidate was a timestamp or an id. A name that appears
only in an agent's or a tool's text (a paper's authors, a commit's author) is left: it comes from public material.

A copied dataset's sessions keep their raw text in the dataset's own files (`copied/`), not in their session rows:
each session's names are masked in its own record there (a JSON line, a Parquet row, or a whole transcript file), and
the dataset's credit line says so (2026-11-03; before, those files went out as copied, the names in them unmasked).
"""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Callable, Iterable

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .tiers import SHAREABLE_COPIED
from .util import safe_name

MASK = "[REDACTED]"
SCORE = 0.85
# Matched whole (fullmatch): `$` let a span ending in a newline through, and such a "name" was masked only where a
# newline followed it (13 of 2026-11-02's 885, in 6 sessions; 2026-11-03).
FULL_NAME = re.compile(r"[A-Z][a-zÀ-ſ'’]+(?:[ -][A-Z][a-zÀ-ſ'’]+)+")
TABLES = ("conversations", "conversations_subagents")

Finder = Callable[[str], Iterable[tuple[int, int, float]]]   # text -> (start, end, score) of PERSON spans


def presidio_finder() -> Finder:
    """Presidio's PERSON spans (spaCy en_core_web_lg); imported only when the stage runs."""
    from presidio_analyzer import AnalyzerEngine

    engine = AnalyzerEngine()
    return lambda text: [(r.start, r.end, r.score) for r in engine.analyze(text=text, entities=["PERSON"],
                                                                            language="en")]


CHUNK = 50_000   # spaCy refuses texts over 1,000,000 characters; one typed message held 1.7 million (a paste)


def _chunks(text: str):
    """(offset, piece) pieces of at most CHUNK characters, cut at a line break where there is one."""
    i = 0
    while i < len(text):
        j = min(len(text), i + CHUNK)
        if j < len(text):
            cut = text.rfind("\n", i + 1, j)
            j = cut + 1 if cut > i else j
        yield i, text[i:j]
        i = j


def names_in(text: str, finder: Finder) -> set[str]:
    """The full names a finder sees in one typed message, read in pieces when it is long."""
    out = set()
    for offset, piece in _chunks(text or ""):
        for start, end, score in finder(piece):
            span = piece[start:end].strip()
            if score >= SCORE and FULL_NAME.fullmatch(span) and not looks_technical(span):
                out.add(span)
    return out


def _pattern(names: Iterable[str]) -> re.Pattern | None:
    """Every form a name takes in stored text: as written, and JSON-escaped (raw transcripts escape non-ASCII)."""
    forms = set()
    for n in names:
        forms.add(n)
        forms.add(json.dumps(n)[1:-1])
    if not forms:
        return None
    alt = "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True))
    # not inside a longer word; but right after a JSON escape (`\nMaxwell` in a raw transcript) it is a word's start
    return re.compile(rf"(?:(?<![\w\\])|(?<=\\[nrtbf]))(?:{alt})(?!\w)")


def mask(text: str | None, pattern: re.Pattern | None) -> tuple[str | None, int]:
    """A name written against another (no space between) reads as part of a longer word, and stands alone once the
    other is masked: masked again until nothing matches (3 such left in the copied files of 2026-11-02, 2026-11-03)."""
    if text is None or pattern is None:
        return text, 0
    total = 0
    while True:
        text, n = pattern.subn(MASK, text)
        total += n
        if not n:
            return text, total


# Terms the finder takes for people that are not (products, tools, phrases): never masked. Kept in a file read term
# by term, one a line, so the list is data, not code.
DENY_FILE = Path(__file__).with_name("names_deny.txt")


# A candidate holding one of these words names a product, a tool or a technical thing, not a person (the finder's
# commonest mistakes on version 2026-10-11: model and tool names, loaders, kernels, roadmaps).
NOT_A_NAME_WORDS = {
    "claude", "qwen", "gemini", "copilot", "docker", "git", "github", "linux", "kotlin", "jetpack", "compose",
    "desktop", "kernel", "loader", "loading", "roadmap", "workflow", "plugin", "plugins", "token", "query", "page",
    "font", "image", "testing", "scan", "sync", "skill", "hooks", "monitor", "model", "preview", "benchmark",
    "embeddings", "codebase", "metadata", "checklist", "dashboard", "provisioner", "requirement", "criteria",
    "triton", "pooler", "session", "server", "endpoint", "webhook", "driver", "notebook", "filter", "api", "sdk",
    "rashifal", "bala", "dasha", "mahadasha", "antardasha", "dosha", "dosh", "vivah", "samvat", "ekadashi", "rashi",
}


def looks_technical(span: str) -> bool:
    return any(w.lower().strip("'’").removesuffix("'s").removesuffix("’s") in NOT_A_NAME_WORDS
               for w in re.split(r"[ -]", span))


def deny_list(path: Path = DENY_FILE) -> set[str]:
    if not path.exists():
        return set()
    return {x.strip() for x in path.read_text().splitlines() if x.strip() and not x.lstrip().startswith("#")}


def _find_file(f: Path, finder: Finder) -> dict[str, set[str]]:
    found: dict[str, set[str]] = defaultdict(set)
    t = pq.read_table(f, columns=["session_key", "content", "typed_by_developer"])
    t = t.filter(pc.fill_null(t["typed_by_developer"], False))
    for key, content in zip(t["session_key"].to_pylist(), t["content"].to_pylist()):
        hit = names_in(content, finder)
        if hit:
            found[key] |= hit
    return found


_WORKER_FINDER: Finder | None = None


def _worker_init() -> None:
    global _WORKER_FINDER
    _WORKER_FINDER = presidio_finder()


def _worker_find(f: str) -> dict[str, set[str]]:
    return _find_file(Path(f), _WORKER_FINDER)


def find(tier: Path, finder: Finder | None = None, procs: int = 1, deny: set[str] | None = None) -> dict[str, set[str]]:
    """Pass 1: each session's full names, from its typed messages (copies of queued messages included), less the deny
    list. A finder given is used here, in this process; else each of `procs` processes loads Presidio once."""
    files = sorted((tier / "conversations").glob("*/*.parquet"))
    found: dict[str, set[str]] = defaultdict(set)
    if finder is not None or procs <= 1:
        parts = (_find_file(f, finder or presidio_finder()) for f in files)
    else:
        from concurrent.futures import ProcessPoolExecutor

        pool = ProcessPoolExecutor(max_workers=procs, initializer=_worker_init)
        parts = pool.map(_worker_find, [str(f) for f in files])
    for part in parts:
        for key, names in part.items():
            found[key] |= names
    deny = deny_list() if deny is None else deny
    return {k: v - deny for k, v in found.items() if v - deny}


def _rewrite(src: Path, dst: Path, found: dict[str, set[str]], text_columns: tuple[str, ...]) -> dict:
    """Pass 2 for one file: mask each session's names in its text columns (and raw files' content)."""
    table = pq.read_table(src)
    keys = table["session_key"].to_pylist()
    if not any(k in found for k in keys):
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        return {"rows": 0, "occurrences": 0}
    patterns = {k: _pattern(v) for k, v in found.items()}
    rows_changed = occurrences = 0
    cols = {}
    for name in table.column_names:
        col = table[name]
        if name in text_columns:
            values, changed = [], set()
            for i, (k, v) in enumerate(zip(keys, col.to_pylist())):
                new, n = mask(v, patterns.get(k))
                values.append(new)
                if n:
                    occurrences += n
                    changed.add(i)
            rows_changed += len(changed)
            cols[name] = pa.array(values, type=col.type)
        elif name == "files":
            values = []
            for k, fs in zip(keys, col.to_pylist()):
                p = patterns.get(k)
                if fs and p is not None:
                    new_fs = []
                    for x in fs:
                        new, n = mask(x.get("content"), p)
                        occurrences += n
                        new_fs.append({**x, "content": new})
                    values.append(new_fs)
                else:
                    values.append(fs)
            cols[name] = pa.array(values, type=col.type)
        else:
            cols[name] = col
    dst.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table(cols, schema=table.schema), dst, compression="zstd")
    return {"rows": rows_changed, "occurrences": occurrences}


def copied_units(found: dict[str, set[str]]) -> dict[str, dict[int | None, set[str]]]:
    """Where each copied dataset's session is in the dataset's own files, from its key (`<source>:<dataset>:<file>`,
    then `:<n>` for a record): `copied/<source>/<dataset>/<file>` and the record's line (from 1, JSON lines), row
    (from 0, Parquet) or None (the whole file, an agent's own transcript), with the session's names."""
    units: dict[str, dict[int | None, set[str]]] = defaultdict(lambda: defaultdict(set))
    for key, found_names in found.items():
        source, _, rest = key.partition(":")
        if source not in SHAREABLE_COPIED:
            continue
        dataset, _, path = rest.partition(":")
        record = re.fullmatch(r"(.+\.(?:jsonl|parquet)):(\d+)", path)
        file, unit = (record[1], int(record[2])) if record else (path, None)
        units[f"copied/{source}/{safe_name(dataset)}/{file}"][unit] |= found_names
    return units


def _mask_value(value, pattern: re.Pattern) -> tuple[object, int]:
    """Every string in a Parquet row's value, nested ones too, masked."""
    if isinstance(value, str):
        return mask(value, pattern)
    if isinstance(value, list):
        parts = [_mask_value(v, pattern) for v in value]
        return [v for v, _ in parts], sum(n for _, n in parts)
    if isinstance(value, dict):
        parts = {k: _mask_value(v, pattern) for k, v in value.items()}
        return {k: v for k, (v, _) in parts.items()}, sum(n for _, n in parts.values())
    return value, 0


def _mask_copied_file(src: Path, dst: Path, units: dict[int | None, set[str]]) -> int:
    """One copied file: each record's names masked in that record only (a whole-file session's in every record);
    every other byte as it was. Written beside `dst` and moved over it, so a file `dst` is linked to never changes."""
    whole = units.get(None, set())
    patterns = {u: _pattern(v | whole) for u, v in units.items() if u is not None}
    default = _pattern(whole)
    occurrences = 0
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".masking")
    if src.suffix == ".parquet":
        table = pq.read_table(src)
        rows = table.to_pylist()
        for i, row in enumerate(rows):
            p = patterns.get(i, default)
            if p is not None:
                rows[i], n = _mask_value(row, p)
                occurrences += n
        if occurrences:
            pq.write_table(pa.Table.from_pylist(rows, schema=table.schema), tmp, compression="zstd")
        else:
            shutil.copyfile(src, tmp)
    else:
        with open(src, "rb") as fin, open(tmp, "wb") as fout:
            for i, raw in enumerate(fin, 1):
                p = patterns.get(i, default)
                if p is not None:
                    text, n = mask(raw.decode("utf-8", "surrogateescape"), p)
                    if n:
                        raw = text.encode("utf-8", "surrogateescape")
                        occurrences += n
                fout.write(raw)
    tmp.replace(dst)
    return occurrences


def mask_copied(tier: Path, out: Path, found: dict[str, set[str]], log=print) -> dict:
    """The copied datasets' files of `tier`, each session's names masked in its own record, written to the same paths
    under `out`; each such dataset's credit line in `out`'s changelog says how many. Run by `write`, or alone on a
    tier written before this step existed (it reads `tier`, so running it again gives the same files)."""
    tier, out = Path(tier), Path(out)
    units = copied_units(found)
    per_dataset: dict[str, dict] = defaultdict(lambda: {"sessions": 0, "occurrences": 0})
    files = 0
    for rel, file_units in sorted(units.items()):
        src = tier / rel
        if not src.is_file():
            raise FileNotFoundError(f"{rel}: a copied session's file is not in the tier")
        n = _mask_copied_file(src, out / rel, file_units)
        source, dataset = rel.split("/")[1:3]
        d = per_dataset[(source, dataset)]
        d["sessions"] += len(file_units)
        d["occurrences"] += n
        files += 1
    note = out / "changelog.json"
    stats = {"files": files, "sessions": sum(d["sessions"] for d in per_dataset.values()),
             "occurrences": sum(d["occurrences"] for d in per_dataset.values())}
    if note.exists():
        c = json.loads(note.read_text())
        credits = c.get("copied_datasets") or {}
        for (source, dataset), d in per_dataset.items():
            key = f"{source}/{dataset}" if f"{source}/{dataset}" in credits else source
            e = credits.get(key)
            if e is None or not d["occurrences"]:
                continue
            e["names_masked"] = dict(d)
            end = " redacted, nothing else changed."
            if e.get("credit", "").endswith(end):
                e["credit"] = (e["credit"][: -len(end)] + f" redacted; people's full names masked in {d['sessions']:,} "
                               f"of its sessions ({d['occurrences']:,} occurrences); nothing else changed.")
        if "names_masked" in c:
            c["names_masked"]["copied_files"] = stats
        note.write_text(json.dumps(c, indent=1) + "\n")
    log(f"names: {stats['occurrences']:,} occurrences masked in {stats['files']:,} copied files "
        f"({stats['sessions']:,} sessions)")
    return stats


def write(tier: Path, out: Path, finder: Finder | None = None, log=print, procs: int = 1,
          deny: set[str] | None = None, found: dict[str, set[str]] | None = None) -> dict:
    """Mask full names in a tier written by collect.tiers (tier A), into an empty folder; every other file copied.
    The names found, by session, go beside the folder (`<out>.names.json`), never into it: they are for review.
    `found` (names by session, as that file holds them) masks those instead of finding them again."""
    tier, out = Path(tier), Path(out)
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty: the masked tier is written once, into an empty folder")
    if found is None:
        found = find(tier, finder, procs=procs, deny=deny)
    side = out.parent / f"{out.name}.names.json"
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps({k: sorted(v) for k, v in sorted(found.items())}, indent=0) + "\n")
    log(f"names: {sum(len(v) for v in found.values()):,} names in {len(found):,} sessions' typed messages")
    stats = {"sessions": len(found), "names": sum(len(v) for v in found.values()), "rows": 0, "occurrences": 0}
    for f in sorted(tier.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(tier)
        dst = out / rel
        if f.suffix == ".parquet" and rel.parts[0] in TABLES:
            s = _rewrite(f, dst, found, ("content",))
        elif f.suffix == ".parquet" and rel.parts[0] == "data":
            s = _rewrite(f, dst, found, ())
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(f, dst)
            continue
        stats["rows"] += s["rows"]
        stats["occurrences"] += s["occurrences"]
    note = out / "changelog.json"
    if note.exists():
        c = json.loads(note.read_text())
        c["names_masked"] = {**stats, "rule": "full names found in typed messages (Presidio PERSON, score >= 0.85, "
                                              "two or more capitalised words), masked wherever they occur in the "
                                              "same session"}
        note.write_text(json.dumps(c, indent=1) + "\n")
    log(f"names: {stats['occurrences']:,} occurrences masked in {stats['rows']:,} rows")
    stats["copied_files"] = mask_copied(tier, out, found, log=log)
    return stats
