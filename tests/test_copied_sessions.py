"""The copied datasets' sessions: SWE-chat v2 one per transcript (its agent's own reader, its subagents, its labels
carried over), DataClaw one per record or raw transcript, OpenHands one per trajectory; and one dedup over every
source."""

import json
import tempfile
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from collect import conv_build, copied_sessions, dedup

CC_SID = "0b1c2d3e-aaaa-bbbb-cccc-123456789abc"


def cc_lines(prompt: str) -> list[dict]:
    return [{"type": "user", "uuid": "u1", "sessionId": CC_SID, "timestamp": "2026-01-01T00:00:00Z",
             "message": {"role": "user", "content": prompt}},
            {"type": "assistant", "uuid": "a1", "timestamp": "2026-01-01T00:00:01Z",
             "message": {"id": "m1", "role": "assistant", "model": "claude-x", "content": [
                 {"type": "tool_use", "id": "toolu_task", "name": "Task", "input": {"prompt": "look"}}],
                 "usage": {"input_tokens": 5, "output_tokens": 1}}},
            {"type": "user", "uuid": "u2", "timestamp": "2026-01-01T00:00:02Z", "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "toolu_task", "content": "done"}]}}]


def files_of(root: Path) -> list[dict]:
    return [{"path": str(p.relative_to(root)), "local": str(p), "bytes": p.stat().st_size, "sha256": f"h{i}"}
            for i, p in enumerate(sorted(x for x in root.rglob("*") if x.is_file()))]


def write_jsonl(path: Path, lines: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def swechat(d: Path, sub_label: bool = False) -> dict:
    root = d / "SALT-NLP__SWE-chat" / "rev1"
    write_jsonl(root / "transcripts" / f"{CC_SID}.jsonl", cc_lines("fix  the bug"))
    write_jsonl(root / "transcripts_subagent" / "agent1__toolu_task.jsonl",
                [{"type": "user", "uuid": "s1", "isSidechain": True, "message": {"role": "user", "content": "look"}}])
    sessions = pa.Table.from_pylist([
        {"session_id": CC_SID, "agent": "Claude Code", "repo_id": "o/r", "canonical_checkpoint_pk": "o/r#c1",
         "strategy": "manual-commit", "transcript_path": f"transcripts/{CC_SID}.jsonl"},
        {"session_id": "gone", "agent": "Codex", "repo_id": "o/r", "canonical_checkpoint_pk": None, "strategy": None,
         "transcript_path": None}])
    # SWE-chat stores times in nanoseconds, which pyarrow will not read into Python's datetime.
    at = pa.array([1_770_748_648_903_617_239, None], type=pa.int64()).cast(pa.timestamp("ns", tz="UTC"))
    pq.write_table(sessions.append_column("created_at", at), root / "sessions.parquet")
    pq.write_table(pa.Table.from_pylist([   # SWE-chat's own record of each repository's GitHub metadata
        {"repo_id": "o/r", "license_type": "MIT", "repo_github_metadata": json.dumps({"license": {"spdx_id": "MIT"}, "language": "Python"})},
        {"repo_id": "o/none", "license_type": None, "repo_github_metadata": json.dumps({"license": None, "language": None})},
        {"repo_id": "o/odd", "license_type": "Other", "repo_github_metadata": json.dumps({"license": {"spdx_id": "NOASSERTION"}})}]),
        root / "repositories.parquet")
    pq.write_table(pa.Table.from_pylist([{"session_id": CC_SID, "tool_use_id": "toolu_task", "agent_id": "agent1",
                                          "transcript_path": "transcripts_subagent/agent1__toolu_task.jsonl"}]),
                   root / "subagent_tasks.parquet")
    labelled = [{"session_id": CC_SID, "turn_type": "user_prompt", "content": "fix the bug",
                 "prompt_intent": "debug", "prompt_pushback": "non_pushback", "language": "en"}]
    if sub_label:   # a prompt an agent wrote, which SWE-chat labels too: its row is in conversations_subagents
        labelled.append({"session_id": CC_SID, "turn_type": "user_prompt", "content": "look", "prompt_intent": "explore",
                         "prompt_pushback": "correction", "language": "en"})
    pq.write_table(pa.Table.from_pylist(labelled), root / "conversations.parquet")
    return {"id": "SALT-NLP/SWE-chat", "url": "https://huggingface.co/datasets/SALT-NLP/SWE-chat", "revision": "rev1",
            "license": "odc-by", "copied": True, "files": files_of(root)}


def test_swechat_sessions_rows_subagents_and_labels(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        entry = swechat(Path(d))
        monkeypatch.setattr(copied_sessions, "entries", lambda ledger: iter([("swe-chat-v2", entry)]))
        rows = copied_sessions.rows(None)["swe-chat-v2"]
        cc, gone = rows
        assert (cc["dedup_key"], cc["agent"], cc["repo"], cc["n_user_messages"]) == (f"claude-code:{CC_SID}", "Claude Code", "o/r", 1)
        assert [n for n, _ in cc["_read"]] == ["full.jsonl", "subagents/toolu_task/agent-agent1.jsonl"]
        assert cc["_published"] == [f"copied/swe-chat-v2/SALT-NLP__SWE-chat/transcripts/{CC_SID}.jsonl",
                                    "copied/swe-chat-v2/SALT-NLP__SWE-chat/transcripts_subagent/agent1__toolu_task.jsonl"]
        assert (gone["_read"], gone["extra"]["why_no_transcript"]) == ([], "none recorded")
        assert (cc["extra"]["created_at"], gone["extra"]["created_at"]) == ("2026-02-10T18:37:28Z", None)
        # the repository's licence and language as SWE-chat recorded them; the dataset's own licence apart
        assert (cc["repo_license"], cc["repo_language"], cc["extra"]["dataset_license"]) == ("MIT", "Python", "odc-by")
        assert cc["extra"]["swechat_license_type"] == "MIT"   # SWE-chat's own reading, kept beside GitHub's
        assert copied_sessions._swechat_repos(Path(d) / "SALT-NLP__SWE-chat" / "rev1") == {
            "o/r": {"repo_license": "MIT", "repo_language": "Python", "license_type": "MIT"},
            "o/none": {"repo_license": "none", "repo_language": None, "license_type": None},
            "o/odd": {"repo_license": "unrecognized", "repo_language": None, "license_type": "Other"}}
        labels = copied_sessions.swechat_labels(None)
        out = conv_build.extract_job(("swe-chat-v2", [(r, [(n, Path(p)) for n, p in r["_read"]]) for r in rows], d,
                                      "part-000", labels))
        main = pq.read_table(next(f for f in out["files"] if "/conversations/" in f)).to_pylist()
        sub = pq.read_table(next(f for f in out["files"] if "subagents" in f)).to_pylist()
        prompt = next(r for r in main if r["turn_type"] == "user_prompt")
        assert (prompt["prompt_intent"], prompt["prompt_pushback"], prompt["language"]) == ("debug", "non_pushback", "en")
        assert {r["checkpoint_pk"] for r in main} == {"o/r#c1"} and main[0]["turn_id"] == f"{CC_SID}#0"
        assert sub[0]["task_pk"] == "o/r#c1#toolu_task"
        assert out["per"]["gone"]["conversations_error"] == "no file to read"
        assert out["per"][CC_SID]["typed"] == 1


def test_dataclaw_records_and_raw_transcripts(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "u__data" / "rev"
        write_jsonl(root / "conversations.jsonl", [
            {"session_id": "s-1", "source": "claude", "model": "claude-x", "start_time": "2026-01-01T00:00:00Z",
             "messages": [{"role": "user", "content": "add a flag", "timestamp": "2026-01-01T00:00:00Z"},
                          {"role": "assistant", "content": "ok", "thinking": "plan", "timestamp": "2026-01-01T00:00:01Z",
                           "usage": {"input_tokens": 3, "output_tokens": 2},
                           "tool_uses": [{"tool": "Bash", "input": {"command": "ls"}, "output": {"text": "a.py"},
                                          "status": "success"}]}]},
            {"session_id": "s-2", "messages": [{"role": "user", "content": "hi"}]}])
        write_jsonl(root / "raw" / "2026-04-24.jsonl", cc_lines("go"))
        entry = {"id": "u/data", "url": "https://huggingface.co/datasets/u/data", "revision": "rev", "license": "mit",
                 "copied": True, "files": files_of(root)}
        monkeypatch.setattr(copied_sessions, "entries", lambda ledger: iter([("dataclaw", entry)]))
        rows = copied_sessions.rows(None)["dataclaw"]
        by = {r["source_id"]: r for r in rows}
        assert set(by) == {"u/data:conversations.jsonl:1", "u/data:conversations.jsonl:2", "u/data:raw/2026-04-24.jsonl"}
        assert by["u/data:conversations.jsonl:1"]["dedup_key"] == "claude-code:s-1"
        assert by["u/data:raw/2026-04-24.jsonl"]["dedup_key"] == f"claude-code:{CC_SID}"
        assert by["u/data:conversations.jsonl:1"]["has_tool_results"] is True
        out = conv_build.extract_job(("dataclaw", [(r, [(n, Path(p)) for n, p in r["_read"]]) for r in rows], d, "p"))
        main = pq.read_table(next(f for f in out["files"] if "/conversations/" in f)).to_pylist()
        one = [r for r in main if r["session_key"] == "dataclaw:u/data:conversations.jsonl:1"]
        assert [(r["turn_type"], r["content"]) for r in one] == [
            ("user_prompt", "add a flag"), ("assistant_thinking", "plan"), ("assistant_response", "ok"),
            ("tool_use", '{"command": "ls"}'), ("tool_result", "a.py")]
        assert one[2]["input_tokens"] == 3 and one[3]["command"] == "ls" and one[4]["tool_name"] == "Bash"
        # The export does not mark inserted text: the developer's, unless the text shows a harness's (2026-10-05).
        assert out["per"]["u/data:conversations.jsonl:1"]["typed"] == 1
        assert out["per"]["u/data:raw/2026-04-24.jsonl"]["typed"] == 1          # Claude Code's own marks


def test_openhands_trajectories(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "OpenHands__feedback" / "rev"
        root.joinpath("data").mkdir(parents=True)
        ev = lambda **k: {"action": None, "content": None, "extras": None, "id": 0, "message": None,
                          "observation": None, "source": None, "timestamp": None, **k}
        pq.write_table(pa.Table.from_pylist([{"version": "1.0", "feedback": "positive", "permissions": "public", "timestamp": None,
            "trajectory": [ev(id=1, source="environment", action="initialize"),
                           ev(id=2, source="user", action="message", content="fix the tests"),
                           ev(id=3, source="agent", action="run", extras=json.dumps({"command": "pytest", "thought": "Run them."})),
                           ev(id=4, source="agent", observation="run", content="1 failed", extras=json.dumps({"cause": 3})),
                           ev(id=5, source="agent", observation="agent_state_changed"),
                           ev(id=6, source="agent", action="message", content="Fixed.")]}]),
            root / "data" / "train-00000-of-00001.parquet")
        entry = {"id": "OpenHands/feedback", "url": "u", "revision": "rev", "license": "mit", "copied": True,
                 "files": files_of(root)}
        monkeypatch.setattr(copied_sessions, "entries", lambda ledger: iter([("openhands-feedback", entry)]))
        rows = copied_sessions.rows(None)["openhands-feedback"]
        assert len(rows) == 1 and rows[0]["has_tool_results"] and rows[0]["n_user_entries"] == 1
        assert (rows[0]["repo_license"], rows[0]["extra"]["dataset_license"]) == (None, "mit")   # no repository
        out = conv_build.extract_job(("openhands-feedback", [(rows[0], [(n, Path(p)) for n, p in rows[0]["_read"]])], d, "p"))
        main = pq.read_table(out["files"][0]).to_pylist()
        assert [(r["turn_type"], r["content"]) for r in main] == [
            ("system_event", None), ("user_prompt", "fix the tests"), ("assistant_response", "Run them."),
            ("tool_use", '{"command": "pytest"}'), ("tool_result", "1 failed"), ("system_event", None),
            ("assistant_response", "Fixed.")]
        # OpenHands' `run` holds its command (the partner session's measurement of rc20: 2,422 such calls had none)
        assert main[4]["tool_call_id"] == "3" and main[3]["tool_call_id"] == "3" and main[3]["command"] == "pytest"
        assert out["per"][rows[0]["source_id"]]["typed"] == 1


def test_one_dedup_over_every_source():
    entire = {"source": "entire", "source_id": "o/r:s", "dedup_key": "claude-code:s", "has_tool_results": True, "bytes": 10}
    swe = {"source": "swe-chat-v2", "source_id": "s", "dedup_key": "claude-code:s", "has_tool_results": True, "bytes": 10}
    claw = {"source": "dataclaw", "source_id": "u/d:c.jsonl:1", "dedup_key": "claude-code:s", "has_tool_results": True,
            "bytes": 99}
    pairs = dedup.assign([swe, claw, entire])
    assert claw["duplicate_of"] is None and entire["duplicate_of"] == swe["duplicate_of"] == "dataclaw:u/d:c.jsonl:1"
    claw["bytes"] = 10
    dedup.assign([swe, claw, entire])
    assert entire["duplicate_of"] is None and swe["duplicate_of"] == "entire:o/r:s"   # same size: the agreed order
    assert [x["source"] for x in entire["also_in"]] == ["swe-chat-v2", "dataclaw"] and sum(pairs.values()) == 2


def test_an_agent_not_recorded_has_one_key_in_every_source():
    from collect.sources.entire import namespace

    # SWE-chat writes "unknown" where Entire records no agent: one session, found in both, was kept twice (2026-10-27)
    assert namespace(None) == namespace("") == namespace("unknown") == namespace(" Unknown ") == "unknown-agent"
    assert namespace("agent") == "agent" and namespace("Claude Code") == "claude-code"
    entire = {"source": "entire", "source_id": "o/r:s", "dedup_key": f"{namespace(None)}:s", "has_tool_results": True,
              "bytes": 10}
    swe = {"source": "swe-chat-v2", "source_id": "s", "dedup_key": f"{namespace('unknown')}:s",
           "has_tool_results": True, "bytes": 10}
    dedup.assign([swe, entire])
    assert entire["duplicate_of"] is None and swe["duplicate_of"] == "entire:o/r:s"


def test_a_copied_sessions_text_is_read_as_the_copy_stages_it():
    from collect.build import _readable

    row = {"_read": [("full.jsonl", "/dl/t.jsonl"), ("subagents/c/agent-a.jsonl", "/dl/s.jsonl")]}
    staged = {"/dl/t.jsonl": "/stage/aa.jsonl.txt", "/dl/s.jsonl": "/stage/bb.jsonl.txt"}
    assert _readable(row, {}, {}, staged) == [("full.jsonl", Path("/stage/aa.jsonl.txt")),
                                               ("subagents/c/agent-a.jsonl", Path("/stage/bb.jsonl.txt"))]
    # Its main file left out of the copy (not text): nothing is read, not the download.
    assert _readable(row, {}, {}, {"/dl/s.jsonl": "/stage/bb.jsonl.txt"}) == []
    assert _readable({"_read": [("data/x.parquet:3", "/dl/x.parquet")]}, {}, {}, {}) == [
        ("data/x.parquet:3", Path("/dl/x.parquet"))]
    assert _readable({"_read": []}, {}, {}, staged) == []


def test_a_copy_taken_mid_upload_stops_the_build(monkeypatch):
    import pytest

    with tempfile.TemporaryDirectory() as d:
        entry = swechat(Path(d))
        for f in entry["files"]:
            if f["path"].startswith("transcripts/"):
                Path(f["local"]).unlink()
        entry["files"] = [f for f in entry["files"] if Path(f["local"]).exists()]
        monkeypatch.setattr(copied_sessions, "entries", lambda ledger: iter([("swe-chat-v2", entry)]))
        with pytest.raises(RuntimeError, match="copy it again"):
            copied_sessions.rows(None)


def test_dataclaw_records_from_several_files_are_read_by_one_index_each(monkeypatch):
    from collect import formats

    calls = []
    real_open = open

    def counting_open(path, mode="r", *a, **k):
        if str(path).endswith(".jsonl") and "b" in mode:
            calls.append(str(path))
        return real_open(path, mode, *a, **k)

    with tempfile.TemporaryDirectory() as d:
        files = []
        for name in ("a", "b"):
            f = Path(d) / f"{name}.jsonl"
            f.write_text("\n".join(json.dumps({"session_id": f"{name}{i}", "messages": [{"role": "user", "content": str(i)}]})
                                   for i in range(5)) + "\n")
            files.append(f)
        formats._OFFSETS.clear()
        monkeypatch.setattr("builtins.open", counting_open)
        for i in range(1, 6):   # interleaved, as a worker's records come
            for f in files:
                rows, _ = formats.dataclaw([(f"{f.name}:{i}", f)], {"session_key": "k"})
                assert rows[0]["content"] == str(i - 1)
    assert len(calls) == 2 + 10   # each file indexed once, then one read a record


def test_swechat_labels_land_before_masks_and_on_subagent_rows(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        entry = swechat(Path(d), sub_label=True)
        monkeypatch.setattr(copied_sessions, "entries", lambda ledger: iter([("swe-chat-v2", entry)]))
        rows = copied_sessions.rows(None)["swe-chat-v2"]
        labels = copied_sessions.swechat_labels(None)
        out = conv_build.extract_job(("swe-chat-v2", [(r, [(n, Path(p)) for n, p in r["_read"]]) for r in rows], d,
                                      "part-000", labels, {CC_SID: ["the bug"]}))
        main = pq.read_table(next(f for f in out["files"] if "/conversations/" in f)).to_pylist()
        sub = pq.read_table(next(f for f in out["files"] if "subagents" in f)).to_pylist()
        prompt = next(r for r in main if r["turn_type"] == "user_prompt")
        assert prompt["content"] == "fix  [REDACTED]" and prompt["prompt_pushback"] == "non_pushback"   # matched unmasked
        assert next(r for r in sub if r["turn_type"] == "user_prompt")["prompt_pushback"] == "correction"
        got = out["per"][CC_SID]["labels"]
        assert (got["typed"], got["other"], got["by_text"], got["pushback_other"]) == (1, 1, 2, 1), got


def _p(content, typed=True, uuid=None, block=0):
    return {"turn_type": "user_prompt", "content": content, "typed_by_developer": typed, "source_entry_uuid": uuid,
            "content_block_index": block, "prompt_intent": None, "prompt_pushback": None, "language": None}


def _lab(content, push="correction", uuid=None, block=0):
    return ((uuid, block) if uuid else None, copied_sessions.label_key(content), ("debug", push, "en"))


def test_a_label_lands_by_entry_id_only_where_the_texts_agree():
    ide = _p("<ide_opened_file>The user opened a.py</ide_opened_file>", uuid="u1", block=0)
    ask = _p("fix the bug in a.py", uuid="u1", block=1)
    counts = conv_build._label([ide, ask], [_lab("fix the bug in a.py", uuid="u1", block=0)])   # SWE-chat dropped block 0
    assert (ide["prompt_pushback"], ask["prompt_pushback"]) == (None, "correction")
    assert counts == {"id_text_disagrees": 1, "typed": 1, "by_text": 1, "pushback_typed": 1, "pushback_by_text": 1}
    row = _p("fix the bug in a.py and run the tests", uuid="u2")
    counts = conv_build._label([row], [_lab("fix the bug in a.py", uuid="u2")])   # SWE-chat's copy, cut
    assert row["prompt_pushback"] == "correction" and counts["by_id"] == 1


def test_a_labels_text_key_holds_masks_wrappers_and_images_alike():
    for ours, theirs in (("<timestamp>Friday, Aug 28, 2026, 6:36 PM (UTC+5:30)</timestamp>\n<user_query>\nhi there\n"
                          "</user_query>", "<timestamp>Friday, Aug 28, 2026, 6:36 PM (UTC+5:30)</timestamp> hi there"),
                         ("mail me at jane@corp.io", "mail me at <PRESIDIO_ANONYMIZED_EMAIL_ADDRESS>"),
                         ("[Image 1] too much white space [Image: image/png]", "[Image 1] too much  white space"),
                         # SWE-chat keeps a tool's block the row's text no longer holds (47 labels lost, 2026-10-05)
                         ("fix the bug", "<system_instruction>Be brief.</system_instruction>\nfix the bug"),
                         ("fix the bug", "<in-app-browser-context>page</in-app-browser-context>\nfix the bug")):
        row = _p(ours)
        conv_build._label([row], [_lab(theirs)])
        assert row["prompt_pushback"] == "correction", (ours, theirs)
    row = _p("hi there, and more")
    assert conv_build._label([row], [_lab("hi there")]) == {"none": 1, "pushback_none": 1} and row["prompt_pushback"] is None


def test_a_label_lands_where_the_row_masks_what_swechats_copy_shows():
    """Version 2026-10-11: SWE-chat's copies of 506 sessions turned duplicates of Entire's, whose rows were the same
    entry for entry but masked for personal data (a name in a path); 467 labels landed on no row."""
    row = _p("run it in /Users/[REDACTED]/proj and /Users/[REDACTED]/x", uuid="u1")
    counts = conv_build._label([row], [_lab("run it in /Users/alice/proj and /Users/alice/x", uuid="u1")])
    assert row["prompt_pushback"] == "correction" and counts["by_id_masked"] == 1
    row = _p("run it in /Users/[REDACTED]/proj please")   # no entry id: the text, masks aside
    counts = conv_build._label([row], [_lab("run it in /Users/bob/proj please")])
    assert row["prompt_pushback"] == "correction" and counts["by_text_masked"] == 1
    for ours, theirs in (("fix /Users/[REDACTED]/a.py now", "fix /Users/alice/b.py now"),   # differs past the mask
                         ("fix /Users/[REDACTED]/a.py", "fix /Users//a.py"),                 # a mask stands for something
                         ("[REDACTED]", "anything at all, said once")):                     # all masked, no entry id
        row = _p(ours)
        assert conv_build._label([row], [_lab(theirs)]) == {"none": 1, "pushback_none": 1}, (ours, theirs)
    row = _p("[REDACTED]", uuid="u3")   # all masked, the same entry: SWE-chat's label for it
    assert conv_build._label([row], [_lab("my token is hunter2-xyz", uuid="u3")])["by_id_masked"] == 1


def test_a_label_lands_by_its_start_where_the_row_masks_a_name_in_it():
    """Version 2026-10-11: one Project Runner label had landed by its first 200 characters a week before; the Entire
    copy masked a name at character 93 of it, and the start differed."""
    start = "Implement the plan in /Users/{}/proj: " + "x" * 200
    row = _p(start.format("[REDACTED]") + " and the rest as ours has it")
    counts = conv_build._label([row], [_lab(start.format("alice") + " and SWE-chat's copy, cut otherwise")])
    assert row["prompt_pushback"] == "correction" and counts["by_prefix_masked"] == 1
    row = _p(start.format("[REDACTED]") + " one")   # two of SWE-chat's begin so: none
    assert conv_build._label([row], [_lab(start.format("alice") + " two"), _lab(start.format("bob") + " three")])["none"] == 2
    many = "/Users/[REDACTED] " * 12 + "y" * 200   # a start mostly masked (110 of its 200) says too little
    row = _p(many + " ours")
    assert conv_build._label([row], [_lab(many.replace("[REDACTED]", "al") + " theirs")])["none"] == 1
    edge = "z" * 195 + "{} and more"   # a mask across the 200th character: the start ends before it
    row = _p(edge.format("[REDACTED]") + " of ours")
    counts = conv_build._label([row], [_lab(edge.format("alice") + " of theirs")])
    assert row["prompt_pushback"] == "correction" and counts["by_prefix_masked"] == 1


def _i(content, uuid):
    return {**_p(content, False, uuid), "turn_type": "system_injected"}


def _q(content, subtype="user_prompt_enqueued", typed=True):
    return {**_p(content, typed), "turn_type": "queue_operation", "role": "metadata", "kind": "queued",
            "queue_op_subtype": subtype}


def test_a_label_lands_on_a_queued_message_no_prompt_row_holds():
    """150 Claude Code sessions of version 2026-10-11 drawn (2026-10-12): 74 of SWE-chat's 2,405 labels there (3%)
    landed on no row and held the text of a message queued while the agent worked. Over the whole version, such a
    label lands on its delivery's row by entry id where SWE-chat names one (pass 6); one whose queued row is the
    message's only row lands there (140 labels, 0.1% of Claude Code's 149,522)."""
    queued, done = _q("also bump the version"), _q("also bump the version", "user_prompt_delivered")
    counts = conv_build._label([queued, done], [_lab("also bump the version")])
    assert (queued["prompt_pushback"], done["prompt_pushback"]) == ("correction", None)
    assert counts == {"typed": 1, "by_queued": 1, "pushback_typed": 1, "pushback_by_queued": 1}
    queued, delivered = _q("run the tests"), _p("run the tests")   # its delivery is a prompt row: the label stays there
    counts = conv_build._label([queued, delivered], [_lab("run the tests")])
    assert (queued["prompt_pushback"], delivered["prompt_pushback"]) == (None, "correction") and counts["by_text"] == 1
    queued = _q("fix /Users/[REDACTED]/app.py first")   # the Entire copy masks a name SWE-chat's shows
    assert conv_build._label([queued], [_lab("fix /Users/alice/app.py first")])["by_queued"] == 1
    for row in (_q(None), _q("x", "user_prompt_discarded"), _q("x", "task_notification"), _q("other words")):
        assert conv_build._label([row], [_lab("x")]) == {"none": 1, "pushback_none": 1}, row


def test_a_label_lands_on_the_delivery_of_a_prompt_the_agent_scheduled_by_its_entry():
    """Version 2026-10-11 measured (2026-10-12): SWE-chat v2 files Claude Code's delivery of a prompt the agent
    scheduled for itself (a meta entry, our system_injected row) as a prompt and labels it, by its entry id; the prompt
    was also queued, so a queued row holds the same text."""
    queued, delivered = _q("Check CI on PR #9", typed=False), _i("Check CI on PR #9", "e1")
    counts = conv_build._label([queued, delivered], [_lab("Check CI on PR #9", "non_pushback", uuid="e1")])
    assert (queued["prompt_pushback"], delivered["prompt_pushback"]) == (None, "non_pushback")
    assert counts["by_id_injected"] == 1 and counts["other"] == 1
    row = _i("Check CI on PR #10", "e2")   # the same entry, another text: none
    assert conv_build._label([row], [_lab("Check CI on PR #9", uuid="e2")])["none"] == 1
    queued, delivered = _q("Check CI on PR #11", typed=False), _i("Check CI on PR #11", "e4")
    conv_build._label([queued, delivered], [_lab("Check CI on PR #11")])   # no entry id: neither row is plain
    assert (queued["prompt_pushback"], delivered["prompt_pushback"]) == (None, None)
    row = _i("fix /Users/[REDACTED]/a.py", "e5")   # the Entire copy masks a name SWE-chat's shows
    assert conv_build._label([row], [_lab("fix /Users/alice/a.py", uuid="e5")])["by_id_injected"] == 1
    # an older pass placed it by text first (on the prompt row, though the entry id names the other): no label an
    # earlier pass gives moves
    said, injected = _p("look again"), _i("look again", "e3")
    conv_build._label([said, injected], [_lab("look again", uuid="e3")])
    assert (said["prompt_pushback"], injected["prompt_pushback"]) == ("correction", None)


def test_a_queued_text_said_more_than_once_takes_a_label_only_in_turn_order():
    """The independent review of the queued pass (2026-10-12): a message queued, delivered as a prompt, then queued
    again took the second message's label onto the first. A text a prompt row holds is left to the prompt passes, and
    queued rows take labels only as many as SWE-chat's untaken ones with their text, in order."""
    first, delivered, again = _q("check the build"), _p("check the build"), _q("check the build")
    conv_build._label([first, delivered, again], [_lab("check the build", "non_pushback"),
                                                  _lab("check the build", "failure_report")])
    got = (first["prompt_pushback"], delivered["prompt_pushback"], again["prompt_pushback"])
    assert got == (None, "non_pushback", None)
    one, two = _q("ship it"), _q("ship it")   # queued twice, never a prompt row: in order
    counts = conv_build._label([one, two], [_lab("ship it", "rejection"), _lab("ship it", "non_pushback")])
    assert (one["prompt_pushback"], two["prompt_pushback"]) == ("rejection", "non_pushback")
    assert counts["by_queued"] == 2
    one, two = _q("ship it"), _q("ship it")   # two rows, one label: which is not plain
    assert conv_build._label([one, two], [_lab("ship it")])["none"] == 1 and one["prompt_pushback"] is None
    one = _q("ship it")   # one row, two labels: nor is this
    assert conv_build._label([one], [_lab("ship it", "rejection"), _lab("ship it")])["none"] == 2


def test_a_label_lands_on_a_codex_message_holding_an_image():
    """SWE-chat v2 keeps a Codex message with an image as a prompt per block (the tags around the image, the
    developer's words) and labels each: 517 tag blocks (2026-10-12). The row holds the message whole."""
    tags = "<image name=[Image #1]>\n[Image: unknown]\n</image>"
    files = "# Files mentioned by the user:\n\n## a.png: /tmp/a.png\n\n## My request for Codex:\n"
    for ours, words in ((f"{tags}\n[Image #1] how do i pull them?", "[Image #1] how do i pull them?"),
                        ("too much space here\n\n<image>\n[Image: unknown]\n</image>", "too much space here"),
                        ('<image name=[Image #1] path="/var/folders/T/paste-1.png">\n[Image: unknown]\n</image>\n'
                         "fix the layout", "fix the layout"),
                        (f"{files}why red\n{tags}", f"{files}why red"),
                        (f"{tags}\n{tags.replace('#1', '#2')}\nboth are off", "both are off")):
        row = _p(ours)
        counts = conv_build._label([row], [_lab("<image name=[Image #1]>", "non_pushback"),
                                           _lab("</image>", "rejection"), _lab(words, "failure_report")])
        assert row["prompt_pushback"] == "failure_report" and counts["by_image"] == 1 and counts["none"] == 2, ours
    # the app's record of the words has a row of its own, typed, after the message's: the label stays there
    shown, said = _p(f"{tags}\nlook", typed=False), _p("look")
    counts = conv_build._label([shown, said], [_lab("<image name=[Image #1]>"), _lab("look", "rejection")])
    assert (shown["prompt_pushback"], said["prompt_pushback"]) == (None, "rejection") and "by_image" not in counts
    for ours in (tags, f"{tags} glance"):   # no words; other words
        row = _p(ours)
        assert conv_build._label([row], [_lab("<image name=[Image #1]>"), _lab("look")])["none"] == 2, ours
    assert conv_build._label([_p(tags)], [_lab("")])["none"] == 1   # an image alone: not SWE-chat's prompt of no text
    for ours, theirs in (("<image name=[Image #1]> look </image> twice", "twice"),   # tags round the developer's words
                         ('change <image href="a.svg"></image> to img please', "change to img please")):   # markup
        row = _p(ours)
        assert conv_build._label([row], [_lab(theirs)])["none"] == 1, ours
    ide = "# Context from my IDE setup:\n\n## Active file: a.py\n\n## My request for Codex:\nwhy red"
    for ours, theirs in ((f"{tags}\n{ide}", ide),   # tags before the IDE's header: the header comes off too
                         (f"{tags}\nfix /Users/[REDACTED]/a.py", "fix /Users/alice/a.py")):   # a mask in the words
        row = _p(ours)
        assert conv_build._label([row], [_lab(theirs)])["by_image"] == 1, ours
    row = _p(f"{tags}\nlook")   # SWE-chat's own block holds the tags (5 of its 517): without them on both sides
    lab = _lab("<image name=[Image #1]>\n[Image: image/png]\n</image>\nlook", "rejection")
    assert conv_build._label([row], [lab])["by_image"] == 1 and row["prompt_pushback"] == "rejection"
    whole = _p(f"{tags}\nlook")   # SWE-chat's text of the whole message took it by text first: it stays
    conv_build._label([whole], [_lab(f"{tags}\nlook", "rejection"), _lab("look", "non_pushback")])
    assert whole["prompt_pushback"] == "rejection"
    # said twice: the image's row takes none (the plain row's label, by text, is the older passes' as before)
    with_image, plain = _p(f"{tags}\ncontinue"), _p("continue")
    conv_build._label([with_image, plain], [_lab("continue", "rejection"), _lab("continue", "non_pushback")])
    assert (with_image["prompt_pushback"], plain["prompt_pushback"]) == (None, "rejection")


def test_masked_agrees_places_each_piece_once_in_order():
    m = "[REDACTED]"
    assert conv_build._masked_agrees(f"a{m}b{m}c", "aXbYc") and conv_build._masked_agrees(f"{m}{m}", "xy")
    assert conv_build._masked_agrees(f"a{m}b", "a b b") and conv_build._masked_agrees(f"{m}end", "the end")
    assert not conv_build._masked_agrees(f"a{m}b{m}c", "aXcYb") and not conv_build._masked_agrees(f"{m}{m}", "x")
    assert not conv_build._masked_agrees("no mask", "no mask") and not conv_build._masked_agrees(f"a{m}", "a")
    assert not conv_build._masked_agrees(f"ab{m}ba", "aba")   # the two ends may not share a character


def test_repeated_prompts_take_labels_in_order_and_a_shared_start_takes_none():
    a, b, c = _p("continue"), _p("continue"), _p("continue")   # a repeat SWE-chat gave no label of its own gets none
    counts = conv_build._label([a, b, c], [_lab("continue", push="non_pushback"), _lab("continue", push="rejection")])
    assert (a["prompt_pushback"], b["prompt_pushback"], c["prompt_pushback"]) == ("non_pushback", "rejection", None)
    assert counts["typed"] == 2 and counts["by_text"] == 2
    head = "Implement the following plan: " + "x" * 200
    row = _p(head + " step one")
    counts = conv_build._label([row], [_lab(head + " step one, cut otherwise")])
    assert row["prompt_pushback"] == "correction" and counts["by_prefix"] == 1
    one, two = _p(head + " one"), _p(head + " two")   # two of ours begin so: no label by the start
    assert conv_build._label([one, two], [_lab(head + " three")]) == {"none": 1, "pushback_none": 1}
    row = _p(head + " one")   # two of SWE-chat's begin so: none either
    assert conv_build._label([row], [_lab(head + " two"), _lab(head + " three")])["none"] == 2
    assert row["prompt_pushback"] is None


def test_swechat_labels_come_in_its_turn_order(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "SALT-NLP__SWE-chat" / "rev1"
        root.mkdir(parents=True)
        pq.write_table(pa.Table.from_pylist([   # stored out of order: the later turn first
            {"session_id": "s", "turn_number": 5, "source_entry_uuid": None, "content_block_index": 0, "turn_type": "user_prompt",
             "content": "continue", "prompt_intent": "x", "prompt_pushback": "rejection", "language": "en"},
            {"session_id": "s", "turn_number": 2, "source_entry_uuid": "u2", "content_block_index": 0, "turn_type": "user_prompt",
             "content": "continue", "prompt_intent": "x", "prompt_pushback": "non_pushback", "language": "en"}]),
            root / "conversations.parquet")
        entry = {"id": "SALT-NLP/SWE-chat", "copied": True, "files": files_of(root)}
        monkeypatch.setattr(copied_sessions, "entries", lambda ledger: iter([("swe-chat-v2", entry)]))
        got = copied_sessions.swechat_labels(None)["s"]
        assert [(eid, labels[1]) for eid, _, labels in got] == [(("u2", 0), "non_pushback"), (None, "rejection")]


def test_a_label_taken_by_entry_id_is_not_given_again_by_text():
    """The partner's review of rc4: the text rule gave the second row the label the first had taken by id, and
    SWE-chat's own label for the second was lost; a row met first by text must not take the id's label either."""
    one, two = _p("it still fails", uuid="u1"), _p("it still fails", uuid="u9")
    counts = conv_build._label([one, two], [_lab("it still fails", push="failure_report", uuid="u1"),
                                            _lab("it still fails", push="non_pushback", uuid="u2")])
    assert (one["prompt_pushback"], two["prompt_pushback"]) == ("failure_report", "non_pushback")
    assert (counts["typed"], counts["by_id"], counts["by_text"], counts.get("none", 0)) == (2, 1, 1, 0)
    first, second = _p("x again"), _p("x again", uuid="u2")   # the id's row comes second
    conv_build._label([first, second], [_lab("x again", push="rejection", uuid="u2"), _lab("x again", push="non_pushback")])
    assert (first["prompt_pushback"], second["prompt_pushback"]) == ("non_pushback", "rejection")


def test_a_label_taken_by_entry_id_is_not_given_again_by_its_start():
    head = "Implement the following plan: " + "x" * 200
    short = _p(head[:150], uuid="u1")   # our row cut shorter than SWE-chat's: its text begins theirs
    other = _p(head + " two")   # alone among our rows to begin with SWE-chat's 200 characters
    counts = conv_build._label([short, other], [_lab(head + " one", uuid="u1")])
    assert (short["prompt_pushback"], other["prompt_pushback"]) == ("correction", None) and counts["by_id"] == 1


def test_a_dataclaw_record_counts_each_typed_message_and_its_tool_loaded_line_is_the_harnesss(monkeypatch):
    """#24: a DataClaw record holds a whole session on one line, so counted by place every session with typed
    messages was 1 (4,042 DataClaw and OpenHands sessions of version 2026-10-04 until counted again by hand): each
    conversational turn is its own message. #23: Claude Code's `Tool loaded.` in an export is the harness's text, as
    its reader marks it in a transcript (82 DataClaw rows read as typed); a message only opening with it is not."""
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "u__data" / "rev"
        write_jsonl(root / "conversations.jsonl", [
            {"session_id": "s-1", "source": "claude", "model": "claude-x",
             "messages": [{"role": "user", "content": "add a flag"}, {"role": "assistant", "content": "ok"},
                          {"role": "user", "content": "Tool loaded."},
                          {"role": "user", "content": "now run the tests"}, {"role": "assistant", "content": "done"},
                          {"role": "user", "content": "Tool loaded. Then rename it"}]}])
        entry = {"id": "u/data", "url": "https://huggingface.co/datasets/u/data", "revision": "rev", "license": "mit",
                 "copied": True, "files": files_of(root)}
        monkeypatch.setattr(copied_sessions, "entries", lambda ledger: iter([("dataclaw", entry)]))
        rows = copied_sessions.rows(None)["dataclaw"]
        out = conv_build.extract_job(("dataclaw", [(r, [(n, Path(p)) for n, p in r["_read"]]) for r in rows], d, "p"))
        main = pq.read_table(next(f for f in out["files"] if "/conversations/" in f)).to_pylist()
        users = [(r["content"], r["typed_by_developer"], r["actor"], r["kind"]) for r in main if r["role"] == "user"]
        assert users == [("add a flag", True, "developer", "text"), ("Tool loaded.", False, "harness", "text"),
                         ("now run the tests", True, "developer", "text"),
                         ("Tool loaded. Then rename it", True, "developer", "text")], users
        assert out["per"]["u/data:conversations.jsonl:1"]["typed"] == 3
        from collect import events
        assert events.typed_messages(main, by_id=False) == 1   # by place: what the count was before #24



def test_a_dataclaw_record_reads_tool_written_text_and_a_loops_firings_as_not_typed(monkeypatch):
    """#18: DataClaw's records keep no author's mark. A /loop's later firings repeat its command (302 rows of version
    2026-10-04 read as typed), and a button's or a plan click's text reads as a message; the developer's own stay typed."""
    loop = "<command-message>loop</command-message>\n<command-name>/loop</command-name>\n<command-args>check the deploy</command-args>"
    with tempfile.TemporaryDirectory() as d:
        root = Path(d) / "u__data" / "rev"
        write_jsonl(root / "conversations.jsonl", [
            {"session_id": "s-1", "source": "claude", "model": "claude-x",
             "messages": [{"role": "user", "content": loop}, {"role": "assistant", "content": "ok"},
                          {"role": "user", "content": loop}, {"role": "assistant", "content": "checked"},
                          {"role": "user", "content": "@agent Try Again"}, {"role": "assistant", "content": "again"},
                          {"role": "user", "content": "Implement the plan."}, {"role": "assistant", "content": "done"},
                          {"role": "user", "content": "fix the flaky test"}, {"role": "assistant", "content": "fixed"}]}])
        entry = {"id": "u/data", "url": "https://huggingface.co/datasets/u/data", "revision": "rev", "license": "mit",
                 "copied": True, "files": files_of(root)}
        monkeypatch.setattr(copied_sessions, "entries", lambda ledger: iter([("dataclaw", entry)]))
        rows = copied_sessions.rows(None)["dataclaw"]
        out = conv_build.extract_job(("dataclaw", [(r, [(n, Path(p)) for n, p in r["_read"]]) for r in rows], d, "p"))
        main = pq.read_table(next(f for f in out["files"] if "/conversations/" in f)).to_pylist()
        users = [(r["typed_by_developer"], r["actor"], r["kind"]) for r in main if r["role"] == "user"]
        assert users == [(True, "developer", "command"), (False, "harness", "command"), (False, "developer", "command"),
                         (False, "developer", "plan"), (True, "developer", "text")], users
        assert out["per"]["u/data:conversations.jsonl:1"]["typed"] == 2   # the first /loop and the request
