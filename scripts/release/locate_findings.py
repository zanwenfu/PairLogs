"""Where the release check's findings are: the check's own units over a folder (tier A as published), largest first
as the check takes them, each unit's findings printed with its file, the dump and line, and the value's shape (letters
as a, digits as 9) only, never the value. The check keeps the place of a finding only in its scratch ledger; this
gives it again without the whole run: it stops once the units done have scanned --stop-gb and found --want findings.

  ERRATA_DATA_ROOT=<scratch> ERRATA_STATE_ROOT=<scratch> ERRATA_GITLEAKS=... ERRATA_NOT_SECRETS=...
  ERRATA_KNOWN_SECRETS=... python locate_findings.py TIER --procs 16 --stop-gb 14 --want 1
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import FIRST_COMPLETED, wait
from pathlib import Path

from collect import check, config
from collect.ledger import Ledger
from collect.util import process_pool


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tier", type=Path)
    ap.add_argument("--procs", type=int, default=8)
    ap.add_argument("--stop-gb", type=float, default=14.0)
    ap.add_argument("--want", type=int, default=1, help="findings to find before stopping at --stop-gb")
    a = ap.parse_args()
    cfg = config.load()
    ledger = Ledger(cfg.ledger)
    work = cfg.data / "tmp" / f"locate-{os.getpid()}"
    work.mkdir(parents=True)
    result = {"files": 0}
    units = check._units(ledger, a.tier, result)
    marks = check.machine_marks(cfg)
    chunk = max(check.CHUNK_BYTES // a.procs, 1)
    jobs = [(i, kind, path, extra, marks, str(work), chunk) for i, (kind, path, extra) in enumerate(units)]
    order = sorted(jobs, key=lambda j: -os.stat(j[2]).st_size)
    print(f"{len(order):,} units; largest first", flush=True)
    scanned, found, done = 0, 0, 0
    pending, nxt = {}, 0
    with process_pool(a.procs) as pool:
        while pending or nxt < len(order):
            stop = scanned >= a.stop_gb * 1e9 and found >= a.want
            while not stop and nxt < len(order) and len(pending) < a.procs:
                pending[pool.submit(check._unit, order[nxt])] = order[nxt]
                nxt += 1
            if not pending:
                break
            ready, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in ready:
                job = pending.pop(fut)
                r = fut.result()
                done += 1
                scanned += r["scanned_bytes"]
                n = sum(r["findings"].values())
                if n or r["machine_values"]:
                    found += n
                    print(json.dumps({"rank": order.index(job), "kind": job[1],
                                      "file": str(Path(job[2]).relative_to(a.tier)),
                                      "span": job[3][1] if job[1] == "copied" and len(job[3]) > 1 else None,
                                      "findings": dict(r["findings"]), "examples": r["examples"],
                                      "machine_values": r["machine_values"]}, default=str), flush=True)
            if done % 50 == 0:
                print(f"{done:,} units, {scanned / 1e9:,.1f} GB scanned, {found} findings", flush=True)
    print(f"stopped: {done:,} of {len(order):,} units, {scanned / 1e9:,.1f} GB scanned, {found} findings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
