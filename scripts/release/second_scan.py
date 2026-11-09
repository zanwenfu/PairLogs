"""A second, independent secret scanner (TruffleHog, its own detectors, verification off) over a tier as published, read
exactly as the release check reads it (collect.check's units and dumps): each worker runs TruffleHog where the check
runs gitleaks. Nothing is verified against any service (--no-verification): a found key is never used.

Prints, and writes as JSON, the findings by detector (and distinct values by their sha256) and where they are. The
values themselves are never printed or stored: a private JSON-lines file (mode 600) holds, for each finding, the unit's
published file, the line, the detector, the value's sha256, length and shape (letters a, digits 9).

  ERRATA_DATA_ROOT=<scratch> ERRATA_STATE_ROOT=<scratch> PYTHONPATH=<code> python second_scan.py TIER OUT.json HITS.jsonl
      --trufflehog PATH [--procs 16] [--every N] [--control] [--files LIST --want SHA256S --values-out VALUES.jsonl]
  --every N: only every N-th unit (largest first), to time a sample.  --control: a planted token in a conversations
  file and in a copied file (tier_check's controls) must be found, or it exits 2.
  --files: only the units of these published files (one path a line, relative to TIER, as the hits file names them).
  --want / --values-out: the values whose sha256 is listed in --want (one a line) are written, one JSON string a line,
  to --values-out (mode 600), for mask_finding.py --values to mask; never printed. It exits 1 if a wanted sha256 is
  not found again.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from pathlib import Path

from collect import check, config
from collect.ledger import Ledger

TH = None          # the TruffleHog binary, set in each worker
HITS = None        # this worker's private hits file
WANT = frozenset()  # sha256s whose values this worker writes to VALS
VALS = None        # this worker's private values file
UNIT = re.compile(r"unit-(\d{7})")


def shape(s: str) -> str:
    return re.sub(r"[A-Za-z]", "a", re.sub(r"\d", "9", s))


PREFIX = re.compile(r"^([a-z][a-z0-9+.-]{1,15}://|[A-Za-z]{2,12}[_-](?:[A-Za-z]{2,8}[_-])?)")
PLACEHOLDER = re.compile(r"^(pass(word)?|pwd|secret|changeme|example|xxx+|\*+|test|admin|root|postgres|user|"
                         r"your[_-]?\w*|my[_-]?\w*)$|[<>${}]", re.I)


def prefix(raw: str) -> str:
    """A value's public format marker only (ghp_, glpat-, npm_, sk-, postgres://, ...): never any of its secret part."""
    m = PREFIX.match(raw or "")
    return m.group(1) if m else ""


def placeholder_password(raw: str) -> bool | None:
    """For a URI with credentials: whether its password is a placeholder (password, changeme, ${VAR}, <...>, ...)."""
    m = re.match(r"^[a-z][a-z0-9+.-]*://[^:/@\s]*:([^@\s]*)@", raw or "")
    return None if not m else bool(PLACEHOLDER.search(m.group(1)) or not m.group(1))


def th_find_all(paths, work, procs=1) -> dict:
    """scan.find_all's contract, by TruffleHog: {path: [{"rule", "secret", "line"}]}; each finding also logged, without
    its value, to the worker's private hits file."""
    paths = [str(p) for p in paths]
    if not paths:
        return {}
    p = subprocess.run([TH, "filesystem", *paths, "--no-verification", "--no-update", "--json", "--no-color",
                        "--results=unverified,unknown", "--concurrency=1"], capture_output=True, text=True)
    if p.returncode not in (0, 183):
        raise RuntimeError(f"trufflehog exit {p.returncode}: {p.stderr[-500:]}")
    out = defaultdict(list)
    with open(HITS, "a", encoding="utf-8") as log:
        for line in p.stdout.splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if "DetectorName" not in d:
                continue
            fs = ((d.get("SourceMetadata") or {}).get("Data") or {}).get("Filesystem") or {}
            raw = d.get("Raw") or ""
            f = {"rule": f"trufflehog:{d['DetectorName']}", "secret": raw, "line": fs.get("line")}
            out[fs.get("file") or ""].append(f)
            sha = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()
            log.write(json.dumps({"path": fs.get("file"), "line": fs.get("line"), "detector": d["DetectorName"],
                                  "decoder": d.get("DecoderName"), "verified": bool(d.get("Verified")),
                                  "sha256": sha, "len": len(raw), "shape": shape(raw)[:24], "prefix": prefix(raw),
                                  "placeholder_password": placeholder_password(raw)}) + "\n")
            if sha in WANT:
                fd = os.open(VALS, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as v:
                    v.write(json.dumps(raw) + "\n")
    return dict(out)


def init(trufflehog: str, hits_dir: str, want: frozenset = frozenset()) -> None:
    global TH, HITS, WANT, VALS
    from collect import scan
    TH = trufflehog
    HITS = os.path.join(hits_dir, f"hits-{os.getpid()}.jsonl")
    WANT, VALS = want, os.path.join(hits_dir, f"values-{os.getpid()}.jsonl")
    scan.find_all = th_find_all   # _Pass.flush looks it up when it runs


def pool(procs: int, trufflehog: str, hits_dir: str, want: frozenset = frozenset()) -> ProcessPoolExecutor:
    ctx = multiprocessing.get_context("forkserver")
    ctx.set_forkserver_preload(["collect.facts", "collect.scan", "collect.build", "collect.hfcopy_build"])
    return ProcessPoolExecutor(max_workers=procs, mp_context=ctx, initializer=init,
                               initargs=(trufflehog, hits_dir, want))


def run(tier: Path, procs: int, trufflehog: str, hits_dir: Path, every: int = 1, files: set | None = None,
        want: frozenset = frozenset()) -> dict:
    cfg = config.load()
    ledger = Ledger(cfg.ledger)
    work = cfg.data / "tmp" / f"second-scan-{os.getpid()}"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        result = {"files": 0}
        units = check._units(ledger, tier, result)
        marks = check.machine_marks(cfg)
        chunk = max(check.CHUNK_BYTES // procs, 1)
        jobs = [(i, kind, path, extra, marks, str(work), chunk) for i, (kind, path, extra) in enumerate(units)]
        if files is not None:
            jobs = [j for j in jobs if str(Path(j[2]).relative_to(tier)) in files]
        order = sorted(jobs, key=lambda j: -os.stat(j[2]).st_size)[::every]
        unit_path = {j[0]: str(Path(j[2]).relative_to(tier)) for j in order}
        got = Counter()
        scanned, done = 0, 0
        with pool(procs, trufflehog, str(hits_dir), want) as ex:
            pending, nxt = {}, 0
            while pending or nxt < len(order):
                while nxt < len(order) and len(pending) < 2 * procs:
                    pending[ex.submit(check._unit, order[nxt])] = order[nxt]
                    nxt += 1
                ready, _ = wait(pending, return_when=FIRST_COMPLETED)
                for fut in ready:
                    pending.pop(fut)
                    r = fut.result()
                    done += 1
                    scanned += r["scanned_bytes"]
                    got.update({k: v for k, v in r["findings"].items() if k.startswith("trufflehog:")})
                    if done % 100 == 0:
                        print(f"{done:,} of {len(order):,} units, {scanned / 1e9:,.1f} GB, "
                              f"{sum(got.values()):,} findings", flush=True)
        return {"units": len(order), "of_units": len(jobs), "scanned_bytes": scanned, "findings": dict(got),
                "unit_path": unit_path}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def gather(hits_dir: Path, unit_path: dict, tier: Path) -> list[dict]:
    """Every worker's hits, each with the published file it came from (a dump's unit, or the file itself)."""
    hits = []
    for f in sorted(hits_dir.glob("hits-*.jsonl")):
        for line in open(f, encoding="utf-8"):
            h = json.loads(line)
            m = UNIT.search(h["path"] or "")
            if m:
                h["published_file"] = unit_path.get(int(m.group(1)), "?")
            else:
                try:
                    h["published_file"] = str(Path(h["path"]).relative_to(tier))
                except ValueError:
                    h["published_file"] = "?"
            del h["path"]
            hits.append(h)
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tier", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("hits", type=Path)
    ap.add_argument("--trufflehog", required=True)
    ap.add_argument("--procs", type=int, default=16)
    ap.add_argument("--every", type=int, default=1)
    ap.add_argument("--control", action="store_true")
    ap.add_argument("--files", type=Path)
    ap.add_argument("--want", type=Path)
    ap.add_argument("--values-out", type=Path)
    a = ap.parse_args()
    if (a.want is None) != (a.values_out is None):
        ap.error("--want and --values-out go together")
    files = {l.strip() for l in open(a.files) if l.strip()} if a.files else None
    want = frozenset(l.strip() for l in open(a.want) if l.strip()) if a.want else frozenset()
    os.umask(0o077)
    hits_dir = a.hits.with_name(a.hits.name + ".parts")
    shutil.rmtree(hits_dir, ignore_errors=True)
    hits_dir.mkdir(parents=True)
    if a.control:
        spec = importlib.util.spec_from_file_location("tier_check", Path(__file__).with_name("tier_check.py"))
        tc = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(tc)
        work = hits_dir / "controls"
        for kind in ("conversations", "copied"):
            ctl = tc.control(a.tier, work, kind)
            cdir = hits_dir / f"control-{kind}"
            cdir.mkdir()
            r = run(ctl, 2, a.trufflehog, cdir)
            ok = any("github" in k.lower() for k in r["findings"])
            print(f"control, a token planted in a {kind} file: {r['findings']}: {'found' if ok else 'NOT FOUND'}")
            shutil.rmtree(ctl, ignore_errors=True)
            if not ok:
                return 2
        shutil.rmtree(work, ignore_errors=True)
    r = run(a.tier, a.procs, a.trufflehog, hits_dir, a.every, files, want)
    hits = gather(hits_dir, r.pop("unit_path"), a.tier)
    missing = 0
    if a.values_out:
        vals = {json.loads(l) for f in sorted(hits_dir.glob("values-*.jsonl")) for l in open(f, encoding="utf-8")}
        fd = os.open(a.values_out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as v:
            for raw in sorted(vals):
                v.write(json.dumps(raw) + "\n")
        got = {hashlib.sha256(x.encode("utf-8", "surrogatepass")).hexdigest() for x in vals}
        missing = len(want - got)
        print(f"values written: {len(vals):,} for {len(want & got):,} of {len(want):,} wanted sha256s; "
              f"not found again: {missing:,}")
    with open(a.hits, "w", encoding="utf-8") as fh:
        for h in hits:
            fh.write(json.dumps(h) + "\n")
    shutil.rmtree(hits_dir, ignore_errors=True)
    by = Counter(h["detector"] for h in hits)
    distinct = {d: len({h["sha256"] for h in hits if h["detector"] == d}) for d in by}
    nfiles = {d: len({h["published_file"] for h in hits if h["detector"] == d}) for d in by}
    # each detector's distinct values by their format prefix; a prefix shown only when 3 or more distinct values share
    # it (a common format marker, ghp_ or postgres://), never a rarer one (it could be part of someone's secret)
    formats = {}
    for d in by:
        vals = {h["sha256"]: h for h in hits if h["detector"] == d}
        pre = Counter(h["prefix"] or "(none)" for h in vals.values())
        formats[d] = {(p if n >= 3 or p == "(none)" else "(other)"): 0 for p, n in pre.items()}
        for p, n in pre.items():
            formats[d][p if n >= 3 or p == "(none)" else "(other)"] += n
        ph = Counter(h["placeholder_password"] for h in vals.values() if h["placeholder_password"] is not None)
        if ph:
            formats[d]["URI password a placeholder / not"] = f"{ph[True]} / {ph[False]}"
    summary = {**r, "findings_logged": len(hits), "by_detector": dict(by.most_common()), "distinct_values": distinct,
               "files": nfiles, "formats_of_distinct_values": formats, "verified": sum(h["verified"] for h in hits)}
    a.out.write_text(json.dumps(summary, indent=1))
    print(f"scanned {r['scanned_bytes'] / 1e9:,.2f} GB in {r['units']:,} of {r['of_units']:,} units; "
          f"{len(hits):,} findings, {sum(distinct.values()):,} distinct values")
    for d, n in by.most_common(40):
        print(f"  {d}: {n:,} findings, {distinct[d]:,} distinct values, in {nfiles[d]:,} files; formats {formats[d]}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
