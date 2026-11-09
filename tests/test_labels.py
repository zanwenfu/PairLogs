"""The labels every reader shares (2026-10-05): who wrote a user message by its text, the developer's words in a
wrapped message, tool categories by SWE-chat's own rules, one name per agent, and a transcript's own format."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from collect import events
from collect import formats
from collect import scan
from collect.facts import _iso
from collect.model import canonical_agent

ROOT = Path(__file__).resolve().parents[1]


def user_row(text, typed=True, actor="developer", kind="text"):
    return events.row(role="user", turn_type="user_prompt", content=text, kind=kind, actor=actor,
                      typed_by_developer=typed)


def test_harness_text_in_a_user_message_is_not_typed_whatever_the_agent():
    cases = {
        "<task-notification>\n<task-id>b1</task-id> done</task-notification>": (False, "harness", "notice"),
        "This session is being continued from a previous conversation that ran out of context.": (False, "harness", "compaction"),
        "<local-command-stdout>Total cost: $0.12</local-command-stdout>": (False, "harness", "notice"),
        "[Request interrupted by user for tool use]": (False, "developer", "interrupt"),
        "<turn_aborted>The user interrupted.</turn_aborted>": (False, "developer", "interrupt"),
        "Base directory for this skill: /x/skills/pdf": (False, "harness", "notice"),
        "<system-reminder>Plan mode is on</system-reminder>": (False, "harness", "notice"),
        "<environment_context><cwd>/w</cwd></environment_context>": (False, "harness", "notice"),
        "# AGENTS.md instructions for /w": (False, "harness", "notice"),
        "Review the change in $ARGUMENTS and report.": (False, "harness", "notice"),
        "Run ${CLAUDE_PLUGIN_ROOT}/scripts/check.sh": (False, "harness", "notice"),
        '{"task_id": "t1", "tool_use_id": "u", "description": "build", "task_type": "local_bash"}': (False, "harness", "notice"),
    }
    for text, want in cases.items():
        got = events.user_text_rule(text)
        assert (got["typed"], got["actor"], got["kind"]) == want, (text, got)
    cmd = events.user_text_rule("<command-message>review</command-message>\n<command-name>/review</command-name>\n<command-args>pr 12</command-args>")
    assert cmd == {"typed": True, "actor": "developer", "kind": "command", "command": "/review pr 12"}
    for plain in ("fix the failing test", "Caveat: this is my own note", '{"task": "a json the developer pasted"}', "$5 is fine"):
        assert events.user_text_rule(plain) is None, plain


def test_wrapped_messages_keep_the_developers_words():
    cursor = ("<timestamp>Mon Oct 5</timestamp>\n<attached_files>\n<file a.py>x=1</file>\n</attached_files>\n"
              "<user_query>\nfix the bug in a.py\n</user_query>")
    assert events.unwrap(cursor) == "fix the bug in a.py"
    # Text outside the query is kept (a merged second message), each block Cursor adds is not.
    assert events.unwrap("also check b.py\n<user_query>fix a</user_query>\n<image_files>img</image_files>") == "also check b.py\nfix a"
    assert events.unwrap("<timestamp>t</timestamp><user_query></user_query>") == ""   # nothing typed (#17's review)
    gemini = "<USER_REQUEST>\nadd tests\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nThe current local time is 3pm.\n</ADDITIONAL_METADATA>"
    assert events.unwrap(gemini) == "add tests"
    assert events.unwrap("<USER_REQUEST>a</USER_REQUEST> and more words") == "a and more words"
    ide = "<ide_opened_file>The user opened a.py in the IDE.</ide_opened_file>\nwhy does this fail?"
    assert events.unwrap(ide) == "why does this fail?"
    assert events.unwrap("<ide_selection>lines 1-3</ide_selection>") == ""
    assert events.unwrap("ship it\n<system-reminder>todo list empty</system-reminder>") == "ship it"
    assert events.unwrap("<ide_selection>unclosed and then words") == "<ide_selection>unclosed and then words"


def test_unwrapping_takes_linear_time_on_unclosed_tags():
    code = r"""
import time
from collect import events
text = "<user_query>" + "<timestamp>x " * 200000 + "</user_query>" + "<ide_selection>y " * 200000
t = time.perf_counter()
events.unwrap(text)
print(round(time.perf_counter() - t, 3))
"""
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=ROOT,
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert got.returncode == 0 and float(got.stdout) < 5, (got.stdout, got.stderr[-500:])


def test_a_row_marked_typed_is_told_again_by_its_text():
    rows = [user_row("<task-notification>t</task-notification>"),
            user_row("<ide_opened_file>a.py</ide_opened_file>"),
            user_row("<ide_opened_file>a.py</ide_opened_file>\nexplain this"),
            user_row("   "),
            user_row("<command-name>/clear</command-name>", kind="command"),
            user_row("Do $ARGUMENTS now", typed=None, actor=None)]
    out = events.finish(rows, {"session_key": "s"})
    told = [(r["content"], r["typed_by_developer"], r["actor"], r["kind"]) for r in out]
    assert told[0] == ("<task-notification>t</task-notification>", False, "harness", "notice")
    assert told[1] == ("<ide_opened_file>a.py</ide_opened_file>", False, "harness", "notice")
    assert told[2] == ("explain this", True, "developer", "text") and out[2]["char_count"] == len("explain this")
    assert told[3][1:] == (True, "developer", "text")      # no text to tell by: left as its reader read it (2026-10-07)
    assert told[4][1:] == (True, "developer", "command")   # a command its reader decided: left as it is
    assert told[5][1:] == (None, None, "text")             # unknown on purpose (a program's run): never made typed


def test_a_message_whose_author_the_format_does_not_record():
    base = {"raw_file": "f", "raw_line": 1}
    plain = formats._prompt_row(base, "add a login page")
    assert (plain["typed_by_developer"], plain["actor"], plain["kind"]) == (True, "developer", "text")
    wrapped = formats._prompt_row(base, "<USER_REQUEST>add a login page</USER_REQUEST>")
    assert (wrapped["content"], wrapped["typed_by_developer"]) == ("add a login page", True)
    notice = formats._prompt_row(base, "[Request interrupted by user]")
    assert (notice["typed_by_developer"], notice["actor"], notice["kind"]) == (False, "developer", "interrupt")
    ide = formats._prompt_row(base, "<ide_selection>x</ide_selection>")
    assert (ide["content"], ide["typed_by_developer"], ide["actor"]) == ("<ide_selection>x</ide_selection>", False, "harness")


def test_tool_rows_carry_swechats_categories():
    def call(name, cid, command=None):
        return events.row(role="tool_use", turn_type="tool_use", tool_name=name, tool_call_id=cid, command=command,
                          kind="tool_call", actor="agent", typed_by_developer=False)

    def result(cid):
        return events.row(role="tool_result", turn_type="tool_result", tool_call_id=cid, kind="tool_result",
                          actor="tool", typed_by_developer=False, content="ok")

    rows = [call("Read", "1"), result("1"), call("Bash", "2", "GOCACHE=/tmp/x go test ./..."), result("2"),
            call("exec_command", "3", "/usr/bin/git status"), call("TaskCreate", "4"), call("mcp__x__y", "5"),
            call("run_terminal_cmd", "6", "ls -la"), call("exec", "7", "git log"), call("MultiEdit", "8"),
            call("Bash", "9", "python3 x.py"), call("bash", "10", "   ")]
    out = events.finish(rows, {"session_key": "s"})
    got = [(r["tool_name"], r["turn_type"], r["category"], r["bash_category"]) for r in out]
    assert got == [("Read", "tool_use", "Research", None), ("Read", "tool_result", "Research", None),
                   ("Bash", "tool_use", "Action", "test/build"), ("Bash", "tool_result", "Action", "test/build"),
                   ("exec_command", "tool_use", "Action", "git"), ("TaskCreate", "tool_use", "Orchestration", None),
                   ("mcp__x__y", "tool_use", "Other", None), ("run_terminal_cmd", "tool_use", "Action", "file ops"),
                   ("exec", "tool_use", "Other", None), ("MultiEdit", "tool_use", "Other", None),
                   ("Bash", "tool_use", "Action", "other"), ("bash", "tool_use", "Action", None)]   # no command: not known
    assert events.bash_category("A=1 B=2 ~/.cargo/bin/cargo build") == "package manager"
    assert events.bash_category(None) == "other"


def test_one_name_per_agent():
    assert {canonical_agent(x) for x in ("claude-code", "Claude Code", "claude_code", " CLAUDE   code ")} == {"Claude Code"}
    assert {canonical_agent(x) for x in ("opencode", "Opencode", "OpenCode")} == {"OpenCode"}
    assert canonical_agent("Codex CLI") == "Codex" and canonical_agent("Factory Droid CLI") == "Factory AI Droid"
    assert canonical_agent("Pi Coding Agent") == "Pi" and canonical_agent("unknown") is None and canonical_agent("other") is None
    assert canonical_agent("Oh My Pi") == "Oh My Pi" and canonical_agent("Cursor CLI") == "Cursor CLI" and canonical_agent(None) is None


def test_a_transcripts_own_format_over_the_agent_recorded():
    lines = [{"timestamp": "2026-10-01T00:00:00Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "/w"}},
             {"timestamp": "2026-10-01T00:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "hi"}},
             {"timestamp": "2026-10-01T00:00:01Z", "type": "response_item",
              "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}},
             {"timestamp": "2026-10-01T00:00:02Z", "type": "response_item",
              "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "hello"}]}}]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "full.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        assert events.sniff([("full.jsonl", path)]) == "codex"
        session = {"session_key": "entire:s1", "agent": "Claude Code"}
        rows, _ = events.for_session("Claude Code", [("full.jsonl", path)], session)
    assert session["agent"] == "Codex" and rows and {r["agent"] for r in rows} == {"Codex"}
    assert [(r["role"], r["content"]) for r in rows if r["role"] in ("user", "assistant")] == [("user", "hi"), ("assistant", "hello")]


def test_zero_times_are_no_times():
    assert _iso(0) is None and _iso("1970-01-01T00:00:00.000Z") is None and _iso(-5) is None
    assert _iso("2026-10-05T01:02:03Z") == "2026-10-05T01:02:03.000Z"


def test_the_published_columns():
    from collect import build, conv_build

    names = build.SCHEMA.names
    assert "truncated" not in names and "truncated_reason" not in names
    assert str(build.SCHEMA.field("redactions").type) == "struct<secrets: int64, emails: int64, home_users: int64>"
    for schema in (conv_build.SCHEMA, conv_build.SUB_SCHEMA):
        assert "user_id" not in schema.names and {"category", "bash_category"} <= set(schema.names)


def test_session_labels_from_rows_where_the_facts_lack_them():
    from collect import conv_build

    rows = [events.row(role="user", turn_type="user_prompt", timestamp="2026-10-05T01:00:00.000Z"),
            events.row(role="assistant", turn_type="assistant_response", model="m1", timestamp="2026-10-05T01:00:05.000Z"),
            events.row(role="tool_use", turn_type="tool_use", model="m1"),
            events.row(role="tool_result", turn_type="tool_result", timestamp="2026-10-05T01:00:09.000Z")]
    got = conv_build.row_facts(rows)
    assert got == {"n_user_entries": 1, "n_agent_messages": 1, "n_tool_calls": 1, "models": ["m1"],
                   "started_at": "2026-10-05T01:00:00.000Z", "ended_at": "2026-10-05T01:00:09.000Z",
                   "has_tool_results": True, "has_timestamps": True}


def test_the_written_session_takes_its_rows_labels_and_its_formats_agent(tmp_path):
    """A stored row with no facts (a format read after its facts were made) and an agent its transcript contradicts:
    the published row has its rows' labels and the transcript's agent, and the hidden-items tally as three counts."""
    import pyarrow.parquet as pq
    from collections import Counter

    from collect import build
    from collect.model import LABELS

    row = {k: None for k in LABELS}
    row.update(source="entire", source_id="o/r:s1", agent="Claude Code", models=[], also_in=[], files=[])
    conv = {"session_key": "entire:s1", "session_id": "s1", "n_conversation_rows": 4, "n_subagent_rows": 0,
            "typed": 1, "agent": "Codex",
            "row_facts": {"n_user_entries": 1, "n_agent_messages": 2, "n_tool_calls": 3, "models": ["m1"],
                          "started_at": "2026-10-05T01:00:00.000Z", "ended_at": "2026-10-05T01:00:09.000Z",
                          "has_tool_results": True, "has_timestamps": True}}
    got = build._write_part(([row], {}, {}, {}, tmp_path / "data" / "entire", "part-000", {"o/r:s1": conv}))
    assert got["rows"] == 1
    out = pq.read_table(next((tmp_path / "data" / "entire").glob("*.parquet"))).to_pylist()[0]
    assert out["agent"] == "Codex" and out["n_user_messages"] == 1
    assert (out["n_user_entries"], out["n_agent_messages"], out["n_tool_calls"], out["models"]) == (1, 2, 3, ["m1"])
    assert (out["started_at"], out["ended_at"], out["has_tool_results"]) == ("2026-10-05T01:00:00.000Z", "2026-10-05T01:00:09.000Z", True)
    assert out["redactions"] == {"secrets": 0, "emails": 0, "home_users": 0}


def test_a_sessions_times_are_its_messages_where_they_carry_times(tmp_path):
    """A recorded start can be a file's own time or a feedback's: 1,585 sessions ended before they started
    (2026-10-05). The messages' first and last times are published; a recorded time that differs is kept."""
    import json

    import pyarrow.parquet as pq

    from collect import build
    from collect.model import LABELS

    row = {k: None for k in LABELS}
    row.update(source="openhands-feedback", source_id="o/r:x", agent="OpenHands", models=[], also_in=[], files=[],
               started_at="2026-10-05T03:00:00Z")   # the feedback's time, after the session
    conv = {"session_key": "openhands-feedback:x", "n_conversation_rows": 2, "n_subagent_rows": 0, "typed": 1,
            "row_facts": {"started_at": "2026-10-05T01:00:00.000Z", "ended_at": "2026-10-05T01:30:00.000Z"}}
    build._write_part(([row], {}, {}, {}, tmp_path / "d", "part-000", {"o/r:x": conv}))
    out = pq.read_table(next((tmp_path / "d").glob("*.parquet"))).to_pylist()[0]
    assert (out["started_at"], out["ended_at"]) == ("2026-10-05T01:00:00.000Z", "2026-10-05T01:30:00.000Z")
    assert json.loads(out["source_facts"])["recorded_started_at"] == "2026-10-05T03:00:00.000Z"
    assert "recorded_ended_at" not in json.loads(out["source_facts"])   # none was recorded


def test_a_duplicate_with_no_stored_counts_takes_them_from_its_own_files(tmp_path):
    """Version 2026-10-11: 460 SWE-chat v2 duplicates of Project Runner and Kimi Code published no counts (facts.py
    has no parser for them, and a duplicate has no rows); the written row now reads its own files for them."""
    import json

    import pyarrow.parquet as pq

    from collect import build
    from collect.model import LABELS
    from tests.test_formats_more import runner

    path = tmp_path / "t" / "full.jsonl"
    path.parent.mkdir()
    path.write_text("\n".join(json.dumps(x) for x in runner("bridge", "add tests for a.py")) + "\n")
    row = {k: None for k in LABELS}
    row.update(source="swe-chat-v2", source_id="s1", agent="Project Runner", models=[], also_in=[], files=[],
               duplicate_of="entire:o/r:s1", _read=[("full.jsonl", str(path))],
               _session={"session_id": "s1", "checkpoint_pk": None, "strategy": None})
    bare = {**row, "source_id": "s2", "_read": []}   # a duplicate with no file: nothing to count
    build._write_part(([row, bare], {}, {}, {}, tmp_path / "d", "part-000", {}))
    out = {r["source_id"]: r for r in pq.read_table(next((tmp_path / "d").glob("*.parquet"))).to_pylist()}
    one = out["s1"]
    assert (one["n_user_messages"], one["n_agent_messages"], one["models"], one["has_tool_results"]) == (1, 1, ["gemini-x"], False)
    assert one["started_at"] and one["ended_at"] and one["n_conversation_rows"] is None   # a duplicate's: no rows
    assert out["s2"]["n_agent_messages"] is None and out["s2"]["n_user_messages"] is None


def test_a_stored_placeholder_time_is_published_as_no_time(tmp_path):
    import pyarrow.parquet as pq

    from collect import build
    from collect.model import LABELS

    row = {k: None for k in LABELS}
    row.update(source="specstory", source_id="o/r:f.md", agent=None, models=[], also_in=[], files=[],
               started_at="1970-01-01T00:00:00.000Z", ended_at="2026-10-05T01:02:03Z")
    build._write_part(([row], {}, {}, {}, tmp_path / "data" / "specstory", "part-000", {}))
    out = pq.read_table(next((tmp_path / "data" / "specstory").glob("*.parquet"))).to_pylist()[0]
    assert out["started_at"] is None and out["ended_at"] == "2026-10-05T01:02:03.000Z"


def test_the_card_counts_each_subsets_hidden_items_and_states_the_bar(tmp_path):
    """card.write over a session holding hidden items: the tally is three counts since rc15 (the partner session's
    review found it still read the per-rule list, a TypeError once a count was above 0), and the text says what was
    done: most secrets, no feedback into the session files, unless the build was strict."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from collect import build, card

    v = tmp_path / "2026-10-05"
    (v / "data" / "entire").mkdir(parents=True)
    row = {f.name: None for f in build.SCHEMA}
    row.update(source="entire", source_id="o/r:s", session_key="entire:s", agent="Claude Code", models=[], also_in=[],
               files=[], repo="o/r", bytes=10, redactions={"secrets": 3, "emails": 1, "home_users": 2})
    pq.write_table(pa.Table.from_pylist([row], schema=build.SCHEMA), str(v / "data" / "entire" / "part-000-00000.parquet"))
    changelog = {"version": v.name, "built_at": "2026-10-05T00:00:00Z", "scraped": {}, "copied": {}, "conversations": {},
                 "scan": {"rules": "x"}, "duplicate_pairs": {}, "gaps": [], "licences_looked_up": True,
                 "failed_sources": [], "redaction": "most"}
    card.write(v, changelog, log=lambda m: None)
    text = (v / "README.md").read_text()
    assert "| entire | 3 | 1 | 2 |" in text and "The bar is most secrets" in text
    assert "and in the session's files" not in text
    card.write(v, {**changelog, "redaction": "strict"}, log=lambda m: None)
    text = (v / "README.md").read_text()
    assert "The bar is most secrets" not in text and "and in the session's files" in text


def test_shell_calls_keep_their_command_and_are_read_by_what_they_run():
    """The partner session's review of rc15: Cursor's and Copilot's terminal tools and Codex's `local_shell` lost their
    command, and Codex's `bash -lc ...` was always `other`."""
    assert events.tool_fields("run_terminal_cmd", {"command": "git status"})["command"] == "git status"
    assert events.tool_fields("run_in_terminal", {"command": "npm test"})["command"] == "npm test"
    assert events.tool_fields("local_shell", {"action": {"type": "exec", "command": ["bash", "-lc", "ls"]}})["command"] == "bash -lc ls"
    for command, want in (("bash -lc git status", "git"), ("bash -lc 'cd x && pytest -q'", "file ops"),
                          ('/bin/zsh -c "uv sync"', "package manager"), ("sh -c make", "test/build"),
                          ("bash script.sh", "other"), ("bash -lc", "other"), ("git log", "git")):
        assert events.bash_category(command) == want, command


def test_a_slash_command_is_read_in_linear_time_and_as_before():
    import re

    for raw in ("<command-message>review</command-message>\n<command-name>/review</command-name>\n<command-args>pr 12</command-args>",
                "<command-name>/clear</command-name>", "<command-name>/x</command-name><command-name>/y</command-name>",
                "<command-name>unclosed", "nothing"):
        name = re.search(r"<command-name>(.*?)</command-name>", raw, re.S)
        args = re.search(r"<command-args>(.*?)</command-args>", raw, re.S)
        old = " ".join(x for x in ((name.group(1).strip() if name else ""), (args.group(1).strip() if args else "")) if x)
        assert events._command(raw) == old, raw
    code = r"""
import time
from collect import events
raw = "<command-name>" + "<command-name>x " * 300000
t = time.perf_counter()
events.user_text_rule(raw)
print(round(time.perf_counter() - t, 3))
"""
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=ROOT,
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert got.returncode == 0 and float(got.stdout) < 2, (got.stdout, got.stderr[-500:])


def test_a_pasted_excerpt_holding_a_command_tag_is_not_a_command():
    assert events.user_text_rule("look at this log:\n<command-name>/review</command-name>") is None
    assert events.user_text_rule("<command-message>review</command-message><command-name>/review</command-name>")["kind"] == "command"


def test_stored_and_copied_rows_give_one_name_per_agent():
    """The partner session's mutants of rc18: canonical_agent in coerce and in copied rows had no test of its own."""
    from collect import copied_sessions
    from collect.model import coerce

    assert coerce({"agent": "claude-code"})["agent"] == "Claude Code"
    assert coerce({"agent": "unknown"})["agent"] is None
    entry = {"id": "u/data", "url": "https://huggingface.co/datasets/u/data", "revision": "r"}
    assert copied_sessions._row("dataclaw", entry, "u/data:f:1", agent="codex")["agent"] == "Codex"


def test_a_sessions_typed_count_is_told_by_its_formats_agent(tmp_path, monkeypatch):
    """A Codex rollout recorded as Claude Code's is counted as Codex's (by place, not by entry id)."""
    from collect import conv_build

    lines = [{"timestamp": "2026-10-01T00:00:00Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "/w"}},
             {"timestamp": "2026-10-01T00:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "hi"}},
             {"timestamp": "2026-10-01T00:00:01Z", "type": "response_item",
              "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}}]
    path = tmp_path / "full.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    told = []
    real = events.count_typed
    monkeypatch.setattr(events, "count_typed",
                        lambda agent, rows, **kw: told.append((agent, kw.get("per_turn"))) or real(agent, rows, **kw))
    row = {"source": "entire", "source_id": "o/r:s1", "agent": "Claude Code", "repo": "o/r", "extra": {}}
    out = conv_build.extract_job(("entire", [(row, [("full.jsonl", path)])], str(tmp_path), "part-000"))
    # a transcript is counted by its entries, never per turn (#24's rule is for a copied dataset's export)
    assert told == [("Codex", False)] and out["per"]["o/r:s1"]["agent"] == "Codex"


def test_codex_text_a_harness_wrote_is_its_own_when_no_event_marks_the_session():
    """An older Codex rollout with no event marks: a user message whose text is a harness's is the harness's, any
    other the developer's (the partner session's mutant of rc18 removed the branch with no test going red)."""
    from collect import formats

    lines = [{"timestamp": "2026-10-01T00:00:00Z", "type": "session_meta", "payload": {"id": "s1", "cwd": "/w"}},
             {"timestamp": "2026-10-01T00:00:01Z", "type": "response_item",
              "payload": {"type": "message", "role": "user",
                          "content": [{"type": "input_text", "text": "<environment_context>\n<cwd>/w</cwd>\n</environment_context>"}]}},
             {"timestamp": "2026-10-01T00:00:02Z", "type": "response_item",
              "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fix it"}]}}]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "full.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        rows, _ = formats.codex([("full.jsonl", path)], {"session_key": "s", "agent": "Codex"})
    told = [(r["typed_by_developer"], r["actor"], r["kind"]) for r in rows if r["role"] == "user"]
    assert told == [(False, "harness", "notice"), (True, "developer", "text")]   # typed since 2026-10-07


def test_only_specstorys_markdown_chats_are_collected_and_built():
    """100 stored SpecStory rows were no chat: AppleDouble `._x.md` files, JSON, `.gitkeep`, a backup (2026-10-05)."""
    from collect.build import collected
    from collect.sources.specstory import in_history

    assert in_history(".specstory/history/2026-01-02_03-04Z-add.md") and in_history("a/b/.specstory/history/x.md")
    for path in (".specstory/history/._2026-01-02.md", ".specstory/history/notes.json", ".specstory/history/.gitkeep",
                 ".specstory/history/x.md.bak", ".specstory/x.md", ".specstory/history/sub/x.md"):
        assert not in_history(path), path
    assert collected({"source": "specstory", "files": [{"path": ".specstory/history/x.md"}]})
    assert not collected({"source": "specstory", "files": [{"path": ".specstory/history/._x.md"}]})
    assert collected({"source": "entire", "files": [{"path": "._anything"}]})


def test_the_developers_words_after_a_reminder_stay_theirs():
    """The partner session's measurement of rc20: 14 messages opening with a reminder had the developer's words after
    it, and the reminder's lead decided first. Unwrap first, then the rule on what is left."""
    words = "<system-reminder>todo list is empty</system-reminder>\n请修复登录页面"
    assert events.tell(words)[:4] == ("请修复登录页面", True, "developer", "text")
    assert events.tell("<system-reminder>a</system-reminder>\n<!-- OMO_INTERNAL: x -->")[1:4] == (False, "harness", "notice")
    assert events.tell("<system-reminder>unclosed reminder")[1:4] == (False, "harness", "notice")
    rows = events.finish([user_row(words)], {"session_key": "s"})
    assert (rows[0]["content"], rows[0]["typed_by_developer"]) == ("请修复登录页面", True)


def test_a_template_counts_near_the_start_of_a_typed_message_only():
    mid = ("The deploy command I wrote last week works for the staging cluster but not for production, and the logs "
           "show nothing useful. Its body uses $ARGUMENTS to pass the issue number; why is it empty?")
    assert mid.index("$ARGUMENTS") > 120
    rows = events.finish([user_row(mid), user_row("Fix issue $ARGUMENTS and open a PR")], {"session_key": "s"})
    assert [r["typed_by_developer"] for r in rows] == [True, False]
    assert events.tell(mid)[1] is False   # a format that marks no author: a template anywhere is a template


def test_shell_commands_from_every_shape_of_input():
    assert events.tool_fields("Bash", "git status")["command"] == "git status"           # DataClaw: the input is the command
    assert events.tool_fields("run_shell_command", "{'command': 'ls -la'}")["command"] == "ls -la"   # a dict's repr
    assert events.tool_fields("exec", {"command": "make"})["command"] == "make"          # Devin's exec
    assert events.tool_fields("exec", "const x = 1; console.log(x)")["command"] is None  # Codex's exec: JavaScript
    assert events.tool_fields("run", {"command": "pytest"})["command"] == "pytest"       # OpenHands' run
    assert events.tool_fields("PowerShell", {"command": "Get-ChildItem"})["command"] == "Get-ChildItem"
    assert events.tool_fields("Shell", {"command": "git log"})["command"] == "git log"   # Cursor's, by name case aside
    rows = events.finish([events.row(role="tool_use", turn_type="tool_use", tool_name="Shell", tool_call_id="1",
                                     command="git log", kind="tool_call", actor="agent", typed_by_developer=False)],
                         {"session_key": "s"})
    assert (rows[0]["category"], rows[0]["bash_category"]) == ("Action", "git")


def test_categories_case_aside_but_swechats_own_names_as_it_files_them():
    for name, want in (("Execute", "Action"), ("execute", "Other"), ("create", "Action"), ("Shell", "Action"),
                       ("copilot_readFile", "Research"), ("read_file_v2", "Research"), ("StrReplace", "Action"),
                       ("spawn_agent", "Other"), ("subagent", "Other"), ("runSubagent", "Orchestration"),
                       ("PowerShell", "Other"), ("exec", "Other"), ("FetchURL", "Research"), ("Agent", "Other"),
                       ("MultiEdit", "Other"), ("multiedit", "Action")):
        assert events.category(name) == want, name
    assert events.bash_tool("Shell") and events.bash_tool("RUN_TERMINAL_CMD") and not events.bash_tool("execute")


def test_a_shell_wrapper_is_read_in_linear_time():
    code = r"""
import time
from collect import events
t = time.perf_counter()
events.bash_category("bash -" + "c" * 40000)
print(round(time.perf_counter() - t, 3))
"""
    got = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=ROOT,
                         env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert got.returncode == 0 and float(got.stdout) < 1, (got.stdout, got.stderr[-500:])


def test_a_rollout_whose_first_line_is_long_is_still_known_by_its_format():
    """The rebuilt version's review: 208 Codex rollouts recorded as Claude Code's still had no rows, as their first
    line (the session's instructions) ran past the 4 KB the sniff read."""
    lines = [{"timestamp": "2026-10-01T00:00:00Z", "type": "session_meta",
              "payload": {"id": "s1", "cwd": "/w", "instructions": "You are Codex. " * 2000}},
             {"timestamp": "2026-10-01T00:00:01Z", "type": "event_msg", "payload": {"type": "user_message", "message": "hi"}},
             {"timestamp": "2026-10-01T00:00:01Z", "type": "response_item",
              "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}}]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "full.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        assert len(path.read_text().split("\n")[0]) > 4096
        assert events.sniff([("full.jsonl", path)]) == "codex"
        session = {"session_key": "entire:s1", "agent": "Claude Code"}
        rows, _ = events.for_session("Claude Code", [("full.jsonl", path)], session)
    assert session["agent"] == "Codex" and [r["content"] for r in rows if r["role"] == "user"] == ["hi"]


def test_shell_input_is_the_developers_action_not_typed_text():
    assert events.tell("<bash-input>ls -la</bash-input>")[1:4] == (False, "developer", "shell")


def _jsonl(d, lines, name="full.jsonl"):
    path = Path(d) / name
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    return [(name, path)]


def test_a_transcripts_format_names_its_agent_only_where_the_recorded_reader_read_nothing():
    """The rebuilt version's review, checked over every Entire session: 162 sessions recorded as Claude Code's were
    Cursor's, Codex's, Copilot CLI's or Pi's transcripts; and 254 Factory Droid sessions look like Pi's to the
    sniff, so the recorded agent's reader comes first."""
    cursor = [{"role": "user", "message": {"content": [{"type": "text", "text": "<user_query>add a flag</user_query>"}]}},
              {"role": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}]
    copilot = [{"type": "session.start", "data": {"sessionId": "s"}, "id": "1", "parentId": None, "timestamp": "2026-10-01T00:00:00Z"},
               {"type": "user.message", "data": {"content": "fix it"}, "id": "2", "parentId": "1", "timestamp": "2026-10-01T00:00:01Z"}]
    droid = [{"type": "message", "id": "3", "parentId": None, "message": {"role": "user", "content": [{"type": "text", "text": "build it"}]}},
             {"type": "message", "id": "4", "parentId": "3", "message": {"role": "assistant", "content": [{"type": "text", "text": "done"}]}}]
    cc_lines = [{"type": "user", "uuid": "u1", "sessionId": "s", "message": {"role": "user", "content": "hi"}}]
    with tempfile.TemporaryDirectory() as d:
        assert events.sniff(_jsonl(d, cursor)) == "cursor" and events.sniff(_jsonl(d, copilot)) == "copilot cli"
        assert events.sniff(_jsonl(d, droid)) == "pi"   # a Droid file looks like Pi's to the sniff
        for recorded, lines, want in (("Claude Code", cursor, "Cursor"), ("Claude Code", copilot, "Copilot CLI"),
                                      ("Factory AI Droid", droid, "Factory AI Droid"), (None, cc_lines, "Claude Code"),
                                      ("Agent", cc_lines, "Agent")):
            session = {"session_key": "s", "agent": recorded}
            rows, _ = events.for_session(recorded, _jsonl(d, lines), session)
            assert rows and session["agent"] == want, (recorded, want, session["agent"])


def test_a_claude_code_file_that_is_one_subagents_run_is_the_sessions_own_thread():
    """56 Entire sessions were a subagent's run alone, every entry a sidechain's: read as the session's thread, its
    prompts its parent agent's."""
    lines = [{"type": "user", "uuid": "u1", "sessionId": "s", "isSidechain": True, "agentId": "a1",
              "message": {"role": "user", "content": "Search the repo for the flag"}},
             {"type": "assistant", "uuid": "a1m", "sessionId": "s", "isSidechain": True, "agentId": "a1",
              "message": {"role": "assistant", "content": [{"type": "text", "text": "Found it."}]}}]
    with tempfile.TemporaryDirectory() as d:
        rows, subs = events.claude_code(_jsonl(d, lines), {"session_key": "s"})
    assert not subs and [(r["role"], r["actor"], r["typed_by_developer"]) for r in rows] == [
        ("user", "agent", False), ("assistant", "agent", False)]


def test_a_python_dict_input_is_read_quietly_and_never_when_huge():
    """#17's review, 2026-10-05: literal_eval warned on every backslash escape (dozens a run), burying real warnings."""
    import warnings

    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        assert events._args("{'path': 'C:\\<dir>'}") == {"path": "C:\\<dir>"}   # `\\<`: an escape Python warns of
    assert not seen
    huge = "{'content': '" + "x" * (1 << 20) + "'}"
    assert events._args(huge) is huge


def test_an_empty_wrapper_leaves_nothing_and_a_request_is_its_content():
    """#17's review of rc22: an empty query or request left the raw wrapper, which tell() took as typed."""
    for text in ("<timestamp>Mon</timestamp>\n<user_query></user_query>", "<image_files>a.png</image_files>\n<user_query>\n</user_query>",
                 "<USER_REQUEST></USER_REQUEST>\n<ADDITIONAL_METADATA>x</ADDITIONAL_METADATA>"):
        assert events.unwrap(text) == "" and events.tell(text)[1] is False, text
    assert events.unwrap("<USER_REQUEST>do X</USER_REQUEST>\n<ADDITIONAL_METADATA>m</ADDITIONAL_METADATA>\n<USER_RULES>r</USER_RULES>") == "do X"
    assert events.unwrap("<USER_REQUEST>look</USER_REQUEST>\n[Image: image/png]") == "look\n[Image: image/png]"
    assert events.unwrap("<USER_REQUEST>do X") == "do X" and events.unwrap("<user_query>\nfix it") == "fix it"
    assert events.unwrap("<timestamp>Mon</timestamp>\n\nfix the bug") == "fix the bug"
    assert events.tell("<attached_files>a.py</attached_files>\nfix the bug")[:4] == ("fix the bug", True, "developer", "text")
    assert events.unwrap("see <timestamp>x</timestamp> here") == "see <timestamp>x</timestamp> here"   # mid-text: kept


def test_unwrap_and_the_markdown_trim_take_linear_time():
    """#17's review of rc23: a pass per uppercase name, and popping lines off a list's front, were quadratic."""
    import string
    import time

    from collect import formats

    def letters(i: int) -> str:   # a distinct uppercase name for each number
        name = ""
        while True:
            i, r = divmod(i, 26)
            name = string.ascii_uppercase[r] + name
            if not i:
                return name

    tags = "".join(f"<{letters(i)}>x</{letters(i)}>" for i in range(64_000))
    t0 = time.time()
    # Bounds far below a quadratic pass's (a pass per name over 1.3 MB: about a minute) and well above a loaded
    # machine's linear time (0.2 s alone; 0.7 s beside other jobs on the VM, 2026-10-05).
    assert events.unwrap("<USER_REQUEST>fix it</USER_REQUEST>" + tags) == "fix it"
    assert time.time() - t0 < 2.0
    t0 = time.time()
    assert formats._md_said([], {}, 0, "---\n" * 400_000, True) == 0
    assert time.time() - t0 < 3.0
    assert events.unwrap("<USER_REQUEST>fix it</USER_REQUEST>and the tests") == "fix it\nand the tests"
    assert events.unwrap("<USER_REQUEST>a</USER_REQUEST><X>y</X> <B>open") == "a <B>open"   # an unclosed block stays


def test_a_result_takes_the_latest_call_of_its_id():
    """OpenCode numbers its calls afresh in each message (`call_0`), so an id repeats within a session: a result's
    call is the latest before it with its id, not the session's last (about 1,000 results had no bash_category,
    2026-10-05). A result with no id keeps what its reader gave it."""
    use = lambda name, cid, command=None: events.row(role="tool_use", turn_type="tool_use", tool_name=name, tool_call_id=cid,
                                                     command=command, kind="tool_call", actor="agent")
    result = lambda name, cid, **kw: events.row(role="tool_result", turn_type="tool_result", tool_name=name, tool_call_id=cid,
                                                kind="tool_result", actor="tool", **kw)
    rows = [use("bash", "call_0", "git status"), result("bash", "call_0"), use("read", "call_0"), result(None, "call_0"),
            result("bash", "call_9"), use("bash", "call_9", "npm test"), result("bash", None, bash_category="git")]
    out = events.finish(rows, {"session_key": "s"})
    assert [(r["tool_name"], r["bash_category"]) for r in out] == [
        ("bash", "git"), ("bash", "git"), ("read", None), ("read", None),
        ("bash", "package manager"),   # a result written before its call: any call of its id
        ("bash", "package manager"), ("bash", "git")]


def test_a_negative_token_count_records_no_count():
    from collect import formats

    assert formats._ints(input_tokens=97948, output_tokens=-64, cache_read_input_tokens=0) == {
        "input_tokens": 97948, "cache_read_input_tokens": 0}


def test_the_subagent_table_leaves_out_the_columns_a_subagent_never_fills(tmp_path):
    """queue_op_subtype and copy_of were empty in every subagent row (the audit, 2026-10-05): a subagent queues no
    messages. Its rows still carry the keys; the table leaves them out."""
    import pyarrow.parquet as pq

    from collect import conv_build

    assert not set(conv_build.SUB_LEFT_OUT) & set(conv_build.SUB_SCHEMA.names)
    assert set(conv_build.SUB_LEFT_OUT) <= set(conv_build.SCHEMA.names)
    w = conv_build._Writer(tmp_path / "s.parquet", conv_build.SUB_SCHEMA)
    w.add([{**events.row(role="user", turn_type="user_prompt", content="go", queue_op_subtype=None, copy_of=None),
            "task_pk": "t", "agent_id": "a"}])
    assert pq.read_table(w.close()).to_pylist()[0]["content"] == "go"


def test_every_session_with_no_rows_says_why():
    """133 kept sessions had no rows and no reason (the rc23 review, 2026-10-05): each now records one, and the 5
    OpenCode exports recorded as Claude Code's are read by their own format."""
    user = {"info": {"id": "m1", "sessionID": "s", "role": "user", "time": {"created": 1770000000000}},
            "parts": [{"type": "text", "text": "rename it"}]}
    with tempfile.TemporaryDirectory() as d:
        def files(name, text):
            path = Path(d) / name
            path.write_text(text)
            return [(name, path)]

        doc = files("full.jsonl", json.dumps({"info": {"id": "s", "version": "1.18.0"}, "messages": [user]}, indent=2))
        assert events.sniff(doc) == "opencode"
        session = {"session_key": "entire:s", "agent": "Claude Code"}
        rows, _ = events.for_session("Claude Code", doc, session)
        assert session["agent"] == "OpenCode" and [r["content"] for r in rows] == ["rename it"]
        meta = files("full.jsonl", "\n".join(json.dumps(e) for e in (   # entry types the format check did not know
            {"type": "pr-link", "prNumber": 1, "prUrl": "u", "sessionId": "s", "timestamp": "2026-10-01T00:00:00Z"},
            {"type": "agent-name", "agentName": "x", "sessionId": "s"})) + "\n")
        assert events.sniff(meta) == "claude code" and events.no_rows_reason(meta) == "the transcript holds no messages"
        acp = files("full.jsonl", json.dumps({"method": "session/update", "params": {"sessionId": "s"}, "timestamp": 1}) + "\n")
        assert events.no_rows_reason(acp) == "no reader for this file's format"
        assert events.no_rows_reason(files("a.md", "<!-- Generated by SpecStory, Markdown v2.1.0 -->\n\n")) == "a SpecStory file with no messages"
        assert events.no_rows_reason(files("b.md", "# My notes\n\nNothing here.\n")) == "a markdown file with no chat in it"
        assert events.no_rows_reason(files("c.md", "  \n")) == "the session's files are empty"
        assert events.no_rows_reason([("full.jsonl", Path(d) / "gone.jsonl")]) == "the session's files are missing"


def test_the_conversations_step_records_why_a_session_has_no_rows():
    from collect import conv_build

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "f.md"
        path.write_text("<!-- Generated by SpecStory, Markdown v2.1.0 -->\n\n")
        out = conv_build.extract_job(("specstory", [({"source_id": "o/r:f.md", "repo": "o/r", "agent": None, "extra": {}},
                                                     [(".specstory/history/f.md", path)])], d, "part-000"))
    assert out["per"]["o/r:f.md"]["conversations_error"] == "a SpecStory file with no messages"
    assert out["per"]["o/r:f.md"]["n_conversation_rows"] == 0


def test_why_no_rows_reads_a_files_head_and_names_a_copied_record(monkeypatch):
    """#17's review of cd0979d: a copied dataset's export can be gigabytes (DataClaw's: 3.3 GB), read whole for each
    of its records with no messages; and its format says nothing of one record."""
    import time

    from collect import conv_build, formats

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "export.jsonl"
        with open(path, "w") as fh:
            fh.write(" " * (20 << 20) + "\n")   # 20 MB of nothing in its head: not called empty, not read whole
        assert len(events._head(path)) == events.HEAD_BYTES   # a head, never the whole export
        t0 = time.time()
        assert events.no_rows_reason([("export.jsonl", path)]) == "no reader for this file's format"
        assert time.time() - t0 < 0.5
        monkeypatch.setitem(formats.READERS, "dataclaw", lambda files, session: ([], []))
        out = conv_build.extract_job(("dataclaw", [({"source_id": "o/r:1", "repo": None, "agent": "Codex", "extra": {},
                                                      "_reader": "dataclaw"}, [("export.jsonl", path)])], d, "part-000"))
    assert out["per"]["o/r:1"]["conversations_error"] == "the record holds no messages"


def test_the_card_counts_where_swechats_licence_reading_differs_from_githubs(tmp_path):
    """Counted from the version's own rows at each build (a number written into the text would go stale on the next
    update): SWE-chat's license_type against GitHub's identification, kept sessions only."""
    import json

    import pyarrow as pa
    import pyarrow.parquet as pq

    from collect import build, card

    v = tmp_path / "2026-10-05"
    (v / "data" / "swe-chat-v2").mkdir(parents=True)
    rows = []
    for sid, ours, theirs, dup in (("a", "MIT", "MIT", None), ("b", "unrecognized", "MPL-2.0", None),
                                   ("c", "none", None, None), ("d", "none", "MIT", "swe-chat-v2:b")):
        row = {f.name: None for f in build.SCHEMA}
        row.update(source="swe-chat-v2", source_id=sid, session_key=f"swe-chat-v2:{sid}", models=[], also_in=[], files=[],
                   repo="o/r", repo_license=ours, duplicate_of=dup, bytes=1, redactions={"secrets": 0, "emails": 0, "home_users": 0},
                   source_facts=json.dumps({"swechat_license_type": theirs}))
        rows.append(row)
    pq.write_table(pa.Table.from_pylist(rows, schema=build.SCHEMA), str(v / "data" / "swe-chat-v2" / "part-000-00000.parquet"))
    changelog = {"version": v.name, "built_at": "2026-10-05T00:00:00Z", "scraped": {}, "copied": {}, "conversations": {},
                 "scan": {"rules": "x"}, "duplicate_pairs": {}, "gaps": [], "licences_looked_up": True,
                 "failed_sources": [], "redaction": "most"}
    card.write(v, changelog, log=lambda m: None)
    assert "it differs from GitHub's for 1 of SWE-chat v2's 3 kept sessions" in (v / "README.md").read_text()


def test_a_tools_blocks_in_a_typed_message_are_not_the_developers():
    """#17's audit of version 2026-10-04's typed rows, then ours over all of them (2026-10-05): about 1,700 held a
    block a tool, an IDE or a plugin puts into a message, 330 nothing else; some messages are an event, not typing."""
    for text in ("<system_instruction>Be brief.</system_instruction>", "<ci-monitor-event>build failed</ci-monitor-event>",
                 "<bash-notification>done</bash-notification>", "<heartbeat>tick</heartbeat>",
                 "<system-notification>x</system-notification>", "<in-app-browser-context>page</in-app-browser-context>",
                 "<blank>", "<blank>\n<blank>"):
        assert events.tell(text)[1] is False, text
    assert events.tell("<system_instruction>Be brief.</system_instruction>\nfix the test")[:4] == (
        "fix the test", True, "developer", "text")
    assert events.unwrap("<git_diff_from_branch_to_main>d</git_diff_from_branch_to_main>\nreview it") == "review it"
    assert events.unwrap('<plugin_info kind="mcp">x</plugin_info>\n<user_query>go</user_query>') == "go"   # attributes
    assert events.unwrap("<system_instruction_x>keep</system_instruction_x> go") == "<system_instruction_x>keep</system_instruction_x> go"
    for text, who in (('<scheduled-task name="daily" file="/x">run the report</scheduled-task>', "harness"),
                      ('<wake reason="timer" current-time="t">check</wake>', "harness"),
                      ("<create-pr-command>make a PR</create-pr-command>", "harness"),
                      ("<codex_delegation><input>do it</input></codex_delegation>", "agent"),
                      ('<message from="a" to="b">hi</message>', "peer")):
        assert events.tell(text)[1:3] == (False, who), text
    reply = ('<send_user_message_question_reply>\n[{"questionItemId": "q1", "question": "Which one?", "answer": "The second"}, '
             '{"questionItemId": "q2", "question": "Tests?", "answer": "Yes"}]\n</send_user_message_question_reply>\nand go')
    assert events.tell(reply)[:4] == ("The second\n\nYes\n\nand go", True, "developer", "text")   # the answers only
    assert events.tell('<pasted_content id="1">my log</pasted_content>')[1] is True   # the developer's own paste
    assert events.tell("<div>my html</div> why is this broken?")[1] is True
    nested = ("<timestamp>Mon</timestamp>\n<user_query>\n<timestamp>Mon</timestamp>\n<user_query>fix it</user_query>\n"
              "</user_query>")
    assert events.unwrap(nested) == "fix it"


def test_cursor_judges_the_innermost_query():
    """Cursor quotes a timestamped query inside another; its rule judged the outer one and called Cursor's own
    instruction typed (5 rows, 2026-10-05)."""
    from collect.facts import cursor_author

    nested = ("<timestamp>Mon</timestamp>\n<user_query>\n<timestamp>Mon</timestamp>\n<user_query>Briefly inform the user "
              "about the task result</user_query>\n</user_query>")
    assert cursor_author(nested) == (False, "harness", "notice")
    assert cursor_author("<user_query>\n<timestamp>t</timestamp>\nfix it</user_query>") == (True, "developer", "text")


def test_a_message_with_no_tools_block_keeps_its_text_as_written():
    """Stripping every typed text that holds a `<` changed the spacing of 6,500 messages with no block in them
    (measured over version 2026-10-04 before release, 2026-10-05)."""
    for text in ("  if a < b:\n    swap()\n", "see <div>here</div>\n\n", "x<y "):
        assert events.unwrap(text) == text


def test_a_longer_tag_sharing_a_blocks_name_is_not_that_block():
    text = "<system_instruction_x>mine</system_instruction_x> <system_instruction>b</system_instruction> go"
    assert events.unwrap(text) == "<system_instruction_x>mine</system_instruction_x>  go"


def test_cursor_judges_a_query_past_its_own_timestamp():
    from collect.facts import cursor_author

    assert cursor_author("<user_query><timestamp>t</timestamp>Briefly inform the user about the task result</user_query>") == (
        False, "harness", "notice")


def test_a_copied_dataset_gets_a_viewer_config_only_where_its_files_are_one_kind():
    """A dataset holding a manifest, normalized sessions and raw transcripts read as one table fails the viewer
    (dharshanrai/optim-agent-corpus-harvest, 2026-10-05)."""
    from collect import hfcopy_build

    one = {"files": [{"path": "a/conversations.jsonl"}, {"path": "b/conversations.jsonl"}, {"path": ".gitattributes"}]}
    mixed = {"files": [{"path": "MANIFEST.jsonl"}, {"path": "normalized/x/conversations.jsonl"}, {"path": "raw/t/1.jsonl"}]}
    assert hfcopy_build._configs_for("dataclaw", "o__d", "dataclaw/o__d", one) == [
        ("dataclaw.o__d", ["copied/dataclaw/o__d/a/conversations.jsonl", "copied/dataclaw/o__d/b/conversations.jsonl"])]
    assert hfcopy_build._configs_for("dataclaw", "o__m", "dataclaw/o__m", mixed) == []


def test_the_card_says_which_labels_are_rules_and_which_a_model_wrote_and_names_an_unknown_agent(tmp_path, monkeypatch):
    import pyarrow as pa
    import pyarrow.parquet as pq

    from collect import build, card

    v = tmp_path / "2026-10-05"
    (v / "data" / "specstory").mkdir(parents=True)
    row = {f.name: None for f in build.SCHEMA}
    row.update(source="specstory", source_id="o/r:f.md", session_key="specstory:o/r:f.md", agent=None, models=[],
               also_in=[], files=[], repo="o/r", bytes=1, redactions={"secrets": 0, "emails": 0, "home_users": 0})
    pq.write_table(pa.Table.from_pylist([row], schema=build.SCHEMA), str(v / "data" / "specstory" / "part-000-00000.parquet"))
    card.write(v, {"version": v.name, "built_at": "2026-10-05T00:00:00Z", "scraped": {}, "copied": {}, "conversations": {},
                   "scan": {"rules": "x"}, "duplicate_pairs": {}, "gaps": [], "licences_looked_up": True,
                   "failed_sources": [], "redaction": "most"}, log=lambda m: None)
    text = (v / "README.md").read_text()
    assert "labels that state facts only" not in text and "No label here was written by a model" in text
    monkeypatch.setenv("ERRATA_CARD_HF", "1")   # a Hugging Face upload asks for the front matter
    card.write(v, {"version": v.name, "built_at": "2026-10-05T00:00:00Z", "scraped": {}, "copied": {}, "conversations": {},
                   "scan": {"rules": "x"}, "duplicate_pairs": {}, "gaps": [], "licences_looked_up": True,
                   "failed_sources": [], "redaction": "most"}, log=lambda m: None)
    hf_text = (v / "README.md").read_text()
    assert hf_text.startswith("---\nlicense: other")
    assert "Each version is a dated tag" in hf_text and "dataset viewer" in hf_text   # Hugging Face's own terms
    monkeypatch.delenv("ERRATA_CARD_HF")
    card.write(v, {"version": v.name, "built_at": "2026-10-05T00:00:00Z", "scraped": {}, "copied": {}, "conversations": {},
                   "scan": {"rules": "x"}, "duplicate_pairs": {}, "gaps": [], "licences_looked_up": True,
                   "failed_sources": [], "redaction": "most"}, log=lambda m: None)
    text = (v / "README.md").read_text()
    assert text.startswith("# PairLogs") and "config_name" not in text
    assert "Each version is a dated folder, with its checksum list beside it" in text and "dataset viewer" not in text
    assert "Pushback moments" not in text and "## Labels written by a model" in text   # no pushback column: no recipe
    assert "SWE-chat's own prompt labels, which a model wrote, are carried over" in text
    assert "| unknown 1 |" in text and "None 1" not in text
    assert "listed and downloadable but not in the viewer" in hf_text and "not in the viewer" not in text   # its viewer only


def test_the_card_names_each_subsets_largest_repositories_and_counts_aiders_benchmark_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("ERRATA_CLUSTER", json.dumps({"name": "Example University's cluster", "host": "login.example.edu", "root": "/srv/scratch/owner1/errata-data", "globus": "Example Scratch", "globus_root": "/owner1/errata-data", "owner": "owner1", "account": "Example account"}))
    """5,356 of Aider's 5,817 sessions came from one repository of its benchmark's runs, and the card did not say so
    (2026-10-05)."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from collect import build, card

    v = tmp_path / "2026-10-05"
    (v / "data" / "aider").mkdir(parents=True)
    (v / "conversations" / "aider").mkdir(parents=True)
    rows = []
    for i, repo in enumerate(["o/bench", "o/bench", "o/r"]):
        row = {f.name: None for f in build.SCHEMA}
        row.update(source="aider", source_id=f"{repo}:{i}.md", session_key=f"aider:{repo}:{i}.md", agent="Aider", models=[],
                   also_in=[], files=[], repo=repo, bytes=1, redactions={"secrets": 0, "emails": 0, "home_users": 0})
        rows.append(row)
    pq.write_table(pa.Table.from_pylist(rows, schema=build.SCHEMA), str(v / "data" / "aider" / "part-000-00000.parquet"))
    task = ("# Introduction\n####\n\nUse the above instructions to modify the supplied files: a.py\n"
            "Don't change the names of existing functions or classes, as they may be referenced from other code.")
    conv = [("aider:o/bench:0.md", 0, "user_prompt", "harness", task), ("aider:o/bench:0.md", 1, "system_event", "harness", "Applied edit"),
            ("aider:o/bench:0.md", 2, "assistant_response", "agent", "```\nok\n```"),   # test output a notice follows
            ("aider:o/bench:0.md", 3, "system_event", "harness", "Finished summarizing chat history."),
            ("aider:o/bench:1.md", 0, "user_prompt", "developer", task),
            ("aider:o/r:2.md", 0, "user_prompt", "harness", "<blank>"), ("aider:o/r:2.md", 1, "system_event", "harness", "x"),
            ("aider:o/r:2.md", 2, "assistant_response", "agent", "```\nprint(1)\n```")]   # not a benchmark session
    conv.append(("aider:o/r:2.md", 3, "user_prompt", "developer", "[REDACTED]"))   # a message that was a secret
    pq.write_table(pa.Table.from_pylist([{**dict(zip(("session_key", "turn_number", "turn_type", "actor", "content"), c)),
                                          "source": "aider", "typed_by_developer": c[3] == "developer",
                                          "char_count": len(c[4]),
                                          "pushback": (True if c[0] == "aider:o/bench:1.md" else None) if c[3] == "developer"
                                          else None} for c in conv]),
                   str(v / "conversations" / "aider" / "part-000.parquet"))
    copied = {"swe-chat-v2": {"copied": True, "rows": 1, "configs": [], "note": "Copied.",
                              "license_line": "odc-by, copied from https://huggingface.co/datasets/s/c (revision 0123456789ab), by s",
                              "redactions": {"secrets": {"masked": 7}, "emails": 0, "home_users": 0}},
              "dataclaw/a__b": {"copied": False, "reason": "its data files are another's",
                                "license_line": "mit, copied from https://huggingface.co/datasets/a/b (revision 0123456789ab), by a",
                                "configs": [], "note": "Linked.", "redactions": {"secrets": {}, "emails": 0, "home_users": 0}}}
    card.write(v, {"version": v.name, "built_at": "2026-10-05T00:00:00Z", "scraped": {}, "copied": copied,
                   "conversations": {}, "scan": {"rules": "x"}, "duplicate_pairs": {}, "gaps": [],
                   "licences_looked_up": True, "failed_sources": [], "redaction": "most", "key_runs_masked": 61,
                   "check_found_masked": {"strings": 21, "distinct": 15, "personal": 4, "string_files": 9}},
               log=lambda m: None)
    text = (v / "README.md").read_text()
    assert "(61 in the sessions' files of this version;" in text and "redacted whole): aider 1." in text
    assert "a key broken over lines can keep its tail after the break" in text
    assert "`pushback` is `true` when a typed message pushes back" in text
    assert "Typed rows: 1 pushback, 0 not, 1 unread. Every other row is empty." in text
    for unread, line in (({"content_filter": 1}, "1 unread (1 refused by Azure's content filter)."),
                         ({"content_filter": 1, "high_only": True},
                          "1 unread (1 refused by Azure's content filter, even set to block only high-severity content)."),
                         ({"cyber_policy": 1}, "1 unread (1 refused under the service's cybersecurity policy, asked "
                                               "three more times)."),
                         ({"content_filter": 2}, "1 unread. Every other row is empty.")):   # reasons only where they add up
        card.write(v, {"version": v.name, "built_at": "2026-10-05T00:00:00Z", "scraped": {}, "copied": copied,
                       "conversations": {}, "scan": {"rules": "x"}, "duplicate_pairs": {}, "gaps": [],
                       "licences_looked_up": True, "failed_sources": [], "redaction": "most",
                       "pushback_labels": {"model": "gpt-6-luna", "date": "2026-10-05",
                                           "rows_unread": {k: n for k, n in unread.items() if k != "high_only"},
                                           **({"content_filter": {"high_only": True}} if unread.get("high_only") else {}),
                                           "cut": {"over": 6000, "head": 4500, "tail": 1500, "distinct": 3, "rows": 4},
                                           "copies": {"rows": 9, "text_differs": 2, "label_differs": 1}}},
                   log=lambda m: None)
        readme = (v / "README.md").read_text()
        assert "Each message was read on its own by gpt-6-luna, with no session context and no reasoning (2026-10-05)" in readme
        assert line in readme
        assert "Two come from a model instead: `pushback`, a model's reading of each typed message" in readme
        assert "No label here was written by a model" not in readme and "## Quick start" in readme
        assert "## Where it is" in readme and "/srv/scratch/owner1/errata-data/" in readme and "hf download" not in readme
        where = readme.split("## Where it is", 1)[1].split("\n## ", 1)[0]
        assert "the collection \"Example Scratch\", path `/owner1/errata-data/" in where   # its paths start at the root
        assert "rsync -a --info=progress2 <netid>@login.example.edu:/srv/scratch/owner1/errata-data/" in where
        assert scan.redact(where, [], pii=True)[0] == where   # the build masks the card: nothing here may read as an email
        assert "columns=['session_key', 'agent', 'repo', 'started_at', 'n_user_messages'])" in readme   # not 11 GB of files
        assert "relative to the folder that holds `" in readme and "(`/srv/scratch/owner1/errata-data` on the cluster)" in readme
        assert "| `source` | The source the session came from" in readme and "`strategy`, `source` |" not in readme
        assert "ask the maintainer (the folder's owner, `owner1`), naming it" in readme
        assert "on its first text row, else its first tool call." in readme and "on the call's first row" not in readme
        assert ("(a subagent queues no messages, and nobody types to one). Each row carries its parent session's ids, "
                "and `turn_number` counts within the subagent's own rows, so `turn_id` repeats the parent's values: "
                "`session_key` and `turn_pk` identify a row.") in readme
        assert "See Conversations" not in readme and "- Their text was scanned" not in readme
        assert "Copied datasets keep every session (only their secrets are masked)" in readme
        assert "on its sessions' rows.\n- `prompt_intent` and `prompt_pushback`" in readme   # one list, not two
        assert "empty where the session's files were not read" in readme and "0 where they were read and gave no rows" in readme
        assert "load_dataset" not in readme and "config_name" not in readme   # no Hugging Face front matter by default
        assert "| Pushback moments | `pushback = true`" in readme and "## Labels written by a model" in readme
        assert "- A text over 6,000 characters was sent as its first 4,500 and last 1,500 characters (3 texts, 4 rows)." in readme
        assert ("Of 9 typed rows that copy an earlier typed message of their session (`copy_of`), 2 differ from it in "
                "text and were read on their own; 1 of those got a different label.") in readme
        assert "less `queue_op_subtype`, `copy_of` and `pushback` (a subagent queues no messages, and nobody" in readme
    assert "After a full check of the published text, 21 more spans of 15 strings it found, and 4 personal" in text
    assert "with a single message, queued or not, is a program's call: who wrote it is unknown" in text
    assert "| swe-chat-v2 (copied files) | 7 | 0 | 0 |" in text and "which are masked for secrets only" in text
    assert "| swe-chat-v2 (copied files) | copied: 1 row in its data files |" in text   # apart from the sessions' row
    assert "| dataclaw/a__b (linked) | linked, not copied" in text and "| dataclaw/a__b (copied files) |" not in text
    assert "- **dataclaw/a__b**: mit, linked, not copied: https://huggingface.co/datasets/a/b (revision" in text
    assert "- **swe-chat-v2**: odc-by, copied from https://huggingface.co/datasets/s/c (revision" in text
    assert "issue #" not in text   # the code's repository is private
    assert "| aider | o/bench 2 (66.7%); o/r 1 (33.3%) |" in text
    assert "1 of Aider's 3 sessions are runs of Aider's benchmark script" in text and "their 1 prompts are the script's" in text
    assert "1 blocks of its test output that a notice follows" in text and "duplicates included" in text
    assert "SWE-chat redacted personal information in prompts, replies and subagents' text" in text
    assert "an orchestrator's templates (megaplan's" in text and "OpenClaw's metadata blocks" in text
    assert "no `tool_result` row follows" in text and "a failed edit's (`# 1 SEARCH/REPLACE block failed to match!`)" in text


def test_tool_written_messages_in_formats_with_no_authors_mark_are_not_typed():
    """#17's evaluation of version 2026-10-04 (2026-10-05): DataClaw's and SpecStory's typed rows held Claude Code's
    own requests, a hook's feedback, a skill's body, a scheduler's firings and an orchestrator's templates."""
    import time

    harness = [("You are a code reviewer. Your job is to review code changes and provide actionable feedback.\n\n---", "notice"),
               ("# Session Logs (/sl) - Session Continuity Command\n\nLoad and summarize", "notice"),
               ("## Context\n\n- Current git status: On branch main\nYour branch is up to date", "notice"),
               ('You are reviewing the changes on branch "a" against "main". There are most likely issues in the '
                'current implementation. Raise comments', "notice"),
               ("## Skill Invocation\n\nYou MUST run the `/ship` skill.\n\n# Global Agent Instructions\n\nYou are an "
                "autonomous coding agent running in a headless orchestration session.", "notice"),
               ("[SUGGESTION MODE: Suggest what the user might naturally type next into Claude Code.]\n\nFIRST: look", "notice"),
               ("Your task is to create a detailed summary of the conversation so far, paying close attention", "compaction"),
               ("Stop hook feedback:\n\nAn active /goal is still running.", "notice"),
               ("# /loop — schedule a recurring or self-paced prompt\n\nParse the input below", "notice"),
               ("[cron:e81bf234-b277-4890-8869-8fbb5aa17912 Trading Bot Loop] Run the analysis", "notice"),
               ("Warmup", "notice"),
               ("Execute the approved plan in the repository.\n\n        Project directory:\n        /w\n", "notice"),
               ("You are already running inside the megaplan harness for this step.", "notice"),
               ("You are an independent reviewer. Critique the plan against the code.\n\nmegaplan state: x", "notice")]
    for text, kind in harness:
        assert events.tell(text)[1:4] == (False, "harness", kind), text[:40]
    for text in ("[cron: every day] check the build", "Warmup the cache first, then run it",
                 "You are creating an implementation plan for the following idea.\n\nA todo app",   # neither mark
                 "Review the implementation against the success criteria.", "Continue from where you left off.",
                 'You are reviewing the changes on branch "a" against "main". Find the bug.',   # no second sentence
                 "## Context\nI changed the parser"):
        assert events.tell(text)[1:4] == (True, "developer", "text"), text[:40]
    steer = ("User steering update:\n<user_input>\nYou may compile more configs\n</user_input>\n"
             "Internal instruction: Re-evaluate the active plan using this user steering update.")
    assert events.tell(steer) == ("You may compile more configs", True, "developer", "text", None)
    bridged = ('Conversation info (untrusted metadata):\n```json\n{\n  "has_reply_context": true\n}\n```\n\n'
               'Sender (untrusted metadata):\n```json\n{"label": "x"}\n```\n\n'
               'Replied message (untrusted, for context):\n```json\n{\n  "body": "fix the buttons"\n}\n```\n\nwhy not?')
    assert events.tell(bridged) == ("why not?", True, "developer", "text", None)
    ide = ("# Context from my IDE setup:\n\n## Active file: a.py\n\n## Open tabs:\n- a.py: a.py\n\n"
           "## My request for Codex:\nrename the helper")
    assert events.tell(ide) == ("rename the helper", True, "developer", "text", None)
    assert events.tell("# In app browser:\n- The user has the in-app browser open.\n\n## My request for Codex:\nfix it")[0] == "fix it"
    quoted = "the bridge wrote Sender (untrusted metadata):\n```json\n{}\n```\nbefore it"   # mid-text: kept
    assert events.tell(quoted)[0] == quoted
    t0 = time.time()   # an unclosed block and a long run of spaces fail late: linear time
    events.unwrap("Sender (untrusted metadata):\n```json\n" + "x\n" * 400_000)
    events.unwrap(" " * 400_000 + "x")
    assert time.time() - t0 < 3.0


def test_claude_codes_warmup_of_a_subagent_is_the_harnesss():
    from collect import formats  # noqa: F401  (the readers registered)

    import json
    import tempfile
    from pathlib import Path

    lines = [{"type": "user", "uuid": "u1", "sessionId": "s", "isSidechain": True, "message": {"role": "user", "content": "Warmup"}},
             {"type": "user", "uuid": "u2", "sessionId": "s", "isSidechain": True, "message": {"role": "user", "content": "check a.py"}}]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "agent-1.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        rows, subs = events.for_session("Claude Code", [("agent-1.jsonl", path)], {"session_key": "k", "agent": "Claude Code"})
    got = [(r["content"], r["actor"], r["typed_by_developer"]) for r in rows + subs if r["role"] == "user"]
    assert got == [("Warmup", "harness", False), ("check a.py", "agent", False)]


def test_standing_gaps_point_to_sections_the_card_has():
    """A gap note's "see ..." names a section of the card: "see Labels" named none (the card of 2026-10-04, 2026-10-06)."""
    from collect import build
    assert not [g for g in build.STANDING_GAPS if "see Labels" in g]
    assert any(g.endswith("see Session table columns in full, under Reference.") for g in build.STANDING_GAPS)


def test_program_texts_found_in_the_2026_10_25_audit_are_not_typed():
    cases = {
        "[SYSTEM DIRECTIVE: OH-MY-OPENCODE - TODO CONTINUATION]\n\nIncomplete tasks remain": ("harness", "notice"),
        "Tool budget soft limit reached after 17 tool calls (soft 17, hard 25).": ("harness", "notice"),
        "Continue if you have next steps, or stop and ask for clarification if you are unsure how to proceed.":
            ("harness", "notice"),
        "[System: Your previous response was truncated by the output length limit. Continue exactly": ("harness", "notice"),
        "Generate a file named AGENTS.md that serves as a contributor guide for this repository.": ("harness", "command"),
        "Create or update `AGENTS.md` for this repository.\n\nThe goal is a compact instruction file": ("harness", "command"),
        "[Function Response: read_many_files]--- ~/x/ast.h ---": ("tool", "tool_result"),
        "Thoroughness: very thorough\n\nI need to understand how the output commands work": ("agent", "notice"),
        "PLEASE IMPLEMENT THIS PLAN:\n# Debug-First Rift Creation": ("developer", "plan"),
        "Implement the following plan:\n\n# Unify MediaLightbox Layouts": ("developer", "plan"),
    }
    for text, (actor, kind) in cases.items():
        got = events.user_text_rule(text)
        assert got and got["typed"] is False and (got["actor"], got["kind"]) == (actor, kind), text


def test_what_people_type_near_those_markers_stays_typed():
    for plain in ("please implement this plan carefully, the one we discussed", "thoroughness matters here: check all",
                  "continue if you can", "Implement the plan we wrote yesterday"):
        assert events.user_text_rule(plain) is None, plain
