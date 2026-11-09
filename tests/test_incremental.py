"""An incremental version (build.version with `base`): what decides that a session is read again, and how the
version before's files are carried over."""

import json
import os
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from collect import build

ALSO = pa.list_(pa.struct([("source", pa.string()), ("source_id", pa.string()), ("found_url", pa.string())]))
ROW = {"source": "entire", "source_id": "o/r:s1", "dedup_key": "claude-code:s1", "content_sha256": "c1",
       "repo": "o/r", "repo_license": "mit", "agent": "Claude Code", "duplicate_of": None, "also_in": [],
       "files": [{"path": "full.jsonl", "sha256": "f1", "local": "/data/raw/f1"}],
       "extra": {"checkpoint_id": "abc", "oids": ["x"], "facts_version": 3}}


def test_a_sessions_fingerprint_moves_with_what_it_is_made_from_and_nothing_else(monkeypatch):
    fp = build.inputs_fp(ROW)
    assert build.inputs_fp(dict(reversed(list(ROW.items())))) == fp                      # order of keys
    assert build.inputs_fp({**ROW, "also_in": [{"source": "dataclaw", "source_id": "z", "found_url": None}]}) == fp
    assert build.inputs_fp({**ROW, "extra": {**ROW["extra"], "oids": ["y"], "facts_version": 4}}) == fp
    assert build.inputs_fp({**ROW, "files": [{**ROW["files"][0], "local": "/elsewhere/f1"}]}) == fp   # where it sits
    for changed in ({"files": [{**ROW["files"][0], "sha256": "f2"}]}, {"content_sha256": "c2"},
                    {"duplicate_of": "swe-chat-v2:s1"}, {"repo_license": "apache-2.0"},
                    {"extra": {**ROW["extra"], "checkpoint_id": "abd"}}, {"agent": "Codex"}):
        assert build.inputs_fp({**ROW, **changed}) != fp, changed
    copied = {**ROW, "source": "dataclaw", "_read": [("conversations.jsonl:3", "/data/copied/a/r1/c.jsonl")],
              "extra": {"dataset": "u/data", "revision": "r1"}}
    assert build.inputs_fp({**copied, "_read": [("conversations.jsonl:3", "/moved/c.jsonl")]}) == build.inputs_fp(copied)
    assert build.inputs_fp({**copied, "extra": {"dataset": "u/data", "revision": "r2"}}) != build.inputs_fp(copied)
    assert build.dataset_label(copied) == "dataclaw/u__data" and build.dataset_label({**copied, "source": "swe-chat-v2"}) == "swe-chat-v2"
    monkeypatch.setattr(build, "READ_RULES", "a later rule")
    assert build.inputs_fp(ROW) != fp   # a change of the rules reads every session again


def _table(path, rows, schema):
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), str(path))


def test_the_version_befores_files_are_linked_written_again_without_what_moved_or_patched(tmp_path):
    base, out = tmp_path / "2026-10-04", tmp_path / ".2026-10-11.part"
    data = pa.schema([("session_key", pa.string()), ("also_in", ALSO), ("n", pa.int64())])
    conv = pa.schema([("session_key", pa.string()), ("turn_number", pa.int64())])
    _table(base / "data/entire/part-000-00000.parquet",
           [{"session_key": k, "also_in": [], "n": i} for i, k in enumerate(["entire:a", "entire:b", "entire:c"])], data)
    _table(base / "data/aider/part-000-00000.parquet", [{"session_key": "aider:x", "also_in": [], "n": 9}], data)
    _table(base / "data/specstory/part-000-00000.parquet", [{"session_key": "specstory:gone", "also_in": [], "n": 1}], data)
    _table(base / "conversations/entire/part-000.parquet",
           [{"session_key": k, "turn_number": t} for k in ("entire:a", "entire:b", "entire:c") for t in range(2)], conv)
    _table(base / "conversations_subagents/entire/part-000.parquet", [{"session_key": "entire:a", "turn_number": 0}], conv)
    for rel in ("copied/dataclaw/u__keep/conversations.jsonl", "copied/dataclaw/u__again/conversations.jsonl",
                "copied/swe-chat-v2/S__C/sessions.parquet", "copied/matches.parquet"):
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text(rel)
    _table(out / "data/entire/part-2026-10-11-000-00000.parquet", [{"session_key": "entire:b", "also_in": [], "n": 1}], data)
    (out / "copied/dataclaw/u__again").mkdir(parents=True)
    (out / "copied/dataclaw/u__again/conversations.jsonl").write_text("staged again")
    moved = [{"source": "dataclaw", "source_id": "u/keep:conversations.jsonl:1", "found_url": None}]
    got = build._merge(base, out, drop={"entire:b", "specstory:gone"}, also_of={"entire:c": moved},
                       copied_keep={"dataclaw/u__keep", "swe-chat-v2"})
    d = pq.read_table(out / "data/entire/part-000-00000.parquet").to_pylist()
    assert [(r["session_key"], r["also_in"]) for r in d] == [("entire:a", []), ("entire:c", moved)]
    assert [r["session_key"] for r in pq.read_table(out / "conversations/entire/part-000.parquet").to_pylist()] == \
           ["entire:a", "entire:a", "entire:c", "entire:c"]
    same = lambda rel: os.stat(out / rel).st_ino == os.stat(base / rel).st_ino
    assert same("conversations_subagents/entire/part-000.parquet") and same("data/aider/part-000-00000.parquet")
    assert not same("data/entire/part-000-00000.parquet")
    assert not (out / "data/specstory/part-000-00000.parquet").exists()                   # every row gone
    assert same("copied/dataclaw/u__keep/conversations.jsonl") and same("copied/swe-chat-v2/S__C/sessions.parquet")
    assert (out / "copied/dataclaw/u__again/conversations.jsonl").read_text() == "staged again"
    assert not (out / "copied/matches.parquet").exists()                                    # computed again
    assert got["data: rows dropped"] == 2 and got["data: also_in patched"] == 1 and got["data: files emptied"] == 1
    assert got["conversations: rows dropped"] == 2 and got["copied: files linked"] == 2
    _table(base / "conversations/aider/part-2026-10-11-000.parquet", [{"session_key": "aider:x", "turn_number": 0}], conv)
    _table(out / "conversations/aider/part-2026-10-11-000.parquet", [{"session_key": "aider:x", "turn_number": 0}], conv)
    with pytest.raises(RuntimeError):   # a name in both: never written over
        build._merge(base, out, drop=set(), also_of={}, copied_keep=set())


def test_the_counts_are_read_from_the_versions_own_tables(tmp_path):
    rows = [{**{f.name: None for f in build.SCHEMA}, "session_key": f"entire:{k}", "duplicate_of": dup,
             "redactions": {"secrets": s, "emails": e, "home_users": 0}, "also_in": [], "models": [],
             "files": [{"path": p, "bytes": 1, "sha256": "x", "content": "y"} for p in files],
             "source_facts": json.dumps(facts)}
            for k, dup, s, e, files, facts in [("a", None, 2, 1, ["f1", "f2"], {}),
                                               ("b", None, 0, 0, [], {"conversations_error": "no reader for agent 'Zed'"}),
                                               ("c", "entire:a", 0, 0, [], {})]]
    _table(tmp_path / "data/entire/part-x-00000.parquet", rows, build.SCHEMA)
    conv = pa.schema([("session_key", pa.string())])
    _table(tmp_path / "conversations/entire/part-x.parquet", [{"session_key": "entire:a"}] * 5, conv)
    _table(tmp_path / "conversations/entire/part-y.parquet", [{"session_key": "entire:a"}] * 2, conv)
    scraped, tables = build._final_counts(tmp_path, {"entire": {"new": 3, "changed": 1}})
    assert scraped == {"entire": {"rows": 3, "files": 2, "duplicates": 1, "secrets": {"masked": 2}, "emails": 1,
                                  "home_users": 0, "new": 3, "changed": 1}}
    assert tables == {"entire": {"rows": 7, "subagent_rows": 0, "sessions": 2,
                                 "sessions_without_rows": {"no reader for agent 'Zed'": 1}}}


def test_an_incremental_versions_check_reads_only_what_holds_new_text(tmp_path):
    from collect import check

    class Ledger:
        def get_state(self, *a, **k):
            return {}

    base, v = tmp_path / "2026-10-04", tmp_path / "2026-10-11"
    conv = pa.schema([("session_key", pa.string()), ("turn_number", pa.int64())])
    _table(base / "conversations/entire/part-000.parquet", [{"session_key": "entire:a", "turn_number": 0}], conv)
    (v / "conversations/entire").mkdir(parents=True)
    os.link(base / "conversations/entire/part-000.parquet", v / "conversations/entire/part-000.parquet")   # carried
    _table(v / "conversations/entire/part-2026-10-11-000.parquet", [{"session_key": "entire:b", "turn_number": 0}], conv)
    for rel in ("copied/dataclaw/u__kept/c.jsonl", "copied/dataclaw/u__again/c.jsonl"):
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text("{}\n")
    (v / "copied/dataclaw/u__kept").mkdir(parents=True)
    os.link(base / "copied/dataclaw/u__kept/c.jsonl", v / "copied/dataclaw/u__kept/c.jsonl")      # the same file
    (v / "copied/dataclaw/u__again").mkdir(parents=True)
    (v / "copied/dataclaw/u__again/c.jsonl").write_text('{"staged": "again"}\n')                  # its path, new text
    (v / "changelog.json").write_text("{}")
    result = {"files": 0}
    got = {(kind, str(Path(path).relative_to(v))) for kind, path, _ in check._units(Ledger(), v, result, base)}
    assert got == {("conversations", "conversations/entire/part-2026-10-11-000.parquet"),
                   ("copied", "copied/dataclaw/u__again/c.jsonl"), ("doc", "changelog.json")}, got
    assert result["carried_files"] == 2
    every = {str(Path(path).relative_to(v)) for _, path, _ in check._units(Ledger(), v, {"files": 0})}
    assert "conversations/entire/part-000.parquet" in every and "README.md" not in every   # a full check reads all


def test_a_copied_dataset_new_or_at_another_revision_is_staged_again_whatever_its_sessions(tmp_path):
    base = tmp_path / "2026-10-04"
    (base / "copied/dataclaw/u__same").mkdir(parents=True)
    (base / "copied/dataclaw/u__moved").mkdir(parents=True)
    (base / "copied/swe-chat-v2/S__C").mkdir(parents=True)
    (base / "changelog.json").write_text(json.dumps({"copied": {
        "dataclaw/u__same": {"copied": True, "revision": "r1"}, "dataclaw/u__moved": {"copied": True, "revision": "r1"},
        "dataclaw/u__gone": {"copied": True, "revision": "r1"}, "swe-chat-v2": {"copied": True, "revision": "s1"},
        "dataclaw/u__linked": {"copied": False, "revision": "r1"}}}))
    entries = [("dataclaw", {"id": "u/same", "copied": True, "revision": "r1"}),
               ("dataclaw", {"id": "u/moved", "copied": True, "revision": "r2"}),      # another revision
               ("dataclaw", {"id": "u/new", "copied": True, "revision": "r1"}),        # new this week
               ("dataclaw", {"id": "u/linked", "copied": True, "revision": "r1"}),     # linked before, copied now
               ("dataclaw", {"id": "u/still-linked", "copied": False, "revision": "r1"}),
               ("swe-chat-v2", {"id": "S/C", "copied": True, "revision": "s1"})]
    rows = {"swe-chat-v2": [{"source": "swe-chat-v2", "source_id": "s1", "_read": [], "extra": {"dataset": "S/C"}},
                            {"source": "swe-chat-v2", "source_id": "s2", "_read": [], "extra": {"dataset": "S/C"},
                             "duplicate_of": "entire:o/r:s2"}]}
    assert build.restage_for(base, entries, rows, process={"swe-chat-v2:s2"}) == \
        {"dataclaw/u__moved", "dataclaw/u__new", "dataclaw/u__linked"}   # a duplicate read again: no staging
    assert build.restage_for(base, entries, rows, process={"swe-chat-v2:s1"}) == \
        {"dataclaw/u__moved", "dataclaw/u__new", "dataclaw/u__linked", "swe-chat-v2"}   # a kept session read again



def test_an_incremental_build_under_strict_redaction_is_refused(tmp_path, monkeypatch):
    # Strict redaction sweeps a secret found in one session through every file: the sessions carried over unread
    # would keep it, so only a build of every session will do.
    monkeypatch.setattr(build, "STRICT", True)
    with pytest.raises(RuntimeError, match="incremental build under strict redaction"):
        build.version(None, None, {}, base=tmp_path / "2026-10-04")


def test_a_staged_files_own_secrets_are_counted(tmp_path):
    # Staging masks runs after a key's prefix; the build kept its emails and home folders but dropped those secrets
    # from the session's redactions until 2026-10-06.
    import hashlib
    from collections import Counter
    raw = tmp_path / "full.jsonl"
    data = b'{"text": "token sk-ant-api03-' + b"A" * 30 + b' from zoe.lin@fastmail.net in /Users/zoe/app"}\n'
    raw.write_bytes(data)
    staged, counts, utf8 = build._stage((str(raw), str(tmp_path / "s.jsonl.txt"), True, hashlib.sha256(data).hexdigest()))
    kept = build._staged_counts(counts)
    assert utf8 and kept["secrets"] == Counter({"key-run": 1}) and (kept["emails"], kept["home_users"]) == (1, 1)
    assert "sk-ant-api03-AAAA" not in (tmp_path / "s.jsonl.txt").read_text()


def test_a_file_stored_under_a_digest_is_staged_as_what_its_own_name_says(tmp_path):
    # Entire's files fetched again are stored under a blob digest with no extension; their kind is their name's.
    assert build.staged_name("f1", "full.jsonl") == "f1.jsonl.txt"
    stored = {**ROW, "files": [{"path": "full.jsonl", "sha256": "f1", "local": "/data/raw/5a986f7701284f6c"},
                               {"path": "metadata.json", "sha256": "f2", "local": "/data/raw/0be1"}]}
    raw, jobs = build.stage_jobs({"entire": [stored, {**ROW, "source_id": "o/r:s2", "duplicate_of": "entire:o/r:s1",
                                                      "files": [{"path": "x.jsonl", "sha256": "f9", "local": "/l/f9"}]}]},
                                 Path("/stage"))
    assert raw == {"f1": "/data/raw/5a986f7701284f6c", "f2": "/data/raw/0be1"}             # a duplicate's files are not staged
    assert [j[1] for j in jobs] == ["/stage/f1.jsonl.txt", "/stage/f2.json.txt"]
    assert build.staged_name("f2", "metadata.json") == "f2.json.txt"
    assert build.staged_name("f3", "notes.md") == "f3.txt"
    from collect import scan
    assert scan.json_like(build.staged_name("f1", "full.jsonl")) and not scan.json_like("5a986f7701284f6c.txt")
    raw = tmp_path / "5a986f7701284f6c953a3937d233bed268ce7abb"   # as such a file is stored
    line = json.dumps({"type": "user", "message": {"content": "see /Users/alice/x and"}, "cwd": "/Users/alice/p"})
    raw.write_text(line + "\n")
    import hashlib
    sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    staged = tmp_path / build.staged_name(sha, "full.jsonl")
    _, counts, ok = build._stage((str(raw), str(staged), True, sha))
    out = staged.read_text()
    assert ok and counts["home_users"] == 2 and "alice" not in out
    assert list(json.loads(out)) == ["type", "message", "cwd"]   # still the record, its strings whole
