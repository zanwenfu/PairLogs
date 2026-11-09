"""Small helpers shared by every stage."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    """A file's digest, read in pieces: a transcript can run to tens of megabytes."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for piece in iter(lambda: f.read(1 << 20), b""):
            h.update(piece)
    return h.hexdigest()


def sha256_files(paths: Iterable[Path]) -> str:
    """One digest over several files, each prefixed by its name and length, so moving bytes between files changes it."""
    h = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.name):
        h.update(f"{path.name}\0{path.stat().st_size}\0".encode())
        with open(path, "rb") as f:
            for piece in iter(lambda: f.read(1 << 20), b""):
                h.update(piece)
    return h.hexdigest()


def write_atomic(path: Path, data: bytes) -> None:
    """Write a file whole or not at all."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> Iterator[dict]:
    """Every complete row; a line cut off by a stopped run is skipped, not raised."""
    if not path.exists():
        return
    with open(path, "rb") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                yield row


def safe_name(text: str) -> str:
    """A string as a file name: owner/name becomes owner__name, and nothing can climb out of its folder."""
    out = text.replace("/", "__").replace("\\", "__")
    out = "".join(c if c.isalnum() or c in "._-" else "_" for c in out)
    return out.lstrip(".") or "_"


def process_pool(workers: int):
    """A process pool whose workers start from a clean server process, not by forking this one.

    The sources run in threads; a worker forked while another thread held a lock (Python's import lock, say)
    inherits it held, and waits on it forever. A run with three sources at once stopped so, every worker and the
    main process waiting on a lock (2026-10-03). Workers started from a fork server inherit no thread's lock.
    """
    import multiprocessing
    from concurrent.futures import ProcessPoolExecutor

    ctx = multiprocessing.get_context("forkserver")
    # Workers need only the collector's modules. Without this the fork server imports the calling script again (its
    # default), which runs a script that has no `if __name__ == "__main__"` guard a second time.
    ctx.set_forkserver_preload(["collect.facts", "collect.scan", "collect.build", "collect.hfcopy_build"])
    return ProcessPoolExecutor(max_workers=workers, mp_context=ctx)
