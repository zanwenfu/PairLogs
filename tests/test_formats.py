"""Conversations rows for the formats other than Claude Code's: who typed what by each agent's own marks, SWE-chat's
roles and turn types, token placement, and the counts facts.py gives for the same file."""

import json
import tempfile
from pathlib import Path

from collect import events, facts

SESSION = {"source": "entire", "session_key": "entire:o/r:s1", "session_id": "s1", "repo_id": "o/r", "agent": "x"}
ROLES = {"user", "assistant", "tool_use", "tool_result", "metadata"}
TURNS = {"user_prompt", "system_injected", "assistant_response", "assistant_thinking", "tool_use", "tool_result",
         "summary", "system_event", "file_snapshot", "progress", "queue_operation"}


def run(agent: str, lines=None, doc=None, name="full.jsonl"):
    """Rows for one transcript, and facts.py's count for the same file."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / name
        path.write_text(json.dumps(doc) if doc is not None else "\n".join(json.dumps(x) for x in lines) + "\n")
        rows, subs = events.for_session(agent, [(name, path)], {**SESSION, "agent": agent})
        counted = facts.for_session(agent, [path]).get("n_user_messages")
    assert all(r["role"] in ROLES and r["turn_type"] in TURNS for r in rows + subs)
    assert [r["turn_number"] for r in rows] == list(range(len(rows)))
    return rows, counted


def users(rows):
    return [(r["content"], r["typed_by_developer"], r["kind"]) for r in rows if r["role"] == "user"]


def codex_event(kind, **payload):
    return {"type": kind, "timestamp": "2026-10-01T00:00:00Z", "payload": payload}


def test_codex_typed_by_its_marks_and_its_tokens_once_a_call():
    lines = [
        codex_event("session_meta", id="s1"),
        codex_event("turn_context", model="gpt-x"),
        codex_event("response_item", type="message", role="user", content=[{"type": "input_text", "text": "# AGENTS.md instructions"}]),
        codex_event("response_item", type="message", role="user", content=[{"type": "input_text", "text": "fix  the bug"}]),
        codex_event("event_msg", type="user_message", message="fix the bug"),
        codex_event("response_item", type="reasoning", summary=[{"type": "summary_text", "text": "think"}]),
        codex_event("response_item", type="function_call", name="shell", arguments='{"command": ["ls"]}', call_id="c1"),
        codex_event("token_usage_record", usage={"input_tokens": 10, "output_tokens": 2, "cached_input_tokens": 4}),
        codex_event("event_msg", type="token_count", info={"last_token_usage": {"input_tokens": 99}}),
        codex_event("response_item", type="function_call_output", call_id="c1", output="a.py"),
        codex_event("response_item", type="message", role="user", content=[{"type": "input_text", "text": "<turn_aborted>"}]),
        codex_event("response_item", type="message", role="assistant", content=[{"type": "output_text", "text": "done"}]),
        # A mark with an image, whose text sits inside the model-facing message.
        codex_event("event_msg", type="user_message", message="see this", images=["data:image/png;base64,x"]),
        codex_event("response_item", type="message", role="user", content=[
            {"type": "input_text", "text": "<image name=a>"}, {"type": "input_image", "image_url": "x"},
            {"type": "input_text", "text": "see this"}]),
        # Typed, but never sent to the model as such: a row of its own.
        codex_event("event_msg", type="user_message", message="/review"),
        # A session with both kinds of record: only the records are read, never the counts beside them.
        codex_event("response_item", type="message", role="assistant", content=[{"type": "output_text", "text": "next"}]),
        codex_event("event_msg", type="token_count", info={"last_token_usage": {"input_tokens": 99}}),
        codex_event("token_usage_record", usage={"input_tokens": 20, "output_tokens": 5}),
    ]
    rows, counted = run("Codex", lines)
    assert users(rows) == [("# AGENTS.md instructions", False, "notice"), ("fix  the bug", True, "text"),
                           ("<turn_aborted>", False, "interrupt"),
                           ("<image name=a>\n[Image: unknown]\nsee this", True, "text"), ("/review", True, "text")]
    assert all(r["turn_type"] == "user_prompt" for r in rows if r["role"] == "user")   # as SWE-chat files Codex
    call = next(r for r in rows if r["turn_type"] == "tool_use")
    assert (call["command"], call["input_tokens"], call["output_tokens"], call["cache_read_input_tokens"]) == ("ls", 10, 2, 4)
    assert [r["input_tokens"] for r in rows if r["input_tokens"] is not None] == [10, 20]   # never token_count's
    assert next(r for r in rows if r["turn_type"] == "tool_result")["tool_name"] == "shell"
    assert events.count_typed("Codex", rows) == counted == 3


def test_codex_newer_versions_mark_typed_messages_with_items():
    lines = [codex_event("response_item", type="message", role="user", content=[{"type": "input_text", "text": "<environment_context>"}]),
             codex_event("event_msg", type="item_completed", item={"type": "UserMessage", "content": [
                 {"type": "text", "text": "see this"}, {"type": "image", "url": "x"}]}),
             codex_event("response_item", type="message", role="user", content=[
                 {"type": "input_text", "text": "<image name=a>"}, {"type": "input_image", "image_url": "x"},
                 {"type": "input_text", "text": "see this"}])]
    rows, counted = run("Codex", lines)
    assert [u[1] for u in users(rows)] == [False, True]
    assert events.count_typed("Codex", rows) == counted == 1


def test_codex_without_any_mark_is_typed():
    """An older rollout with no typed-message mark: a message no rule names is the developer's, typed (the owner's rule
    of 2026-10-07: typed misses no message a person may have written). The cheap count cannot tell: empty."""
    lines = [codex_event("response_item", type="message", role="user", content=[{"type": "input_text", "text": "hi"}])]
    rows, counted = run("Codex", lines)
    assert users(rows) == [("hi", True, "text")]
    assert events.count_typed("Codex", rows) == 1 and counted is None


def test_gemini_both_file_shapes():
    doc = {"sessionId": "s", "messages": [
        {"id": "1", "type": "user", "content": "add a test", "timestamp": "2026-10-01T00:00:00Z"},
        {"id": "2", "type": "gemini", "content": "ok", "model": "gemini-x", "tokens": {"input": 5, "output": 1},
         "thoughts": [{"subject": "plan", "description": "write it"}],
         "toolCalls": [{"id": "t1", "name": "write_file", "args": {"file_path": "t.py"}, "status": "success",
                        "result": [{"functionResponse": {"response": {"output": "ok"}}}]}]},
        {"id": "3", "type": "info", "content": "Request cancelled."}]}
    rows, counted = run("Gemini CLI", doc=doc)
    assert users(rows) == [("add a test", True, "text")] and counted == 1
    assert [r["turn_type"] for r in rows] == ["user_prompt", "assistant_thinking", "assistant_response", "tool_use",
                                              "tool_result", "system_event"]
    assert rows[2]["input_tokens"] == 5 and rows[3]["file_path"] == "t.py" and rows[4]["tool_name"] == "write_file"
    # The {role, parts} shape: the developer's text wrapped in <USER_REQUEST>; `turns` repeats `messages`.
    msgs = [{"role": "user", "parts": [{"text": "<USER_REQUEST>\nfix it\n</USER_REQUEST>\n<ADDITIONAL_METADATA>t</ADDITIONAL_METADATA>"}]},
            {"role": "model", "parts": [{"text": "fixed"}]}, {"role": "model", "parts": [{"text": ""}]},
            {"role": "user", "parts": [{"text": "<USER_REQUEST>\nthanks\n</USER_REQUEST>"}]}]
    rows, counted = run("Gemini CLI", doc={"sessionId": "s", "messages": msgs, "turns": msgs})
    assert [r["turn_type"] for r in rows] == ["user_prompt", "assistant_response", "user_prompt"]
    assert events.count_typed("Gemini CLI", rows) == counted == 2   # rows with no id are each their own message


def test_copilot_authorship_is_its_source_not_its_task_id():
    def msg(content, **data):
        return {"type": "user.message", "id": content, "data": {"content": content, **data}}
    lines = [msg("typed in the terminal"), msg("typed from the web", parentAgentTaskId="task-1", delivery="idle"),
             msg("<skill-context>steps</skill-context>"), msg("ship", source="skill-ship"),
             msg("for the worker", source="agent-worker"),
             {"type": "assistant.message", "data": {"messageId": "m1", "content": "on it", "outputTokens": 7,
                                                    "toolRequests": [{"toolCallId": "c1", "name": "bash", "arguments": {"command": "ls"}}]}},
             {"type": "tool.execution_complete", "data": {"toolCallId": "c1", "success": False, "result": {"content": "no"}}},
             {"type": "hook.start", "data": {}}]
    rows, counted = run("Copilot CLI", lines)
    assert [u[1] for u in users(rows)] == [True, True, False, False, False]
    assert events.count_typed("Copilot CLI", rows) == counted == 2
    result = next(r for r in rows if r["turn_type"] == "tool_result")
    assert (result["tool_name"], result["is_error"]) == ("bash", True)
    assert next(r for r in rows if r["turn_type"] == "assistant_response")["output_tokens"] == 7
    # A source not seen before is the developer's: typed misses no message a person may have written (2026-10-07).
    rows, counted = run("Copilot CLI", [msg("?", source="something-new")])
    assert users(rows) == [("?", True, "text")] and counted == 1 == events.count_typed("Copilot CLI", rows)


def test_pi_skills_extensions_and_shell_commands():
    def m(role, content, **kw):
        return {"type": "message", "id": role, "message": {"role": role, "content": content, **kw}}
    lines = [{"type": "session", "id": "s", "version": 3},
             m("user", [{"type": "text", "text": "go"}, {"type": "image", "mimeType": "image/png", "data": "x"}]),
             m("user", "<skill name=x>steps</skill>"),
             {"type": "custom_message", "id": "c", "customType": "ext", "content": "from an extension"},
             m("assistant", [{"type": "thinking", "thinking": "t"}, {"type": "toolCall", "id": "k", "name": "read",
                                                                    "arguments": {"path": "a.py"}}],
               usage={"input": 3, "output": 1, "cacheRead": 2}),
             m("assistant", [{"type": "text", "text": "first"}, {"type": "text", "text": "second"},
                             {"type": "toolCall", "id": "j", "name": "ls", "arguments": {}}], usage={"input": 6, "output": 2}),
             m("toolResult", [{"type": "text", "text": "body"}], toolCallId="k", toolName="read"),
             m("bashExecution", None, command="git status", output="clean", exitCode=0)]
    rows, counted = run("Pi", lines)
    assert users(rows) == [("go\n[Image: image/png]", True, "text"), ("<skill name=x>steps</skill>", False, "notice"),
                           ("from an extension", False, "notice"), ("git status", False, "shell")]
    call = next(r for r in rows if r["turn_type"] == "tool_use")
    assert (call["file_path"], call["input_tokens"], call["cache_read_input_tokens"]) == ("a.py", 3, 2)
    # One call's counts once: on its first text row, not its second text or its tool call.
    assert [(r["content"], r["input_tokens"]) for r in rows
            if r["turn_type"] in ("assistant_response", "tool_use")][-3:] == [("first", 6), ("second", None), ("{}", None)]
    assert events.count_typed("Pi", rows) == counted == 1


def test_opencode_synthetic_parts_steps_and_repeated_ids():
    def user(mid, parts):
        return {"info": {"id": mid, "role": "user", "time": {"created": 1}}, "parts": parts}
    doc = {"info": {"id": "s"}, "messages": [
        user("m1", [{"type": "text", "text": "read a.py"}, {"type": "text", "text": "contents of a.py", "synthetic": True},
                    {"type": "file", "mime": "image/png"}]),
        {"info": {"id": "m2", "role": "assistant", "modelID": "x", "time": {"created": 2}}, "parts": [
            {"type": "step-start"}, {"type": "reasoning", "text": "r"},
            {"type": "tool", "tool": "read", "callID": "c", "state": {"status": "completed", "input": {"filePath": "a.py"}, "output": "x"}},
            {"type": "step-finish", "tokens": {"input": 9, "output": 3, "cache": {"read": 1, "write": 0}}},
            {"type": "step-start"}, {"type": "text", "text": "done"},
            {"type": "step-finish", "tokens": {"input": 12, "output": 4, "cache": {"read": 0, "write": 0}}}]},
        user("m1", [{"type": "text", "text": "a different message under the same id"}])]}
    rows, counted = run("OpenCode", doc=doc)
    assert users(rows) == [("contents of a.py", False, "notice"), ("read a.py\n[Image: image/png]", True, "text"),
                           ("a different message under the same id", True, "text")]
    call = next(r for r in rows if r["turn_type"] == "tool_use")
    text = next(r for r in rows if r["turn_type"] == "assistant_response")
    assert (call["file_path"], call["input_tokens"], text["input_tokens"]) == ("a.py", 9, 12)   # each step its own
    assert events.count_typed("OpenCode", rows) == counted == 2


def test_cursor_and_factory_droid():
    lines = [{"role": "user", "message": {"content": [{"type": "text", "text": "<user_query>add a flag</user_query>"}]}},
             {"role": "user", "message": {"content": [{"type": "text", "text": "<git_status>clean</git_status>"}]}},
             {"role": "assistant", "message": {"content": [{"type": "text", "text": "ok"},
                                                           {"type": "tool_use", "name": "Grep", "input": {"pattern": "flag"}}]}},
             {"type": "turn_ended", "status": "ok"}]
    lines += [{"role": "user", "message": {"content": [{"type": "text", "text": f"<user_query>{q}</user_query>"}]}}
              for q in ("Briefly inform the user about the task result and perform any follow-up actions",
                        "Implement the plan as specified, it is attached for your reference.",
                        "Execute the selected diff-tab commit-and-push action.", "y")]
    rows, counted = run("Cursor", lines)
    assert [(u[1], u[2]) for u in users(rows)] == [(True, "text"), (False, "notice"), (False, "notice"), (False, "plan"),
                                                   (False, "command"), (True, "text")]
    assert next(r for r in rows if r["turn_type"] == "tool_use")["pattern"] == "flag"
    assert events.count_typed("Cursor", rows) == counted == 2
    lines = [{"type": "message", "id": "1", "message": {"role": "user", "content": [], "hookEventName": "SessionStart",
                                                        "hookResults": [{"exitCode": 0, "stdout": "ok"}], "visibility": "user_only"}},
             {"type": "message", "id": "2", "message": {"role": "user", "content": [{"type": "text", "text": "<system-reminder>x</system-reminder>"}],
                                                        "visibility": "llm_only"}},
             {"type": "message", "id": "3", "message": {"role": "user", "content": [{"type": "text", "text": "build it"}]}},
             {"type": "message", "id": "4", "message": {"role": "assistant", "content": [
                 {"type": "thinking", "thinking": "t"}, {"type": "tool_use", "id": "u", "name": "Execute", "input": {"command": "make"}}]}},
             {"type": "message", "id": "5", "message": {"role": "user", "content": [
                 {"type": "tool_result", "tool_use_id": "u", "is_error": True, "content": "fail"}]}}]
    rows, counted = run("Factory AI Droid", lines)
    assert [r["turn_type"] for r in rows] == ["progress", "user_prompt", "user_prompt", "assistant_thinking", "tool_use", "tool_result"]
    # Nothing marks inserted text: the developer's unless the text shows a harness's (2026-10-05); facts.py's count
    # stays unknown, and a session with rows publishes its rows' count (decision 56).
    assert users(rows) == [("<system-reminder>x</system-reminder>", False, "notice"), ("build it", True, "text")]
    assert events.count_typed("Factory AI Droid", rows) == 1 and counted is None


def test_devin_rows_and_unknown_authorship():
    lines = [{"role": "user", "message_id": "u", "content": "deploy", "metadata": {"is_user_input": True}},
             {"role": "assistant", "message_id": "a", "content": "", "thinking": {"thinking": "t"},
              "tool_calls": [{"id": "c", "name": "exec", "arguments": {"command": "make"}, "index": 0}],
              "metadata": {"generation_model": "m", "metrics": {"input_tokens": 4, "output_tokens": 2}}},
             {"role": "tool", "message_id": "t", "tool_call_id": "c", "content": "ok"}]
    rows, counted = run("Devin", lines)
    assert [r["turn_type"] for r in rows] == ["user_prompt", "assistant_thinking", "tool_use", "tool_result"]
    assert rows[2]["input_tokens"] == 4 and rows[3]["tool_name"] == "exec"
    # Devin marks every user line as input, so the mark tells nothing: the developer's, unless the text shows a
    # harness's (2026-10-05; who typed was left unknown before). facts.py's count stays unknown, so stored rows need
    # no new count; a session with rows publishes its rows' typed count (decision 56).
    assert users(rows) == [("deploy", True, "text")] and counted is None


# Formats with a reader but no facts parser (2026-10-05). The write step fills a session's counts from its
# conversation rows wherever its stored facts are None, so a parser would only change rows already filled. Adding one
# means a new FACTS_VERSION, and relabel then counts every stored row of every source again, which is not worth it.
NO_FACTS_PARSER = {"grok cli", "qwen code", "kiro", "roger roger agent", "antigravity", "project runner", "kimi code"}


def test_every_format_has_an_extractor_and_a_parser():
    from collect import formats

    assert set(formats.EXTRACTORS) | set(events.EXTRACTORS) == set(facts.PARSERS) | NO_FACTS_PARSER
    assert not NO_FACTS_PARSER & set(facts.PARSERS)


def test_codex_a_count_with_nothing_in_it_moves_nothing_on():
    lines = [codex_event("event_msg", type="user_message", message="go"),
             codex_event("response_item", type="message", role="user", content=[{"type": "input_text", "text": "go"}]),
             codex_event("response_item", type="function_call", name="shell", arguments="{}", call_id="c1"),
             codex_event("event_msg", type="token_count", info=None, rate_limits={"primary": {}}),
             codex_event("event_msg", type="token_count", info={"last_token_usage": {"input_tokens": 7, "output_tokens": 1}})]
    rows, _ = run("Codex", lines)
    assert next(r for r in rows if r["turn_type"] == "tool_use")["input_tokens"] == 7


def test_codex_with_both_kinds_of_mark_reads_one():
    lines = [codex_event("event_msg", type="user_message", message="hello"),
             codex_event("event_msg", type="item_completed", item={"type": "UserMessage", "content": [{"type": "text", "text": "hello"}]}),
             codex_event("response_item", type="message", role="user", content=[{"type": "input_text", "text": "hello"}])]
    rows, _ = run("Codex", lines)
    assert users(rows) == [("hello", True, "text")] and events.count_typed("Codex", rows) == 1


def test_gemini_a_message_written_again_is_read_once_as_last_written():
    lines = [{"sessionId": "s", "startTime": "2026-10-01T00:00:00Z"},
             {"id": "u1", "type": "user", "content": "add it", "timestamp": "2026-10-01T00:00:01Z"},
             {"id": "g1", "type": "gemini", "content": "", "toolCalls": [{"id": "t", "name": "write_file", "args": {"file_path": "a"}}]},
             {"id": "u2", "type": "user", "content": "and test it", "timestamp": "2026-10-01T00:00:03Z"},
             {"$set": {"lastUpdated": "2026-10-01T00:00:04Z"}},
             {"id": "g1", "type": "gemini", "content": "", "toolCalls": [{"id": "t", "name": "write_file", "args": {"file_path": "a"},
                                                                         "status": "success", "result": "written"}]},
             {"id": "u1", "type": "user", "content": "add it", "timestamp": "2026-10-01T00:00:01Z"}]
    rows, counted = run("Gemini CLI", lines)
    assert [(r["source_entry_uuid"], r["turn_type"]) for r in rows] == [
        ("u1", "user_prompt"), ("g1", "tool_use"), ("g1", "tool_result"), ("u2", "user_prompt")]   # g1 at its first place
    assert events.count_typed("Gemini CLI", rows) == counted == 2


def test_gemini_parts_of_every_kind():
    msgs = [{"role": "user", "parts": [{"text": "<USER_REQUEST>look</USER_REQUEST>"}, {"inlineData": {"mimeType": "image/png", "data": "x"}}]},
            {"role": "model", "parts": [{"text": "planning", "thought": True}, {"text": "reading"},
                                        {"functionCall": {"name": "read_file", "args": {"file_path": "a.py"}}}]},
            {"role": "user", "parts": [{"functionResponse": {"name": "read_file", "response": {"output": "x = 1"}}}]}]
    rows, counted = run("Gemini CLI", doc={"sessionId": "s", "messages": msgs})
    assert [(r["turn_type"], r["content"]) for r in rows if r["turn_type"] != "tool_use"] == [
        ("user_prompt", "look\n[Image: image/png]"), ("assistant_thinking", "planning"),
        ("assistant_response", "reading"), ("tool_result", '{"output": "x = 1"}')]
    assert next(r for r in rows if r["turn_type"] == "tool_use")["file_path"] == "a.py"
    assert counted == 1


def test_copilot_a_subagents_events_are_its_own_thread():
    lines = [{"type": "user.message", "id": "1", "data": {"content": "review it"}},
             {"type": "assistant.message", "id": "2", "data": {"messageId": "m", "content": "",
                                                              "toolRequests": [{"toolCallId": "task1", "name": "task", "arguments": {}}]}},
             {"type": "user.message", "id": "3", "agentId": "w1", "data": {"content": "check a.py", "source": "agent-main",
                                                                          "parentToolCallId": "task1"}},
             {"type": "assistant.message", "id": "4", "agentId": "w1", "data": {"messageId": "s", "content": "fine",
                                                                               "parentToolCallId": "task1"}},
             {"type": "assistant.message", "id": "5", "agentId": "w2", "data": {"messageId": "t", "content": "also fine"}}]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "full.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines))
        rows, subs = events.for_session("Copilot CLI", [("full.jsonl", path)], {**SESSION, "checkpoint_pk": "o/r#c1"})
    assert [r["content"] for r in rows if r["role"] == "user"] == ["review it"]
    assert [(r["task_pk"], r["agent_id"], r["content"], r["typed_by_developer"]) for r in subs] == [
        ("o/r#c1#task1", "w1", "check a.py", False), ("o/r#c1#task1", "w1", "fine", False),
        ("o/r#c1#w2", "w2", "also fine", False)]   # an agentId alone is a thread too
    assert [r["turn_pk"] for r in subs] == ["o/r#c1#task1#0", "o/r#c1#task1#1", "o/r#c1#w2#0"]


def test_pi_shell_output_is_told_apart_from_tool_results():
    lines = [{"type": "message", "id": "b", "message": {"role": "bashExecution", "command": "ls", "output": "a", "exitCode": 0}}]
    rows, _ = run("Pi", lines)
    assert [(r["role"], r["kind"], r["actor"]) for r in rows] == [("user", "shell", "developer"), ("tool_result", "shell", "harness")]


def _md(source: str, name: str, text: str, agent: str = "Claude Code"):
    from collect import conv_build

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "f.md"
        path.write_text(text)
        out = conv_build.extract_job((source, [({"source_id": f"o/r:{name}", "repo": "o/r", "agent": agent, "extra": {}},
                                                [(name, path)])], d, "part-000"))
        rows = [r for f in out["files"] if "subagents" not in f for r in __import__("pyarrow.parquet").parquet.read_table(f).to_pylist()]
    return rows, out["per"][f"o/r:{name}"]


def test_specstory_blocks_tool_uses_and_thinking():
    text = ("<!-- Claude Code Session 0b1c2d3e-aaaa-bbbb-cccc-123456789abc (2026-01-02 03:04:05Z) -->\n\n"
            "_**User (2026-01-02 03:04Z)**_\n\nadd a test\n\n---\n\n"
            "_**Agent (claude-opus-4-1 2026-01-02 03:05Z)**_\n\n"
            "<think><details><summary>Thought Process</summary>\nlook first\n</details></think>\n\nReading it.\n\n"
            '<tool-use data-tool-type="read" data-tool-name="Read"><details>\n<summary>Tool use: **Read**</summary>\n'
            " `./a.py`\n\n```text\nx = 1\n```\n</details></tool-use>\n\nDone.\n\n---\n")
    rows, per = _md("specstory", ".specstory/history/2026-01-02_03-04Z-add.md", text)
    assert [(r["turn_type"], r["content"]) for r in rows] == [
        ("user_prompt", "add a test"), ("assistant_thinking", "look first"), ("assistant_response", "Reading it."),
        ("tool_use", "`./a.py`\n\n```text\nx = 1\n```"), ("assistant_response", "Done.")]
    assert rows[3]["tool_name"] == "Read" and rows[1]["model"] == "claude-opus-4-1"
    assert per["session_id"] == "0b1c2d3e-aaaa-bbbb-cccc-123456789abc" and rows[0]["turn_id"].startswith("0b1c2d3e")
    # What SpecStory rendered marks no author: the developer's, unless the text shows a harness's (2026-10-05).
    assert per["typed"] == 1 and rows[0]["timestamp"] is not None


def test_specstory_a_user_turn_the_export_kept_empty_is_the_developers():
    """SpecStory keeps the turn of what the developer sent but not its content (pasted output, an image): typed, with
    no text (1,734 rows of version 2026-10-04 read as the harness's); a turn holding only the harness's markup stays
    the harness's."""
    text = ("_**User**_\n\nthe build fails\n\n---\n\n_**Assistant**_\n\nRun it again and paste the output.\n\n---\n\n"
            "_**User**_\n\n\n\n---\n\n_**Assistant**_\n\nYour error message is: module not found.\n\n---\n\n"
            "_**User**_\n\n<!-- a comment the app left -->\n\n---\n\n_**Assistant**_\n\nDone.\n\n---\n")
    rows, per = _md("specstory", ".specstory/history/2026-01-02_03-04Z-fix.md", text, agent="Cursor")
    assert [(r["content"], r["typed_by_developer"], r["actor"], r["kind"]) for r in rows if r["role"] == "user"] == [
        ("the build fails", True, "developer", "text"), ("", True, "developer", "text"),
        ("<!-- a comment the app left -->", False, "harness", "notice")]
    assert per["typed"] == 2



def test_specstory_a_calls_summary_says_what_it_did():
    """112,079 calls said what they read or edited in the summary alone, and had empty content (2026-10-05)."""
    text = ("_**Agent (gpt-5 2026-01-02 03:05Z)**_\n\n"
            '<tool-use data-tool-type="read" data-tool-name="read_file_v2"><details><summary>Tool use: **read_file_v2** • '
            "Read file: src/a.ts</summary></details></tool-use>\n\n"
            '<tool-use data-tool-type="grep" data-tool-name="grep"><details><summary>Tool use: **grep** • Grep for '
            "&quot;x&quot;</summary>\n\nsrc/a.ts:1\n</details></tool-use>\n\n"
            '<tool-use data-tool-type="write" data-tool-name="edit_file_v2"><details><summary>Tool use: **edit_file_v2**'
            "</summary></details></tool-use>\n")
    rows, _ = _md("specstory", ".specstory/history/2026-01-02_03-04Z-a.md", text, agent="Cursor")
    assert [(r["tool_name"], r["content"]) for r in rows] == [
        ("read_file_v2", "Read file: src/a.ts"), ("grep", 'Grep for "x"\n\nsrc/a.ts:1'), ("edit_file_v2", "")]


def test_specstory_an_older_files_tool_calls_are_calls():
    """Older files render a call with no <tool-use> and no tool name, as a `---` piece of its own (4,790 files)."""
    text = ("_**User**_\n\nfix the bug\n\n---\n\n_**Assistant**_\n\nLet me look.\n\n---\n\n<details>\n"
            '            <summary>Searched codebase &quot;parse&quot; • **2** results</summary>\n        \n'
            "| File | Lines |\n|------|-------|\n| `src/a.ts` | L1-50 |\n\n</details>\n\n---\n\n"
            "Read file: src/a.ts\n\n---\n\nRead file: src/b.ts\n\n---\n\n"
            "The bug is on line 3.\nRead file: is what I ran.\n\n```typescript\nconst x = 1;\n```\n\n---\n\n"
            "_**Assistant**_\n\nOne.\n\n---\n\nTwo.\n\n---\n")
    rows, _ = _md("specstory", ".specstory/history/2025-03-01_10-00-fix.md", text, agent=None)
    assert [(r["turn_type"], r["tool_name"], r["content"]) for r in rows] == [
        ("user_prompt", None, "fix the bug"), ("assistant_response", None, "Let me look."),
        ("tool_use", None, 'Searched codebase "parse" • **2** results\n\n| File | Lines |\n|------|-------|\n| `src/a.ts` | L1-50 |'),
        ("tool_use", None, "Read file: src/a.ts"), ("tool_use", None, "Read file: src/b.ts"),
        ("assistant_response", None, "The bug is on line 3.\nRead file: is what I ran.\n\n```typescript\nconst x = 1;\n```"),
        ("assistant_response", None, "One.\n\n---\n\nTwo.")]   # text alone stays one row, as in newer files
    assert [r["content_block_index"] for r in rows[1:6]] == [0, 1, 2, 3, 4]
    assert {(r["actor"], r["typed_by_developer"], r["kind"], r["category"]) for r in rows if r["turn_type"] == "tool_use"} == {
        ("agent", False, "tool_call", None)}


def test_specstory_an_intermediate_versions_calls_are_named():
    """An intermediate version writes `Tool use: **name**` over a call's rendering, outside any <tool-use> block
    (130,221 pieces); some versions open a piece with an empty `_****_` mark."""
    text = ("_**Assistant**_\n\n_****_\n\nI'll check.\n\n---\n\nTool use: **search_replace**\n\n<details><summary>Edit "
            "file: src/a.ts</summary>\n\n```diff\n-a\n+b\n```\n\n</details>\n\n---\n\nTool use: **read_file**\n\n"
            "Read file: src/a.ts\n\n---\n\n_****_\n\nTool use: **run_terminal_cmd**\n\n```bash\nnpm test\n```\n\n```\nok\n```\n"
            "Tool use: **grep**\n\n---\n\n_****_\n\n---\n\n_**Agent (claude-sonnet-4 2026-01-02 03:05Z)**_\n\n"
            "Tool use: **Bash**\n\n`ls`\n\n---\n\nTool use: **Read**\n\n{\"file_path\": \"/w/a.py\", \"limit\": 50}\n\n---\n\n"
            "Tool use: **Bash**\n\n{\"command\": \"git status\", \"description\": \"see\"}\n\n---\n\n"
            "Read file: is how it starts.\n\n---\n\n<details><summary>Notes</summary>"
            "mine</details>\n\n---\n\n"
            '<tool-use data-tool-type="read" data-tool-name="Read"><details><summary>Tool use: **Read**</summary>\n`./b.py`\n')
    rows, _ = _md("specstory", ".specstory/history/2025-09-01_10-00-x.md", text, agent=None)
    assert [(r["turn_type"], r["tool_name"], r["content"], r["command"]) for r in rows] == [
        ("assistant_response", None, "I'll check.", None),
        ("tool_use", "search_replace", "Edit file: src/a.ts\n\n```diff\n-a\n+b\n```", None),
        ("tool_use", "read_file", "Read file: src/a.ts", None),
        ("tool_use", "run_terminal_cmd", "```bash\nnpm test\n```\n\n```\nok\n```", "npm test"),
        ("tool_use", "grep", "", None),
        ("tool_use", "Bash", "`ls`", "ls"),
        ("tool_use", "Read", '{"file_path": "/w/a.py", "limit": 50}', None),
        ("tool_use", "Bash", '{"command": "git status", "description": "see"}', "git status"),
        # In an Agent block, a bare rendering, a `Read file:` line and a <tool-use> never closed stay the agent's text
        ("assistant_response", None, "Read file: is how it starts.\n\n---\n\n<details><summary>Notes</summary>mine</details>"
         "\n\n---\n\n" '<tool-use data-tool-type="read" data-tool-name="Read"><details><summary>Tool use: **Read**</summary>\n`./b.py`', None)]
    assert rows[3]["bash_category"] == "package manager" and rows[1]["category"] == "Action" and rows[2]["category"] == "Research"
    assert rows[6]["file_path"] == "/w/a.py" and rows[6]["tool_input_json"] == '{"file_path": "/w/a.py", "limit": 50}'
    assert rows[7]["bash_category"] == "git" and rows[5]["tool_input_json"] is None

def test_claude_code_a_notice_marked_the_harnesss_is_a_notice_by_its_text():
    """Claude Code's own marks (origin, isMeta) said who wrote 28,546 task notifications, but their kind stayed `text`
    while an unmarked one's, and every other format's, read `notice` (2026-10-05)."""
    note = "<task-notification>\n<task-id>b1</task-id>\n<summary>done</summary>\n</task-notification>"
    lines = [{"type": "user", "uuid": "u1", "sessionId": "s", "message": {"role": "user", "content": "fix it"}},
             {"type": "user", "uuid": "u2", "sessionId": "s", "origin": {"kind": "task-notification"},
              "message": {"role": "user", "content": note}},
             {"type": "user", "uuid": "u3", "sessionId": "s", "message": {"role": "user", "content": note}},
             {"type": "user", "uuid": "u4", "sessionId": "s", "origin": {"kind": "auto-continuation"},
              "message": {"role": "user", "content": "Continue from where you left off."}},
             {"type": "user", "uuid": "u5", "sessionId": "s", "isMeta": True,
              "message": {"role": "user", "content": "Caveat: The messages below were generated by the user while running local commands."}},
             {"type": "user", "uuid": "u6", "sessionId": "s", "origin": {"kind": "task-notification"},
              "message": {"role": "user", "content": "[Request interrupted by user]"}},
             {"type": "user", "uuid": "u7", "sessionId": "s", "origin": {"kind": "task-notification"},
              "message": {"role": "user", "content": "<command-message>loop</command-message>\n<command-name>/loop</command-name>"}}]
    rows, _ = run("Claude Code", lines)
    assert [(r["actor"], r["typed_by_developer"], r["kind"]) for r in rows] == [
        ("developer", True, "text"), ("harness", False, "notice"), ("harness", False, "notice"),
        ("harness", False, "text"),       # a harness's prompt with no notice's lead stays text
        ("harness", False, "notice"),     # isMeta: the same
        ("harness", False, "text"),       # an interrupt's lead is not a notice's
        ("harness", False, "text")]       # nor a command's


def test_typed_rows_take_their_pushback_label_by_session_and_turn(tmp_path, monkeypatch):
    """gpt-6-luna's reading of each typed message (2026-10-05), joined by (session_key, turn_number): typed rows
    only, none without a labels file, and no column in the subagents' table."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    from collect import conv_build

    text = ("_**User**_\n\nfix the bug\n\n---\n\n_**Agent (m)**_\n\nDone.\n\n---\n\n"
            "_**User**_\n\nthat's wrong, revert it\n\n---\n\n")
    monkeypatch.delenv("ERRATA_PUSHBACK_LABELS", raising=False)
    monkeypatch.setattr(conv_build, "_PUSHBACK", None)
    rows, _ = _md("specstory", ".specstory/history/x.md", text)
    typed = [r for r in rows if r["typed_by_developer"]]
    assert len(typed) == 2 and all(r["pushback"] is None for r in rows)
    labels = tmp_path / "pushback.parquet"
    agent_row = next(r for r in rows if not r["typed_by_developer"])   # a key for a row nobody typed: not filled
    pq.write_table(pa.table({"session_key": [typed[1]["session_key"], agent_row["session_key"], "other:key"],
                             "turn_number": [typed[1]["turn_number"], agent_row["turn_number"], 0],
                             "pushback": [True, True, True]}), labels)
    monkeypatch.setenv("ERRATA_PUSHBACK_LABELS", str(labels))
    monkeypatch.setattr(conv_build, "_PUSHBACK", None)
    rows, _ = _md("specstory", ".specstory/history/x.md", text)
    assert [r["pushback"] for r in rows if r["typed_by_developer"]] == [None, True]
    assert all(r["pushback"] is None for r in rows if not r["typed_by_developer"])
    assert "pushback" in conv_build.SCHEMA.names and "pushback" not in conv_build.SUB_SCHEMA.names
    assert conv_build.pushback_meta() is None   # the run's record goes into the changelog only where it exists
    (tmp_path / "meta.json").write_text('{"model": "gpt-6-luna", "rows_unread": {"content_filter": 3}}')
    assert conv_build.pushback_meta() == {"model": "gpt-6-luna", "rows_unread": {"content_filter": 3}}
    monkeypatch.setattr(conv_build, "_PUSHBACK", None)


def test_aider_sessions_inputs_notices_and_replies():
    text = ("# aider chat started at 2025-06-03 14:43:55\n\n> Aider v0.84.0\n> Main model: gpt-4o\n\n"
            "#### fix the bug\n#### in a.py\n\nHere is the fix:\n\na.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE\n\n"
            "> Applied edit to a.py\n\n# aider chat started at 2025-07-18 17:00:26\n\n#### thanks\n\nYou're welcome.\n")
    rows, per = _md("aider", ".aider.chat.history.md", text, agent="Aider")
    assert [(r["turn_type"], r["kind"], r["content"]) for r in rows] == [
        ("system_event", "notice", "aider chat started at 2025-06-03 14:43:55"),
        ("system_event", "notice", "Aider v0.84.0\nMain model: gpt-4o"),
        ("user_prompt", "text", "fix the bug\nin a.py"),
        ("assistant_response", "text", "Here is the fix:\n\na.py\n<<<<<<< SEARCH\nx = 1\n=======\nx = 2\n>>>>>>> REPLACE"),
        ("system_event", "notice", "Applied edit to a.py"),
        ("system_event", "notice", "aider chat started at 2025-07-18 17:00:26"),
        ("user_prompt", "text", "thanks"), ("assistant_response", "text", "You're welcome.")]
    # Aider's markdown marks no author: the developer's, unless the text shows a harness's (2026-10-05).
    assert per["typed"] == 2 and per["session_id"] is None and rows[2]["turn_id"] == "aider:o/r:.aider.chat.history.md#2"


BENCH_TASK = ("#### # Introduction  \n####   \n#### Clean up phone numbers.  \n#### ####  \n####   \n"
              "#### Use the above instructions to modify the supplied files: phone_number.h  \n"
              "#### Don't change the names of existing functions or classes, as they may be referenced from other code like unit tests, etc.  \n"
              "#### Only use standard libraries, don't suggest installing any packages.  \n")
BENCH_TEST = ("#### -- The CXX compiler identification is GNU 11.4.0  \n#### error: x  \n#### ####  \n####   \n"
              "#### See the testing errors above.  \n#### The tests are correct, don't try and change them.  \n"
              "#### Fix the code in phone_number.h to resolve the errors.  \n")
EDIT = "phone_number.h\n```\n<<<<<<< SEARCH\nint y;\n=======\nint x;\n>>>>>>> REPLACE\n```\n"


def test_aider_benchmark_prompts_test_output_and_a_failed_edits_notice_are_not_the_developers_or_the_agents():
    """Aider's benchmark script sends each exercise and each failed test run as input, ending in fixed instructions,
    and writes each test run's output into the history unmarked; Aider writes a failed edit's explanation with `> `
    on its first line only (paraemsi/Reducing-violations-paper-data, 92% of Aider's sessions, 2026-10-05)."""
    text = ("# aider chat started at 2025-08-27 13:51:50\n\n> Aider v0.86.2.dev1  \n\n" + BENCH_TASK + "\n" + EDIT +
            "\n> Tokens: 2.3k sent, 1.1k received.  \n> Applied edit to phone_number.h  \n"
            "```\n-- The CXX compiler identification is GNU 11.4.0\nerror: x\n```\n" + BENCH_TEST + "\n" + EDIT +
            "\n> Tokens: 3k sent, 1k received.  \n> # 1 SEARCH/REPLACE block failed to match!\n\n"
            "## SearchReplaceNoExactMatch: This SEARCH block failed to exactly match lines in phone_number.h\n"
            "<<<<<<< SEARCH\nint y;\n=======\nint x;\n>>>>>>> REPLACE\n\n"
            "The SEARCH section must exactly match an existing block of lines including all white space, comments, "
            "indentation, docstrings, etc\n\n# The other 1 SEARCH/REPLACE block were applied successfully.\n"
            "Don't re-send them.\nJust reply with fixed versions of the block above that failed to match.  \n\n"
            "I'll fix the SEARCH block.  \n\n" + EDIT + "\n> Applied edit to phone_number.h  \n```\nAll tests passed\n```\n"
            "# aider chat started at 2025-08-28 09:00:00\n\n#### fix it\n\n```\nprint(1)\n```\n\n"
            "> Applied edit to a.py  \n```\nok\n```\n")
    rows, _ = _md("aider", ".aider.chat.history.md", text, agent="Aider")
    got = [(r["turn_type"], r["actor"], r["typed_by_developer"], r["content"].split("\n")[0].strip()) for r in rows]
    assert got == [
        ("system_event", "harness", False, "aider chat started at 2025-08-27 13:51:50"),
        ("system_event", "harness", False, "Aider v0.86.2.dev1"),
        ("user_prompt", "harness", False, "# Introduction"),
        ("assistant_response", "agent", False, "phone_number.h"),
        ("system_event", "harness", False, "Tokens: 2.3k sent, 1.1k received."),
        ("system_event", "harness", False, "```"),                      # the benchmark's test output
        ("user_prompt", "harness", False, "-- The CXX compiler identification is GNU 11.4.0"),
        ("assistant_response", "agent", False, "phone_number.h"),
        ("system_event", "harness", False, "Tokens: 3k sent, 1k received."),
        ("assistant_response", "agent", False, "I'll fix the SEARCH block."),
        ("system_event", "harness", False, "Applied edit to phone_number.h"),
        ("system_event", "harness", False, "```"),                      # its last test run, ending the session
        ("system_event", "harness", False, "aider chat started at 2025-08-28 09:00:00"),
        ("user_prompt", "developer", True, "fix it"),
        ("assistant_response", "agent", False, "```"),
        ("system_event", "harness", False, "Applied edit to a.py"),
        ("assistant_response", "agent", False, "```")]                 # not a benchmark session: the agent's
    failed = rows[8]["content"]
    assert "# 1 SEARCH/REPLACE block failed to match!" in failed and failed.rstrip().endswith("block above that failed to match.")
    assert "I'll fix" not in failed and rows[9]["raw_line"] == text.split("\n").index("I'll fix the SEARCH block.  ") + 1
    assert rows[5]["content"] == "```\n-- The CXX compiler identification is GNU 11.4.0\nerror: x\n```"
    from collect import formats

    assert formats.aider_benchmark("See the testing errors above.") is None   # both of an ending's sentences


def test_aider_a_bare_fence_is_the_benchmarks_only_after_a_notice_and_before_its_test_prompt_or_the_end():
    head = "# aider chat started at 2025-08-27 13:51:50\n\n" + BENCH_TASK + "\n" + EDIT + "\n> Applied edit to phone_number.h  \n"
    rows, _ = _md("aider", ".aider.chat.history.md", head + "```\nok\n```\n#### thanks  \n", agent="Aider")
    assert [(r["turn_type"], r["typed_by_developer"]) for r in rows][-2:] == [("assistant_response", False), ("user_prompt", True)]
    rows, _ = _md("aider", ".aider.chat.history.md", head + "```\nok\n```\n" + BENCH_TEST + "\n```\nint x;\n```\n", agent="Aider")
    assert [r["turn_type"] for r in rows][-3:] == ["system_event", "user_prompt", "assistant_response"]   # a reply, not after a notice


def test_a_transcript_whose_agent_has_no_reader_is_read_by_its_own_format():
    cc = [{"type": "file-history-snapshot", "messageId": "m0", "snapshot": {}},
          {"type": "user", "uuid": "u1", "sessionId": "s", "message": {"role": "user", "content": "hello"}}]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "full.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in cc) + "\n")
        rows, _ = events.for_session("unknown", [("full.jsonl", path)], dict(SESSION))
        assert [(r["turn_type"], r["typed_by_developer"]) for r in rows] == [("file_snapshot", False), ("user_prompt", True)]
        path.write_text(json.dumps({"sessionId": "s", "projectHash": "h", "messages": [{"id": "1", "type": "user", "content": "hi"}]}))
        rows, _ = events.for_session(None, [("full.jsonl", path)], dict(SESSION))
        assert [r["content"] for r in rows] == ["hi"]
        path.write_text(json.dumps({"type": "session.start", "protocol_version": 1}) + "\n")   # another agent's events
        assert events.for_session("Zed", [("full.jsonl", path)], dict(SESSION)) is None


def test_specstory_lines_are_counted_once():
    text = "".join(f"_**User**_\n\nq{i}\n\n---\n\n_**Agent (m)**_\n\na{i}\n\n---\n\n" for i in range(3))
    rows, _ = _md("specstory", ".specstory/history/x.md", text)
    assert [r["raw_line"] for r in rows] == [1, 7, 13, 19, 25, 31]


def test_pi_a_forked_subagents_task_is_its_parents():
    task = "Task: You are a delegated subagent running from a fork of the parent session. Treat the inherited conversation as reference."
    lines = [{"type": "message", "id": "1", "message": {"role": "user", "content": [{"type": "text", "text": task}]}},
             {"type": "message", "id": "2", "message": {"role": "user", "content": [{"type": "text", "text": "and the tests"}]}}]
    rows, counted = run("Pi", lines)
    assert [(r["typed_by_developer"], r["actor"]) for r in rows] == [(False, "agent"), (True, "developer")]
    assert events.count_typed("Pi", rows) == counted == 1


def test_codex_how_a_session_runs_decides_who_typed():
    def meta(source, originator="codex-tui"):
        return codex_event("session_meta", id="s", source=source, originator=originator)
    ask = [codex_event("event_msg", type="user_message", message="check the logs"),
           codex_event("response_item", type="message", role="user", content=[{"type": "input_text", "text": "check the logs"}])]
    rows, counted = run("Codex", [meta({"subagent": {"thread_spawn": {"parent_thread_id": "p"}}})] + ask)
    assert users(rows) == [("check the logs", False, "notice")] and counted == 0 == events.count_typed("Codex", rows)
    # An `exec` run records no sign of who wrote its prompt: typed, as every message a person may have written is
    # (2026-10-07; a run driven turn by turn holds a person's replies, `はい、ではその方向性で...`).
    rows, counted = run("Codex", [meta("exec", "codex_exec")] + ask)
    assert users(rows) == [("check the logs", True, "text")] and counted == 1 == events.count_typed("Codex", rows)
    review = "## Code review guidelines:\n# Review Guidelines\n\nYou are acting as a reviewer for a proposed code change"
    notes = "# Diff comments:\n\n## User Comment 1\nFile: a.py\nthis is wrong"
    lines = [meta("cli")] + [x for t in (review, notes) for x in (
        codex_event("event_msg", type="user_message", message=t),
        codex_event("response_item", type="message", role="user", content=[{"type": "input_text", "text": t}]))]
    rows, counted = run("Codex", lines)
    assert [(u[1], u[2]) for u in users(rows)] == [(True, "command"), (True, "annotation")] and counted == 2
    assert next(r for r in rows if r["kind"] == "command")["command"] == "/review"


def test_claude_code_run_from_a_program_is_typed():
    """A run through the SDK records no sign of who wrote its prompt: typed, as every message a person may have written
    is (the owner's rule of 2026-10-07; unknown from 2026-10-05)."""
    from tests.test_events import agent, text, user

    lines = [{**user("summarize the repo"), "entrypoint": "sdk-ts"}, agent(text("ok"))]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "full.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        rows, _ = events.for_session("Claude Code", [("full.jsonl", path)], dict(SESSION))
    assert [(r["typed_by_developer"], r["actor"]) for r in rows if r["role"] == "user"] == [(True, "developer")]
    assert events.count_typed("Claude Code", rows) == 1
    # An app built on the SDK, where the developer queued a message while the agent worked: interactive, typed.
    lines += [{"type": "queue-operation", "operation": "enqueue", "content": "and the tests"},
              {**user("and the tests"), "entrypoint": "sdk-ts"}]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "full.jsonl"
        path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
        rows, _ = events.for_session("Claude Code", [("full.jsonl", path)], dict(SESSION))
    assert events.count_typed("Claude Code", rows) == 2


def test_claude_code_an_sdk_runs_single_prompt_recorded_as_queued_is_one_typed_message():
    """A run through the SDK records its prompt as an enqueue and its delivery: one typed message, the delivery its
    copy (an SDK run is typed since 2026-10-07; counted twice before #17's evaluation of 2026-10-05)."""
    from tests.test_events import agent, text, user

    def rows_of(lines):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "full.jsonl"
            path.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
            return events.for_session("Claude Code", [("full.jsonl", path)], dict(SESSION))[0]

    queued = [{"type": "queue-operation", "operation": "enqueue", "content": "summarize the repo"}]
    rows = rows_of(queued + [{**user("summarize the repo"), "entrypoint": "sdk-cli"}, agent(text("ok"))])
    assert [(r["turn_type"], r["typed_by_developer"], r["actor"], r["copy_of"] is not None) for r in rows
            if r["typed_by_developer"] is not False and r["role"] in ("user", "metadata")
            and r["turn_type"] in ("queue_operation", "user_prompt")] == [
        ("queue_operation", True, "developer", False), ("user_prompt", True, "developer", True)]
    assert events.count_typed("Claude Code", rows) == 1
    more = queued + [{**user("summarize the repo"), "entrypoint": "sdk-cli"}, agent(text("ok")),
                     {**user("now the tests"), "entrypoint": "sdk-cli"}]   # a second message: kept
    assert events.count_typed("Claude Code", rows_of(more)) == 2
    cli = queued + [{**user("summarize the repo"), "entrypoint": "cli"}, agent(text("ok"))]   # interactive: kept
    assert events.count_typed("Claude Code", rows_of(cli)) == 1
    never = [{"type": "queue-operation", "operation": "enqueue", "content": "and the docs"},   # never delivered
             {**user("summarize the repo"), "entrypoint": "sdk-cli"}, agent(text("ok"))]
    assert events.count_typed("Claude Code", rows_of(never)) == 2   # two messages: kept


def test_codex_an_oh_my_codex_workers_prompts_are_its_leaders():
    meta = codex_event("session_meta", id="s", source="cli", originator="codex-tui")
    inbox = "Read $OMX_TEAM_STATE_ROOT/team/t1/workers/worker-2/inbox.md, work now, report progress"
    mail = "1 new msg(s): read $OMX_TEAM_STATE_ROOT/team/t1/mailbox/worker-2.json"
    lines = [meta] + [x for t in (inbox, mail, "and run the tests") for x in (
        codex_event("event_msg", type="user_message", message=t),
        codex_event("response_item", type="message", role="user", content=[{"type": "input_text", "text": t}]))]
    rows, _ = run("Codex", lines)
    assert [(u[1], u[2]) for u in users(rows)] == [(False, "notice"), (False, "notice"), (True, "text")]
    assert [r["actor"] for r in rows if r["role"] == "user"] == ["agent", "agent", "developer"]
    assert events.count_typed("Codex", rows) == 1
    rows, _ = run("Codex", [meta, codex_event("event_msg", type="user_message", message=inbox)])   # its event alone
    assert [(u[1], u[2]) for u in users(rows)] == [(False, "notice")]


def test_codex_a_forked_helper_threads_inherited_messages_are_the_developers_copies():
    """A helper thread forked from its parent (multi-agent v2) carries the parent's conversation before its own: the
    developer's messages there are theirs, typed copies (copy_of -1: 890 rows of version 2026-10-04 read as an
    agent's); the app's stay the app's; the thread's own prompts are its parent agent's."""
    def at(n, entry):
        return {**entry, "ordinal": str(n)}

    def msg(role, t):
        return codex_event("response_item", type="message", role=role, content=[{"type": "input_text", "text": t}])
    meta = codex_event("session_meta", id="c", forked_from_id="p", subagent_history_start_ordinal="4",
                       source={"subagent": {"thread_spawn": {"parent_thread_id": "p"}}}, originator="codex-tui")
    lines = [at(0, meta), at(1, msg("user", "# AGENTS.md instructions for /repo\n<INSTRUCTIONS>x</INSTRUCTIONS>")),
             at(2, msg("user", "keep going")), at(3, msg("developer", "<turn_aborted> stopped </turn_aborted>")),
             at(5, msg("user", "Check the build and report"))]
    rows, counted = run("Codex", lines)
    got = [(r["content"][:12], r["typed_by_developer"], r["actor"], r["copy_of"]) for r in rows if r["role"] == "user"]
    assert got == [("# AGENTS.md ", False, "harness", None), ("keep going", True, "developer", -1),
                   ("<turn_aborte", False, "harness", None), ("Check the bu", False, "agent", None)], got
    assert events.count_typed("Codex", rows) == counted == 0   # a copy is never counted again
    unforked = [at(0, codex_event("session_meta", id="c", source={"subagent": {"thread_spawn": {"parent_thread_id": "p"}}})),
                at(1, msg("user", "keep going"))]
    assert users(run("Codex", unforked)[0]) == [("keep going", False, "notice")]   # a thread not forked: its parent's


def test_codex_a_legacy_forks_inherited_messages_are_the_developers_copies():
    """A fork that records no ordinal (history_mode legacy) writes its parent's history in one burst when it is made:
    the developer's messages there are their copies (read as an agent's in version 2026-10-04); the thread's own
    prompts, after the burst, are its parent agent's."""
    def at(ms, entry):
        return {**entry, "timestamp": f"2026-10-01T00:00:{ms // 1000:02d}.{ms % 1000:03d}Z"}

    def msg(role, t):
        return codex_event("response_item", type="message", role=role, content=[{"type": "input_text", "text": t}])

    def typed(t):
        return codex_event("event_msg", type="user_message", message=t)
    meta = codex_event("session_meta", id="c", forked_from_id="p", history_mode="legacy",
                       source={"subagent": {"thread_spawn": {"parent_thread_id": "p"}}}, originator="codex-tui")
    lines = [at(0, meta), at(1, msg("user", "the provider list is too small")), at(2, typed("the provider list is too small")),
             at(3, msg("assistant", "Adding them.")), at(4, msg("user", "<recommended_plugins>\n- Airtable\n</recommended_plugins>")),
             at(5, msg("user", "and the tool calls are missing")), at(6, typed("and the tool calls are missing")),
             at(9000, msg("user", "Review commit 1a2b for regressions")), at(9001, typed("Review commit 1a2b for regressions"))]
    rows, counted = run("Codex", lines)
    got = [(r["content"][:14], r["typed_by_developer"], r["actor"], r["copy_of"]) for r in rows if r["role"] == "user"]
    assert got == [("the provider l", True, "developer", -1), ("<recommended_p", False, "harness", None),
                   ("and the tool c", True, "developer", -1), ("Review commit ", False, "agent", None)], got
    assert events.count_typed("Codex", rows) == counted == 0   # copies are never counted again
    slow = [at(0, meta), at(1, msg("user", "the provider list is too small")), at(4000, msg("user", "Review commit 1a2b"))]
    assert users(run("Codex", slow)[0]) == [("the provider list is too small", True, "text"), ("Review commit 1a2b", False, "notice")]


def test_cursor_a_stagewise_change_request_holds_the_developers_words():
    """The stagewise toolbar sends what the developer typed through Cursor's fix button, as a change request: their
    words are typed (28 rows of version 2026-10-04 read as a button); stagewise's own setup prompt is not."""
    head = ("For the code present, we get this error:\n```\n```\n    Ah, sorry, it wasn't an error. The user has submitted "
            "a change request. Here is the request, please implement it:\n    ```\n\n")
    request = head + "<request>\n  <user_goal>fix nav spacing and logo spacing</user_goal>\n  <url>http://localhost:3000/</url>\n</request>\n```"
    assert events.tell(request, template_anywhere=False) == ("fix nav spacing and logo spacing", True, "developer", "text", None)
    setup = head + "<task>\n  Implement the stagewise dev-tool into an existing web project\n</task>"
    assert events.tell(setup, template_anywhere=False)[1:4] == (False, "developer", "command")
    plain = "For the code present, we get this error:\n```\nx is not defined\n```\nHow can I resolve this?"
    assert events.tell(plain, template_anywhere=False)[1] is False   # the fix button itself: a click
