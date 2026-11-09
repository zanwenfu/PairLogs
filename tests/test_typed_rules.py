"""errata-data#18: text a tool, an IDE, a script or another agent wrote, read as not typed in every format (read over
version 2026-10-04's typed rows by their openings, 2026-10-06); what a person wrote, alike at a glance, stays typed."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from collect import events as E  # noqa: E402

BUTTON, PLAN = (False, "developer", "command"), (False, "developer", "plan")
HARNESS, AGENT, PEER = (False, "harness", "notice"), (False, "agent", "notice"), (False, "peer", "notice")
TYPED = (True, "developer", "text")


def told(text: str) -> tuple:
    _, typed, actor, kind, _ = E.tell(text)
    return typed, actor, kind


def test_buttons_and_dialog_answers_are_the_developers_choice_not_typing():
    for text in ("@agent Try Again", '@agent Continue: "Continue to iterate?"', '@agent 继续: "是否继续迭代？"', "@agent 重试",
                 "@agent Intentar de nuevo", "@agent Spróbuj ponownie", '@agent Enable: "Enable GPT-5 (Preview) for all clients"',
                 '@agent Pause: "Continue to iterate?"',
                 "@agent Try Again <!-- specstory-relocated: processed=2026-05-18T22:21:17Z source=cv-builder -->",
                 "For the code present, we get this error:\n```\nType 'string' is not assignable\n```\nHow can I resolve this?",
                 "Resolve this merge conflict @Branch",
                 "Add file to the chat? (Y)es/(N)o/(A)ll/(S)kip all/(D)on't ask again [Yes]: all",
                 "Run shell command? (Y)es/(N)o/(S)kip all/(D)on't ask again [Yes]: y"):
        assert told(text) == BUTTON, text


def test_a_button_with_many_comments_after_it_is_told_in_linear_time():
    """The independent review of 2026-10-12: the button pattern skipped comments itself and took exponential time on a
    button followed by many comments and a word (2.5 s at 25 comments); the comments now come off first."""
    t0 = time.time()
    assert told("@agent Try " + "<!-- -->" * 26 + " x") == TYPED
    assert told("@agent Try Again" + " <!-- relocated -->" * 26) == BUTTON
    assert time.time() - t0 < 0.5
    for text in ("<!-- x -->@agent Try Again", "@agent Try<!-- x --> Again"):   # comments elsewhere: as before, typed
        assert told(text) == TYPED, text


def test_a_plan_accepted_with_a_click():
    for text in ("Implement the plan.", "Start implementation",
                 "Implement the plan as specified, it is attached for your reference. Do NOT edit the plan file itself."):
        assert told(text) == PLAN, text


def test_harness_ide_hook_and_script_text():
    for text in ("System: Please continue.", "File editing session",
                 "Briefly inform the user about the task result and perform any follow-up actions (if needed).",
                 "Please analyze this codebase and create a CLAUDE.md file, which will be given to future instances of "
                 "Claude Code to operate in this repository.",
                 "Analyze this codebase to generate or update `.github/copilot-instructions.md` for guiding AI coding agents.",
                 "Review the current code changes (staged, unstaged, and untracked files) and provide prioritized findings.",
                 "VERIFY GATE: substantive edits this turn have NOT been independently verified. A clean compile is not.",
                 "[ROUGHNECK:FULL] Respond in fragments and bullets.",
                 "IMPORTANT: After completing the task below, you MUST output a JSON object in a ```json code fence",
                 "Review this change for security vulnerabilities. Changed files (you may Read these and any other "
                 "file in the repo):\n- crates/core/src/lib.rs",
                 "Analyze these staged git changes and output ONLY a single-line conventional commit message",
                 "You are an independent heavy-review checker. Review one focused dimension of the executed patch.",
                 "Fix the failing CI actions. I've attached the failure logs.",
                 "Stage 1 review assessment for desloppify issue #559. Work in /Users/u/Documents/desloppify.",
                 "Let's work on that. Use this prompt when the user wants execution from the available context.",
                 "# Spec iterate\n\nI added my thoughts, decisions and comments to the specs. They are tagged with \"->\".",
                 "Commit & Push\n\nGoal: Commit current work with meaningful commit boundaries and push cleanly.",
                 "[delegate worktree]\nRun all file edits, tests, and git commands inside this worktree: /w"):
        assert told(text) == HARNESS, text


def test_another_agents_or_sessions_prompt():
    for text in ("The following is the Codex agent history whose request action you are assessing.",
                 "# Task Tool Invocation\n\nSubagent type: worker\nTask description: Fix the review comments",
                 "Task: [Read from: /a/plan.md] You are a delegated subagent running from a fresh context."):
        assert told(text) == AGENT, text
    relayed = ('Another Claude session sent a message:\n<teammate-message teammate_id="drift">\n{"type":'
               '"idle_notification"}\n</teammate-message>')
    assert told(relayed) == PEER


def test_an_orchestrators_relayed_message_and_a_team_tools_report_are_not_typed():
    """Version 2026-10-11's new typed rows, read by hand (2026-10-12): 255 rows of an orchestrator's messages to its
    workers and 20 of a team tool's relayed reports had been read as typed."""
    for text, who in (("[from neatlogs-hack-8] From the orchestrator. PR #101 is merged; build it.", PEER),
                      ("[from locke-1] Orchestrator: the user will run 'neon login'. Wait.", PEER),
                      ("[from neatlogs-hack-8] Probe result accepted; build it.", PEER),
                      ("[bb system]\n\nw1 completed:\n\nW1 execution is complete.", HARNESS)):
        assert told(text) == who, text
    for text in ("[from me] fix the bug", "[from the docs] see section 2", "fix what [from locke-1] said",
                 "[from locke-1]no space after it"):   # a developer's own brackets
        assert told(text) == TYPED, text


def test_an_orchestrators_and_a_runners_own_notices_are_not_typed():
    """Version 2026-10-11 read by an independent review (2026-10-12): other agents' messages and an orchestrator's
    notices to its workers still read as typed, each at the message's start."""
    for text in ("--- Orchestration Messages (2) ---\n──── From: TERM_0E55 (heartbeat) ────\nSubject: alive",
                 "Approved from the orchestrator. Keep CONTRIBUTING.md as the entry point.",
                 "Parent check-in (ENT-2766): you went idle with an unresolved blocker. Please resume.",
                 "Mid-run steering from the parent orchestrator:\n\nFix the reported Prettier issue.",
                 "Queued follow-up from the parent orchestrator:\n\nContinue.",
                 '8781-4a65-801c-15b0bf1ee922">\n[from locke-1] Orchestrator, Entire setup: do not commit those files.'):
        assert told(text) == PEER, text
    for text in ('There are merge conflicts on PR #15 "Wire budget" (ao/x → main). Rebase onto the base branch.',
                 'The following 1 unresolved review comment(s) are on PR #2 "docs" (ao/y → main) as of just now.',
                 "AO transferred the previous agent's context in hidden system instructions. Continue a clear action.",
                 "Reports since your previous turn:\n\n[done] [M0 scaffold](ao://sessions/locke/locke-5)",
                 "Implement this single task in the current worktree. Commit the completed changes on the branch."):
        assert told(text) == HARNESS, text
    for role in ("Worker", "Critic", "Promote"):
        assert told(f"You are the {role} for one harness DAG node.\nWork this node only.") == AGENT, role
    dialog = "Your earlier AskUserQuestion tool call has finished. Its result:\n\n"
    asked = {"questions": [{"question": "Which scope?", "options": [{"label": "Slice 1"}, {"label": "Slice 2"}]}]}
    assert told(dialog + json.dumps({**asked, "answers": {"Which scope?": "Slice 1"}})) == BUTTON   # a choice
    for answers in ({"Which scope?": "1 with caveat, see my notes"}, {}):   # their own words, or no answer: typed
        assert told(dialog + json.dumps({**asked, "answers": answers, "response": "x"})) == TYPED, answers
    assert told(dialog + "{not json") == TYPED
    for text in ("I have added the env variables to .env.local\n\nReports since your previous turn:\n\n[done] [M0]",
                 "in two minutes remind me to turn off the fire\nAgent error: Agent exited with code 1:",
                 "Agent error: Agent exited with code 1: Error: --output-format=stream-json requires --verbose",  # pasted
                 "Coordinator: please check the build", "There are merge conflicts in my branch, help",
                 "I approved it from the orchestrator UI", "The following 2 tests fail on PR #3, fix them"):
        assert told(text) == TYPED, text


def test_a_swarms_handoff_through_a_command_is_not_typed_and_a_command_is():
    handoff = ("<command-message>swarm-persona</command-message> <command-name>/swarm-persona</command-name> "
               "<command-args>You have new handoff mail. If idle, read it.</command-args>")
    _, typed, actor, kind, command = E.tell(handoff)
    assert (typed, actor, kind, command.split()[0]) == (False, "harness", "command", "/swarm-persona")
    _, typed, actor, kind, _ = E.tell("<command-name>/review</command-name> <command-args>check the tests</command-args>")
    assert (typed, actor, kind) == (True, "developer", "command")


def test_what_a_person_wrote_stays_typed():
    for text in ("@agent-astronomy-expert Review the work being done in this repo",
                 "@agents/claude/skills/commit/SKILL.md 에서 references 파일을 참고한다고 하였는데",
                 "@agent please refactor the parser so that it handles nested quotes",
                 "Implement the plan. Also add tests for the parser.", "Start implementation of the cache layer",
                 "Stage 2 of the migration is done, continue", "Let's work on that.",
                 "Review this change for security vulnerabilities.", "Continue from where you left off.",
                 "Fix the failing CI actions", "System: please continue with the refactor"):
        assert told(text) == TYPED, text


def test_codexs_attached_files_header_keeps_the_developers_request():
    text = ("# Files mentioned by the user:\n\n## shot.png: /var/folders/vf/T/shot.png\n\n## My request for Codex:\n"
            "这个图你认为没问题？")
    assert E.unwrap(text) == "这个图你认为没问题？"
    assert E.tell(text)[:2] == ("这个图你认为没问题？", True)
    # a long paste Codex moved into an attachment: no words after the header, the message is still the developer's
    pasted = ("# Files mentioned by the user:\n\n## Fix only the Task 1 review findings on the existing branch: codex/…: "
              "C:\\Users\\u\\.codex/attachments/7/pasted-text.txt\n\nThe attached pasted text file(s) contain the user's "
              "request. Read and act on that content.\n\n## My request for Codex:")
    assert E.tell(pasted)[:4] == (pasted, True, "developer", "text")


def test_cursors_picked_element_is_taken_off_the_developers_words_stay():
    text = ("[Image]\n\n\n@\n```browser_element\nThe user selected this node in the browser preview (blue outline).\n\n"
            "tag: ul\ndom_path: div > ul\n```\n ollama option vẫn chưa chọn được ?")
    got = E.unwrap(text)
    assert "browser_element" not in got and "dom_path" not in got and got.endswith("ollama option vẫn chưa chọn được ?")
    assert E.tell(text)[1] is True
    # never closed: to the end; and many in a row, in linear time
    assert E.unwrap("look @\n```browser_element\ntag: ul") == "look @"
    big = "x @\n```browser_element\ntag: div\n```\n" * 20000 + "```browser_element\n" + "y" * 200000
    t0 = time.time()
    assert E.unwrap(big).count("x @") == 20000
    assert time.time() - t0 < 2.0


def test_a_scripts_bots_and_orchestrators_prompts_sent_into_many_sessions_are_not_typed():
    # read over version 2026-11-01's typed openings shared by 10+ sessions (2026-11-02); made-up text after each lead
    for text in ("You are analyzing a code repository for potential issues. The repository files are available in /w.",
                 "Review the current branch changes and report actionable findings. Prioritize correctness.",
                 "Work on Linear issue AB-1: <issue identifier=\"AB-1\"> <title>Guard the teardown</title>",
                 "# Batch translation\n\nTranslate the VALUES of this JSON object from English to Greek.",
                 '"# Context\n\nYour name is **Ana**, a Maestro-managed AI agent. You are executing tasks',
                 '"[delegate worktree]\nRun all file edits, tests, and git commands inside this worktree: /w',
                 "[roster setup=E task=F2] HACKATHON GUARDRAILS (non-negotiable): one task only.",
                 "# Independent assessment\n\nAssess the delivery at `$STAMPED_JUDGE_TARGET` using the assessor.",
                 "Execute the BMAD dev-story workflow for story 4.8. READ this skill first: .claude/skills/x/SKILL.md",
                 "Requested change: Move the exam to day 2.\n\nEditable files:\n- solver.py",
                 "0a. Study `specs/*` with up to 250 parallel subagents to learn the application specifications.",
                 "/review /pr-review-toolkit:review-pr\nScope: review the commits unique to this branch vs origin/HEAD",
                 "Review ForgeMark changes on branch \"a\" against \"main\" for concrete, actionable defects caused by "
                 "the diff.",
                 "You are one of 10 workers doing manual cleanup review for v4 data.",
                 "Summarize the task tool output above and continue with your task.",
                 "Read task details in .agent-task.md and follow CLAUDE.md."):
        assert told(text) == HARNESS, text
    assert told("Task: [Read from: /a/plan.md] You are reviving a previous subagent conversation.") == AGENT


def test_a_person_s_words_alike_at_a_glance_to_those_prompts_stay_typed():
    for text in ("Review the current branch changes", "Work on Linear issue AB-1 next, it blocks the release",
                 "Requested change: rename the flag to --dry-run", "You are working in /tmp/x, keep it there",
                 "Review the diff for concrete defects", "/review", "can you review the changes in this branch",
                 "Continue from where you left off.", "\"quoted words\" are what I meant"):
        assert told(text) == TYPED, text


def test_a_browser_instruction_and_a_side_sessions_event_are_the_harnesss_blocks():
    block = "<browser_instruction># Claude in Chrome browser automation\nUse the tools.</browser_instruction>"
    assert told(block) == HARNESS
    assert E.tell(block + "\n\nopen the settings page")[:2] == ("open the settings page", True)
    event = "<side-sessions-event>None of the sessions you started is working now.</side-sessions-event>"
    assert told(event) == HARNESS
