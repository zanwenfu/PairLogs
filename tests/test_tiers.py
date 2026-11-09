"""collect.tiers: tier A full text where licences allow it, tier B pointers (never text) for every other kept session."""

import json
from datetime import date

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from collect import cli, tiers

FILES = pa.list_(pa.struct([("path", pa.string()), ("bytes", pa.int64()), ("sha256", pa.string()),
                            ("content", pa.large_string())]))


def session(key, source, licence, dup=None, repo="o/r"):
    return {"session_key": key, "source": source, "repo": repo, "repo_url": f"https://github.com/{repo}",
            "repo_license": licence, "duplicate_of": dup, "found_url": "u", "found_commit": "c", "checkpoint_pk": "p",
            "session_id": key.split(":")[-1], "source_id": key, "strategy": None, "agent": "Claude Code",
            "models": ["m"], "started_at": "2026-01-01", "ended_at": "2026-01-02", "n_user_messages": 1,
            "n_tool_calls": 0, "files": [{"path": "full.jsonl", "bytes": 5, "sha256": "ab", "content": "SECRET TEXT"}]}


@pytest.fixture
def version(tmp_path):
    v = tmp_path / "2026-10-11"
    rows = {
        "entire": [session("entire:a", "entire", "MIT", repo="o/licensed"),
                   session("entire:b", "entire", "none", repo="o/unlicensed"),
                   session("entire:c", "entire", "unrecognized", repo="o/other"),
                   session("entire:d", "entire", "Apache-2.0", dup="entire:a")],
        "dataclaw": [session("dataclaw:x", "dataclaw", None, repo=None)],
        "swe-chat-v2": [session("swe-chat-v2:s", "swe-chat-v2", "MIT", repo="o/sc"),
                        session("swe-chat-v2:t", "swe-chat-v2", "MIT", dup="entire:a")],
    }
    for src, rs in rows.items():
        (v / "data" / src).mkdir(parents=True)
        schema = pa.schema([(k, pa.list_(pa.string()) if k == "models" else FILES if k == "files" else pa.int64()
                             if k.startswith("n_") else pa.string()) for k in rs[0]])
        pq.write_table(pa.Table.from_pylist(rs, schema=schema), v / "data" / src / "0000.parquet")
        (v / "conversations" / src).mkdir(parents=True)
        conv = [{"session_key": r["session_key"], "turn_number": i, "content": "hello"}
                for r in rs if r["duplicate_of"] is None for i in range(2)]
        pq.write_table(pa.Table.from_pylist(conv), v / "conversations" / src / "0000.parquet")
    (v / "copied" / "dataclaw" / "u__d").mkdir(parents=True)
    (v / "copied" / "dataclaw" / "u__d" / "c.jsonl").write_text("{}\n")
    (v / "copied" / "swe-chat-v2" / "S").mkdir(parents=True)
    (v / "copied" / "swe-chat-v2" / "S" / "conversations.parquet").write_text("not ours to share")
    (v / "changelog.json").write_text(json.dumps({"version": "2026-10-11", "copied": BUILD_COPIED}))
    return v


# A build's changelog, as stored: each copied dataset keyed <source>/<name>
BUILD_COPIED = {
    "dataclaw/u__d": {"url": "https://h/u/d", "revision": "r1", "license": "mit", "copied": True, "rows": 1,
                      "license_line": "mit, copied from https://h/u/d (revision r1), by u",
                      "redactions": {"secrets": {"generic": 2}}},
    "openhands-feedback/O__f": {"url": "https://h/O/f", "revision": "r2", "license": "mit", "copied": True, "rows": 1,
                                "license_line": "mit, copied from https://h/O/f (revision r2), by O",
                                "redactions": {"secrets": {}}},
    "swe-chat-v2/S": {"url": "https://h/S", "revision": "r3", "license": "odc-by", "copied": True, "rows": 1,
                      "license_line": "odc-by, copied from https://h/S (revision r3), by S",
                      "redactions": {"secrets": {}}}}


def test_each_tier_credits_the_copied_datasets_it_carries(version, tmp_path):
    tiers.write(version, tmp_path / "out", log=lambda m: None)
    a = json.loads((tmp_path / "out" / "a" / "changelog.json").read_text())["copied_datasets"]
    b = json.loads((tmp_path / "out" / "b" / "changelog.json").read_text())["copied_datasets"]
    assert sorted(a) == ["dataclaw/u__d", "openhands-feedback/O__f"] and sorted(b) == ["swe-chat-v2/S"]
    assert a["dataclaw/u__d"]["credit"] == ("Copied from https://h/u/d at revision r1, by u, under its mit licence; "
                                            "2 secret spans redacted, nothing else changed.")


def test_a_published_versions_credits_are_carried_as_they_are(version, tmp_path):
    published = {"dataclaw/u__d": {"credit": "as published"}, "swe-chat-v2/S": {"credit": "as published"}}
    (version / "changelog.json").write_text(json.dumps({"version": "2026-10-11", "copied_datasets": published}))
    tiers.write(version, tmp_path / "out", log=lambda m: None)
    a = json.loads((tmp_path / "out" / "a" / "changelog.json").read_text())["copied_datasets"]
    assert a == {"dataclaw/u__d": {"credit": "as published"}}


def test_tier_a_holds_licensed_and_mit_copied_sessions_only(version, tmp_path):
    counts = tiers.write(version, tmp_path / "out", log=lambda m: None)
    a = pa.concat_tables([pq.read_table(f) for f in (tmp_path / "out" / "a" / "data").rglob("*.parquet")],
                         promote_options="default")
    assert sorted(a["session_key"].to_pylist()) == ["dataclaw:x", "entire:a", "entire:d"]
    conv = pa.concat_tables([pq.read_table(f) for f in (tmp_path / "out" / "a" / "conversations").rglob("*.parquet")])
    assert sorted(set(conv["session_key"].to_pylist())) == ["dataclaw:x", "entire:a"]
    assert counts["a"] == {"sessions": 2, "copies": 1, "rows": {"conversations": 4, "conversations_subagents": 0}}
    # a copy whose own source is SWE-chat stays out, though its kept session is in tier A
    assert "swe-chat-v2:t" not in a["session_key"].to_pylist()


def test_copied_files_only_where_the_licence_allows(version, tmp_path):
    tiers.write(version, tmp_path / "out", log=lambda m: None)
    assert (tmp_path / "out" / "a" / "copied" / "dataclaw" / "u__d" / "c.jsonl").exists()
    assert not (tmp_path / "out" / "a" / "copied" / "swe-chat-v2").exists()


def test_tier_b_points_to_every_other_kept_session_without_text(version, tmp_path):
    counts = tiers.write(version, tmp_path / "out", log=lambda m: None)
    b = pq.read_table(tmp_path / "out" / "b" / "pointers.parquet")
    assert sorted(b["session_key"].to_pylist()) == ["entire:b", "entire:c", "swe-chat-v2:s"]
    assert "content" not in str(b.schema) and "SECRET TEXT" not in str(b.to_pylist())
    assert b["files"].to_pylist()[0] == [{"path": "full.jsonl", "bytes": 5, "sha256": "ab"}]
    # the rebuild list names the GitHub sources' repositories only (SWE-chat comes from its own release)
    assert (tmp_path / "out" / "b" / "repositories.txt").read_text() == "o/other\no/unlicensed\n"
    assert counts["b"] == {"sessions": 3, "by_source": {"entire": 2, "swe-chat-v2": 1}, "repositories": 2,
                           "also_in": {"sessions": 0, "places": 0}}


def test_tier_b_lists_every_other_place_each_session_was_found(version, tmp_path):
    f = version / "data" / "entire" / "0000.parquet"
    t = pq.read_table(f)
    elsewhere = {"source": "entire", "source_id": "z", "found_url": "https://github.com/p/q"}
    also = [[elsewhere] if k == "entire:b" else [] for k in t["session_key"].to_pylist()]
    t = t.append_column("also_in", pa.array(also, tiers.ALSO_IN))
    pq.write_table(t, f)
    counts = tiers.write(version, tmp_path / "out", log=lambda m: None)
    b = {r["session_key"]: r["also_in"] for r in pq.read_table(tmp_path / "out" / "b" / "pointers.parquet").to_pylist()}
    # a source whose rows have no also_in (here SWE-chat's) lists none
    assert b == {"entire:b": [elsewhere], "entire:c": [], "swe-chat-v2:s": []}
    assert counts["b"]["also_in"] == {"sessions": 1, "places": 1}


def test_no_session_in_both_tiers_and_none_lost(version, tmp_path):
    tiers.write(version, tmp_path / "out", log=lambda m: None)
    a = {k for f in (tmp_path / "out" / "a" / "data").rglob("*.parquet")
         for k, d in zip(pq.read_table(f)["session_key"].to_pylist(), pq.read_table(f)["duplicate_of"].to_pylist())
         if d is None}
    b = set(pq.read_table(tmp_path / "out" / "b" / "pointers.parquet")["session_key"].to_pylist())
    kept = {r["session_key"] for f in (version / "data").rglob("*.parquet") for r in pq.read_table(f).to_pylist()
            if r["duplicate_of"] is None}
    assert not a & b and a | b == kept


def test_refuses_a_folder_already_written(version, tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "x").write_text("x")
    with pytest.raises(FileExistsError):
        tiers.write(version, tmp_path / "out", log=lambda m: None)


@pytest.mark.parametrize("source,licence,ok", [("entire", "MIT", True), ("specstory", "GPL-3.0", True),
                                                ("entire", "none", False), ("aider", "unrecognized", False),
                                                ("entire", None, False), ("entire", "", False),
                                                ("dataclaw", None, True), ("openhands-feedback", None, True),
                                                ("swe-chat-v2", "MIT", False)])
def test_shareable(source, licence, ok):
    assert tiers.shareable(source, licence) is ok


def test_only_file_reads_repositories_one_a_line(tmp_path):
    f = tmp_path / "repositories.txt"
    f.write_text("# tier B\no/a\n\n  o/b  \n")
    assert cli.read_only_file(f) == ["o/a", "o/b"]


# -- Labels beside the tiers (tier_labels, 2026-11-03): tier B's message labels, SWE-chat's prompts, owners' types ----
TEXT_COLUMNS = ("content", "tool_input_json", "command", "file_path", "pattern", "prompt_intent", "prompt_pushback",
                "language")


def conv_row(key, n, typed, content, pushback=None, **more):
    # the version's own columns and types (conversations of 2026-11-02, printed 2026-11-03)
    return {"session_key": key, "turn_number": n, "typed_by_developer": typed,
            "actor": "developer" if typed else "agent",
            "kind": "text", "is_error": None if typed else False, "copy_of": None, "pushback": pushback,
            "content": content, "tool_input_json": '{"command": "SECRET TOOL"}', "command": "SECRET CMD",
            "file_path": "/home/SECRET/path", "pattern": None, "prompt_intent": "debug",
            "prompt_pushback": "correction", "language": "ENGLISH", **more}


@pytest.fixture
def labelled(version):
    for src in ("entire", "swe-chat-v2", "dataclaw"):
        keys = {"entire": ["entire:a", "entire:b", "entire:c"], "swe-chat-v2": ["swe-chat-v2:s"],
                "dataclaw": ["dataclaw:x"]}[src]
        rows = []
        for k in keys:
            rows += [conv_row(k, 0, True, f"SECRET TYPED {k}", pushback=k.endswith("b")),
                     conv_row(k, 1, False, "SECRET REPLY"), conv_row(k, 2, True, "")]
        pq.write_table(pa.Table.from_pylist(rows), version / "conversations" / src / "0000.parquet")
    (version / "conversations_subagents" / "entire").mkdir(parents=True)
    sub = [{"session_key": "entire:b", "task_pk": "p#t1", "turn_pk": "p#t1#0", "turn_number": 0,
            "typed_by_developer": False, "actor": "harness", "kind": "text", "is_error": False,
            "content": "SECRET HELPER PROMPT", "command": "SECRET CMD"},
           {"session_key": "entire:a", "task_pk": "p#t2", "turn_pk": "p#t2#0", "turn_number": 0,
            "typed_by_developer": False, "actor": "agent", "kind": "text", "is_error": False,
            "content": "SECRET", "command": None}]
    pq.write_table(pa.Table.from_pylist(sub), version / "conversations_subagents" / "entire" / "0000.parquet")
    return version


def all_rows(folder):
    files = sorted(folder.rglob("*.parquet"))
    return pa.concat_tables([pq.read_table(f) for f in files], promote_options="default") if files else None


def test_tier_b_carries_our_message_labels_without_text(labelled, tmp_path):
    from collect.pushback import text_id
    counts = tiers.write(labelled, tmp_path / "out", log=lambda m: None)
    conv = all_rows(tmp_path / "out" / "b" / "labels" / "conversations")
    assert conv.column_names == ["session_key", "turn_number", "typed_by_developer", "actor", "kind", "is_error",
                                 "copy_of", "pushback", "text_id"]
    assert not set(TEXT_COLUMNS) & set(conv.column_names) and "SECRET" not in str(conv.to_pylist())
    # tier B's sessions only, every row of each, the version's labels as they are
    assert sorted({k for k in conv["session_key"].to_pylist()}) == ["entire:b", "entire:c", "swe-chat-v2:s"]
    assert len(conv) == 9 and counts["labels"]["b"]["message_labels"]["rows"]["conversations"] == 9
    rows = {(r["session_key"], r["turn_number"]): r for r in conv.to_pylist()}
    assert rows[("entire:b", 0)]["pushback"] is True and rows[("entire:c", 0)]["pushback"] is False
    # a text's id on typed rows only (an empty typed message too), as pushback.apply computes it
    assert rows[("entire:b", 0)]["text_id"] == text_id("SECRET TYPED entire:b")
    assert rows[("entire:b", 2)]["text_id"] == text_id("") and rows[("entire:b", 1)]["text_id"] is None
    # one label file for each of the version's files that holds a tier-B row, under its source
    assert (tmp_path / "out" / "b" / "labels" / "conversations" / "swe-chat-v2" / "0000.parquet").exists()
    assert not (tmp_path / "out" / "b" / "labels" / "conversations" / "dataclaw").exists()


def test_tier_b_labels_cover_the_helpers_rows_by_their_own_key(labelled, tmp_path):
    tiers.write(labelled, tmp_path / "out", log=lambda m: None)
    sub = all_rows(tmp_path / "out" / "b" / "labels" / "conversations_subagents")
    assert sub.column_names == ["session_key", "task_pk", "turn_number", "typed_by_developer", "actor", "kind",
                                "is_error", "text_id"]
    assert sub.to_pylist() == [{"session_key": "entire:b", "task_pk": "p#t1", "turn_number": 0,
                                "typed_by_developer": False, "actor": "harness", "kind": "text", "is_error": False,
                                "text_id": None}]


def test_swe_chat_prompts_keep_swe_chats_keys_and_who_wrote_them_only(labelled, tmp_path):
    src = tmp_path / "prompts.parquet"
    pq.write_table(pa.table({"turn_id": ["s1#3", "s1#9", "s2#0", "s3#4"], "session_id": ["s1", "s1", "s2", "s3"],
                             "turn_number": [3, 9, 0, 4],
                             "cls": ["typed by a person", "program", "person action", "unknown"],
                             "our_pb": [True, None, None, None], "head": ["SECRET"] * 4, "pp": ["correction"] * 4}),
                   src)
    counts = tiers.write(labelled, tmp_path / "out", log=lambda m: None, swe_prompts=src)
    t = pq.read_table(tmp_path / "out" / "b" / "labels" / "swe_chat_prompts.parquet")
    assert t.column_names == ["turn_id", "session_id", "turn_number", "written_by", "pushback"]
    assert t["written_by"].to_pylist() == ["typed", "program", "action", "unknown"]
    assert "SECRET" not in str(t.to_pylist()) and "correction" not in str(t.to_pylist())
    assert counts["labels"]["b"]["swe_chat_prompts"]["written_by"] == {"action": 1, "program": 1, "typed": 1,
                                                                       "unknown": 1}


def test_swe_chat_prompts_refuse_a_key_that_names_two_prompts(labelled, tmp_path):
    src = tmp_path / "prompts.parquet"
    pq.write_table(pa.table({"turn_id": ["s1#3", "s1#3"], "session_id": ["s1", "s1"], "turn_number": [3, 3],
                             "cls": ["program", "program"], "our_pb": [None, None]}), src)
    with pytest.raises(ValueError):
        tiers.write(labelled, tmp_path / "out", log=lambda m: None, swe_prompts=src)


SIGNALS = {"o": {"type": "User", "created": "2020-11-02T00:00:00Z", "public_repos": 7, "university": False},
           "org": {"type": "Organization", "created": "2016-11-02T00:00:00Z", "public_repos": 40, "university": True},
           "gone": {"type": "gone", "created": None, "public_repos": None, "university": False}}


def test_each_tier_names_its_repositories_owner_type_and_ages_only_as_totals(labelled, tmp_path):
    rows = [session("entire:e", "entire", "MIT", repo="org/x"), session("entire:f", "entire", "none", repo="gone/y"),
            session("entire:g", "entire", "none", repo="nobody/z")]
    schema = pq.read_schema(labelled / "data" / "entire" / "0000.parquet")
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), labelled / "data" / "entire" / "0001.parquet")
    counts = tiers.write(labelled, tmp_path / "out", log=lambda m: None, signals=SIGNALS)
    a = pq.read_table(tmp_path / "out" / "a" / "repositories.parquet").to_pylist()
    b = pq.read_table(tmp_path / "out" / "b" / "repositories.parquet").to_pylist()
    # tier A's repositories: its kept sessions' and the copies kept with them (entire:d, in o/r)
    assert a == [{"repo": "o/licensed", "owner_type": "person"}, {"repo": "o/r", "owner_type": "person"},
                 {"repo": "org/x", "owner_type": "organisation"}]
    assert {r["repo"]: r["owner_type"] for r in b} == {"gone/y": "not found", "nobody/z": "not looked up",
                                                        "o/other": "person", "o/sc": "person", "o/unlicensed": "person"}
    assert pq.read_schema(tmp_path / "out" / "a" / "repositories.parquet").names == ["repo", "owner_type"]
    totals = json.loads((tmp_path / "out" / "a" / "changelog.json").read_text())["labels"]["owners"]
    # on the version's date (2026-10-11): 6 and 10 years less 22 days
    assert totals["median_account_age_years"] == {"person": 5.94, "organisation": 9.94}
    assert totals["median_public_repositories"] == {"person": 7, "organisation": 40}
    assert totals["university_profiles"] == {"person": 0, "organisation": 1}
    assert counts["labels"]["b"]["owners"]["owners_not_looked_up"] == 1
    assert counts["labels"]["b"]["owners"]["owners"] == {"not found": 1, "person": 1}


def test_labels_are_added_once_and_change_nothing_else_in_a_written_tier(labelled, tmp_path):
    from collect import tier_labels
    tiers.write(labelled, tmp_path / "out", log=lambda m: None)
    out = tmp_path / "out"
    before = {p: p.read_bytes() for p in out.rglob("*") if p.is_file() and "labels" not in p.parts
              and p.name != "changelog.json"}
    with pytest.raises(FileExistsError):
        tier_labels.add(labelled, {"a": out / "a", "b": out / "b"}, log=lambda m: None)
    import shutil
    shutil.rmtree(out / "b" / "labels")
    tier_labels.add(labelled, {"a": out / "a", "b": out / "b"}, signals=SIGNALS, log=lambda m: None)
    after = {p: p.read_bytes() for p in out.rglob("*") if p.is_file() and "labels" not in p.parts
             and p.name not in ("changelog.json", "repositories.parquet")}
    assert after == before


def test_owners_written_again_from_a_fuller_lookup_change_only_the_missing(labelled, tmp_path):
    from collect import tier_labels
    tiers.write(labelled, tmp_path / "out", log=lambda m: None, signals={"o": SIGNALS["o"]})
    a = tmp_path / "out" / "a"
    before = pq.read_table(a / "repositories.parquet").to_pylist()
    note = json.loads((a / "changelog.json").read_text())
    assert note["labels"]["owners"]["owners_not_looked_up"] == 0   # tier A's owner is "o"
    b = tmp_path / "out" / "b"
    assert json.loads((b / "changelog.json").read_text())["labels"]["owners"]["owners_not_looked_up"] == 0
    # a tier whose lookup missed an owner: written again with it, the rest as it was
    rows = [session("entire:e", "entire", "MIT", repo="org/x")]
    schema = pq.read_schema(labelled / "data" / "entire" / "0000.parquet")
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), a / "data" / "entire" / "0001.parquet")
    tier_labels.owners_again({"a": a}, {"o": SIGNALS["o"]}, date(2026, 10, 11), log=lambda m: None)
    missed = {r["repo"]: r["owner_type"] for r in pq.read_table(a / "repositories.parquet").to_pylist()}
    assert missed["org/x"] == "not looked up"
    tier_labels.owners_again({"a": a}, SIGNALS, date(2026, 10, 11), log=lambda m: None)
    after = pq.read_table(a / "repositories.parquet").to_pylist()
    assert [r for r in after if r["repo"] != "org/x"] == before
    assert {r["repo"]: r["owner_type"] for r in after}["org/x"] == "organisation"
    owners = json.loads((a / "changelog.json").read_text())["labels"]["owners"]
    assert owners["owners"] == {"organisation": 1, "person": 1} and owners["owners_not_looked_up"] == 0
    assert owners["median_account_age_years"] == {"person": 5.94, "organisation": 9.94}
    with pytest.raises(FileNotFoundError):   # a tier never given owners is not written by this path
        (b / "repositories.parquet").unlink()
        tier_labels.owners_again({"b": b}, SIGNALS, date(2026, 10, 11), log=lambda m: None)
