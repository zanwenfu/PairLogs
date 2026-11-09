"""A built version checked from its published files, as a reader gets them, before it can be published.

The build masks and scans its staged text until a scan finds nothing; this reads the version back instead:
- every file's content from the Parquet subsets;
- the conversations tables' free text, a cell a line, in the parts the build scanned (conv_build), and every
  string of theirs for values naming the run machine;
- the copied datasets' files (a Parquet file as one JSON line per row);
- `copied/matches.parquet`, the card and the changelog.

It scans that text again with the same rules, a file to a worker on every core, each worker writing out its file's
text, scanning it a piece at a time and removing it. It also looks for any value naming the run machine: its home,
data and state folders, in labels and in contents. `collect publish` refuses a version without a passing check
recorded for it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pyarrow.dataset as ds
import pyarrow.parquet as pq

from . import conv_build, scan
from .config import Config
from .hfcopy_build import _text_columns, view_parts, view_span
from .ledger import Ledger

CHUNK_BYTES = 4 << 30  # text written out at once, over all the workers (each scans its share at CHUNK_BYTES / procs)
BATCH = 16
SPLIT_BYTES = 512 << 20  # a copied Parquet file larger than this has its parts shared among units (_spans)
LOG_BYTES = 4e9  # progress logged about this often, in bytes scanned


def machine_marks(cfg: Config) -> list[str]:
    """Strings that would name the run machine in a published value."""
    marks = {str(Path.home()), str(cfg.data.resolve()), str(cfg.state.resolve())}
    return sorted(m for m in marks if len(m) > 4)


class _Pass:
    """Published text written out a file at a time, as a reader gets each file, and scanned once about `chunk` bytes
    are waiting; published files on disk are scanned where they are. Never two files in one dump: gitleaks' private-key
    pattern runs from a header to the next `KEY-----` anywhere after it, so a header in one file and a key's end in
    the next made a finding in neither (seen on 2026-10-03, when dumps were concatenated). One to a worker (_unit):
    gitleaks reads each dump as a file of its own, so where it is scanned does not change what is found."""

    def __init__(self, work: Path, result: dict, chunk: int):
        self.work, self.result, self.chunk = work, result, chunk
        self.dumps = work / "dumps"
        self.dumps.mkdir(parents=True)
        self.pending: list[Path] = []
        self.size = 0
        self.n = 0

    def _new(self, suffix: str) -> Path:
        self.n += 1
        path = self.dumps / f"{self.n:08d}{suffix}"
        self.pending.append(path)
        return path

    def text(self, text: str, name: str) -> None:
        """One published file's text, in a dump of its own (named for its kind, as the build named it)."""
        path = self._new(".jsonl.txt" if ".jsonl" in name else ".json.txt" if name.endswith(".json") else ".txt")
        path.write_text(text, encoding="utf-8")
        self._grow(len(text))

    def lines(self, rows) -> None:
        """One published Parquet file, a JSON line per row, in a dump of its own."""
        path = self._new(".jsonl.txt")
        size = 0
        with open(path, "w", encoding="utf-8") as fh:
            for line in rows:
                fh.write(line + "\n")
                size += len(line) + 1
        self._grow(size)

    def file(self, path: Path) -> None:
        self.pending.append(path)
        self._grow(path.stat().st_size)

    def _grow(self, n: int) -> None:
        self.size += n
        if self.size >= self.chunk:
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        found = scan.find_all(self.pending, self.work / "scan", 1)   # one gitleaks: the worker is one of `procs`
        for path, fs in found.items():
            for f in fs:
                self.result["findings"][f["rule"]] += 1
                if len(self.result["examples"]) < 10:
                    shape = re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", f["secret"]))[:24]
                    self.result["examples"].append({"rule": f["rule"], "file": Path(path).name, "line": f["line"],
                                                    "shape": shape})
        self.result["scanned_bytes"] += self.size
        for path in self.pending:
            if path.parent == self.dumps:  # the dumps only: a published file is never touched
                path.unlink()
        self.pending, self.size = [], 0


def _personal(r: dict, text: str, where: str, json_like: bool = False) -> None:
    """An email or a home-path name the build would still mask: the scraped text is published masked for them."""
    if not text or not ("@" in text or "%40" in text or "sers" in text or "home" in text or "users" in text.lower()):
        return   # HOME_WINDOWS reads `users` in any case: `C:\\USERS\\` too (issue #11)
    try:
        _, counts = scan.redact(text, [], pii=True, json_like=json_like)
    except RuntimeError:
        counts = {"emails": 0, "home_users": 1}
    n = counts["emails"] + counts["home_users"]
    if n:
        r["findings"]["personal data (email or home-path name)"] += n
        if len(r["examples"]) < 10:
            r["examples"].append({"rule": "personal", "where": where, "count": n})


def _machine(r: dict, marks: list[str], value: str, where: str) -> None:
    if any(m in value for m in marks):
        r["machine_values"] += 1
        if len(r["machine_examples"]) < 10:
            r["machine_examples"].append(where)


def _data(r: dict, p: _Pass, marks: list[str], path: Path, sub: str, schema) -> None:
    """A Parquet file of a data subset: each row's values, and each of its files' content as it is and decoded. Read
    with its subset's schema, as the subset read whole gives its rows."""
    for batch in ds.dataset(str(path), format="parquet", schema=schema).to_batches(batch_size=BATCH):
        for row in batch.to_pylist():
            r["rows"] += 1
            where = f"{sub}:{row.get('source_id')}"
            _machine(r, marks, json.dumps({k: v for k, v in row.items() if k != "files"}, ensure_ascii=False), where)
            for f in row.get("files") or []:
                r["files"] += 1
                content = f.get("content") or ""
                _machine(r, marks, content, f"{where}:{f.get('path')}")
                _personal(r, content, f"{where}:{f.get('path')}", scan.json_like(f.get("path") or ""))
                p.text(content, f.get("path") or "")
                if scan.json_like(f.get("path") or ""):   # and as its text reads decoded (build._decoded)
                    p.lines(conv_build.decoded_lines(content))


def _conversations(r: dict, p: _Pass, marks: list[str], path: Path, table: str) -> None:
    """A conversations file: every string for values naming the machine; its view, a cell a line, in the parts the
    build scanned, for personal data (the scraped sources' rows), a private key's structure, and the scan."""
    r["files"] += 1
    where = f"{table}:{path.parent.name}"   # once a file: per value, 12 million path objects in a sample's slice
    for value in conv_build.strings(str(path)):
        _machine(r, marks, value, where)
    scraped = path.parent.name not in conv_build.COPIED   # the scraped sources' rows are masked for personal data
    for part in conv_build.view_parts(str(path)):
        if scraped:
            for line in part:
                _personal(r, conv_build.decode(line) or "", where)
        if any(scan.pem_spans(line) for line in part if scan.PEM_HEADER.search(line) or scan.PEM_END.search(line)):
            r["findings"]["private-key (structure)"] += 1
            if len(r["examples"]) < 10:
                r["examples"].append(f"{where}: a private key's header")
        p.lines(part)
    r["conversation_rows"] += pq.ParquetFile(path).metadata.num_rows


def _copied(r: dict, p: _Pass, path: Path, got: dict, span: tuple[int, int] | None = None) -> None:
    """A copied dataset's file: a JSON file also as its text reads decoded, as the build scanned it; a Parquet file in
    the parts the build scanned (a cell a line, VIEW_LINES lines a part), as they are and decoded; any other file where
    it is. gitleaks' findings depend on the text around them, so a check in another layout could fail a version the
    build left clean."""
    r["files"] += 0 if span and span[0] else 1   # a file shared among units counts once
    if scan.json_like(path.name):
        p.lines(conv_build.decoded_lines(path))
    if path.suffix == ".parquet":
        cols = _text_columns(pq.ParquetFile(path).schema_arrow)
        parts = (view_span(str(path), cols, got["starts"], got["lines"], *span) if span
                 else view_parts(str(path), cols, got.get("starts"), got.get("lines")))
        for part in parts:
            p.lines(part)
            p.lines(conv_build.decoded_lines("\n".join(part)))   # its cells as they read decoded, as built
    else:
        p.file(path)


def _unit(job: tuple) -> dict:
    """One published file checked, in a worker (or in this process with one): its counts, what it found, and the bytes
    it scanned. Its text is written out under a folder of its own, scanned about every `chunk` bytes, and removed."""
    i, kind, path, extra, marks, work, chunk = job
    r = {"rows": 0, "files": 0, "conversation_rows": 0, "scanned_bytes": 0, "machine_values": 0,
         "machine_examples": [], "findings": Counter(), "examples": []}
    folder = Path(work) / f"unit-{i:07d}"
    try:
        p = _Pass(folder, r, chunk)
        path = Path(path)
        if kind == "data":
            _data(r, p, marks, path, *extra)
        elif kind == "conversations":
            _conversations(r, p, marks, path, *extra)
        elif kind == "copied":
            _copied(r, p, path, *extra)
        else:   # the card and the changelog
            _machine(r, marks, path.read_text(), path.name)
            p.file(path)
        p.flush()
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    return r


def run(cfg: Config, ledger: Ledger, version_dir: Path, procs: int | None = None, log=print,
        base: Path | None = None) -> dict:
    """The check, its text written out a piece at a time and removed whatever happens (a failed check once left
    gigabytes of dumps on a shared disk)."""
    # Its own folder, this run's alone: two checks shared one, and each removed it, under the other's units, at its
    # start and its end (2026-10-05: version 2026-10-04's two builds' checks, one of them crashed by the other).
    work = cfg.data / "tmp" / f"check-{version_dir.name}-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    try:
        return _run(cfg, ledger, version_dir, work, procs or os.cpu_count() or 4, log, base)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _spans(path: Path, got: dict) -> list:
    """A copied Parquet file's parts in ranges of about SPLIT_BYTES of the file each, for workers to share (one 9.4 GB
    table on one worker took over five hours, 2026-10-05); [None] (the file whole) for any other file, a small one, or
    one whose recorded lines are not its rows' cells (view_parts then stops the check as it would)."""
    starts, lines = got.get("starts") or [], got.get("lines")
    size = path.stat().st_size
    if path.suffix != ".parquet" or size <= SPLIT_BYTES or len(starts) < 2 or lines is None:
        return [None]
    pf = pq.ParquetFile(path)
    if pf.metadata.num_rows * len(_text_columns(pf.schema_arrow)) != lines:
        return [None]
    n = min(len(starts), -(-size // SPLIT_BYTES))
    cuts = [len(starts) * i // n for i in range(n + 1)]
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]


def _units(ledger: Ledger, version_dir: Path, result: dict, base: Path | None = None) -> list[tuple]:
    """Every published file to check, (kind, path, its extra arguments), in the order a reader meets them. With
    `base` (the version an incremental build carried files over from), only what holds new text: a table file whose
    path the version before has too is that file, or it less some sessions' rows (or a session row's also_in moved),
    all checked then; a copied file is passed over only when it is the version before's very file (a dataset staged
    again keeps its paths, its content new)."""
    units: list[tuple] = []
    carried = lambda p: base is not None and (base / Path(p).relative_to(version_dir)).exists()
    same_file = lambda p: carried(p) and os.stat(p).st_ino == os.stat(base / Path(p).relative_to(version_dir)).st_ino
    data = version_dir / "data"
    for sub in sorted(data.iterdir()) if data.exists() else []:
        dset = ds.dataset(str(sub), format="parquet")
        units += [("data", f, (sub.name, dset.schema)) for f in dset.files if not carried(f)]
        result["carried_files"] = result.get("carried_files", 0) + sum(1 for f in dset.files if carried(f))
    for table in conv_build.TABLES:
        root = version_dir / table
        paths = sorted(root.rglob("*.parquet")) if root.exists() else []
        units += [("conversations", str(path), (table,)) for path in paths if not carried(path)]
        result["carried_files"] = result.get("carried_files", 0) + sum(1 for p in paths if carried(p))
    copied = version_dir / "copied"
    view_starts = ledger.get_state("view_starts", version_dir.name, {}) or {}   # where the build's parts began
    for path in sorted(copied.rglob("*")) if copied.exists() else []:
        if not path.is_file():
            continue
        if path.name == "matches.parquet" and path.parent == copied:
            result["files"] += 1
            continue  # the build's own index of identifiers (dataset, file, row, session id), never scanned by it
        if same_file(path):
            result["carried_files"] = result.get("carried_files", 0) + 1
            continue
        got = view_starts.get(str(path.relative_to(version_dir))) or {}
        units += [("copied", str(path), (got,) + ((span,) if span else ())) for span in _spans(path, got)]
    units += [("doc", str(version_dir / doc), ()) for doc in ("README.md", "changelog.json")
              if (version_dir / doc).exists()]   # an incremental build writes no card
    return units


def _run(cfg: Config, ledger: Ledger, version_dir: Path, work: Path, procs: int, log, base: Path | None = None) -> dict:
    marks = machine_marks(cfg)
    changelog = json.loads((version_dir / "changelog.json").read_text())
    result = {"version": version_dir.name, "built_at": changelog.get("built_at"), "rows": 0, "files": 0,
              "scanned_bytes": 0, "machine_values": 0, "machine_examples": [], "findings": Counter(), "examples": [],
              "conversation_rows": 0}
    chunk = max(CHUNK_BYTES // procs, 1)
    jobs = [(i, kind, path, extra, marks, str(work), chunk)
            for i, (kind, path, extra) in enumerate(_units(ledger, version_dir, result, base))]
    # Largest first, so the last file a worker takes is a small one: one process at a time, a sample's check spent
    # three quarters of its time outside the scan, on its personal-data and decoded readings (the partner's
    # profile of rc8, 2026-10-04).
    order = sorted(jobs, key=lambda j: -os.stat(j[2]).st_size)
    got: dict[int, dict] = {}
    scanned, logged = 0, 0.0
    from .util import process_pool

    pool = process_pool(procs) if procs > 1 else None
    try:
        for job, r in zip(order, pool.map(_unit, order, chunksize=1) if pool else map(_unit, order)):
            got[job[0]] = r
            scanned += r["scanned_bytes"]
            if scanned - logged >= LOG_BYTES:
                logged = scanned
                log(f"check: scanned {scanned / 1e9:,.1f} GB; findings so far "
                    f"{sum(sum(x['findings'].values()) for x in got.values())}")
    finally:
        if pool:
            pool.shutdown(cancel_futures=True)
    for i in sorted(got):   # in the order a reader meets the files, so the examples kept are the first ones met
        r = got[i]
        for k in ("rows", "files", "conversation_rows", "scanned_bytes", "machine_values"):
            result[k] += r[k]
        result["findings"].update(r["findings"])
        result["machine_examples"] += r["machine_examples"][:max(0, 10 - len(result["machine_examples"]))]
        result["examples"] += r["examples"][:max(0, 10 - len(result["examples"]))]
    result["findings"] = dict(result["findings"])
    result["ok"] = not result["findings"] and not result["machine_values"]
    result["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ledger.set_state("check", version_dir.name, result)
    log(f"check: {version_dir.name}: {result['rows']:,} rows, {result['conversation_rows']:,} conversation rows, "
        f"{result['files']:,} files, "
        f"{result['scanned_bytes'] / 1e9:,.1f} GB scanned; findings {result['findings'] or 0}; values naming the run "
        f"machine {result['machine_values']}: {'PASS' if result['ok'] else 'FAIL'}")
    return result


def passed(ledger: Ledger, version_dir: Path) -> bool:
    """Whether this very build passed its check (a version built again under the same name has a new time)."""
    got = ledger.get_state("check", version_dir.name) or {}
    built_at = json.loads((version_dir / "changelog.json").read_text()).get("built_at")
    return bool(got.get("ok")) and got.get("built_at") == built_at
