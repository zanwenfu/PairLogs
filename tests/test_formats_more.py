"""Readers for the formats the collection had none for until 2026-10-05: Entire's hook log (Grok CLI, Qwen Code),
Kiro, Roger Roger Agent, Antigravity, SWE-chat v2's Project Runner and Kimi Code, the hourly files of a session-logging
tool found with SpecStory's, and OpenCode's two other file shapes (#7). Every file here is made up, shaped like the
collection's (read for structure only)."""

import json
import tempfile
from pathlib import Path

from collect import conv_build, events, facts

SESSION = {"source": "entire", "session_key": "entire:o/r:s1", "session_id": "s1", "repo_id": "o/r", "agent": "x",
           "checkpoint_pk": "o/r#c1"}
ROLES = {"user", "assistant", "tool_use", "tool_result", "metadata"}
TURNS = {"user_prompt", "system_injected", "assistant_response", "assistant_thinking", "tool_use", "tool_result",
         "summary", "system_event", "file_snapshot", "progress", "queue_operation"}


def read(agent: str, lines=None, text=None, name="full.jsonl"):
    """(rows, subagent rows) for one transcript."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / name
        path.write_text(text if text is not None else "\n".join(json.dumps(x) for x in lines) + "\n")
        rows, subs = events.for_session(agent, [(name, path)], {**SESSION, "agent": agent})
    assert all(r["role"] in ROLES and r["turn_type"] in TURNS for r in rows + subs)
    assert [r["turn_number"] for r in rows] == list(range(len(rows)))
    return rows, subs


def users(rows):
    return [(r["content"], r["typed_by_developer"], r["actor"], r["kind"]) for r in rows if r["role"] == "user"]


def turns(rows):
    return [r["turn_type"] for r in rows]


def hook(event, **kw):
    return {"v": 1, "agent": "grok", "event": event, "session_id": "s", "ts": "2026-10-01T00:00:00Z", "cwd": "/w", **kw}


def test_hook_log_turns_calls_and_last_messages():
    lines = [hook("SessionStart", source="new"),
             hook("UserPromptSubmit", prompt="<user_query>\nfix the parser\n</user_query>"),
             hook("PreToolUse", tool_name="read_file", tool_use_id="t1", tool_input={"target_file": "a.py"}),
             hook("PostToolUse", tool_name="read_file", tool_use_id="t1", tool_input={"target_file": "a.py"},
                  tool_response={"type": "FileNotFound", "FileNotFound": "a.py"}),
             hook("PreToolUse", tool_name="run_terminal_command", tool_use_id="t2", tool_input={"command": "ls"}),
             hook("PostToolUse", tool_name="run_terminal_command", tool_use_id="t2", tool_input={"command": "ls"},
                  tool_response={"command": "ls", "exit_code": 0, "current_dir": "/w", "description": ""}),
             hook("PreToolUse", tool_name="grep", tool_use_id="t3", tool_input={"pattern": "x"}),   # no result came
             hook("Stop", reason="end_turn", last_assistant_message="It reads now."),
             hook("UserPromptSubmit", prompt="<system-reminder>\nTodos changed.\n</system-reminder>"),
             hook("UserPromptSubmit", prompt="/to-tickets the plan"),
             hook("Notification", notification_type="idle_prompt", message="Waiting for input"),
             hook("PreCompact", source="auto"), hook("PostCompact", source="auto"),
             hook("StopFailure", error="rate limited", error_details="", last_assistant_message="Stopped early."),
             hook("Stop", reason="shutdown", last_assistant_message="")]
    rows, subs = read("Grok CLI", lines)
    assert users(rows) == [("fix the parser", True, "developer", "text"),   # finish takes off <user_query>
                           ("<system-reminder>\nTodos changed.\n</system-reminder>", False, "harness", "notice"),
                           ("/to-tickets the plan", True, "developer", "command")]
    assert turns(rows) == ["system_event", "user_prompt", "tool_use", "tool_result", "tool_use", "tool_result", "tool_use",
                           "assistant_response", "user_prompt", "user_prompt", "system_event", "summary", "system_event",
                           "system_event", "assistant_response", "system_event"]
    results = [r for r in rows if r["turn_type"] == "tool_result"]
    assert [(r["tool_call_id"], r["tool_name"], r["is_error"]) for r in results] == [("t1", "read_file", True),
                                                                                    ("t2", "run_terminal_command", False)]
    assert next(r for r in rows if r["tool_call_id"] == "t2")["command"] == "ls"   # Grok CLI's shell tool
    assert next(r for r in rows if r["tool_call_id"] == "t1")["file_path"] == "a.py"   # its `target_file`
    assert [r["content"] for r in rows if r["role"] == "assistant"] == ["It reads now.", "Stopped early."]
    assert next(r for r in rows if r["content"] == "rate limited")["is_error"] is True
    assert next(r for r in rows if r["kind"] == "command")["command"] == "/to-tickets the plan"
    assert subs == [] and events.count_typed("Grok CLI", rows) == 2


def test_hook_log_a_subagents_events_are_a_thread_per_type():
    lines = [hook("UserPromptSubmit", prompt="review the change"),
             hook("PreToolUse", tool_name="spawn_subagent", tool_use_id="s1", tool_input={"agent": "general-purpose"}),
             hook("SubagentStart", agent_type="general-purpose"),
             hook("UserPromptSubmit", agent_type="general-purpose", prompt="Read a.py and report."),
             hook("PreToolUse", agent_type="general-purpose", tool_name="read_file", tool_use_id="u1",
                  tool_input={"target_file": "a.py"}),
             hook("PostToolUse", agent_type="general-purpose", tool_name="read_file", tool_use_id="u1",
                  tool_input={"target_file": "a.py"}, tool_response={"type": "FileContent", "FileContent": "x = 1"}),
             hook("SubagentStart", agent_type="explore"),
             hook("PostToolUse", tool_name="spawn_subagent", tool_use_id="s1", tool_input={}, tool_response={"type": "text", "text": "ok"}),
             hook("Stop", reason="end_turn", last_assistant_message="Reviewed.")]
    rows, subs = read("Grok CLI", lines)
    assert users(rows) == [("review the change", True, "developer", "text")]
    # A subagent's start is the parent's notice; the subagent's own events inside the parent's log are its thread.
    assert turns(rows) == ["user_prompt", "tool_use", "system_event", "system_event", "tool_result", "assistant_response"]
    assert [(r["task_pk"], r["agent_id"], r["turn_type"]) for r in subs] == [
        ("o/r#c1#general-purpose", "general-purpose", "user_prompt"),
        ("o/r#c1#general-purpose", "general-purpose", "tool_use"),
        ("o/r#c1#general-purpose", "general-purpose", "tool_result")]
    assert users(subs) == [("Read a.py and report.", False, "agent", "notice")]   # its parent agent's
    assert [r["turn_pk"] for r in subs][:2] == ["o/r#c1#general-purpose#0", "o/r#c1#general-purpose#1"]
    assert events.count_typed("Grok CLI", rows) == 1


def test_hook_log_a_subagents_own_session_is_read_as_its_own():
    """Entire records a subagent's run as a session of its own, every event carrying `agent_type` (13 of the 36 Grok
    CLI sessions in the collection): its rows are the session's, and its prompt is its parent agent's."""
    lines = [hook("UserPromptSubmit", agent_type="general-purpose", prompt="Read a.py and report."),
             hook("PreToolUse", agent_type="general-purpose", tool_name="read_file", tool_use_id="u1",
                  tool_input={"target_file": "a.py"}),
             hook("PostToolUse", agent_type="general-purpose", tool_name="read_file", tool_use_id="u1",
                  tool_input={"target_file": "a.py"}, tool_response={"type": "FileContent", "FileContent": "x = 1"}),
             hook("PreToolUse", agent_type="general-purpose", tool_name="grep", tool_use_id="u2", tool_input={"pattern": "x"})]
    rows, subs = read("Grok CLI", lines)
    assert subs == [] and turns(rows) == ["user_prompt", "tool_use", "tool_result", "tool_use"]
    assert users(rows) == [("Read a.py and report.", False, "agent", "notice")]
    assert events.count_typed("Grok CLI", rows) == 0


def test_qwen_code_hook_log_and_an_empty_prompt():
    lines = [hook("SessionStart", agent="qwen", source="startup", model="qwen3-coder"),
             hook("UserPromptSubmit", agent="qwen", prompt=""),
             hook("UserPromptSubmit", agent="qwen", prompt="write hello.py"),
             hook("PreToolUse", agent="qwen", tool_name="write_file", tool_use_id="w", tool_input={"file_path": "hello.py", "content": "print(1)"}),
             hook("PostToolUse", agent="qwen", tool_name="write_file", tool_use_id="w", tool_input={}, tool_response="Wrote hello.py"),
             hook("Stop", agent="qwen", last_assistant_message="Done.")]
    rows, _ = read("Qwen Code", lines)
    assert users(rows) == [("write hello.py", True, "developer", "text")]   # an empty prompt adds no row
    call = next(r for r in rows if r["turn_type"] == "tool_use")
    assert (call["file_path"], call["model"]) == ("hello.py", "qwen3-coder")
    assert next(r for r in rows if r["turn_type"] == "tool_result")["content"] == "Wrote hello.py"


def test_kiro_prompts_responses_and_calls():
    doc = {"conversation_id": "c", "history": [
        {"user": {"content": {"Prompt": {"prompt": "list the files"}}},
         "assistant": {"ToolUse": {"message_id": "m1", "tool_uses": [{"id": "t1", "name": "fs_read", "args": {"path": "."}}]}}},
        {"user": {"content": None}, "assistant": {"Response": {"message_id": "m2", "content": "Two files."}}},
        {"user": {"content": {"Prompt": {"prompt": "<system-reminder>x</system-reminder>"}}},
         "assistant": {"Response": {"message_id": "m3", "content": ""}}}]}
    rows, subs = read("Kiro", text=json.dumps(doc))
    assert turns(rows) == ["user_prompt", "tool_use", "assistant_response", "user_prompt"]
    assert users(rows) == [("list the files", True, "developer", "text"),   # nothing marks the author: _prompt_row
                           ("<system-reminder>x</system-reminder>", False, "harness", "notice")]
    call = rows[1]
    assert (call["tool_name"], call["tool_call_id"], call["file_path"], call["response_id"]) == ("fs_read", "t1", ".", "m1")
    assert subs == []


def test_roger_roger_text_and_blocks():
    lines = [{"type": "user", "timestamp": "2026-10-01T00:00:00Z", "message": "add a flag"},
             {"type": "assistant", "timestamp": "2026-10-01T00:00:05Z", "message": "Added."},
             {"type": "user", "timestamp": "2026-10-01T00:01:00Z", "message": {"content": [{"type": "text", "text": "and docs"}]}},
             {"type": "assistant", "timestamp": "2026-10-01T00:01:05Z", "message": {"content": [{"type": "text", "text": "Done."}]}}]
    rows, _ = read("Roger Roger Agent", lines)
    assert [(r["turn_type"], r["content"], r["typed_by_developer"]) for r in rows] == [
        ("user_prompt", "add a flag", True), ("assistant_response", "Added.", False),
        ("user_prompt", "and docs", True), ("assistant_response", "Done.", False)]
    assert rows[0]["timestamp"] == "2026-10-01T00:00:00.000Z"


def step(i, kind, source="MODEL", **kw):
    return {"step_index": i, "source": source, "type": kind, "status": "DONE", "created_at": "2026-10-01T00:00:00Z", **kw}


def test_antigravity_steps_pair_with_calls_in_order():
    ask = ("<USER_REQUEST>\nfix the build\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nCursor at a.py:3\n</ADDITIONAL_METADATA>\n"
           "<USER_SETTINGS_CHANGE>\nmode: fast\n</USER_SETTINGS_CHANGE>")
    lines = [step(0, "USER_INPUT", "USER_EXPLICIT", content=ask),
             step(1, "CONVERSATION_HISTORY", "SYSTEM", content="# Conversation History\nearlier"),
             step(2, "PLANNER_RESPONSE", thinking="look first", tool_calls=[
                 {"name": "view_file", "args": {"AbsolutePath": "a.py"}}, {"name": "run_command", "args": {"CommandLine": "make"}}]),
             step(3, "VIEW_FILE", content="Created At: x\nx = 1"),
             step(4, "RUN_COMMAND", content="Created At: x\nerror", exit_code=2),
             step(5, "GENERIC", content="no call waits for me"),
             step(6, "PLANNER_RESPONSE", tool_calls=[{"name": "manage_task", "args": {}}]),   # its step never comes
             step(7, "PLANNER_RESPONSE", content="Fixed it.", tool_calls=[{"name": "list_dir", "args": {"DirectoryPath": "."}}]),
             step(8, "LIST_DIRECTORY", content="a.py"),
             step(9, "SYSTEM_MESSAGE", "SYSTEM", content="The following is a <SYSTEM_MESSAGE> not actually sent by the user."),
             step(10, "CHECKPOINT", "SYSTEM", content="# Resuming from a compaction\nsummary"),
             step(11, "ERROR_MESSAGE", "SYSTEM", content="Error: model output error"),
             step(12, "USER_INPUT", "USER_EXPLICIT", content="<USER_REQUEST></USER_REQUEST>\n<ADDITIONAL_METADATA>x</ADDITIONAL_METADATA>")]
    rows, _ = read("Antigravity", lines)
    assert users(rows) == [("fix the build", True, "developer", "text"),
                           ("The following is a <SYSTEM_MESSAGE> not actually sent by the user.", False, "harness", "notice"),
                           ("<USER_REQUEST></USER_REQUEST>\n<ADDITIONAL_METADATA>x</ADDITIONAL_METADATA>", False, "harness", "notice")]
    assert [(r["turn_type"], r["tool_call_id"]) for r in rows if r["role"] in ("tool_use", "tool_result")] == [
        ("tool_use", "2.0"), ("tool_use", "2.1"), ("tool_result", "2.0"), ("tool_result", "2.1"), ("tool_result", None),
        ("tool_use", "6.0"), ("tool_use", "7.0"), ("tool_result", "7.0")]
    calls = {r["tool_call_id"]: r for r in rows if r["turn_type"] == "tool_use"}
    assert (calls["2.0"]["file_path"], calls["2.1"]["command"], calls["7.0"]["file_path"]) == ("a.py", "make", None)
    results = {r["tool_call_id"]: r for r in rows if r["turn_type"] == "tool_result"}
    assert (results["2.0"]["tool_name"], results["2.0"]["is_error"], results["2.1"]["is_error"]) == ("view_file", None, True)
    assert [r["content"] for r in rows if r["turn_type"] in ("assistant_thinking", "assistant_response")] == ["look first", "Fixed it."]
    assert next(r for r in rows if r["turn_type"] == "summary")["content"].startswith("# Resuming")
    assert next(r for r in rows if r["content"] == "Error: model output error")["is_error"] is True
    assert events.count_typed("Antigravity", rows) == 1


def runner(entrypoint, prompt):
    md = {"actual_model": "gemini-x", "entrypoint": entrypoint, "harness": "agy", "requested_model": "auto", "runner_agent": "agy"}
    return [{"type": "session", "session_id": "s", "repo_path": "/w", "timestamp": "2026-10-01T00:00:00Z", "metadata": md},
            {"type": "user", "text": prompt, "timestamp": "2026-10-01T00:00:01Z"},
            {"type": "file", "path": "src/a.py", "timestamp": "2026-10-01T00:00:02Z"},
            {"type": "assistant", "text": "Done.", "model": "gemini-x", "timestamp": "2026-10-01T00:00:03Z",
             "metadata": {**md, "outcome": "ok", "returncode": "0"}},
            {"type": "terminal", "timestamp": "2026-10-01T00:00:04Z", "metadata": {**md, "outcome": "hard_timeout", "returncode": "124"}}]


def test_project_runner_a_delegated_task_is_the_agents():
    rows, _ = read("Project Runner", runner("delegate", "[delegate worktree] add tests for a.py"))
    assert turns(rows) == ["system_event", "user_prompt", "file_snapshot", "assistant_response", "system_event"]
    assert users(rows) == [("[delegate worktree] add tests for a.py", False, "agent", "notice")]
    assert (rows[2]["file_path"], rows[3]["model"], rows[4]["content"], rows[4]["is_error"]) == (
        "src/a.py", "gemini-x", "hard_timeout", True)
    rows, _ = read("Project Runner", runner("bridge", "add tests for a.py"))   # not delegated: _prompt_row
    assert users(rows) == [("add tests for a.py", True, "developer", "text")]


def test_a_format_with_no_facts_parser_keeps_no_stored_counts():
    """The counts dedup ranks copies by stay the stored facts': for Project Runner none (no parser). Filled from the
    rows there, 107 SWE-chat v2 copies outranked Entire's in version 2026-10-11 (has_tool_results first); a duplicate's
    counts are filled where its session row is written (build._write_part, conv_build.counts_of)."""
    from collect import copied_sessions

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "full.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in runner("bridge", "add tests for a.py")) + "\n")
        got = copied_sessions._facts_job(("Project Runner", [str(path)]))
        assert got["parse_error"].startswith("no parser") and "has_tool_results" not in got


def kimi(kind, **kw):
    return {"type": kind, "time": 1770000000000, **kw}


def loop(kind, **kw):
    return kimi("context.append_loop_event", event={"type": kind, "turnId": "1", **kw})


def message(origin, text):
    return kimi("context.append_message", message={"role": "user", "content": [{"type": "text", "text": text}],
                                                   "toolCalls": [], "origin": {"kind": origin}})


def test_kimi_code_origins_steps_and_tokens():
    lines = [kimi("metadata", protocol_version="1", created_at=1770000000000),
             kimi("config.update", profileName="default", systemPrompt="You are Kimi.", subagentNames=[]),
             kimi("turn.prompt", input=[{"type": "text", "text": "fix the test"}], origin={"kind": "user"}),
             message("user", "fix the test"),
             message("injection", "<system-reminder>plan mode is off</system-reminder>"),
             kimi("llm.request", model="kimi-k3", provider="kimi"),
             loop("step.begin", uuid="st1", step=1),
             loop("content.part", stepUuid="st1", part={"type": "think", "think": "read it first"}),
             loop("content.part", stepUuid="st1", part={"type": "text", "text": "Reading."}),
             loop("tool.call", stepUuid="st1", toolCallId="c1", name="ReadFile", args={"path": "a.py"}),
             loop("tool.result", parentUuid="st1", toolCallId="c1", result={"output": "boom", "isError": True}),
             loop("step.end", uuid="st1", step=1, usage={"inputOther": 10, "output": 3, "inputCacheRead": 7, "inputCacheCreation": 0},
                  finishReason="tool_use", messageId="msg1"),
             kimi("usage.record", model="kimi-k3", usage={"inputOther": 10, "output": 3}),
             message("task", "<notification>task t1 finished</notification>"),
             kimi("context.apply_compaction", summary="the story so far", compactedCount=4),
             kimi("turn.ended", turnId=1, reason="failed", error={"code": "x", "message": "the model failed"})]
    rows, _ = read("Kimi Code", lines)
    assert users(rows) == [("fix the test", True, "developer", "text"),   # turn.prompt adds no row
                           ("<system-reminder>plan mode is off</system-reminder>", False, "harness", "notice"),
                           ("<notification>task t1 finished</notification>", False, "harness", "notice")]
    assert turns(rows) == ["system_event", "system_event", "user_prompt", "user_prompt", "assistant_thinking",
                           "assistant_response", "tool_use", "tool_result", "user_prompt", "summary", "system_event"]
    said = next(r for r in rows if r["turn_type"] == "assistant_response")
    assert (said["input_tokens"], said["output_tokens"], said["cache_read_input_tokens"], said["model"], said["response_id"]) == (
        10, 3, 7, "kimi-k3", "msg1")
    result = next(r for r in rows if r["turn_type"] == "tool_result")
    assert (result["tool_name"], result["is_error"], result["content"]) == ("ReadFile", True, "boom")
    assert rows[-2]["content"] == "the story so far" and rows[-1]["is_error"] is True
    assert events.count_typed("Kimi Code", rows) == 1


def iso(t):
    """A time as the readers write it: the tables hold it as a timestamp."""
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z" if t is not None else None


def md_rows(text, name=".specstory/history/2026-02-01_1100-1200_abc123.md", source="specstory", agent=None):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "f.md"
        path.write_text(text)
        out = conv_build.extract_job((source, [({"source_id": f"o/r:{name}", "repo": "o/r", "agent": agent, "extra": {}},
                                                 [(name, path)])], d, "part-000"))
        rows = [r for f in out["files"] if "subagents" not in f for r in __import__("pyarrow.parquet").parquet.read_table(f).to_pylist()]
    return rows, out["per"][f"o/r:{name}"]


WORK_SESSION = """# WORK SESSION (1100-1200)

**Generated:** 2026-02-01T10:11:35.158Z
**Work Period:** 1100-1200
**Focus:** Live session logging
**Duration:** ~60 minutes

---

## Session Overview

This session captures real-time tool interactions and exchanges.

---

## Key Activities

<a name="ps_1769940280415"></a>
## Prompt Set (ps_1769940280415)

**Time:** 2026-02-01T10:04:40.415Z
**Duration:** 0ms
**Tool Calls:** 2

### Read - 2026-02-01 10:04:40 UTC [11:04:40 CEST]

**User Request:** read the readme
and summarize it

**Tool:** Read
**Input:** ```json
{
  "file_path": "README.md"
}
```

**Result:** ✅ Success
**Output:** ```
# Title
```bash
make test
```
### Usage
```

---

### Bash - 2026-02-01 10:04:52 UTC [11:04:52 CEST]

**User Request:** read the readme
and summarize it

**Tool:** Bash
**Input:** ```json
{
  "command": "ls missing",
  "description": "List it"
}
```

**Result:** ❌ Error
**Output:** ```
ls: missing: No such file or directory
```

---

<a name="ps_1769940300000"></a>
## Prompt Set (ps_1769940300000)

**Time:** 2026-02-01T10:05:00.000Z
**Duration:** 0ms
**Tool Calls:** 1

### Grep - 2026-02-01 10:05:00 UTC [11:05:00 CEST]

**System Action:** (Initiated automatically)

**Tool:** Grep
**Input:** ```json
{
  "pattern": "TODO"
}
```

**Result:** ✅ Success

---

### Read - 2026-02-01 10:05:30 UTC [11:05:30 CEST]

**User Request:** continue

**Result:** ✅ Success

---

<a name="ps_1769940400000"></a>
## Prompt Set (ps_1769940400000)

**Time:** 2026-02-01T10:06:40.000Z
**Duration:** 0ms
**Tool Calls:** 0

### Text Exchange - 2026-02-01 10:06:40 UTC [11:06:40 CEST]

**User Message:** continue

---

### Text Exchange - 2026-02-01 10:06:00 UTC [11:06:00 CEST]

**User Message:** why did it fail?

**Assistant Response:** The file is missing.

**Problem 1:** a bold label of its own

**Type:** Text-only exchange (no tool calls)

---
"""


def test_lsl_work_session_layout():
    rows, per = md_rows(WORK_SESSION)
    assert [(r["turn_type"], r["content"]) for r in rows if r["role"] in ("user", "assistant")] == [
        ("user_prompt", "read the readme\nand summarize it"),   # once a prompt set, though on every call
        ("user_prompt", "continue"),   # its request ends where the call's labels begin
        ("user_prompt", "continue"),   # typed again in the next prompt set: a row again
        ("user_prompt", "why did it fail?"),
        ("assistant_response", "The file is missing.\n\n**Problem 1:** a bold label of its own")]
    calls = [r for r in rows if r["turn_type"] == "tool_use"]
    assert [(c["tool_name"], c["file_path"], c["command"], c["pattern"]) for c in calls] == [
        ("Read", "README.md", None, None), ("Bash", None, "ls missing", None), ("Grep", None, None, "TODO"),
        ("Read", None, None, None)]   # an entry whose call the tool did not log: its heading's tool
    results = [r for r in rows if r["turn_type"] == "tool_result"]
    assert [(r["tool_name"], r["is_error"]) for r in results] == [("Read", False), ("Bash", True), ("Grep", False),
                                                                  ("Read", False)]
    assert results[0]["content"] == "# Title\n```bash\nmake test\n```\n### Usage"   # a fence inside an output is kept
    assert results[2]["content"] == ""   # a result with no output
    assert results[1]["bash_category"] == calls[1]["bash_category"] is not None   # a result takes its call's
    assert [iso(r["timestamp"]) for r in rows][:4] == ["2026-02-01T10:04:40.000Z"] * 3 + ["2026-02-01T10:04:52.000Z"]
    assert rows[0]["raw_line"] == 25 and rows[0]["typed_by_developer"] is True   # its entry's heading
    assert per["typed"] == 4 and per["n_conversation_rows"] == len(rows) == 13


SESSION_LOG = """# Claude Code Session Log

**Session:** 2026-01-07_2100-2200
**Type:** Local Project
**Time Range:** 2026-01-07T20:14:11.910Z - 2026-01-07T20:20:00.000Z
**Prompt Sets:** 2
**Classification:** Not coding

---

<a name="ps_1767816851910"></a>
## Prompt Set 1 (ps_1767816851910)

**Time:** 2026-01-07T20:14:11.910Z
**Duration:** 3000ms
**Tool Calls:** 1

### User

# Read session logs for continuity

Read the last log.

## Steps
1. find it

### Assistant

I'll read the log.

### Assistant

**Tool:** Glob
**Input:** ```json
{
  "pattern": "*.md"
}
```

**Result:** ✅ Success
**Output:** ```
a.md
```

---

<a name="ps_1767816900000"></a>
## Prompt Set 2 (ps_1767816900000)

**Time:** 2026-01-07T20:15:00.000Z
**Duration:** 0ms
**Tool Calls:** 0

### User

[Request interrupted by user]

### Assistant



---
"""


def test_lsl_session_log_layout():
    rows, per = md_rows(SESSION_LOG, name=".specstory/history/2026-01-07_2100-2200_g9b30a.md")
    assert [(r["turn_type"], r["content"]) for r in rows] == [
        ("user_prompt", "# Read session logs for continuity\n\nRead the last log.\n\n## Steps\n1. find it"),
        ("assistant_response", "I'll read the log."),
        ("tool_use", '{"pattern": "*.md"}'), ("tool_result", "a.md"),
        ("user_prompt", "[Request interrupted by user]")]
    assert [iso(r["timestamp"]) for r in rows] == ["2026-01-07T20:14:11.910Z"] * 4 + ["2026-01-07T20:15:00.000Z"]
    assert (rows[2]["tool_name"], rows[2]["pattern"], rows[3]["tool_name"]) == ("Glob", "*.md", "Glob")
    assert [(r["typed_by_developer"], r["kind"]) for r in rows if r["role"] == "user"] == [(True, "text"), (False, "interrupt")]
    assert per["typed"] == 1



def test_lsl_a_user_entry_holding_a_result_is_the_tools():
    """34,660 of the logs' 37,116 User entries are a tool's result (`**Result:** ✅`), read as prompts before
    2026-10-05: each is its call's, in the order the calls were made; a prompt or a new prompt set ends the wait."""
    call = lambda tool, key, value: (f"### Assistant\n\n**Tool:** {tool}\n**Input:** ```json\n{{\n  \"{key}\": \"{value}\"\n}}\n```\n\n")
    result = lambda mark, out: f"### User\n\n**Result:** {mark}\n\n{out}\n\n"
    text = ("# Claude Code Session Log\n\n**Session:** 2026-01-07_2100-2200\n\n---\n\n"
            "## Prompt Set 1 (ps_1767816851910)\n\n**Time:** 2026-01-07T20:14:11.910Z\n\n### User\n\ncheck the repo\n\n"
            + call("Bash", "command", "git status") + call("Read", "file_path", "/w/a.py")
            + result("✅ Success", "On branch main") + result("❌ Error", "File does not exist.") + result("✅ Success", "stray")
            + call("Grep", "pattern", "x") + "### User\n\nthanks, now fix it\n\n" + result("✅ Success", "late")
            + call("Glob", "pattern", "*.md") + "---\n\n## Prompt Set 2 (ps_1767816900000)\n\n**Time:** 2026-01-07T20:15:00.000Z\n\n"
            + result("✅ Success", "after the set"))
    rows, per = md_rows(text, name=".specstory/history/2026-01-07_2100-2200_g9b30a.md")
    assert [(r["turn_type"], r["tool_name"], r["content"], r["is_error"]) for r in rows] == [
        ("user_prompt", None, "check the repo", None), ("tool_use", "Bash", '{"command": "git status"}', None),
        ("tool_use", "Read", '{"file_path": "/w/a.py"}', None), ("tool_result", "Bash", "On branch main", False),
        ("tool_result", "Read", "File does not exist.", True), ("tool_result", None, "stray", False),
        ("tool_use", "Grep", '{"pattern": "x"}', None), ("user_prompt", None, "thanks, now fix it", None),
        ("tool_result", None, "late", False), ("tool_use", "Glob", '{"pattern": "*.md"}', None),
        ("tool_result", None, "after the set", False)]
    assert [(r["actor"], r["typed_by_developer"]) for r in rows if r["turn_type"] == "tool_result"] == [("tool", False)] * 5
    assert [r["bash_category"] for r in rows if r["turn_type"] == "tool_result"] == ["git", None, None, None, None]
    assert per["typed"] == 2 and rows[3]["category"] == "Action" and iso(rows[-1]["timestamp"]) == "2026-01-07T20:15:00.000Z"

def test_a_note_in_the_history_folder_gives_no_rows():
    rows, per = md_rows("# My notes\n\nNothing here is a session.\n", name=".specstory/history/notes.md")
    assert rows == [] and per["n_conversation_rows"] == 0


def test_opencode_a_log_line_before_the_export_and_messages_as_lines():
    user = {"info": {"id": "m1", "sessionID": "s", "role": "user", "time": {"created": 1770000000000}},
            "parts": [{"type": "text", "text": "rename it"}]}
    said = {"info": {"id": "m2", "sessionID": "s", "role": "assistant", "modelID": "x", "time": {"created": 1770000001000}},
            "parts": [{"type": "text", "text": "Renamed."}]}
    doc = json.dumps({"info": {"id": "s", "version": "1.18.0"}, "messages": [user, said]}, indent=2)
    shapes = {"log line": "mise ~/.config/mise/config.toml tools: opencode@1.18.0\n" + doc,
              "two log lines": "[opencode-litellm] Discovered 70 models\nmore\n" + doc,
              "lines": json.dumps(user) + "\n" + json.dumps(said) + "\n"}
    for shape, text in shapes.items():
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "full.jsonl"
            path.write_text(text)
            rows, _ = events.for_session("OpenCode", [("full.jsonl", path)], dict(SESSION))
            got = facts.for_session("OpenCode", [path])
        assert [(r["turn_type"], r["content"]) for r in rows] == [("user_prompt", "rename it"),
                                                                  ("assistant_response", "Renamed.")], shape
        assert (got.get("parse_error"), got["n_user_messages"], got["n_agent_messages"]) == (None, 1, 1), shape
    with tempfile.TemporaryDirectory() as d:   # a document followed by something else still fails, and says so
        path = Path(d) / "full.jsonl"
        path.write_text(doc + "\ntrailing words\n")
        assert "parse_error" in facts.for_session("OpenCode", [path])


def test_an_hourly_log_is_claude_codes_and_its_tools_have_categories():
    """The logging tool's files record no agent; their title and their tools are Claude Code's (2026-10-05). Tool
    names SWE-chat never saw get the category of their kind."""
    from collect import events, formats

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "2026-02-01_1100-1200_c197ef.md"
        path.write_text(WORK_SESSION)
        session = {"session_key": "specstory:o/r:x", "agent": None}
        rows, _ = formats.specstory([(path.name, path)], session)
    assert session["agent"] == "Claude Code" and {r["agent"] for r in rows} == {"Claude Code"}
    for name, want in (("run_terminal_command", "Action"), ("execute_bash", "Action"), ("view_file", "Research"),
                       ("fs_read", "Research"), ("write_to_file", "Action"), ("manage_task", "Other"),
                       ("multi_replace_file_content", "Action"), ("read_url_content", "Research"),
                       ("FetchURL", "Research"), ("ReadMediaFile", "Research"), ("spawn_subagent", "Orchestration"),
                       ("browser_subagent", "Orchestration"), ("TodoList", "Other")):
        assert events.CATEGORY_OF.get(name, "Other") == want, name
    assert {"execute_bash", "run_command", "run_terminal_command"} <= events.BASH_TOOLS


def shell_use(name, body, summary=None, kind="shell"):
    summary = summary if summary is not None else f"Tool use: **{name}**"
    return (f'<tool-use data-tool-type="{kind}" data-tool-name="{name}"><details>\n<summary>{summary}</summary>\n\n'
            f"{body}\n</details></tool-use>\n\n")


def test_specstory_a_shell_calls_command_from_each_rendering():
    """SpecStory renders a shell call's command in one of several places, by agent and version (2026-10-05)."""
    uses = [
        shell_use("exec_command", "```\nChunk ID: 1\nWall time: 0.1 seconds\n```", "Tool use: **exec_command** `git status --short`"),
        shell_use("run_terminal_cmd", "```bash\ncd /w && npm test\n```", "Tool use: **run_terminal_cmd** • Run command: cd /w && npm t"),
        shell_use("run_terminal_command_v2", "", "Tool use: **run_terminal_command_v2** • Run command: ls -la"),
        shell_use("run_in_terminal", 'Parameters:\n```json\n{\n  "command": "pytest -q",\n  "explanation": "run"\n}\n```', kind="unknown"),
        shell_use("shell", "**Input:**\n\n- command: bash -lc 'make test'\n- workdir: /w"),
        shell_use("Bash", "Run the tests\n\n`pytest -x`\n\n```text\n3 passed\n```"),
        shell_use("Bash", "```text\noutput only\n```"),
        shell_use("exec_command", "```\nok\n```", "Tool use: **exec_command** `echo &amp; ls`"),
        shell_use("Read", "`a.py`\n\n```text\nx = 1\n```", kind="read"),
        shell_use("Bash", "```text\nsee\n`not a command`\n```"),
        shell_use("shell", "**Input:**\n\n- command: cat <<'EOF' > a.txt\n  hello\nEOF\n- workdir: /w"),
        shell_use("run_in_terminal", 'Parameters:\n```json\n{"commandLine": {"original": "npm tset", "userEdited": "npm test"}, '
                                     '"kind": "terminal"}\n```', kind="unknown"),
        shell_use("shell", "**Input:**\n\n- command: `[bash -lc git diff]`\n- workdir: /w"),
        shell_use("bash", "**Input:**\n\n- command: `ls -la src`"),
        shell_use("shell", '**Input:**\n\n- command: ["bash", "-lc", "make"]'),
    ]
    text = ("<!-- Claude Code Session 0b1c2d3e-aaaa-bbbb-cccc-123456789abc (2026-01-02 03:04:05Z) -->\n\n"
            "_**User (2026-01-02 03:04Z)**_\n\nrun it\n\n---\n\n_**Agent (m 2026-01-02 03:05Z)**_\n\n" + "".join(uses) + "---\n")
    rows, _ = md_rows(text, name=".specstory/history/2026-01-02_03-04Z-run.md", agent="Claude Code")
    calls = [r for r in rows if r["turn_type"] == "tool_use"]
    assert [(c["tool_name"], c["command"], c["bash_category"]) for c in calls] == [
        ("exec_command", "git status --short", "git"),         # the summary's backticked text; the fence is output
        ("run_terminal_cmd", "cd /w && npm test", "file ops"),  # the fence, whole, over the summary's cut-off text
        ("run_terminal_command_v2", "ls -la", "file ops"),     # the summary's, when no fence holds it
        ("run_in_terminal", "pytest -q", "test/build"),        # the Parameters JSON
        ("shell", "bash -lc 'make test'", "test/build"),        # the Input list, read through its shell
        ("Bash", "pytest -x", "test/build"),                   # one backticked line before the output
        ("Bash", None, None),                                  # output alone: no command, no category
        ("exec_command", "echo & ls", "other"),                # the summary's HTML entities read
        ("Read", None, None),                                  # not a shell tool
        ("Bash", None, None),                                  # a backticked line inside the output is output
        ("shell", "cat <<'EOF' > a.txt\n  hello\nEOF", "file ops"),   # a command over several lines
        ("run_in_terminal", "npm test", "package manager"),     # VS Code's commandLine: the developer's edit
        ("shell", "bash -lc git diff", "git"),                 # Codex's argument list, backticked
        ("bash", "ls -la src", "file ops"),                     # backticks off
        ("shell", "bash -lc make", "test/build")]               # a JSON list


AIDER = """# aider chat started at 2025-06-03 14:43:55

> Aider v0.84.0

#### add a cache

Here is the plan.

#### 1. **Setup**

Install the package first.

#### `src/cache.py`

The new module goes here.

#### yes

Done, the cache is in.

#### and the tests
#### for it

Added tests.

#### Next steps:

Run them in CI.

#### 2. Wire it in

Call it from main.

#### **Note**

Restart the server.

> Applied edit to src/cache.py

#### `src/main.py`

Wired.

#### fix `a.py`
#### and `b.py`

Both fixed.
"""


def test_aider_the_models_own_headings_stay_in_its_reply():
    rows, per = md_rows(AIDER, name=".aider.chat.history.md", source="aider", agent="Aider")
    assert [(r["turn_type"], r["content"]) for r in rows] == [
        ("system_event", "aider chat started at 2025-06-03 14:43:55"), ("system_event", "Aider v0.84.0"),
        ("user_prompt", "add a cache"),
        ("assistant_response", "Here is the plan.\n\n#### 1. **Setup**\n\nInstall the package first.\n\n"
                               "#### `src/cache.py`\n\nThe new module goes here."),
        ("user_prompt", "yes"),   # one line, no reply shape: a developer's short message
        ("assistant_response", "Done, the cache is in."),
        ("user_prompt", "and the tests\nfor it"),   # two lines: input
        ("assistant_response", "Added tests.\n\n#### Next steps:\n\nRun them in CI.\n\n#### 2. Wire it in\n\n"
                               "Call it from main.\n\n#### **Note**\n\nRestart the server."),
        ("system_event", "Applied edit to src/cache.py"),
        ("user_prompt", "`src/main.py`"),   # after Aider's notice, not inside a reply: input
        ("assistant_response", "Wired."),
        ("user_prompt", "fix `a.py`\nand `b.py`"),   # reply-shaped, but two lines: input
        ("assistant_response", "Both fixed.")]
    assert per["typed"] == 5


def test_specstory_command_reading_takes_linear_time():
    """A block of unclosed tags: a lazy pattern tried again from each one took 11 s at 180 KB (2026-10-05); at 270 KB
    it would take about 25 s, found by plain search 0.003 s."""
    import time

    from collect import formats

    for block in ("<summary>" * 30_000, "<summary>Tool use: **Bash**</summary>\n" + "```bash\n" * 100_000,
                  "<summary>Tool use: **shell**</summary>\n- command: x\n" + "y\n" * 300_000):
        start = time.perf_counter()
        formats._md_command(block)
        assert time.perf_counter() - start < 2.0
    start = time.perf_counter()
    formats._reply_heading("`" + "a" * 100_000 + " `" * 100_000)
    assert time.perf_counter() - start < 2.0
