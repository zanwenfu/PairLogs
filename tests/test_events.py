"""Conversations rows from Claude Code transcripts: SWE-chat's shape, and who typed what by the study's rules.

The cases are errata-bench's checks/study_holds.py section 1, written as raw transcript entries (the study tests them
on SWE-chat's table rows with the transcript beside them; here the transcript is all there is).

    python -m pytest tests/test_events.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from collect import events as E  # noqa: E402

SESSION = {"session_id": "s1", "checkpoint_pk": "o/r#c1", "repo_id": "o/r", "agent": "Claude Code",
           "strategy": "manual", "source": "entire", "session_key": "entire:o/r:s1"}
N = [0]


def _uuid() -> str:
    N[0] += 1
    return f"u{N[0]}"


def user(content, **kw) -> dict:
    return {"type": "user", "uuid": _uuid(), "permissionMode": "default", "message": {"role": "user", "content": content},
            **kw}


def meta(content, **kw) -> dict:
    return {"type": "user", "uuid": _uuid(), "isMeta": True, "message": {"role": "user", "content": content}, **kw}


def agent(*blocks, mid=None, model="claude-x", usage=None) -> dict:
    msg = {"role": "assistant", "id": mid or f"msg_{_uuid()}", "model": model, "content": list(blocks)}
    if usage:
        msg["usage"] = usage
    return {"type": "assistant", "uuid": _uuid(), "message": msg}


def text(t):
    return {"type": "text", "text": t}


def call(name, inp, cid):
    return {"type": "tool_use", "name": name, "input": inp, "id": cid}


def result(cid, out, **kw):
    return user([{"type": "tool_result", "tool_use_id": cid, "content": out, **kw}])


def queued(t, op="enqueue"):
    return {"type": "queue-operation", "operation": op, "content": t}


def run(entries: list[dict], subagents: dict[str, list[dict]] | None = None):
    root = Path(tempfile.mkdtemp())
    (root / "full.jsonl").write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    files = [("full.jsonl", root / "full.jsonl")]
    for name, sub in (subagents or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(e) for e in sub) + "\n")
        files.append((name, path))
    return E.claude_code(files, SESSION)


def by_content(rows):
    return {r["content"]: r for r in rows if r["content"]}


REJECT = ("The user doesn't want to proceed with this tool use. The tool use was rejected (eg. if it was a file edit, the "
          "new_string was NOT written to the file). To tell you how to proceed, the user said:\n")


def test_notices_interruptions_and_compaction_summaries_are_not_typed():
    rows, _ = run([
        user("Fix the login timeout TASK"),
        agent(text("Looking into the timeout."), call("Bash", {"command": "pytest"}, "c1")),
        result("c1", "1 failed, 12 passed"),
        user("<system-reminder>NOTICE</system-reminder>"),
        user("No, the tests still fail REPLY-ONE."),
        user("[Request interrupted by user]"),
        user("This session is being continued from a previous conversation SUMMARY", isCompactSummary=True),
        user("Implement the following plan:\n# Plan"),
        user("<bash-input>ls</bash-input>"),
        user("   "),
    ])
    got = {r["content"][:20]: (r["turn_type"], r["kind"], r["typed_by_developer"]) for r in rows if r["role"] == "user"}
    assert got == {
        "Fix the login timeou": ("user_prompt", "text", True),
        "<system-reminder>NOT": ("system_injected", "notice", False),
        "No, the tests still ": ("user_prompt", "text", True),
        "[Request interrupted": ("user_prompt", "interrupt", False),
        "This session is bein": ("user_prompt", "compaction", False),
        "Implement the follow": ("user_prompt", "plan", False),
        "<bash-input>ls</bash": ("user_prompt", "shell", False),
    }, got
    c = by_content(rows)
    assert c["This session is being continued from a previous conversation SUMMARY"]["is_continuation"] is True
    res = next(r for r in rows if r["turn_type"] == "tool_result")
    assert res["tool_name"] == "Bash" and res["role"] == "tool_result" and res["content"] == "1 failed, 12 passed"
    tool = next(r for r in rows if r["turn_type"] == "tool_use")
    assert tool["command"] == "pytest" and json.loads(tool["tool_input_json"]) == {"command": "pytest"}


def test_skills_teammates_meta_and_scheduled_prompts_are_not_typed():
    rows, _ = run([
        user("Fix it TEAM-DEV", teamName="t"),
        user('<teammate-message teammate_id="w">done</teammate-message>', teamName="t"),
        {"type": "user", "uuid": _uuid(), "teamName": "t", "message": {"role": "user", "content": "from a teammate, plain"}},
        meta("Base directory for this skill: /x # A skill's instructions for the agent"),
        meta("Continue from where you left off."),
        agent(call("ScheduleWakeup", {"prompt": "/loop Check the run log for new progress"}, "w1")),
        user("Check the run log for new progress"),
        queued("Check the run log for new progress"),
        queued("Check the run log for new progress"),
    ])
    typed = [r["content"] for r in rows if r["typed_by_developer"]]
    assert typed == ["Fix it TEAM-DEV"], typed
    c = by_content([r for r in rows if r["role"] == "user"])
    # Filed as SWE-chat files them (user_prompt), with who wrote them in actor and typed_by_developer.
    for t, actor in (('<teammate-message teammate_id="w">done</teammate-message>', "peer"), ("from a teammate, plain", "peer"),
                     ("Check the run log for new progress", "harness")):
        assert (c[t]["turn_type"], c[t]["actor"], c[t]["typed_by_developer"]) == ("user_prompt", actor, False), t
    assert c["Base directory for this skill: /x # A skill's instructions for the agent"]["turn_type"] == "system_injected"
    assert c["Continue from where you left off."]["turn_type"] == "system_injected"


def test_a_refusal_with_words_a_queued_message_and_a_command_are_the_developer_s():
    rows, _ = run([
        user("Fix the login timeout TASK"),
        agent(call("Bash", {"command": "git checkout main"}, "g")),
        result("g", REJECT + "no, not git checkout REJECT-WORDS"),
        agent(text("Understood, using a branch instead.")),
        queued("<task-notification><task-id>x</task-id></task-notification>"),
        queued("wait, use port 8080 QUEUED"),
        user("<command-message>review</command-message>\n<command-name>/review</command-name>\n"
             "<command-args>check the tests</command-args>"),
        result("z", REJECT + "use main NOTE-WORDS\n\nNote: The user's next message may contain a correction."),
        result("u", REJECT + "Plan being refined via Ultraplan, please wait"),
        result("q", "The user doesn't want to proceed with this tool use. The tool use was rejected."),
    ])
    rej = [(r["kind"], r["typed_by_developer"]) for r in rows if r["turn_type"] == "tool_result"]
    assert rej == [("rejection", True), ("rejection", True), ("tool_result", False), ("rejection", False)], rej
    q = [(r["queue_op_subtype"], r["typed_by_developer"]) for r in rows if r["turn_type"] == "queue_operation"]
    assert q == [("task_notification", False), ("user_prompt_enqueued", True)], q
    cmd = next(r for r in rows if r["kind"] == "command")
    assert cmd["command"] == "/review check the tests" and cmd["typed_by_developer"] and cmd["turn_type"] == "user_prompt"


def test_a_queued_message_delivered_later_is_one_message():
    rows, _ = run([
        user("Fix the login timeout TASK"),
        agent(text("working")),
        queued("wait, use port 8080 QUEUED"),
        queued("", op="dequeue"),
        user("wait, use port 8080 QUEUED"),
        queued("/review check"),  # a bare command queued: typed as a command later
        user("<command-name>/review</command-name><command-args>check</command-args>"),
    ])
    q = next(r for r in rows if r["queue_op_subtype"] == "user_prompt_enqueued" and r["content"].startswith("wait"))
    delivered = next(r for r in rows if r["role"] == "user" and r["content"] == "wait, use port 8080 QUEUED")
    assert delivered["copy_of"] == q["turn_number"] and delivered["typed_by_developer"]
    assert next(r for r in rows if r["queue_op_subtype"] == "user_prompt_delivered")["typed_by_developer"] is False
    assert E.typed_messages(rows) == 3   # the task, the queued message once, the command


def test_loops_their_reruns_and_repeated_commands():
    rows, _ = run([
        user("<command-name>/loop</command-name><command-args>10m check CMD-LOOP</command-args>"),
        agent(text("w")),
        user("<command-name>/loop</command-name><command-args>10m check CMD-LOOP</command-args>"),
        agent(text("w")),
        user("<command-name>/commit</command-name>"),
        agent(text("w")),
        user("<command-name>/commit</command-name>"),
        queued("Check the run log for new progress since last check"),
        queued("Check the run log for new progress since last check"),
        queued("a real thought LOOP-TYPED"),
    ])
    cmds = [(r["command"], r["typed_by_developer"]) for r in rows if r["kind"] == "command"]
    assert cmds == [("/loop 10m check CMD-LOOP", True), ("/loop 10m check CMD-LOOP", False), ("/commit", True),
                    ("/commit", True)], cmds
    # A text queued again is no sign of a program (2026-10-07): its first queuing is typed, as every message a person may
    # have written is; the same text queued again is a replay (Claude Code queued "through tmux" 68,941 times in one
    # session of version 2026-10-04), never counted again.
    q = [(r["content"][:14], r["typed_by_developer"]) for r in rows if r["turn_type"] == "queue_operation"]
    assert q == [("Check the run ", True), ("Check the run ", False), ("a real thought", True)], q


def test_a_swarm_coordinators_handoff_command_is_not_typed():
    """A swarm's coordinator hands work to an agent through a command: its reader makes a command the developer's, the
    text rules give this one to a program (53 rows of version 2026-10-04, corrected by hand; a fresh read made them
    typed again until 2026-10-07). The developer's own commands stay typed."""
    rows, _ = run([
        user("<command-message>swarm-persona</command-message>\n<command-name>/swarm-persona</command-name>\n"
             "<command-args>You have new handoff mail. If idle, run ready_for_next.sh.</command-args>"),
        agent(text("w")),
        user("<command-name>/compact</command-name>"),
    ])
    cmds = [((r["command"] or "")[:14], r["typed_by_developer"], r["actor"]) for r in rows if r["kind"] == "command"]
    assert cmds == [("/swarm-persona", False, "harness"), ("/compact", True, "developer")], cmds


def test_images_are_marked_in_the_prompt_as_swe_chat_writes_them():
    img = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0K"}}
    rows, _ = run([user([img]), agent(text("I see")), user([text("and this?"), img])])
    prompts = [(r["content"], r["typed_by_developer"]) for r in rows if r["turn_type"] == "user_prompt"]
    assert prompts == [("[Image: image/png]", True), ("and this?\n[Image: image/png]", True)], prompts


def test_queued_ui_commands_notices_and_synthetic_messages():
    rows, _ = run([
        user("Fix it"),
        queued("/model"),
        queued("◇ ultraplan Starting Claude Code on the web"),
        queued("Ultraplan approved in browser. Here is the plan: ULTRA-PLAN"),
        agent(text("Prompt is too long"), model="<synthetic>"),
    ])
    q = [(r["kind"], r["typed_by_developer"]) for r in rows if r["turn_type"] == "queue_operation"]
    assert q == [("queued", False), ("queued", False), ("plan", False)], q
    syn = next(r for r in rows if r["model"] == "<synthetic>")
    assert syn["turn_type"] == "assistant_response" and syn["actor"] == "harness" and syn["kind"] == "notice"


def test_swe_chat_numbering_tokens_and_cuts():
    big = "x" * (E.RESULT_CUT + 50)
    m1 = agent(text("Reading."), call("Read", {"file_path": "/a.py"}, "r1"), mid="msg_A",
               usage={"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 3})
    m2 = agent(call("Grep", {"pattern": "TODO"}, "r2"), mid="msg_B", usage={"input_tokens": 7, "output_tokens": 2})
    rows, _ = run([user("Go"), m1, m1, result("r1", big), m2, result("r2", "a.py:3")])
    assert [r["turn_number"] for r in rows] == list(range(len(rows)))
    assert all(r["turn_id"] == f"s1#{r['turn_number']}" for r in rows)
    conv = [(r["turn_type"], r["conversation_turn_number"], r["is_first_turn"]) for r in rows if r["is_conversational"]]
    assert conv == [("user_prompt", 0, True), ("assistant_response", 1, False)], conv
    assert sum(1 for r in rows if r["content"] == "Reading.") == 1   # a message written twice adds nothing
    a = next(r for r in rows if r["content"] == "Reading.")
    assert (a["input_tokens"], a["output_tokens"], a["cache_read_input_tokens"]) == (10, 5, 3)
    g = next(r for r in rows if r["tool_name"] == "Grep" and r["turn_type"] == "tool_use")
    assert (g["input_tokens"], g["pattern"]) == (7, "TODO")   # a message of calls only: on its first call
    rd = next(r for r in rows if r["tool_name"] == "Read" and r["turn_type"] == "tool_use")
    assert rd["file_path"] == "/a.py" and rd["input_tokens"] is None
    cut = next(r for r in rows if r["tool_call_id"] == "r1" and r["turn_type"] == "tool_result")
    assert len(cut["content"]) == E.RESULT_CUT and cut["content_truncated"] and cut["char_count"] == E.RESULT_CUT
    assert all(r[k] == SESSION[k] for r in rows for k in ("session_id", "checkpoint_pk", "repo_id", "agent", "strategy"))


def test_a_subagent_s_turns_go_to_their_own_table():
    rows, subs = run(
        [user("Look into it"), agent(call("Task", {"prompt": "find the bug", "description": "d"}, "toolu_T"))],
        {"subagents/toolu_T/agent-a1.jsonl": [user("find the bug"), agent(text("found it in a.py"))]})
    spawn = next(r for r in rows if r["tool_name"] == "Task")
    assert spawn["orchestration_kind"] == "subagent_spawn"
    assert [(r["task_pk"], r["agent_id"], r["turn_pk"], r["typed_by_developer"], r["actor"]) for r in subs] == [
        ("o/r#c1#toolu_T", "agent-a1", "o/r#c1#toolu_T#0", False, "agent"),
        ("o/r#c1#toolu_T", "agent-a1", "o/r#c1#toolu_T#1", False, "agent")]
    assert E.typed_messages(rows) == 1


def test_a_short_message_typed_twice_while_the_agent_worked_is_typed_both_times():
    # Regression on the study's 112 sessions (2026-10-03): "全部やる" enqueued twice is no scheduled prompt.
    rows, _ = run([user("go"), agent(text("w")), queued("全部やる"), queued("全部やる"), user("全部やる"),
                   agent(text("w")), user("全部やる")])
    assert [r["typed_by_developer"] for r in rows if r["role"] == "user"] == [True, True, True]


def test_turn_types_follow_swe_chat_where_it_is_consistent():
    rows, _ = run([
        user("<local-command-stdout>ok</local-command-stdout>"),
        user("<bash-stdout>files</bash-stdout>"),
        user("<task-notification><task-id>t1</task-id></task-notification>"),
        user("<command-name>/clear</command-name>"),
        user("<command-message>review</command-message>\n<command-name>/review</command-name>"),
        user("This session is being continued from SUMMARY", isCompactSummary=True),
        user("a summary without the usual opening", isCompactSummary=True),
    ])
    got = [(r["content"][:22], r["turn_type"], r["typed_by_developer"]) for r in rows]
    assert got == [("<local-command-stdout>", "system_injected", False), ("<bash-stdout>files</ba", "system_injected", False),
                   ("<task-notification><ta", "user_prompt", False), ("<command-name>/clear</", "user_prompt", True),
                   ("<command-message>revie", "user_prompt", True), ("This session is being ", "user_prompt", False),
                   ("a summary without the ", "user_prompt", False)], got
    assert [r["is_continuation"] for r in rows[-2:]] == [True, False]   # SWE-chat's definition: the opening words


def test_a_background_tasks_notice_is_the_harness_and_pasted_json_is_typed():
    notice = json.dumps({"task_id": "t1", "tool_use_id": "toolu_1", "description": "build it", "task_type": "local_bash"})
    pasted = json.dumps({"log": "tpmjs-conversation-log", "error": "it failed"})
    rows, _ = run([user("go"), agent(text("w")), queued(notice), queued(pasted), user(notice)])
    q = [r for r in rows if r["turn_type"] == "queue_operation"]
    assert [(r["typed_by_developer"], r["actor"], r["kind"], r["queue_op_subtype"]) for r in q] == [
        (False, "harness", "notice", "task_notification"), (True, "developer", "queued", "user_prompt_enqueued")]
    delivered = [r for r in rows if r["role"] == "user" and r["content"] == notice]
    assert [(r["typed_by_developer"], r["actor"], r["kind"]) for r in delivered] == [(False, "harness", "notice")]
    assert E.typed_messages(rows) == 2   # "go" and the pasted JSON


def test_a_queued_plan_is_the_developers_plan_and_its_delivery_a_copy():
    plan = "Ultraplan approved in browser. Here is the plan: do it"
    rows, _ = run([user("go"), agent(text("w")), queued(plan), queued("", op="dequeue"), user(plan)])
    q = next(r for r in rows if r["turn_type"] == "queue_operation" and r["content"] == plan)
    d = next(r for r in rows if r["role"] == "user" and r["content"] == plan)
    assert (q["actor"], q["kind"], q["typed_by_developer"]) == ("developer", "plan", False)
    assert (d["kind"], d["typed_by_developer"], d["copy_of"]) == ("plan", False, q["turn_number"])


def test_a_tool_results_output_is_never_the_entrys_own_text():
    """A Read of a transcript holds command tags: still the tool's result, with its row, and nobody typed it."""
    out = "145\t<command-name>/next</command-name>\n<command-message>next</command-message>"
    rows, _ = run([user("go"), agent(call("Read", {"file_path": "t.jsonl"}, "r1")), result("r1", out),
                   user([{"type": "text", "text": "<command-name>/clear</command-name>"},
                         {"type": "tool_result", "tool_use_id": "r1", "content": "late output"}])])
    assert [(r["turn_type"], r["kind"], r["typed_by_developer"]) for r in rows if r["role"] in ("user", "tool_result")] == [
        ("user_prompt", "text", True), ("tool_result", "tool_result", False),
        ("user_prompt", "command", True), ("tool_result", "tool_result", False)]   # the command's entry keeps its result
    assert next(r for r in rows if r["turn_type"] == "tool_result")["content"] == out
    assert E.typed_messages(rows) == 2


def test_a_compaction_summary_queued_by_compact_is_the_harnesss():
    """#23: `/compact` queues the summary it writes; nobody typed it, while a message queued beside it stays the
    developer's (2 Entire rows of version 2026-10-04 read as typed until fixed by hand)."""
    summary = ("This session is being continued from a previous conversation that ran out of context. The summary "
               "below covers the earlier portion of the conversation.")
    rows, _ = run([user("Fix the login timeout"), agent(text("working")), queued(summary), queued("/compact"),
                   queued("wait, use port 8080")])
    got = [(r["content"][:24], r["typed_by_developer"], r["actor"], r["kind"]) for r in rows
           if r["turn_type"] == "queue_operation"]
    assert got == [(summary[:24], False, "harness", "queued"), ("/compact", False, "harness", "queued"),
                   ("wait, use port 8080", True, "developer", "queued")], got
    assert E.typed_messages(rows) == 2   # the task and the queued message


def test_a_task_notice_that_quotes_a_command_is_a_notice_not_the_command():
    """#23: a background task's notice can hold the command that started it; it is the harness's notice (2 SWE-chat
    v2 rows of version 2026-10-04 read as a typed command until fixed by hand), while a command stays one."""
    notice = ("<task-notification>\n<task-id>a4b0</task-id>\n<status>completed</status>\n<summary>Background command "
              "<command-name>/compound:feedback</command-name> finished</summary>\n</task-notification>")
    rows, _ = run([user("Fix the login timeout"), agent(text("working")), user(notice),
                   user("<command-name>/review</command-name><command-args>check the tests</command-args>")])
    got = [(r["kind"], r["actor"], r["typed_by_developer"], r["command"]) for r in rows if r["role"] == "user"]
    assert got == [("text", "developer", True, None), ("notice", "harness", False, None),
                   ("command", "developer", True, "/review check the tests")], got
    assert E.typed_messages(rows) == 2



def test_a_command_queued_twice_is_typed_each_time():
    """A developer queues the same command again while the agent works (`/compact` 8 times, `/model` 3 times in sessions
    of version 2026-10-04): no sign of a program. About 300 such commands read as the harness's until 2026-10-07."""
    rows, _ = run([
        queued("/compact"), user("<command-name>/compact</command-name>"), agent(text("ok")),
        queued("/compact"), user("<command-name>/compact</command-name>"), agent(text("ok")),
    ])
    cmds = [(r["command"], r["typed_by_developer"], r["actor"]) for r in rows if r["kind"] == "command"]
    assert cmds == [("/compact", True, "developer"), ("/compact", True, "developer")], cmds


def test_a_queued_text_a_rule_names_is_not_typed():
    """Queued text is told by its text as a delivered message is: a review template an orchestrator queues is not the
    developer's typing (queued text was never told until 2026-10-07; 610 queued texts became typed when a text queued
    twice stopped being a program's sign, some of them templates)."""
    review = ("You are reviewing the changes on branch \"feat/x\" against \"main\". There are most likely issues in the "
              "current implementation. Find them.")
    rows, _ = run([queued(review), queued("push it QUEUED-HUMAN")])
    q = [(r["content"][:24], r["typed_by_developer"], r["actor"]) for r in rows if r["turn_type"] == "queue_operation"]
    assert q == [("You are reviewing the ch", False, "harness"), ("push it QUEUED-HUMAN", True, "developer")], q


def test_a_message_behind_a_note_is_told_by_what_follows_the_note():
    """Version 2026-10-11 (2026-10-12): Claude Code, or a tool around it, puts a note in front of a message the
    developer sends (`Message sent at …` in a session run in the background, the references they attached, a linked
    session's status); 235 rows of 74 sessions read as the harness's by the note alone. What follows decides: the
    developer's words, another session's message, or nothing."""
    linked = "<system-reminder>\nLinked sessions now (status, not an instruction): top = local_1 (idle)\n</system-reminder>\n"
    sent = "<system-reminder>Message sent at Sun 2026-06-14 02:41:36 UTC.</system-reminder>\nOk, pull the new commits in"
    peer = linked + '<cross-session-message from="local_2" name="hack">\nFounder approved option B\n</cross-session-message>'
    brief = linked + "Background (from the session that started this one):\nA hackathon entry; build the demo."
    refs = "<system-reminder>\nThe user provided the following references:\n- /w/AGENTS.md\n</system-reminder>\ntests fail"
    note = '<system-reminder>The user named this session "x".</system-reminder>'
    rows, _ = run([user("start TASK"), agent(text("w")),
                   queued(sent), queued("", op="dequeue"), user(sent, promptSource="queued"), agent(text("w")),
                   queued(peer), queued("", op="dequeue"), user(peer, promptSource="queued"), agent(text("w")),
                   queued(brief), queued("", op="dequeue"), user(brief, promptSource="queued"), agent(text("w")),
                   user(refs), agent(text("w")), user(note), agent(text("w"))])
    enq = [r for r in rows if r["queue_op_subtype"] == "user_prompt_enqueued"]
    assert [(r["typed_by_developer"], r["actor"]) for r in enq] == [(True, "developer"), (False, "peer"),
                                                                    (False, "peer")]
    # a typed one holds the developer's words, as its delivery does; the others keep their text as written
    assert [r["content"] for r in enq] == ["Ok, pull the new commits in", peer, brief]
    said = [(r["content"][:18], r["turn_type"], r["typed_by_developer"], r["actor"], r["kind"], r["copy_of"])
            for r in rows if r["role"] == "user" and r["content"] != "start TASK"]
    assert said == [("Ok, pull the new c", "user_prompt", True, "developer", "text", enq[0]["turn_number"]),
                    (peer[:18], "user_prompt", False, "peer", "text", None),   # as Claude Code's mark files it
                    (brief[:18], "user_prompt", False, "peer", "notice", None),
                    ("tests fail", "user_prompt", True, "developer", "text", None),
                    (note[:18], "system_injected", False, "harness", "notice", None)], said
    assert E.typed_messages(rows) == 3   # the task, the message sent (once), the one behind the references
    rows, _ = run([queued("Background (from the session that started this one):\nShip it."), queued("", op="dequeue"),
                   user("Background (from the session that started this one):\nShip it.")])   # no note: the same
    assert [(r["typed_by_developer"], r["actor"]) for r in rows if r["content"]] == [(False, "peer"), (False, "peer")]


def _noted(entries: list[dict]) -> list[dict]:
    """The same entries, a note in front of every message (a time of its own each, as Claude Code writes them)."""
    out = []
    for n, e in enumerate(json.loads(json.dumps(entries))):
        note = f"<system-reminder>Message sent at Sun 2026-06-14 02:{n:02d}:00 UTC.</system-reminder>\n"
        if e.get("type") == "queue-operation" and e.get("content"):
            e["content"] = note + e["content"]
        elif e.get("type") == "user" and isinstance(e["message"]["content"], str):
            e["message"]["content"] = note + e["message"]["content"]
        elif e.get("type") == "user" and not any(b.get("type") == "tool_result" for b in e["message"]["content"]):
            e["message"]["content"] = [text(note)] + e["message"]["content"]   # the note a text block of its own
        out.append(e)
    return out


def test_a_note_in_front_changes_nothing_a_message_is_read_as():
    """The independent review of the change (2026-10-12): with the note in the message's key (its time differs on every
    message), a prompt the agent scheduled, a /loop's firings, a message queued again and a queued command's delivery
    went unrecognised; a plan, a shell input or an interrupt behind a note read as a notice. Each sequence is read
    alike with a note in front of every message and without."""
    wake = "check the run log for new results"
    cases = {
        "scheduled": [user("start"), agent(call("ScheduleWakeup", {"prompt": wake, "delaySeconds": 600}, "c1")),
                      queued(wake), queued("", op="dequeue"), user(wake)],
        "loop": [user("<command-name>/loop</command-name><command-args>5m check the log</command-args>"), agent(text("w")),
                 queued("check the log"), queued("check the log")],
        "queued loop": [user("start"), agent(text("w")), queued("/loop 5m check the log"), agent(text("w")),
                        queued("check the log")],   # a /loop queued, never delivered: its firings are no one's typing
        "again": [user("start"), agent(text("w")), queued("through tmux"), queued("", op="dequeue"), user("through tmux"),
                  agent(text("w")), queued("through tmux"), queued("", op="dequeue"), user("through tmux")],
        "command": [user("start"), agent(text("w")), queued("/review check"), queued("", op="dequeue"),
                    user("<command-name>/review</command-name><command-args>check</command-args>")],
        "plan": [user("start"), agent(text("w")), user("Implement the following plan:\n# Plan\nstep one")],
        "shell": [user("<bash-input>ls</bash-input>"), user("[Request interrupted by user]")],
        "marked delivery": [user("start"), agent(text("w")), queued("Founder approved option B"), queued("", op="dequeue"),
                            user("Founder approved option B", origin={"kind": "peer"})],
        "continued": [user("This session is being continued from a previous conversation that ran out of context."),
                      agent(text("w"))],
        "notice": [user("start"), agent(text("w")), user("<task-notification>\n<task-id>t1</task-id>\n<summary>Background "
                                                        'command "<command-name>/review</command-name>" completed</summary>\n'
                                                        "</task-notification>")],
        "queued plan": [user("start"), agent(text("w")), queued("Implement the following plan:\n# Plan\nstep one"),
                        queued("", op="dequeue"), user("Implement the following plan:\n# Plan\nstep one")],
        "peer": [user("start"), agent(text("w")), queued('<agent-message from="planner">Run the tests</agent-message>'),
                 queued("", op="dequeue"), user('<agent-message from="planner">Run the tests</agent-message>')],
        "steering": [user("start"), agent(text("w")), queued("User steering update: <user_input>make it blue</user_input>"),
                     queued("", op="dequeue"), user("User steering update: <user_input>make it blue</user_input>")],
        "marked loop": [user("<command-name>/loop</command-name><command-args>5m check the log</command-args>",
                             turnOrigin="scheduled"), agent(text("w")), queued("check the log")],
        "image": [user("start"), agent(text("w")), user([{"type": "image", "source": {"media_type": "image/png"}}])],
        "swarm": [user("start"), agent(text("w")),
                  user("<command-message>swarm-persona</command-message> <command-name>/swarm-persona</command-name> "
                       "<command-args>You have new handoff mail. If idle, read it.</command-args>")],
        "marked": [user("start"), agent(text("w")), user("check the log", turnOrigin="scheduled"),
                   meta("Base directory for this skill: /s\n\n# Skill")],
        "queued notices": [user("start"), agent(text("w")),
                           queued('{"task_id": "t1", "tool_use_id": "u1", "description": "build", "task_type": "local_bash"}'),
                           queued("<task-notification>\n<task-id>t2</task-id>\n</task-notification>")],
        "blocks": [user("start"), agent(text("w")), queued("fix the test\n<system-reminder>a.py opened</system-reminder>"),
                   queued("", op="dequeue"), user("fix the test\n<system-reminder>a.py opened</system-reminder>")],
        "goal": [user("start"), agent(text("w")), queued("Goal set: ship it"),
                 meta('A session-scoped Stop hook is now active with condition: "ship it".'), queued("", op="dequeue"),
                 user("Goal set: ship it", origin={"kind": "auto-continuation"})],
    }
    for name, entries in cases.items():
        plain, noted = run(entries)[0], run(_noted(entries))[0]
        read = lambda rows: [(r["role"], r["turn_type"], r["queue_op_subtype"], r["typed_by_developer"], r["actor"],
                              r["kind"], r["copy_of"], r["command"], r["is_continuation"],
                              r["content"] if r["typed_by_developer"] and r["kind"] != "command" else None)
                             for r in rows if r["role"] in ("user", "metadata") and r["content"]]   # a command as written
        assert read(noted) == read(plain), (name, read(plain), read(noted))
        assert E.typed_messages(noted) == E.typed_messages(plain), name
    rows, _ = run(cases["peer"] + [queued("<bash-input>ls</bash-input>"), queued("", op="dequeue"),
                                   user("<bash-input>ls</bash-input>")])
    actors = [(r["turn_type"], r["actor"]) for r in rows if (r["content"] or "").startswith(("<agent-message", "<bash-input>"))]
    assert actors == [("queue_operation", "peer"), ("user_prompt", "peer"), ("queue_operation", "developer"),
                      ("user_prompt", "developer")], actors   # a queued copy's actor is its delivery's
    rows, _ = run(cases["marked delivery"])   # Claude Code marks the delivery another's: the queued copy is no one's
    assert [(r["typed_by_developer"], r["actor"]) for r in rows if r["content"] == "Founder approved option B"] == [
        (False, "peer"), (False, "peer")]
    # A /goal typed while the agent works: Claude Code queues `Goal set: …` and delivers it as its own continuation,
    # the command's only record: the developer's, once (#30); queued again after the queue was emptied, still once.
    goal = lambda rows: [(r["turn_type"], r["typed_by_developer"], r["actor"]) for r in rows if r["content"] == "Goal set: ship it"]
    rows, _ = run(cases["goal"])
    assert goal(rows) == [("queue_operation", True, "developer"), ("user_prompt", False, "harness")], goal(rows)
    assert E.typed_messages(rows) == 2
    rows, _ = run(cases["goal"][:3] + [queued("Goal set: ship it", op="popAll")] + cases["goal"][2:])
    assert goal(rows) == [("queue_operation", True, "developer"), ("queue_operation", False, "harness"),
                          ("queue_operation", False, "harness"), ("user_prompt", False, "harness")], goal(rows)
    assert E.typed_messages(rows) == 2
    rows, _ = run(cases["goal"][:5] + [user("Goal set: ship it", origin={"kind": "peer"})])   # a peer's: no one's typing
    assert goal(rows) == [("queue_operation", False, "peer"), ("user_prompt", False, "peer")], goal(rows)
    rows, _ = run(cases["goal"] + [agent(text("w"))] + cases["goal"][2:])   # delivered, then the same goal typed again
    assert [t for _, t, _ in goal(rows)] == [True, False, True, False] and E.typed_messages(rows) == 3, goal(rows)
    # A format that keeps no queue nor marks (DataClaw's copies): a goal queued twice shows two deliveries at once, typed
    # once; Claude Code's note that the goal's Stop hook is set is no one's typing beside the goal's command (its output
    # before, its delivery next), else the developer's, the goal's only trace.
    from collect import formats
    hook = 'A session-scoped Stop hook is now active with condition: "ship it". Briefly acknowledge the goal.'
    say = {"role": "assistant", "content": "on it"}
    said = lambda *texts: [{"role": "user", "content": t} if isinstance(t, str) else t for t in texts]
    cmd = "<command-name>/goal</command-name>\n<command-message>goal</command-message>\n<command-args>ship it</command-args>"
    for messages, want in (
            (said(hook, "Goal set: ship it", "Goal set: ship it", say, "Goal set: ship it", say),
             [(False, "harness"), (True, "developer"), (False, "harness"), (True, "developer")]),
            (said(cmd, "<local-command-stdout>Goal set: ship it</local-command-stdout>", hook, say),
             [(True, "developer"), (False, "harness"), (False, "harness")]),
            (said(hook, "[Request interrupted by user]", say, "go on", say),
             [(True, "developer"), (False, "developer"), (True, "developer")])):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "c.jsonl"
            f.write_text(json.dumps({"messages": messages}) + "\n")
            formats._OFFSETS.clear()
            rows, _ = formats.dataclaw([(f"{f.name}:1", f)], {"session_key": "k"})
        assert [(r["typed_by_developer"], r["actor"]) for r in rows if r["role"] == "user"] == want, rows
    # A message sent while the agent works, as newer Claude Code records it (#30): an enqueue with no text, then an
    # attachment holding it, read inside the agent's turn; some versions write the attachment alone.
    def attached(prompt, origin="human", at="2026-09-29T21:55:26.064Z"):
        return {"type": "attachment", "uuid": _uuid(), "timestamp": at,
                "attachment": {"type": "queued_command", "prompt": prompt, "commandMode": "prompt",
                               "origin": {"kind": origin} if origin else None, "timestamp": at}}
    bare = lambda at="2026-09-29T21:55:26.065Z": {"type": "queue-operation", "operation": "enqueue", "timestamp": at}
    absorbed = {"type": "queue-operation", "operation": "remove", "reason": "absorbed_mid_turn"}
    qrows = lambda rows: [(r["content"], r["typed_by_developer"], r["actor"]) for r in rows
                          if r["turn_type"] == "queue_operation" and r["content"]]
    rows, _ = run([user("start"), agent(text("w")), bare(), attached([text("Also verify 2263")]), absorbed])
    assert qrows(rows) == [("Also verify 2263", True, "developer")] and E.typed_messages(rows) == 2, qrows(rows)
    assert len([r for r in rows if r["turn_type"] == "queue_operation"]) == 2   # no row added: the enqueue holds it
    rows, _ = run([user("start"), agent(text("w")), bare("2026-09-29T21:55:20.000Z"), attached("Also check the logs"),
                   absorbed])   # an empty enqueue 6 s before is another's: the attachment is a row of its own
    assert [bool(r["content"]) for r in rows if r["turn_type"] == "queue_operation"] == [False, True, False], qrows(rows)
    rows, _ = run([user("start"), agent(text("w")), attached("Don't push yet", origin=None)])   # the attachment alone
    assert qrows(rows) == [("Don't push yet", True, "developer")] and E.typed_messages(rows) == 2, qrows(rows)
    rows, _ = run([user("start"), agent(text("w")), attached("ship it", origin=None), agent(text("w")), user("ship it")])
    assert qrows(rows) == [] and E.typed_messages(rows) == 2   # its words delivered as an entry: that entry is its row
    rows, _ = run([user("start"), agent(text("w")), queued("ship it") | {"timestamp": "2026-09-29T21:55:26.064Z"},
                   attached("ship it")])   # an enqueue holding the text already: the attachment adds nothing
    assert qrows(rows) == [("ship it", True, "developer")] and E.typed_messages(rows) == 2
    rows, _ = run([user("start"), agent(text("w")), attached("Lead here: run the tests", origin="coordinator"),
                   attached("<task-notification>\n<task-id>t1</task-id>\n</task-notification>", origin="task-notification",
                            at="2026-09-29T21:56:00Z"),
                   attached("Goal set: ship it", origin="auto-continuation", at="2026-09-29T21:57:00Z"),
                   attached([text("look"), {"type": "image", "source": {"media_type": "image/png"}}], at="2026-09-29T21:58:00Z")])
    assert [(t, a) for _, t, a in qrows(rows)] == [(False, "peer"), (False, "harness"), (True, "developer"), (True, "developer")]
    assert qrows(rows)[3][0] == "look\n[Image: image/png]" and E.typed_messages(rows) == 3, qrows(rows)
    rows, _ = run([queued('<agent-message from="planner">Run the tests</agent-message>')])   # never delivered: by its text
    assert [r["actor"] for r in rows if r["turn_type"] == "queue_operation"] == ["peer"]
    rows, _ = run([user("<system-reminder>Last: <command-name>/review</command-name></system-reminder>")])
    assert [(r["typed_by_developer"], r["kind"]) for r in rows if r["role"] == "user"] == [(False, "notice")]   # a note alone
    rows, _ = run([user("/loop 5m check the log", turnOrigin="scheduled"), agent(text("w")), queued("check the log")])
    assert [r["typed_by_developer"] for r in rows if r["content"] == "check the log"] == [False]   # a marked /loop written out
    helper = {"subagents/toolu_T/agent-a1.jsonl": [user("Warmup"), agent(text("ready"))]}
    sub = lambda rows: [(r["actor"], r["typed_by_developer"]) for r in rows if r["role"] == "user"]
    assert sub(run([user("start")], helper)[1]) == sub(run([user("start")], {k: _noted(v) for k, v in helper.items()})[1])
    rows, _ = run([user("<system-reminder>Last: <command-name>/review</command-name></system-reminder>\nfix it")])
    assert [(r["kind"], r["content"]) for r in rows if r["role"] == "user"] == [("text", "fix it")]   # a tag in a note
    rows, _ = run(cases["marked loop"])   # a /loop Claude Code runs again still names its prompt: no one types a firing
    assert [r["typed_by_developer"] for r in rows if r["content"] == "check the log"] == [False]
    note = "<system-reminder>Message sent at Sun 2026-06-14 02:41:36 UTC.</system-reminder>"
    rows, _ = run([user([text(note), text("ship it")])])   # the note a text block of its own
    assert [(r["typed_by_developer"], r["turn_type"]) for r in rows if r["role"] == "user"] == [(True, "user_prompt")]
    rows, _ = run(cases["blocks"])   # a queued message holds the developer's words as its delivery does
    assert [r["content"] for r in rows if r["typed_by_developer"]][-2:] == ["fix the test", "fix the test"]


def test_what_follows_the_notes_found_in_linear_time():
    after = E._after_reminders
    assert after("<system-reminder>a</system-reminder>\n <system-reminder>b</system-reminder> go") == "go"
    for t in ("<system-reminder>a</system-reminder>", "<system-reminder>never closed, then words", "plain words"):
        assert after(t) == t   # nothing after the notes, a note never closed, no note: the text as it is
    big = "<system-reminder>x</system-reminder>" * 100000 + "<system-reminder>" + "y" * 500000
    t0 = time.time()
    assert after(big) == big and after(big[:-500017] + "words") == "words"
    assert time.time() - t0 < 2.0
