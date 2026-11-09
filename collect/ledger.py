"""What has been collected: one row per session per source, discovery cursors, and the versions built.

An SQLite file, written by every source's threads through one connection and a
lock. A row is stored with its content digest, so a session seen again is new,
changed or the same, and only what is new or changed is fetched, scanned and
published. Every row's labels are stored whole as JSON; the published table
holds the same labels, so the ledger can be rebuilt from a published version
plus a fresh discovery.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Iterator

from .util import utc_now

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    dedup_key TEXT,
    content_sha256 TEXT,
    row TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_changed TEXT NOT NULL,
    PRIMARY KEY (source, source_id)
);
CREATE INDEX IF NOT EXISTS items_by_key ON items (dedup_key);
CREATE TABLE IF NOT EXISTS state (
    source TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated TEXT NOT NULL,
    PRIMARY KEY (source, key)
);
CREATE TABLE IF NOT EXISTS scans (
    sha256 TEXT NOT NULL,
    rules TEXT NOT NULL,
    findings TEXT NOT NULL,
    scanned_at TEXT NOT NULL,
    PRIMARY KEY (sha256, rules)
);
CREATE TABLE IF NOT EXISTS versions (
    version TEXT PRIMARY KEY,
    built_at TEXT NOT NULL,
    published_at TEXT,
    changelog TEXT
);
"""


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(path), timeout=120, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # -- sessions ---------------------------------------------------------------

    def upsert(self, row: dict) -> str:
        """Store a session's row; "new", "changed" (its content digest moved) or "same".

        A row seen again with the same content keeps its stored labels, so a
        refresh never rewrites what it did not fetch again.
        """
        source, source_id = row["source"], row["source_id"]
        digest = row.get("content_sha256")
        now = utc_now()
        with self._lock:
            old = self._db.execute("SELECT content_sha256, first_seen FROM items WHERE source=? AND source_id=?",
                                   (source, source_id)).fetchone()
            if old and old[0] == digest:
                return "same"
            first_seen = old[1] if old else now
            stored = {**row, "first_seen": first_seen, "last_changed": now}
            self._db.execute(
                "INSERT INTO items (source, source_id, dedup_key, content_sha256, row, first_seen, last_changed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (source, source_id) DO UPDATE SET "
                "dedup_key=excluded.dedup_key, content_sha256=excluded.content_sha256, row=excluded.row, "
                "last_changed=excluded.last_changed",
                (source, source_id, row.get("dedup_key"), digest, json.dumps(stored, ensure_ascii=False),
                 first_seen, now))
            return "changed" if old else "new"

    def update_labels(self, source: str, source_id: str, labels: dict) -> None:
        """Merge labels into a stored row (deduplication's, the scan's) without touching its content digest."""
        with self._lock:
            got = self._db.execute("SELECT row FROM items WHERE source=? AND source_id=?",
                                   (source, source_id)).fetchone()
            if not got:
                raise KeyError((source, source_id))
            row = {**json.loads(got[0]), **labels}
            self._db.execute("UPDATE items SET row=? WHERE source=? AND source_id=?",
                             (json.dumps(row, ensure_ascii=False), source, source_id))

    def get(self, source: str, source_id: str) -> dict | None:
        with self._lock:
            got = self._db.execute("SELECT row FROM items WHERE source=? AND source_id=?",
                                   (source, source_id)).fetchone()
        return json.loads(got[0]) if got else None

    def rows(self, source: str | None = None) -> Iterator[dict]:
        with self._lock:
            if source is None:
                got = self._db.execute("SELECT row FROM items ORDER BY source, source_id").fetchall()
            else:
                got = self._db.execute("SELECT row FROM items WHERE source=? ORDER BY source_id",
                                       (source,)).fetchall()
        for (text,) in got:
            yield json.loads(text)

    def source_ids(self, source: str) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT source_id FROM items WHERE source=? ORDER BY source_id",
                                                   (source,))]

    def count(self, source: str | None = None) -> int:
        with self._lock:
            if source is None:
                return self._db.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            return self._db.execute("SELECT COUNT(*) FROM items WHERE source=?", (source,)).fetchone()[0]

    # -- discovery state ----------------------------------------------------------

    def set_state(self, source: str, key: str, value) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO state (source, key, value, updated) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (source, key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                (source, key, json.dumps(value), utc_now()))

    def get_state(self, source: str, key: str, default=None):
        with self._lock:
            got = self._db.execute("SELECT value FROM state WHERE source=? AND key=?", (source, key)).fetchone()
        return json.loads(got[0]) if got else default

    def state_keys(self, source: str, prefix: str = "") -> dict[str, object]:
        with self._lock:
            got = self._db.execute("SELECT key, value FROM state WHERE source=? AND key LIKE ?",
                                   (source, prefix.replace("%", "\\%") + "%")).fetchall()
        return {k: json.loads(v) for k, v in got}

    # -- secret scans ------------------------------------------------------------
    # A file is scanned once per rule set: its findings are kept by its content digest and the rules' digest.

    def scanned(self, rules: str) -> set[str]:
        with self._lock:
            return {r[0] for r in self._db.execute("SELECT sha256 FROM scans WHERE rules=?", (rules,)).fetchall()}

    def record_scan(self, rules: str, results: dict[str, list]) -> None:
        now = utc_now()
        with self._lock:
            self._db.execute("BEGIN")
            self._db.executemany(
                "INSERT INTO scans (sha256, rules, findings, scanned_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (sha256, rules) DO UPDATE SET findings=excluded.findings, scanned_at=excluded.scanned_at",
                [(sha, rules, json.dumps(f), now) for sha, f in results.items()])
            self._db.execute("COMMIT")

    def findings(self, sha256: str, rules: str) -> list | None:
        with self._lock:
            got = self._db.execute("SELECT findings FROM scans WHERE sha256=? AND rules=?", (sha256, rules)).fetchone()
        return json.loads(got[0]) if got else None

    # -- versions ----------------------------------------------------------------

    def record_version(self, version: str, changelog: dict, published: bool) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO versions (version, built_at, published_at, changelog) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (version) DO UPDATE SET built_at=excluded.built_at, "
                "published_at=excluded.published_at, changelog=excluded.changelog",
                (version, utc_now(), utc_now() if published else None, json.dumps(changelog)))

    def last_published(self) -> str | None:
        with self._lock:
            got = self._db.execute("SELECT version FROM versions WHERE published_at IS NOT NULL "
                                   "ORDER BY published_at DESC LIMIT 1").fetchone()
        return got[0] if got else None
