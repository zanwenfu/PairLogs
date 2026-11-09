"""Conversations: SWE-chat's `conversations` table, for every source (decision 55).

The user chose to mirror SWE-chat's table so the research group's code works on every source alike. Its columns,
names and row types follow SWE-chat v2's dataset card (SALT-NLP/SWE-chat, "conversations.parquet", read 2026-10-03):
one row per content block (a prompt, a response's text, a thinking step, a tool call, a tool result, a metadata
event), `turn_id = {session_id}#{turn_number}`, `role` in user/assistant/tool_use/tool_result/metadata, the same
`turn_type` values, token counts on a response's text row (or its first tool call), `tool_input_json`, `file_path`,
`command`, `pattern` drawn from a call's input, `queue_op_subtype` for queue operations. A subagent's turns go to
their own table, `conversations_subagents`, keyed by `task_pk = {checkpoint_pk}#{tool_use_id}`, as SWE-chat does.

Taken from SWE-chat, never made here (no model is called, and no rule of SWE-chat's is invented):
- `prompt_intent`, `prompt_pushback`, `language`: SWE-chat's own labels (the first two written by an LLM), carried
  over to a prompt row whose session and normalised text match a `user_prompt` row of SWE-chat v2's table; null
  otherwise. The card names SWE-chat as their source.
- `category`, `bash_category`: always null; SWE-chat's rules for them are not published.
- `user_id`: always null; SWE-chat resolves the commit author through GitHub's API.
Keys (agreed with the partner session, 2026-10-03): a row's primary key is (`session_key`, `turn_number`);
`turn_id` is `{session_id}#{turn_number}`, or `{session_key}#{turn_number}` for a session with no id of its own
(Aider, SpecStory); `task_pk` is `{checkpoint_pk}#{tool_use_id}`, or `{session_key}#{tool_use_id}` without a
checkpoint. Rows are built for the kept copy of a session only; its duplicates stay in `sessions`.
A tool result's `content` is cut at 100,000 characters, as SWE-chat's own data shows (its card says 10 KB),
with `content_truncated` set; the whole result is in the raw file at `raw_file`, `raw_line`.

Added columns, after SWE-chat's:
- `source`, `session_key`: this collection's source and row (`<source>:<source_id>`).
- `typed_by_developer`: whether the developer typed it, by the errata-bench study's rules for Claude Code
  (src/errata_bench/study/sessions.py: `transcript_prompts`, `row_kinds`, `_command`; corpus/recover.py: `_prompt`);
  null where a format cannot tell. SWE-chat files a third of its `user_prompt` rows from text nobody typed.
- `actor`: `developer`, `agent`, `tool`, `harness` (the coding tool itself) or `peer` (another agent); null where
  the format does not say.
- `kind`: finer than `turn_type`: `text`, `thinking`, `tool_call`, `tool_result`, `command`, `queued`, `plan`,
  `rejection`, `interrupt`, `shell`, `notice`, `compaction`, `progress`, `snapshot`. (Images and documents are
  marked in the text, `[Image: image/png]`, as SWE-chat writes them.)
- `is_error`: a tool result recorded as an error.
- `copy_of`: the `turn_number` of the row this one repeats (a message queued while the agent worked, then
  delivered): count typed messages where it is null.
- `parent_uuid`: the entry this one answers, as the format records it.
- `content_truncated`: a tool result cut at 100,000 characters.
- `raw_file`, `raw_line`: where the row comes from in the session's published files.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path

from .facts import (CURSOR_ACTIONS, CURSOR_AUTOMATIC, CURSOR_PLAN, HARNESS_ORIGINS, HARNESS_TURNS, PEER, SYNTHETIC,
                    TOOL_LOADED, _iso)

# SWE-chat's columns, but `user_id`: the commit author SWE-chat resolved through GitHub's API, which no transcript holds
# (it was null in every row; dropped 2026-10-05).
SWECHAT = ("turn_id", "session_id", "checkpoint_pk", "repo_id", "turn_number", "conversation_turn_number",
           "source_entry_uuid", "response_id", "content_block_index", "role", "turn_type", "is_conversational",
           "content", "model", "timestamp", "input_tokens", "output_tokens", "cache_creation_input_tokens",
           "cache_read_input_tokens", "is_continuation", "is_first_turn", "word_count", "char_count", "tool_name",
           "tool_call_id", "file_path", "command", "pattern", "tool_input_json", "orchestration_kind", "category",
           "bash_category", "queue_op_subtype", "agent", "strategy", "language", "prompt_intent", "prompt_pushback")
ADDED = ("source", "session_key", "typed_by_developer", "actor", "kind", "is_error", "copy_of", "parent_uuid",
         "content_truncated", "raw_file", "raw_line", "pushback")
RESULT_CUT = 100_000  # characters of a tool result kept in `content`, as SWE-chat's data shows (longest 98,969)
COLUMNS = SWECHAT + ADDED
SUBAGENT_KEYS = ("task_pk", "turn_pk", "agent_id")  # conversations_subagents' own keys, before the rest
EVENTS_VERSION = 1  # raised whenever an extractor changes
CONVERSATIONAL = ("user_prompt", "assistant_response")
TOKENS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def row(**kw) -> dict:
    out = {c: None for c in COLUMNS}
    out.update(kw)
    return out


def _json(value) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, sort_keys=True)


# Drawn from a call's input, as SWE-chat's card describes: the file a call reads or changes, a shell command, a search
# pattern; and a call that starts another agent. Grok CLI names a file `target_file` (as Cursor does), Antigravity
# `AbsolutePath` or `TargetFile` and a command `CommandLine`.
FILE_KEYS = ("file_path", "notebook_path", "path", "filename", "filePath", "absolute_path", "target_file", "AbsolutePath",
             "TargetFile")
SHELL_TOOLS = {"bash", "run_command", "shell", "execute", "exec_command", "run_shell_command", "local_shell",
               "run_terminal_command", "execute_bash", "run_terminal_cmd", "run_in_terminal", "run_terminal_command_v2",
               "powershell"}
COMMAND_TOOLS = {"exec", "run"}   # a command when their input holds one (Devin's, DataClaw's, OpenHands'), no shell's
                                  # label: Codex's `exec` input is JavaScript, its `run` a browser's (2026-10-05)
PATTERN_TOOLS = {"grep", "glob", "search_file_content", "find_files"}
SPAWN_TOOLS = {"task", "agent", "spawn_subagent"}


def _args(args):
    """A tool's input as a dict where it is one written as text: JSON, or a Python dict's repr (DataClaw's Gemini,
    56 KB at most; read only below 1 MB, and its escapes' warnings, dozens a run under Python 3.12, kept out of the
    build's log)."""
    if isinstance(args, str) and args.lstrip().startswith("{") and args.rstrip().endswith("}"):
        try:
            got = json.loads(args)
        except ValueError:
            import ast
            import warnings

            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    got = ast.literal_eval(args.strip()) if len(args) < 1 << 20 else None
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                got = None
        if isinstance(got, dict):
            return got
    return args


def tool_fields(name: str | None, args) -> dict:
    out = {"file_path": None, "command": None, "pattern": None, "orchestration_kind": None}
    lname = (name or "").lower()
    args = _args(args)
    if isinstance(args, str):
        if lname in SHELL_TOOLS and args.strip():   # a shell's input kept as the command itself (DataClaw: 43,000)
            out["command"] = args.strip()
        return out
    if not isinstance(args, dict):
        return out
    for k in FILE_KEYS:
        if isinstance(args.get(k), str):
            out["file_path"] = args[k]
            break
    if lname in SHELL_TOOLS or (lname in COMMAND_TOOLS and "command" in args):
        cmd = next((args[k] for k in ("command", "cmd", "CommandLine") if k in args), None)
        if cmd is None and isinstance(args.get("action"), dict):   # Codex's local_shell: {"action": {"command": [...]}}
            cmd = args["action"].get("command")
        out["command"] = " ".join(map(str, cmd)) if isinstance(cmd, list) else (cmd if isinstance(cmd, str) else None)
    if lname in PATTERN_TOOLS and isinstance(args.get("pattern"), str):
        out["pattern"] = args["pattern"]
    if lname in SPAWN_TOOLS:
        out["orchestration_kind"] = "subagent_spawn"
    return out


# SWE-chat's `category` of a tool's call and result, by the tool's name: SWE-chat's own three sets, read from all
# 11.6 million rows of its conversations table (2026-10-05; no name falls in two), and below them the same kinds of
# tool in agents SWE-chat never saw (Cursor's, Copilot's, Gemini CLI's, OpenCode's). Any other tool is `Other`, as
# SWE-chat files it, names it saw as `Other` included (`MultiEdit`, `file_search`, `exec`, `shell_command`).
CATEGORY = {
    "Research": ("FetchUrl", "Glob", "Grep", "LS", "Read", "WebFetch", "WebSearch", "codebase_investigator", "codesearch",
                 "find_symbol", "get_symbols_overview", "glob", "google_web_search", "grep", "grep_search", "list_dir",
                 "list_directory", "ls", "read", "read_file", "search_for_pattern", "web_fetch", "web_search", "webfetch",
                 "websearch",
                 "codebase_search", "list", "read_many_files", "search_file_content", "semantic_search", "view",
                 "find_by_name", "fs_read", "view_file", "read_url_content", "FetchURL", "ReadMediaFile",
                 "read_file_v2", "ripgrep_raw_search", "glob_file_search", "ReadFile", "rg", "copilot_readFile",
                 "copilot_findTextInFiles", "copilot_listDirectory", "copilot_findFiles"),
    "Action": ("ApplyPatch", "Bash", "Create", "Edit", "Execute", "NotebookEdit", "Write", "apply_patch", "bash", "edit",
               "exec_command", "mcp__acp__Edit", "mcp__acp__Write", "replace", "run_shell_command", "write", "write_file",
               "create_file", "delete_file", "edit_file", "insert_edit_into_file", "local_shell", "multiedit", "patch",
               "replace_string_in_file", "run_in_terminal", "run_terminal_cmd", "search_replace", "shell",
               "str_replace_editor",
               "execute_bash", "fs_write", "replace_file_content", "run_command", "run_terminal_command",
               "write_to_file", "multi_replace_file_content",
               "edit_file_v2", "run_terminal_command_v2", "StrReplace", "copilot_replaceString", "textEditGroup",
               "copilot_createFile", "copilot_multiReplaceString", "copilot_applyPatch", "multi_replace_string_in_file",
               "run_ipython"),
    "Orchestration": ("Task", "TaskCreate", "TaskGet", "TaskList", "TaskOutput", "TaskStop", "TaskUpdate", "task",
                      "spawn_subagent", "browser_subagent", "runSubagent"),
}
# By name, case aside, for the names SWE-chat never saw (Cursor's `Shell`, Copilot CLI's `create`); SWE-chat's own
# names exactly first, as its one name filed two ways by case shows (`Execute` Action, `execute` Other).
CATEGORY_OF = {name: c for c, names in CATEGORY.items() for name in names}
CATEGORY_LOWER = {name.lower(): c for name, c in CATEGORY_OF.items()}
SWECHAT_OTHER = {"execute", "MultiEdit"}   # SWE-chat's names filed Other that a case-blind match would file
                                           # otherwise (its whole table checked, 2026-10-05)


def category(name: str) -> str:
    if name in CATEGORY_OF:
        return CATEGORY_OF[name]
    return "Other" if name in SWECHAT_OTHER else CATEGORY_LOWER.get(name.lower(), "Other")
# SWE-chat's `bash_category`, on its shell tools' rows (the first five, the names it gives one; then the shell tools
# of agents it never saw), by the command's first word, its path and any leading `NAME=value` aside: the rule agrees
# with 99.1% of SWE-chat's own 1,641,344 labels (2026-10-05). A result takes its call's.
BASH_TOOLS = {"Bash", "bash", "exec_command", "run_shell_command", "Execute",
              "local_shell", "run_in_terminal", "run_terminal_cmd", "shell", "execute_bash", "run_command",
              "run_terminal_command", "run_terminal_command_v2"}
BASH_LOWER = {name.lower() for name in BASH_TOOLS} - SWECHAT_OTHER


def bash_tool(name: str) -> bool:
    return name in BASH_TOOLS or name.lower() in BASH_LOWER
BASH_WORDS = {**dict.fromkeys(("git", "gh"), "git"),
              **dict.fromkeys(("cd", "ls", "cat", "find", "tail", "head", "rm", "rmdir", "wc", "mkdir", "cp", "mv",
                               "chmod"), "file ops"),
              **dict.fromkeys(("npm", "npx", "pnpm", "yarn", "bun", "cargo", "uv", "pip", "pip3", "poetry", "brew"),
                              "package manager"),
              **dict.fromkeys(("go", "make", "cmake", "test", "pytest", "deno", "flutter", "gleam", "mvn", "rake",
                               "snforge", "zig"), "test/build")}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=\S*")


SHELL_RUN = re.compile(r"(?:\S*/)?(?:bash|sh|zsh|dash)\s+-[abd-z]*c[a-z]*\s+(.+)", re.S)   # `bash -lc "..."`


def bash_category(command: str | None) -> str:
    """By the command's first word. A command run through a shell's `-c` (Codex's `bash -lc ...`, every one of its
    older shell calls) is read by the command it runs: SWE-chat's rule would file all of them `other` (its own rows
    so wrapped are 0.15% of its labels)."""
    command = (command or "").strip()
    run = SHELL_RUN.fullmatch(command)
    if run:
        command = run.group(1).strip()
        if len(command) > 1 and command[0] == command[-1] and command[0] in "'\"":
            command = command[1:-1]
    for word in command.split():
        if not ASSIGNMENT.fullmatch(word):
            return BASH_WORDS.get(word.rsplit("/", 1)[-1], "other")
    return "other"


def finish(rows: list[dict], session: dict, keys: dict | None = None) -> list[dict]:
    """Numbers, ids and the session's own fields, once a thread's rows are in order (SWE-chat's definitions): every
    row numbered from 0, conversational rows numbered among themselves, the first marked, words and characters
    counted, a result's tool named from its call."""
    # A result's call: the latest call before it with its id. OpenCode numbers its calls afresh in each message
    # (`call_0`), so an id repeats within a session, and a session-wide map gave a result the last call of its id
    # (1,000 results with no bash_category, 2026-10-05). A result written before its call: any call of its id.
    calls = {r["tool_call_id"]: (r["tool_name"], r["command"]) for r in rows
             if r["turn_type"] == "tool_use" and r["tool_call_id"]}
    latest: dict[str, tuple] = {}
    conv = 0
    loops: set[str] = set()
    for n, r in enumerate(rows):
        r.update({k: session.get(k) for k in ("session_id", "checkpoint_pk", "repo_id", "agent", "strategy",
                                              "source", "session_key")})
        r["turn_number"] = n
        if keys:
            r["task_pk"], r["agent_id"] = keys.get("task_pk"), keys.get("agent_id")
            r["turn_pk"] = f"{keys.get('task_pk')}#{n}"
        r["turn_id"] = f"{session.get('session_id') or session.get('session_key')}#{n}"
        r["is_conversational"] = r["turn_type"] in CONVERSATIONAL
        if r["is_conversational"]:
            r["conversation_turn_number"] = conv
            r["is_first_turn"] = conv == 0
            conv += 1
        else:
            r["is_first_turn"] = False
        if r["role"] == "user" and r["typed_by_developer"] and r["kind"] == "text":
            _retell(r)
        if r["role"] == "user" and r["typed_by_developer"] and r["kind"] == "command":
            # A command its reader made the developer's that the text rules give to a program: a swarm coordinator's
            # handoff (53 rows of version 2026-10-04, corrected by hand on 2026-10-06; a reader built them typed).
            rule = user_text_rule(_after_reminders(r["content"] or ""))
            if rule and rule.get("kind") == "command" and rule["typed"] is False:
                r["typed_by_developer"], r["actor"] = False, rule["actor"]
        if r["role"] == "user" and r["typed_by_developer"] and r["kind"] == "command" and (r["command"] or "").startswith("/loop"):
            # A /loop's later firings are its scheduler's, as Claude Code's reader has them; a format that keeps no
            # mark (DataClaw's copies: 302 rows of version 2026-10-04) shows the same command again.
            key = " ".join(r["command"].split())
            if key in loops:
                r["typed_by_developer"], r["actor"] = False, "harness"
            loops.add(key)
        if (r["role"] == "user" and r["typed_by_developer"] and n and rows[n - 1]["role"] == "user"
                and _after_reminders(r["content"] or "").lstrip().startswith(GOAL_SET)
                and " ".join((r["content"] or "").split()) == " ".join((rows[n - 1]["content"] or "").split())):
            # A /goal queued twice before its delivery is typed once, as Claude Code's reader has it; a format that
            # keeps no queue shows both deliveries at once (DataClaw's copies: 2 rows of version 2026-10-11).
            r["typed_by_developer"], r["actor"] = False, "harness"
        if r["role"] == "user" and r["typed_by_developer"] and _after_reminders(r["content"] or "").lstrip().startswith(GOAL_NOTE):
            # Claude Code's note to the model that a /goal's Stop hook is set, in a format that keeps no mark of it
            # (DataClaw's and SpecStory's copies): no one's typing where the goal's command is told beside it (its
            # `Goal set` output just before, or its `Goal set: …` delivered next); else the goal's only trace, the
            # developer's (37 and 2 rows of version 2026-10-11; corrected by hand on 2026-10-12, #30).
            told = n and _after_reminders(rows[n - 1]["content"] or "").lstrip().startswith("<local-command-stdout>" + GOAL_SET)
            after = next((x for x in rows[n + 1:] if x["role"] == "user" and x["typed_by_developer"]), None)
            if told or (after is not None and _after_reminders(after["content"] or "").lstrip().startswith(GOAL_SET)):
                r["typed_by_developer"], r["actor"], r["kind"] = False, "harness", "notice"
        text = r["content"] or ""
        r["word_count"], r["char_count"] = len(text.split()), len(text)
        call = None
        if r["turn_type"] == "tool_use" and r["tool_call_id"]:
            latest[r["tool_call_id"]] = (r["tool_name"], r["command"])
        elif r["turn_type"] == "tool_result" and r["tool_call_id"]:
            call = latest.get(r["tool_call_id"]) or calls.get(r["tool_call_id"])
        if r["turn_type"] == "tool_result" and not r["tool_name"] and call:
            r["tool_name"] = call[0]
        if r["tool_name"] and r["turn_type"] in ("tool_use", "tool_result"):
            r["category"] = category(r["tool_name"])
            # None where no command was recorded (SpecStory's renderings: about 8,000 calls); SWE-chat files those
            # `other`. A result with no id keeps what its reader gave it from the call it paired it with (the hourly
            # logs record no ids).
            if bash_tool(r["tool_name"]) and (r["turn_type"] == "tool_use" or r["tool_call_id"]):
                command = r["command"] if r["turn_type"] == "tool_use" else (call[1] if call else None)
                r["bash_category"] = bash_category(command) if command and command.strip() else None
        if r["is_continuation"] is None:
            r["is_continuation"] = False
        if r["content_block_index"] is None:   # an entry with no blocks (the partner session's reading of v2's card)
            r["content_block_index"] = 0
    return rows


# -- Claude Code --------------------------------------------------------------------------------------------------
# Who typed what follows the errata-bench study's rules, measured on SWE-chat's transcripts, with Claude Code's own
# entry marks (errata-bench G-90, G-94: `origin`, `turnOrigin`, `promptSource`).
# Claude Code's own prompts and a CI job's, given as a user's (the partner session's read of the sample, 2026-10-04):
# the summarizer's, and a security review's.
CC_BUILT_IN = ("Context: This summary will be shown in a list to help users and Claude choose",
               "Review this change for security vulnerabilities.\n\nChanged files (you may Read these")
NOT_A_PROMPT = ("<command-name>", "<command-message>", "<local-command-stdout>", "<local-command-stderr>",
                "<local-command-caveat>", "Caveat:", "<bash-input>", "<bash-stdout>", "<bash-stderr>",
                "[Request interrupted", "<task-notification>", "<system-reminder>", "<user-memory-input>")
REJECTED = "The user doesn't want to"
SAID = "To tell you how to proceed, the user said:"
NOTE = "Note: The user's next message may contain"
NOTICES = ("◇ ultraplan", "Plan being refined via Ultraplan")
PLANS = ("Implement the following plan", "Ultraplan approved in browser")
CONTINUED = "This session is being continued"
GOAL_SET = "Goal set: "   # Claude Code's record of a `/goal` command: `Goal set: <the goal>`
GOAL_NOTE = "A session-scoped Stop hook is now active with condition:"   # and its note to the model
IMAGE = re.compile(r"\[Image[^\]]*\]")
PLACEHOLDER = re.compile(r"<PRESIDIO_ANONYMIZED_[A-Z_]+>|\[REDACTED(?::[^\]]*)?\]|\bREDACTED\b|[\w.+-]+@[\w-]+\.[\w.-]+")
MAIN_PART = re.compile(r"(?:^|/)full\.jsonl(?:\.(\d+))?$")
# A user entry SWE-chat files as `system_injected` (measured on SWE-chat v1 by the partner session, 2026-10-03):
# a meta entry, Claude Code's local-command output and caveats (2,504 against 4), system reminders (27 against 0),
# shell output (197 against 151: one value picked). Every other user entry is `user_prompt`, as SWE-chat files it
# (teammates 969 against 0, task notifications 2,824 against 548, compaction summaries, interruptions, `!` input,
# plans), with `typed_by_developer`, `actor` and `kind` saying who wrote it. Slash commands are `user_prompt` here;
# SWE-chat splits them by tag order (4,588 `<command-name>`-first as system_injected, 2,464 `<command-message>`-first
# as user_prompt), an artifact this does not copy. A message behind a reminder's note is filed as it is without the
# note (_words, 2026-10-12); a note alone, with no image, is the harness's, system_injected.
INJECTED_LEADS = ("<local-command-stdout>", "<local-command-stderr>", "<local-command-caveat>", "Caveat:",
                  "<system-reminder>", "<bash-stdout>", "<bash-stderr>", "<user-memory-input>")
QUEUE_SUBTYPE = {"enqueue": "user_prompt_enqueued", "dequeue": "user_prompt_delivered", "remove": "user_prompt_discarded"}


def _norm(text: str) -> str:
    return " ".join(IMAGE.sub(" ", text or "").split())


_SPACE = re.compile(r"\s*")


def _after_reminders(text: str) -> str:
    """What follows the `<system-reminder>` blocks a message opens with; the text itself where it opens with none, or
    holds nothing else. Claude Code, or a tool around it, puts a note in front of a message the developer sent
    (`Message sent at …`, the references they attached, a linked session's status); the message is judged by what
    follows (version 2026-10-11, 2026-10-12: 235 rows of 74 sessions, the developer's, read as the harness's by the
    note alone). A note never closed, or one with nothing after it, leaves the text as it is."""
    at = _notes_end(text)
    return text[at:] if at is not None and at < len(text) else text


def _notes_end(text: str) -> int | None:
    """Where the `<system-reminder>` notes a text opens with end, spaces after them included; None where it opens with
    none, or one is never closed. Found by plain search from where the last note ended: linear, however many."""
    i, at = _SPACE.match(text).end(), None
    while text.startswith("<system-reminder>", i):
        j = text.find("</system-reminder>", i)
        if j < 0:
            return None
        i = at = _SPACE.match(text, j + len("</system-reminder>")).end()
    return at


def _words(text: str, media: bool) -> str:
    """What a message says behind its notes (_after_reminders); nothing where the notes are all its text and it holds an
    image or a document, as it says nothing without them (an image sent behind a note was the harness's notice)."""
    at = _notes_end(text)
    return "" if media and at is not None and at >= len(text) else _after_reminders(text)


def _media(content) -> bool:
    return isinstance(content, list) and any(isinstance(b, dict) and b.get("type") in ("image", "document") for b in content)


def _key(text: str) -> str:
    """What two copies of one message share: words, image markers out, every anonymised or redacted span one token."""
    return PLACEHOLDER.sub("<X>", _norm(text))


def _tag(raw: str, name: str) -> str | None:
    """The text inside the first `<name>…</name>`, as a lazy pattern finds it, in linear time: a pattern tried again
    from each unclosed tag took quadratic time (the partner session's review of rc15: 2.5 s at 112 KB, hours at a
    few MB), and since rc15 every reader's typed text can reach here."""
    i = raw.find(f"<{name}>")
    j = raw.find(f"</{name}>", i + len(name) + 2) if i >= 0 else -1
    return raw[i + len(name) + 2:j] if i >= 0 and j >= 0 else None


def _command(raw: str) -> str:
    """A slash command as the developer typed it: its name and its arguments."""
    name, args = _tag(raw, "command-name"), _tag(raw, "command-args")
    return " ".join(x for x in ((name or "").strip(), (args or "").strip()) if x)


def _prompt(e: dict) -> bool:
    """The developer writing to the agent (recover._prompt, but for a note in front: judged by what follows it,
    _after_reminders, 2026-10-12): a user entry that is not a tool's result, not meta, and not one of Claude Code's
    notices; an entry of images alone is one."""
    message = e.get("message")
    if e.get("type") != "user" or e.get("isMeta") or not isinstance(message, dict):
        return False
    content = message.get("content")
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        content = _words(" ".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text"),
                         _media(content)) or ("an image" if any(isinstance(b, dict) for b in content) else "")
    if not isinstance(content, str) or not content.strip():
        return False
    return not _after_reminders(content).lstrip().startswith(NOT_A_PROMPT)


def _notice(text: str, lead: str) -> bool:
    """A user entry's text that an unmarked entry's reading takes for a notice: a notice's lead, a built-in prompt or
    a task's JSON notice, and not a command, a shell input or an interrupt (each read by its own kind first)."""
    return (("<command-name>" not in text or lead.startswith("<system-reminder>"))   # a tag inside a note alone
            and not lead.startswith(("<bash-input>", "[Request interrupted"))
            and (lead.startswith(NOT_A_PROMPT) or lead.startswith(CC_BUILT_IN) or _task_notice(text)))


def _task_notice(text: str) -> bool:
    """A background task's notice Claude Code enqueues as a JSON object (`task_id`, `tool_use_id`, `description`,
    `task_type`): the harness's, never the developer's (the partner session's read of 2026-10-03: 95 rows of one
    sample part, 109 in the study's sessions, which the study's rules never counted). Matched by its `task_id` key,
    not by being JSON: a developer pastes JSON as a message too."""
    t = text.strip()
    if not t.startswith("{"):
        return False
    try:
        v = json.loads(t)
    except ValueError:
        return False
    return isinstance(v, dict) and "task_id" in v


# -- Who wrote a user message, by its text: every reader ----------------------------------------------------------
# A harness's, an IDE's or a tool's text in a user message, whatever the agent. Claude Code's reader tells most of it
# by its entries' marks; a format with no marks, or another agent that Claude Code runs, has only the text. Found
# marked typed over the full collection (2026-10-05): in Codex sessions, Claude Code's task notices (1,185), compaction
# summaries (93), local command output (97) and interruptions (61); in OpenCode's, skills' and reminders' text.
# By its lead: who wrote it, and the row's kind.
HARNESS_LEADS = (
    ("<task-notification", "harness", "notice"), ("<local-command-stdout>", "harness", "notice"),
    ("<local-command-stderr>", "harness", "notice"), ("<local-command-caveat>", "harness", "notice"),
    ("Caveat: The messages below", "harness", "notice"), ("<system-reminder>", "harness", "notice"),
    ("<bash-stdout>", "harness", "notice"), ("<bash-stderr>", "harness", "notice"),
    ("<user-memory-input>", "harness", "notice"), ("<user-prompt-submit-hook>", "harness", "notice"),
    (CONTINUED, "harness", "compaction"), ("[Request interrupted", "developer", "interrupt"),
    ("<turn_aborted", "developer", "interrupt"), ("Base directory for this skill", "harness", "notice"),
    ("<bash-input>", "developer", "shell"),   # `!` input: the developer's, a shell's action, as Claude Code's reader has it
    ("<subagent_notification", "agent", "notice"), ("<teammate-message", "peer", "notice"),
    ("<environment_context>", "harness", "notice"), ("<user_instructions>", "harness", "notice"),
    ("# AGENTS.md instructions", "harness", "notice"), ("<permissions instructions>", "harness", "notice"),
    ("<collaboration_mode>", "harness", "notice"),
    # Read over version 2026-10-04's typed rows (#17's audit and ours, 2026-10-05): a message that is a scheduler's,
    # a harness's command template, another agent's delegation or a teammate's message, never typed.
    ("<scheduled-task", "harness", "notice"), ("<wake ", "harness", "notice"), ("<wake>", "harness", "notice"),
    ("<create-pr-command", "harness", "notice"), ("<codex_delegation", "agent", "notice"),
    ("<message from=", "peer", "notice"),
    # Another Claude session's message relayed into the session (943 rows in 7 Codex sessions, 2026-10-06), and a
    # dispatcher's instructions to the worker it starts in a worktree (56).
    ("Another Claude session sent a message:", "peer", "notice"), ("<cross-session-message", "peer", "notice"),
    ("[delegate worktree]", "harness", "notice"),
    # Read over version 2026-10-11's new typed rows (2026-10-12): a team tool relaying a worker's report into its
    # lead's session (20 rows in 2 sessions); an orchestrator's messages to its workers are RELAY_FROM's.
    ("[bb system]", "harness", "notice"),
    # Read over version 2026-10-11's typed rows again (2026-10-25, the dataset paper's audit; rows in brackets): a
    # plugin's todo loop (117), Pi's tool budget (19), OpenCode's prompt to go on (17), a length-limit continuation
    # (25), Codex's and OpenCode's /init bodies (12, 15), Gemini CLI's tool output in an export (25), another agent's
    # brief to a research subagent (44), and plan approvals in Codex's and exported Claude Code sessions (184, 301):
    # a person's action, not their typing.
    ("[SYSTEM DIRECTIVE: OH-MY-OPENCODE", "harness", "notice"), ("Tool budget soft limit reached", "harness", "notice"),
    ("Continue if you have next steps, or stop and ask for clarification", "harness", "notice"),
    ("[System: Your previous response was truncated by the output length limit", "harness", "notice"),
    ("Generate a file named AGENTS.md that serves as a contributor guide", "harness", "command"),
    ("Create or update `AGENTS.md` for this repository", "harness", "command"),
    ("[Function Response:", "tool", "tool_result"), ("Thoroughness: ", "agent", "notice"),
    ("PLEASE IMPLEMENT THIS PLAN:", "developer", "plan"), ("Implement the following plan:", "developer", "plan"),
    # Read over version 2026-10-04's typed rows of formats with no author's mark (#17's evaluation of it and ours,
    # 2026-10-05; DataClaw's and SpecStory's): Claude Code's own requests to the model (a next prompt's suggestion,
    # 1,048; a compaction summary's request, 842), a Stop hook's feedback (1,689) and the /loop skill's body (317).
    ("[SUGGESTION MODE:", "harness", "notice"),
    ("Your task is to create a detailed summary of the conversation so far", "harness", "compaction"),
    ("Stop hook feedback:", "harness", "notice"), ("# /loop — schedule a recurring or self-paced prompt", "harness", "notice"),
    # Commands' and tools' bodies sent as the message, read on the same rows: OpenCode's /review (323), a /sl command
    # (311), a documentation guide's (75), Claude Code commands' bodies with git's status put in (91), a headless
    # orchestrator's instructions (98).
    ("You are a code reviewer. Your job is to review code changes and provide actionable feedback.", "harness", "notice"),
    ("# Session Logs (/sl) - Session Continuity Command", "harness", "notice"),
    ("# Documentation Style Guide\n\nEnforce consistent styling for documentation artifacts", "harness", "notice"),
    ("## Context\n- Current git status:", "harness", "notice"), ("## Context\n\n- Current git status:", "harness", "notice"),
    ("## Context\n- Current pull request status:", "harness", "notice"),
    ("## Context\n\n- Current pull request status:", "harness", "notice"),
    ("# Global Agent Instructions\n\nYou are an autonomous coding agent running in a headless orchestration session",
     "harness", "notice"),
)
# Templates whose opening a person might write, each with its template's second sentence: a review tool's (566), a
# transcript distiller's (33), the headless orchestrator's skill call (44).
TEMPLATE_PAIRS = (("You are reviewing the changes on branch ", "There are most likely issues in the current implementation"),
                  ("You are a context distiller for ", "Read the session transcript below."),
                  ("## Skill Invocation\n\nYou MUST run the `/", "headless orchestration session"))
# A scheduler's firing, opening with its job's id (OpenClaw's cron: 233 rows); Claude Code's warm-up of a subagent,
# the whole message (218 rows; its own transcripts record it in subagents' threads, never typed).
CRON = re.compile(r"\[cron:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[\s\]]")
HARNESS_EXACT = frozenset({"Warmup"})
# An orchestrator's templates (megaplan's: 6,085 rows, every one in DataClaw), each with a second mark, its name
# anywhere or its `Project directory:` line early: 3 rows opening alike with neither stay the developer's.
MEGAPLAN = ("You are already running inside the megaplan harness", "You are the gatekeeper for the megaplan workflow",
            "Read the full megaplan phase prompt from this file", "You are the autonomous megaplan runner",
            "Execute the approved plan in the repository.", "Review the implementation against the success criteria.",
            "Author the planned document sections.", "You are creating an implementation plan for the following idea.",
            "You are an independent reviewer. Critique the plan against",
            "You are revising an implementation plan after critique", "You are preparing an execution-ready briefing document")
TEMPLATE = re.compile(r"\$ARGUMENTS\b|\$\{CLAUDE_PLUGIN_ROOT\}")   # a command's or a skill's body, still a template
CURSOR_BLOCKS = ("timestamp", "manually_attached_skills", "image_files", "attached_files", "plugin_info",
                 "cursor_commands", "external_links", "open_subagent_context")
CURSOR_OPENS = tuple(f"<{name}>" for name in CURSOR_BLOCKS)
CONTEXT_BLOCKS = ("ide_opened_file", "ide_selection", "system-reminder")
# Blocks a tool, an IDE or a plugin puts into a user message, never the developer's typing (read over version
# 2026-10-04's typed rows, #17's audit and ours, 2026-10-05: 1,700 rows held one): removed wherever they are, and a
# message of nothing else is the harness's (tell). A developer's own pasted text, files and images keep theirs.
HARNESS_BLOCKS = ("system_instruction", "system-instruction", "system_notification", "system-notification",
                  "system_reminder", "in-app-browser-context", "git_diff_from_branch_to_main", "uploaded_documents",
                  "cursor_rules_context", "canvas-context", "ci-monitor-event", "bash-notification", "heartbeat",
                  "browser_instruction", "side-sessions-event")   # the last two read over 2026-11-01 (2026-11-02)
QUESTION_REPLY = "<send_user_message_question_reply>"
# Codex's IDE extension opens a message with the editor's context (or the files the developer attached: 199 rows with
# a request, 2026-10-06), then `## My request for Codex:` and the developer's words. With no words after it, the
# attached files are the request: a long paste Codex moved into `pasted-text.txt` ("The attached pasted text file(s)
# contain the user's request", 58 rows), so that message stays as it is, the developer's.
CODEX_FILES = "# Files mentioned by the user:"
CODEX_CONTEXT = ("# Context from my IDE setup:", "# In app browser:", CODEX_FILES)
CODEX_REQUEST = re.compile(r"^## My request for [^:\n]{1,30}:[ \t]*$", re.M)
# OpenClaw's chat bridge opens a message with blocks of metadata, each a header (`Conversation info`, `Sender`,
# `Replied message`, `Forwarded message context`) over a fenced JSON block; the person's message follows.
UNTRUSTED = re.compile(r"\s*[A-Z][A-Za-z ]{0,40} \(untrusted(?: metadata|, for context)\):[ \t]*\n```[A-Za-z]*\n.*?\n```[ \t]*(?:\n|$)", re.S)
# Read over version 2026-10-04's typed rows by their openings, in every format (2026-10-06, errata-data#18): text the
# developer did not type, by whoever did write it. Matched on the message with its spaces collapsed.
# - A button or a dialog's answer: the developer's choice, not their typing. VS Code Copilot's `@agent` buttons in any
#   language (`@agent Try Again`, `@agent 重试`, `@agent Continue: "Continue to iterate?"`, `@agent Enable: "…"`: 1,594
#   rows; `@agent-name …` and `@agents/…` are a person's mentions, and a request after `@agent ` longer than three words
#   stays typed), Cursor's (facts.CURSOR_ACTIONS, through SpecStory; its merge-conflict button), Aider's confirmations
#   with the answer after them (251).
# Matched with the comments after it taken off first (_drop_trailing_comments): a pattern that skipped them took
# exponential time on a button followed by many comments and a word (the independent review of 2026-10-12: 2.5 s at
# 25 comments).
AGENT_BUTTON = re.compile(r'@agent (?:[^\s:"<]{1,30}(?: [^\s:"<]{1,20}){0,2}|[^\s:"<]{1,40}: "[^"\n]{1,200}")\s*\Z')
AIDER_CONFIRM = re.compile(r"(?:Add (?:file|URL|command output) to the chat|Create new file|Allow edits to file|"
                           r"Run shell commands?|Attempt to fix lint errors)\?.{0,160}\(Y\)es/\(N\)o", re.S)
BUTTONS = (*CURSOR_ACTIONS, "Resolve this merge conflict @Branch")
# - A plan accepted with a click: Cursor's (facts.CURSOR_PLAN), Codex's (`Implement the plan.`, 181 rows, after the
#   agent's plan), Copilot's handoff from its plan agent (`Start implementation`, 194).
PLAN_CLICKS = frozenset({"Implement the plan.", "Start implementation"})
# - A harness's, an IDE's, a tool's or a script's text: Cursor's instructions to the agent through SpecStory
#   (facts.CURSOR_AUTOMATIC), Gemini CLI's nudge after a turn that stopped (`System: Please continue.`, 87), SpecStory's
#   header of a Copilot edit session (58); the bodies of /init, of Copilot's instructions command and Codex's /review
#   (140); a hook's and a plugin's injected text (104); prompts that a script or a pipeline sent into many sessions: a
#   JSON-reporting runner (103), a security review (91), a commit-message writer (70), a review checker (51), a CI-fix
#   action (96), a spec command's bodies (67), a commit command's body, a skill's body and a pipeline's review stages.
AUTOMATIC_EXACT = frozenset({"System: Please continue.", "File editing session"})
AUTOMATIC_LEADS = (*CURSOR_AUTOMATIC,
                   "Please analyze this codebase and create a CLAUDE.md file, which will be given to future instances",
                   "Analyze this codebase to generate or update `.github/copilot-instructions.md`",
                   "Review the current code changes (staged, unstaged, and untracked files) and provide prioritized findings.",
                   "VERIFY GATE: substantive edits this turn have NOT been independently verified", "[ROUGHNECK:",
                   "IMPORTANT: After completing the task below, you MUST output a JSON object",
                   "Analyze these staged git changes and output ONLY a single-line conventional commit message",
                   "You are an independent heavy-review checker.",
                   "Fix the failing CI actions. I've attached the failure logs.",
                   "# Spec iterate I added my thoughts, decisions and comments to the specs.",
                   "# Spec Build Out I (Till) drafted some idea that I want to implement.",
                   "Commit & Push Goal: Commit current work with meaningful commit boundaries and push cleanly.")
AUTOMATIC_PAIRS = (("Review this change for security vulnerabilities.", "Changed files (you may Read these"),
                   ("Stage ", "review assessment for desloppify"),
                   ("Let's work on that.", "Use this prompt when the user wants execution from the available context"))
# - Prompts a script, a bot or an orchestrator sent into many sessions, read one by one over version 2026-11-01's
#   typed openings shared by 10+ sessions (2026-11-02: 84 of 206 openings, 1,985 sessions, 2,201 messages; in about
#   1,776 of those sessions the only message read as typed): review bots and a pipeline's review stages, loop and
#   multi-agent orchestrators (Ralph, BMAD, Maestro, firstmate, Orca, a hackathon's roster), issue and PR runners,
#   a scripted Aider benchmark, batch translators and summarizers, research and grading harnesses. A developer's own
#   saved prompts, a bot's text they pasted (a review tool's "Verify each finding..."), commands' bodies and buttons
#   stay as before; so does an opening shared by fewer than 10 sessions, not read.
SCRIPTED_LEADS = ("You are analyzing a code repository for potential issues. The repository files are available in",
                  "Review the current branch changes and report actionable findings.",
                  "You are a futures analyst grounded in Kent Beck",
                  "You are reviewing the results from parallel code analysis for potential issues.",
                  "# Batch translation Translate the VALUES of this JSON object",
                  "You are the Niyam encoder.", "Create the file /home/ubuntu/tracezeta/",
                  "You are working autonomously towards an objective given below. This is iteration",
                  "droid-monkey attacked this Android app", "You are shepherding PR #",
                  "You are the CI/monitoring agent for PR #",
                  "You are implementing a project per the following spec.", "# Ralph Agent Instructions",
                  "You are a developer on this repo. Implement the following request, run the tests",
                  "You are an autonomous coding agent working on issue **",
                  "You are reviewing one task's implementation: first whether it matches its requirements",
                  "Read .orchestrator/plan.md and the bus.", "You are reviewing one semantic feature for clawpatch.",
                  "You are a senior German insurance claims adjuster reviewing a claim.", "FIRSTMATE_OP:",
                  ": Firstmate instruction waiting:", "Translate natural language to a single shell command.",
                  "Here is a command I ran in my terminal and its output. Diagnose any errors",
                  "You summarize shell command output.", "You are one of several workers auditing ",
                  "You are the Cairn session summarizer.", "Read the feedback at tools/title-loop/",
                  "Execute the improvement plan at tools/title-loop/",
                  "Read task details in .agent-task.md and follow CLAUDE.md.",
                  "You are participating in an autonomous multi-agent ",
                  "Continue work from the prior Orca session using the context below.",
                  "Inspect subagent updates above. Answer pending supervisor requests",
                  "Summarize the task tool output above and continue with your task.",
                  "Author the missing conversation turns described in the batch request file",
                  "[SPEC] Continue workflow from previous session.",
                  "You are analyzing a task for a React Native/Expo recipe photo management app called Fasola.",
                  "You are running LOCALLY on a developer's machine, before these changes are pushed, not in the "
                  "trail runner sandbox.",
                  "You have completed your investigation but your findings were not written as JSON.",
                  "You are the Crashworthy fixer.", "You are an adversarial bug hunter operating under the ",
                  "You are an expert developer experience engineer. You analyze raw tool call events",
                  "Run `sh ./02-check.sh`, then implement the @TODO.md")
SCRIPTED_PAIRS = (("Work on Linear issue ", "<issue identifier="),
                  ("# Context Your name is **", "a Maestro-managed AI agent"),
                  ("[roster setup=", "HACKATHON GUARDRAILS"), ("# Potential gains and brain activity", "NARPS"),
                  ("# Independent assessment", "STAMPED_JUDGE_TARGET"),
                  ("Assess the committed research object", "STAMPED_JUDGE_TARGET"),
                  ("Execute the BMAD ", "READ this skill first"),
                  ("Execute the story-automator ", "READ this skill first"),
                  ("Requested change:", "Editable files:"), ("0a. Study `specs/*` with up to ", "parallel"),
                  ("You are working in /", "jrswab/axe"),
                  ("You are working in the Go project at ", "comprehensive code review"),
                  ("You are working in the Go project at /", "jrswab/axe"),
                  ("You are implementing GitHub issue #", "in a dedicated worktree"),
                  ("/review", "Scope: review the commits unique to this branch"),
                  ("Read the plan file at ", "Find the FIRST Task section"),
                  ("You are worker ", "in a multi-agent audit"),
                  ("You are one of ", "workers doing manual cleanup review"),
                  ("# Your Specialist Role", "<specialist_role>"),
                  ("Review ", "for concrete, actionable defects caused by the diff"))
# - Another agent's prompt: Codex's approval reviewer's (662), a Factory Droid subagent's task (182), a Pi subagent's
#   delegated task (75).
AGENT_LEADS = ("The following is the Codex agent history", "# Task Tool Invocation")
DAG_BRIEF = re.compile(r"You are the \w+ for one harness DAG node\.")   # a DAG harness's brief to a node's worker, critic, ...
# An agent orchestrator's message to a worker session, sent in as the worker's user input under the sending session's
# name (`[from neatlogs-hack-8] From the orchestrator. ...`, `[from locke-1] Orchestrator: ...`): another agent's
# message, never typed (255 rows in 10 sessions of version 2026-10-11, read as typed until 2026-10-12); also after a
# reader's leftover markup on a line of its own (Antigravity: `...-15b0bf1ee922">`).
RELAY_FROM = re.compile(r"\[from [A-Za-z][\w.-]*-\d+\] ")
MARKUP_LEFTOVER = re.compile(r'[^\s<>]{1,80}">[ \t]*\r?\n\s*')
# Read over version 2026-10-11 by an independent review (2026-10-12), each at the message's start only (a developer's
# words before one keep the message theirs): another agent's messages to the session (a terminal orchestration tool's
# inbox, an orchestrator's approval or check-in, a parent agent's steering), and an orchestrator's own notices to its
# workers (AO: a PR's merge conflicts or review comments, a context handover, its workers' reports). And the brief a
# Claude Code session writes for a session it starts (`Background (from the session that started this one):`; 2 rows
# of version 2026-10-11 typed, 11 more behind a note, 2026-10-12).
PEER_LEADS = re.compile(r"(?:--- Orchestration Messages \(\d+\) ---|Approved from the orchestrator\b|Parent check-in \("
                        r"|(?:Mid-run steering|Queued follow-up) from the parent orchestrator:"
                        r"|Background \(from the session that started this one\):)")
AUTOMATED_LEADS = re.compile(r"(?:There are merge conflicts on PR #\d+ "
                             r"|The following \d+ unresolved review comment\(s\) are on PR #\d+ "
                             r"|AO transferred the previous agent's context in hidden system instructions\."
                             r"|Reports since your previous turn:"
                             r"|Implement this single task in the current worktree\. )")   # a dispatcher's task brief
# A harness relaying the answers the developer chose in an AskUserQuestion dialog: their choice, not typing, where
# every answer is one of the options offered; an answer in their own words is typed (2 of 13 rows, 2026-10-12).
DIALOG_RESULT = "Your earlier AskUserQuestion tool call has finished. Its result:"


def _dialog_choices_only(lead: str) -> bool:
    try:
        got = json.loads(lead[len(DIALOG_RESULT):].strip())
    except ValueError:
        return False
    if not isinstance(got, dict) or not isinstance(got.get("answers"), dict) or not got["answers"]:
        return False
    offered = {o.get("label") for q in got.get("questions") or [] if isinstance(q, dict)
               for o in q.get("options") or [] if isinstance(o, dict)}
    return all((v in offered) if isinstance(v, str) else (isinstance(v, list) and v and all(x in offered for x in v))
               for v in got["answers"].values())
AGENT_PAIRS = (("Task: [Read from:", "You are a delegated subagent"),
               ("Task: [Read from:", "You are reviving a previous subagent conversation"))   # Pi's, 2026-11-02


def user_text_rule(text: str, template_anywhere: bool = True) -> dict | None:
    """Who wrote a user message that its text shows is not the developer's typing: {typed, actor, kind, command};
    None for text with no such sign. A slash command is the developer's, typed, as Claude Code's reader has it. A
    command's template (`$ARGUMENTS`) anywhere, or, in a message a reader marked typed, in its first 120 characters
    only: a developer writing about commands mentions it mid-text (26 rows, 2026-10-05)."""
    lead = (text or "").lstrip()
    if lead.startswith(("<command-name>", "<command-message>")) and "<command-name>" in lead:   # not a pasted excerpt
        said = _command(lead)
        if said and said.startswith("/swarm-persona") and "You have new handoff mail" in lead[:600]:
            # A swarm's coordinator handing work to an agent through its command (57 rows, 2026-10-06), not typed.
            return {"typed": False, "actor": "harness", "kind": "command", "command": said}
        return {"typed": bool(said), "actor": "developer" if said else "harness", "kind": "command",
                "command": said or None}
    # A program's text wrapped in one quote mark as sent (OpenCode: an orchestrator's `"[delegate worktree] ...`, 25
    # sessions of version 2026-11-01 typed) is told by what follows it.
    bare = lead[1:].lstrip() if lead.startswith('"') else lead
    for mark, who, kind in HARNESS_LEADS:
        if bare.startswith(mark):
            return {"typed": False, "actor": who, "kind": kind}
    left = MARKUP_LEFTOVER.match(lead)
    if RELAY_FROM.match(lead) or (left and RELAY_FROM.match(lead[left.end():])) or PEER_LEADS.match(lead):
        return {"typed": False, "actor": "peer", "kind": "notice"}
    if AUTOMATED_LEADS.match(lead):
        return {"typed": False, "actor": "harness", "kind": "notice"}
    if DAG_BRIEF.match(lead):
        return {"typed": False, "actor": "agent", "kind": "notice"}
    if lead.startswith(DIALOG_RESULT) and _dialog_choices_only(lead):
        return {"typed": False, "actor": "developer", "kind": "command"}
    if lead.strip() == TOOL_LOADED:
        # Claude Code's own line beside a deferred tool's result, sent as a user message. Its reader marks it the
        # harness's text (Entire 629 rows, SWE-chat v2 95, kind text); DataClaw's exports drop the mark, and 82 rows
        # read as typed in version 2026-10-04 until fixed by hand (#23).
        return {"typed": False, "actor": "harness", "kind": "text"}
    flat = " ".join(lead[:2000].split())
    if flat in PLAN_CLICKS or flat.startswith(CURSOR_PLAN):
        return {"typed": False, "actor": "developer", "kind": "plan"}
    if AGENT_BUTTON.match(_drop_trailing_comments(flat)) or AIDER_CONFIRM.match(flat) or flat.startswith(BUTTONS):
        return {"typed": False, "actor": "developer", "kind": "command"}
    if flat.startswith(AGENT_LEADS) or any(flat.startswith(a) and b in flat for a, b in AGENT_PAIRS):
        return {"typed": False, "actor": "agent", "kind": "notice"}
    script = " ".join(bare[:2000].split())
    if (flat in AUTOMATIC_EXACT or flat.startswith(AUTOMATIC_LEADS)
            or any(flat.startswith(a) and b in flat for a, b in AUTOMATIC_PAIRS)
            or script.startswith(SCRIPTED_LEADS) or any(script.startswith(a) and b in script for a, b in SCRIPTED_PAIRS)):
        return {"typed": False, "actor": "harness", "kind": "notice"}
    if (CRON.match(lead) or lead.strip() in HARNESS_EXACT
            or (lead.startswith(MEGAPLAN) and ("megaplan" in lead or "Project directory:" in lead[:300]))
            or any(lead.startswith(a) and b in lead[:2000] for a, b in TEMPLATE_PAIRS)):
        return {"typed": False, "actor": "harness", "kind": "notice"}
    if _task_notice(lead) or TEMPLATE.search(lead if template_anywhere else lead[:120]):
        return {"typed": False, "actor": "harness", "kind": "notice"}
    return None


def _drop_trailing_comments(text: str) -> str:
    """The text with the HTML comments at its end taken off, one by one from the last; linear."""
    end = len(text)
    while True:
        while end and text[end - 1].isspace():
            end -= 1
        if not text.endswith("-->", 0, end) or (i := text.rfind("<!--", 0, end - 3)) < 0:
            return text[:end]
        end = i


def _drop_comments(text: str) -> str:
    out, at = [], 0
    while True:
        i = text.find("<!--", at)
        j = text.find("-->", i + 4) if i >= 0 else -1
        if i < 0 or j < 0:
            break
        out.append(text[at:i])
        at = j + 3
    return "".join(out) + text[at:]


def tell(text: str, template_anywhere: bool = True) -> tuple:
    """A user message told by its text: (content, typed, actor, kind, command). The wrappers and the harness's and
    IDE's blocks are taken off first (unwrap), so the developer's words after a reminder stay theirs (14 rows were
    lost when the reminder's lead decided first, 2026-10-05). Of what is left: nothing, or HTML comments alone, is
    the harness's; a harness's, an IDE's or a tool's text by its lead is that one's, the message kept as written;
    anything else is the developer's words, as the content."""
    clean = unwrap(_after_reminders(text))   # rules that read a message's start read past a note in front of it
    if not _drop_comments(clean).strip():
        return text, False, "harness", "notice", None
    rule = user_text_rule(clean, template_anywhere)
    if rule:
        return text, rule["typed"], rule["actor"], rule["kind"], rule.get("command")
    return clean, True, "developer", "text", None


def _drop_upper_blocks(text: str) -> str:
    """Each `<NAME>…</NAME>` block whose name is uppercase (a harness's: `<ADDITIONAL_METADATA>`, `<USER_RULES>`)
    removed with its contents, a block left where its end is missing; the tags found in one scan, so linear however
    many names (a pass per name was not, #17's review)."""
    tags = list(re.finditer(r"<(/?)([A-Z][A-Z_]*)>", text))
    closes: dict[str, deque] = defaultdict(deque)
    for m in tags:
        if m[1]:
            closes[m[2]].append(m)
    out, at = [], 0
    for m in tags:
        if m[1] or m.start() < at:
            continue
        ends = closes[m[2]]
        while ends and ends[0].start() < m.end():
            ends.popleft()
        if ends:
            out.append(text[at:m.start()])
            at = ends.popleft().end()
    return "".join(out) + text[at:]


def _drop_fenced(text: str, info: str) -> str:
    """Each fenced block whose info string is `info` (```info … ```) removed, the fence lines with it; one never closed
    runs to the end. Linear: each block is found by plain search from where the last one ended."""
    start, out, at = "```" + info, [], 0
    while (i := text.find(start, at)) >= 0:
        line_end = text.find("\n", i)
        j = text.find("\n```", line_end) if line_end >= 0 else -1
        out.append(text[at:i])
        if j < 0:
            return "".join(out)
        k = text.find("\n", j + 4)
        at = k + 1 if k >= 0 else len(text)
    return "".join(out) + text[at:]


def _drop_blocks(text: str, names: tuple) -> str:
    """Each `<name>…</name>` block removed with its contents, its opening tag with attributes or without
    (`<plugin_info kind="…">`, missed before 2026-10-05), a block left where its end is missing. Linear: a pattern
    with a lazy body tried again from each unclosed tag would take quadratic time."""
    for name in names:
        start, end = f"<{name}", f"</{name}>"
        out, at = [], 0
        while True:
            i = text.find(start, at)
            while i >= 0 and text[i + len(start):i + len(start) + 1] not in (">", " ", "\t", "\n"):
                i = text.find(start, i + 1)   # a longer name sharing this one's start
            close = text.find(">", i) if i >= 0 else -1
            j = text.find(end, close + 1) if close >= 0 else -1
            if i < 0 or close < 0 or j < 0:
                break
            out.append(text[at:i])
            at = j + len(end)
        text = "".join(out) + text[at:]
    return text


CURSOR_FIX = "For the code present, we get this error:"
CHANGE_REQUEST = "The user has submitted a change request. Here is the request, please implement it:"


def _change_request(lead: str) -> str | None:
    """The developer's words in a change request the stagewise toolbar sends through Cursor's fix button (`<user_goal>`
    or `<user_message>` in its `<request>`: 28 rows of version 2026-10-04); None for anything else, stagewise's own
    setup prompt (`<task>`) among it. Found by plain search: linear on any text."""
    i = lead.find(CHANGE_REQUEST, 0, 600)
    if not lead.startswith(CURSOR_FIX) or i < 0:
        return None
    j = lead.find("<request>", i, i + 400)
    if j < 0:
        return None
    for tag in ("user_goal", "user_message"):
        a = lead.find(f"<{tag}>", j, j + 200)
        if a >= 0:
            b = lead.find(f"</{tag}>", a)
            return lead[a + len(tag) + 2:b if b >= 0 else len(lead)].strip()
    return None


def unwrap(text: str) -> str:
    """A developer's own words in a message a harness or an IDE wrapped: Cursor's context blocks removed with their
    contents and each `<user_query>` replaced by its content, any other text kept (the partner session's rule,
    2026-10-05: 46 of 121 wrapped messages held the developer's words outside the query too); Gemini CLI's
    `<USER_REQUEST>` and any text after it but the harness's blocks; Claude Code's IDE and reminder blocks. What is left,
    which may be nothing (a message that was a harness's or an IDE's alone)."""
    # An empty query or request leaves nothing, never the wrapper (#17's review, 2026-10-05: tell() took the raw
    # wrapper as typed); a query or request never closed holds the text to the end, as Antigravity's reader reads it.
    lead = text.lstrip()
    if (request := _change_request(lead)) is not None:
        return request
    if lead.startswith("User steering update:") and "<user_input>" in lead:
        # A harness relaying the developer's words in `<user_input>`, its own instruction after (30 rows, 2026-10-05).
        i = lead.find("<user_input>") + len("<user_input>")
        j = lead.find("</user_input>", i)
        text = lead[i:j if j >= 0 else len(lead)].strip()
    if lead.startswith(CODEX_CONTEXT) and (m := CODEX_REQUEST.search(lead)):
        request = lead[m.end():].strip()   # Codex's IDE and in-app browser context, then the developer's request (877 rows)
        if request or not lead.startswith(CODEX_FILES):
            text = request
    if m := UNTRUSTED.match(text):   # OpenClaw's metadata blocks before a chat message (403 rows, 2026-10-05)
        while m:
            text = text[m.end():]
            m = UNTRUSTED.match(text)
        text = text.strip()
    if "<blank>" in text:   # Aider's mark of an empty input line (192 messages of nothing else)
        text = "\n".join("" if ln.strip() == "<blank>" else ln for ln in text.split("\n"))
    if "```browser_element" in text:
        # Cursor's description of the element picked in its browser preview, put into the message as a fenced block
        # (101 Entire rows, 2026-10-06): taken off, as an IDE's selection is; the developer's words around it stay.
        text = _drop_fenced(text, "browser_element").strip()
    if "<user_query>" in text:
        for _ in range(8):   # a query quoted inside another, its own timestamp first (Cursor: 5 rows, 2026-10-05)
            body = _drop_blocks(text, CURSOR_BLOCKS)
            out, at = [], 0
            while (i := body.find("<user_query>", at)) >= 0:
                j = body.find("</user_query>", i + 12)
                out += [body[at:i], body[i + 12:j if j >= 0 else len(body)]]
                at = j + 13 if j >= 0 else len(body)
            new = ("".join(out) + body[at:]).strip()
            done = new == text or "<user_query>" not in new
            text = new
            if done:
                break
    elif text.lstrip().startswith(CURSOR_OPENS):   # Cursor's context first and no query (17 rows on 2026-10-04)
        text = _drop_blocks(text, CURSOR_BLOCKS).strip()
    lead = text.strip()
    if lead.startswith("<USER_REQUEST>"):
        # Its content, and after it any text that is not a harness's own block (`<ADDITIONAL_METADATA>`,
        # `<USER_RULES>`: Gemini CLI's 10 rows on 2026-10-04): the developer's image (`[Image: image/png]`) or words.
        j = lead.find("</USER_REQUEST>")
        rest = _drop_upper_blocks(lead[j + 15:] if j >= 0 else "")
        text = (lead[14:j if j >= 0 else len(lead)].strip() + ("\n" if rest[:1].strip() else "") + rest).strip()
    if text.lstrip().startswith(QUESTION_REPLY):   # Codex's: the agent's questions and the developer's answers
        lead = text.lstrip()
        j = lead.find("</send_user_message_question_reply>")
        try:
            items = json.loads(lead[len(QUESTION_REPLY):j if j >= 0 else len(lead)])
        except ValueError:
            items = None
        if isinstance(items, list):
            answers = [str(x["answer"]).strip() for x in items if isinstance(x, dict) and x.get("answer")]
            after = lead[j + len(QUESTION_REPLY) + 1:] if j >= 0 else ""   # the closing tag: one character longer
            text = ("\n\n".join(answers) + "\n\n" + after.strip()).strip()
    if "<" in text:
        dropped = _drop_blocks(text, CONTEXT_BLOCKS + HARNESS_BLOCKS)
        if dropped != text:   # the text changed only where a block came off (stripping every text with a `<` changed
            text = dropped.strip()   # 6,500 typed messages' spacing, 2026-10-05)
    return text


def _retell(r: dict) -> None:
    """A user row its reader marked typed, told again by its text (tell). A row with no text is left as its reader read
    it: nothing in it tells otherwise (SpecStory keeps a turn the developer sent without its content: formats._prompt_row)."""
    if not (r["content"] or "").strip():
        return
    content, typed, actor, kind, command = tell(r["content"] or "", template_anywhere=False)
    r["content"], r["typed_by_developer"], r["actor"], r["kind"] = content, typed, actor, kind
    if command:
        r["command"] = command


def _automatic(key: str, automatic: set[str]) -> bool:
    """A prompt nobody typed: exactly, or for texts of 30 characters or more, by their opening (study `_automatic`)."""
    for a in automatic:
        if a and (a == key or (len(a) >= 30 and len(key) >= 30 and (a.startswith(key[:60]) or key.startswith(a[:60])))):
            return True
    return False


def _entry_text(e: dict) -> str:
    message = e.get("message") if isinstance(e.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        return " ".join(str(b.get("text") or b.get("content") or "") for b in content if isinstance(b, dict))
    return content if isinstance(content, str) else ""


def _said(e: dict) -> str:
    """What a user entry says: its text, never a tool result's output, which is the tool's whatever it holds (a Read of
    a transcript holds `<command-name>` tags: taken for the entry's own text, it made a typed command and lost the
    result's row; the partner session's review of 2026-10-03, 8 rows in one part of the sample)."""
    message = e.get("message") if isinstance(e.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        return " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return content if isinstance(content, str) else ""


def _teammate(e: dict, key: str) -> bool:
    """Another agent of a team: its message wrapped in `<teammate-message`, or written while a team runs (`teamName`)
    without the `permissionMode` every entry the developer types carries."""
    return key.startswith("<teammate-message") or bool(e.get("teamName") and not e.get("permissionMode"))


def _marks(e: dict, text: str) -> str | None:
    """'peer' or 'harness' by Claude Code's own marks (G-90, G-94), else None."""
    origin = e.get("origin") if isinstance(e.get("origin"), dict) else {}
    if origin.get("kind") == "peer" or e.get("turnOrigin") == "peer" or PEER.match(_after_reminders(text)):
        return "peer"
    if origin.get("kind") in HARNESS_ORIGINS or e.get("turnOrigin") in HARNESS_TURNS or e.get("promptSource") == "system":
        return "harness"
    return None


def _read(files: list[tuple[str, Path]]) -> list[tuple[str, int, dict]]:
    out = []
    for name, local in files:
        with open(local, "rb") as fh:
            for n, raw in enumerate(fh, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    value = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    out.append((name, n, value))
    return out


QUEUED_FROM = ("peer", "coordinator")   # a queued message's origin when another agent or session sent it


def _prompt_text(prompt) -> str:
    """A queued prompt's text as a user entry's reads: its text blocks, an image or a document as its mark."""
    if isinstance(prompt, str):
        return prompt
    parts = []
    for b in prompt if isinstance(prompt, list) else []:
        if isinstance(b, dict) and b.get("type") == "text":
            parts.append(str(b.get("text") or ""))
        elif isinstance(b, dict) and b.get("type") in ("image", "document"):
            src = b.get("source") if isinstance(b.get("source"), dict) else {}
            parts.append(f"\n[{b['type'].capitalize()}: {src.get('media_type') or 'unknown'}]")
    return "".join(t if t.startswith("\n[") else ("\n" if i else "") + t for i, t in enumerate(parts)).strip("\n")


def _seconds(value) -> float | None:
    t = _iso(value)
    try:
        return datetime.fromisoformat(t.replace("Z", "+00:00")).timestamp() if t else None
    except ValueError:
        return None


def _absorb(entries: list[tuple[str, int, dict]]) -> list[tuple[str, int, dict]]:
    """A message sent while the agent works, as newer Claude Code records it: an `enqueue` with no text and, at the same
    moment, a `queued_command` attachment holding the message, which the agent reads inside its turn
    (`absorbed_mid_turn`) and no user entry repeats; some versions write the attachment alone. The enqueue takes the
    attachment's text (and its origin, `_origin`); an attachment no enqueue holds becomes one where no user entry or
    queued text records its words. About 1,000 messages of version 2026-10-11 (Entire's, SWE-chat v2's) were in no row
    until 2026-10-13 (#30)."""
    recorded = set()
    for _, _, e in entries:
        if e.get("type") == "queue-operation" and e.get("operation") == "enqueue" and isinstance(e.get("content"), str):
            recorded.add(_key(_after_reminders(e["content"])))
        elif e.get("type") == "user":
            recorded.add(_key(_after_reminders(_said(e))))
    out, waiting = list(entries), []   # enqueues not yet holding an attachment's message
    for i, (name, n, e) in enumerate(entries):
        if e.get("type") == "queue-operation" and e.get("operation") == "enqueue":
            waiting.append(i)
            continue
        a = e.get("attachment") if e.get("type") == "attachment" else None
        if not isinstance(a, dict) or a.get("type") != "queued_command":
            continue
        text = _prompt_text(a.get("prompt"))
        when = _seconds(a.get("timestamp") or e.get("timestamp"))
        if not text.strip() or when is None:
            continue
        origin = (a.get("origin") or {}).get("kind") if isinstance(a.get("origin"), dict) else None
        j = next((j for j in reversed(waiting) if (s := _seconds(entries[j][2].get("timestamp"))) is not None
                  and abs(s - when) <= 1), None)   # its enqueue, written the same moment (1 ms apart at most, seen)
        if j is not None:
            waiting.remove(j)
            q = entries[j][2]
            if not (isinstance(q.get("content"), str) and q["content"].strip()):
                out[j] = (entries[j][0], entries[j][1], {**q, "content": text, "_origin": origin})
        elif _key(_after_reminders(text)) not in recorded:
            out[i] = (name, n, {"type": "queue-operation", "operation": "enqueue", "content": text, "_origin": origin,
                                **{k: e.get(k) for k in ("timestamp", "uuid", "parentUuid", "logicalParentUuid")}})
    return out


def _typed_sets(entries: list[tuple[str, int, dict]]) -> dict:
    """The study's `transcript_prompts` over the main thread: what was typed, the commands, the queued messages, every
    other user entry, and the prompts nobody typed (a /loop's or a scheduled wake-up's, one enqueued again and again).
    Each by its words, a note in front aside (_after_reminders); an entry Claude Code marks as another's or the
    harness's is among the others, as the reader files it, a /loop it runs again still naming the loop's prompt. Both
    differ from the study's sets on purpose (2026-10-12; tests/regress_study.py's first check names them)."""
    typed, commands, queued, other, scheduled, peers = [], set(), set(), set(), set(), set()
    for _, _, e in entries:
        if e.get("type") == "queue-operation":
            if e.get("operation") == "enqueue" and isinstance(e.get("content"), str):
                queued.add(_key(_after_reminders(e["content"])))
            continue
        message = e.get("message") if isinstance(e.get("message"), dict) else {}
        content = message.get("content")
        if e.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if (isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") in ("ScheduleWakeup", "CronCreate")
                        and isinstance((b.get("input") or {}).get("prompt"), str)):
                    scheduled.add(b["input"]["prompt"])
            continue
        if e.get("type") != "user":
            continue
        text = _said(e)   # its text, never a tool result's output (a Read of an old transcript held "/loop ...")
        words = _words(text, _media(content))
        key = _key(words)   # the message's words: a note's time differs on every message
        if e.get("isSidechain") or e.get("isCompactSummary") or e.get("isVisibleInTranscriptOnly") or _teammate(e, key):
            other.add(key)
        elif mark := _marks(e, text):   # as the reader files it: another's or the harness's, never typed
            other.add(key)
            if mark == "peer":
                peers.add(key)
            if not e.get("isMeta") and "<command-name>" in words and not words.lstrip().startswith("<system-reminder>"):
                commands.add(_key(_command(words)))   # a /loop it runs again still names the loop's prompt
            elif key.startswith("/loop "):
                commands.add(key)   # so does one written out
        elif _prompt(e):
            typed.append(key)
        elif not e.get("isMeta") and "<command-name>" in words and not words.lstrip().startswith("<system-reminder>"):
            commands.add(_key(_command(words)))
        else:
            other.add(key)
    loops = {c.split(" ", 1)[1] for c in (*commands, *typed, *queued) if c.startswith("/loop ") and " " in c}
    loops |= {_key(x) for x in scheduled}
    loops |= {_key(x.split(" ", 1)[1]) for x in scheduled if x.startswith("/loop ") and " " in x}
    loops = {re.sub(r"^\d+\s*[smhd]\w*\s+", "", x) for x in loops} | loops
    # Nobody typed a /loop's prompt or a scheduled one. A text enqueued more than once is no sign: a developer queues
    # the same command or message again while the agent works (`/compact` 8 times, `/model` 3 times in sessions of
    # version 2026-10-04: about 300 typed commands lost until 2026-10-07), as prompts were found in 2026-10-03.
    automatic = {x for x in loops if x.strip()}
    return {"typed": set(typed), "commands": commands, "queued": queued, "other": other, "automatic": automatic,
            "peers": peers,
            "scheduled": {x for x in loops if x.strip()}}


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return content if isinstance(content, str) else ""


def _usage(message: dict) -> dict:
    u = message.get("usage") if isinstance(message.get("usage"), dict) else {}
    return {k: u.get(k) for k in TOKENS if isinstance(u.get(k), int)}


def _cc_thread(file_entries: list[tuple[str, int, dict]], sets: dict, state: dict, subagent: bool) -> list[dict]:
    """One thread's rows, in order: the main conversation, or a subagent's (whose user entries are its parent agent's
    prompts, never the developer's)."""
    rows: list[dict] = []
    waiting, fired, seen_blocks = state["waiting"], state["fired"], state["seen"]
    usage_of: dict[str, dict] = {}
    for fname, line, e in file_entries:
        kind = e.get("type")
        base = {"raw_file": fname, "raw_line": line, "source_entry_uuid": e.get("uuid"),
                "parent_uuid": e.get("parentUuid") or e.get("logicalParentUuid"), "timestamp": _iso(e.get("timestamp"))}
        if kind == "queue-operation":
            op = e.get("operation")
            text = e.get("content") if isinstance(e.get("content"), str) else ""
            said = _after_reminders(text)   # the message, whatever note comes first
            notice = _task_notice(said)
            subtype = ("task_notification" if said.lstrip().startswith("<task-notification") or notice
                       else QUEUE_SUBTYPE.get(op, "other"))
            typed, k, who, shown = False, "notice" if notice else "queued", "harness", text
            if op == "enqueue" and text.strip() and not subagent and not notice:
                key = _key(said)   # its words: a note's time differs on every message, its delivery's and its repeats'
                told = tell(said, template_anywhere=False)
                # a compaction summary `/compact` queued is the harness's, as its delivery is (2 Entire rows read as
                # typed in version 2026-10-04 until fixed by hand, #23)
                # A `/goal` typed while the agent works is queued as `Goal set: …` and delivered as Claude Code's own
                # continuation: the queued row is the command's only record, the developer's (11 rows of version
                # 2026-10-11 counted nowhere from the change of 2026-10-12 until fixed by hand, #30).
                origin = e.get("_origin")   # an absorbed message's, as its attachment marks it (_absorb)
                goal = said.lstrip().startswith(GOAL_SET) and key not in sets["peers"] and origin not in QUEUED_FROM
                typed = not (said.strip().startswith(("<", *NOTICES)) or said.lstrip().startswith(CONTINUED)
                             or re.fullmatch(r"/[\w:.-]+", said.strip())
                             or key in fired or _automatic(key, sets["automatic"])
                             or (key in sets["other"] and key not in sets["typed"] and not goal)
                             or origin in QUEUED_FROM or (origin in HARNESS_ORIGINS and not goal))
                if said.strip().startswith(PLANS):
                    # A plan the developer accepted while the agent worked: theirs, counted once where it was queued
                    # (as the study counts it), never a typed message; its delivery is a copy.
                    if typed:
                        who, k = "developer", "plan"
                        waiting[key] = len(rows)
                    typed = False
                elif typed and not told[1]:
                    # A text a rule names (a review template, an orchestrator's prompt, a hook's) is no one's typing
                    # when queued either, as its delivery is not (finish's retell): queued text was never told by its
                    # text until 2026-10-07, when a text queued twice stopped being taken for a program's.
                    typed, who = False, told[2] or "harness"
                elif typed:
                    who = "developer"
                    fired.add(key)
                    waiting[key] = len(rows)   # its delivery, later, is a copy of this row
                    # its text as a delivered message's is told (finish's retell: notes and tools' blocks off): one
                    # never delivered is the message's only row, and near-copies compare it with the delivered one
                    shown = told[0]
                elif not told[1]:
                    who = told[2] or "harness"   # another's text (a peer's, an agent's), as its delivery is told
                if not typed and who == "harness" and (PEER.match(said) or key in sets["peers"] or origin in QUEUED_FROM):
                    who = "peer"   # as Claude Code's mark files its delivery (_marks)
            rows.append(row(**base, role="metadata", turn_type="queue_operation", queue_op_subtype=subtype,
                            content=shown or None, kind=k, actor=who, typed_by_developer=typed))
            continue
        message = e.get("message") if isinstance(e.get("message"), dict) else {}
        content = message.get("content")
        if kind == "assistant":
            model, mid = message.get("model"), message.get("id") or e.get("uuid")
            if message.get("id") and _usage(message):
                usage_of[mid] = _usage(message)   # the last entry of a message carries its final counts
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
            for i, b in enumerate(blocks):
                if not isinstance(b, dict):
                    continue
                mark = (message.get("id"), json.dumps(b, sort_keys=True))
                if message.get("id") and mark in seen_blocks:
                    continue   # Claude Code wrote this block twice: it adds nothing
                seen_blocks.add(mark)
                common = {**base, "response_id": message.get("id"), "content_block_index": i, "model": model}
                btype = b.get("type")
                if btype == "tool_use":
                    rows.append(row(**common, role="tool_use", turn_type="tool_use", content=_json(b.get("input")),
                                    tool_name=b.get("name"), tool_call_id=b.get("id"), tool_input_json=_json(b.get("input")),
                                    **tool_fields(b.get("name"), b.get("input")), kind="tool_call", actor="agent",
                                    typed_by_developer=False))
                elif btype in ("thinking", "redacted_thinking"):
                    rows.append(row(**common, role="assistant", turn_type="assistant_thinking",
                                    content=b.get("thinking"), kind="thinking", actor="agent", typed_by_developer=False))
                elif btype == "text" and str(b.get("text") or "").strip():
                    synthetic = model == SYNTHETIC   # Claude Code's own message, written in the agent's place
                    rows.append(row(**common, role="assistant", turn_type="assistant_response", content=b.get("text"),
                                    kind="notice" if synthetic else "text", actor="harness" if synthetic else "agent",
                                    typed_by_developer=False))
            continue
        if kind == "system":
            body = e.get("content") if isinstance(e.get("content"), str) else _json(e.get("content"))
            rows.append(row(**base, role="metadata", turn_type="system_event", content=body,
                            kind="compaction" if e.get("subtype") == "compact_boundary" else "notice", actor="harness",
                            typed_by_developer=False))
            continue
        if kind == "summary":
            rows.append(row(**base, role="metadata", turn_type="summary", content=e.get("summary"), kind="compaction",
                            actor="harness", typed_by_developer=False))
            continue
        if kind == "file-history-snapshot":
            rows.append(row(**base, role="metadata", turn_type="file_snapshot", kind="snapshot", actor="harness",
                            typed_by_developer=False))
            continue
        if kind == "progress":
            rows.append(row(**base, role="metadata", turn_type="progress", kind="progress", actor="harness",
                            typed_by_developer=False))
            continue
        if kind != "user":
            continue   # attachments and other entries SWE-chat files no row for
        text = _said(e)
        words = _words(text, _media(content))   # the message, whatever note comes first; the note decides nothing
        key = _key(words)
        wlead = words.lstrip()
        marks = _marks(e, text)
        # Who wrote this user entry, and whether the developer typed it.
        who, typed, k = "harness", False, "text"
        own_row = False   # the entry's text already has its row (a command); its tool results still get theirs
        continuation = wlead.startswith(CONTINUED)
        if subagent or e.get("isSidechain"):
            # a parent agent's prompt to its subagent; Claude Code's own warm-up of it is the harness's
            who = "harness" if words.strip() in HARNESS_EXACT else "agent"
        elif e.get("isCompactSummary") or e.get("isVisibleInTranscriptOnly") or continuation:
            who, k = "harness", "compaction"
        elif _teammate(e, key) or marks == "peer":
            who = "peer"
        elif marks == "harness" or e.get("isMeta"):
            who = "harness"
            if wlead.startswith(GOAL_SET):
                # A queued /goal delivered: its queuing is over, so the same goal queued again later is typed again
                # (2 rows of version 2026-10-11 counted nowhere, a repeat counted at a delivery never counted; #30).
                fired.discard(key)
            if _notice(words, wlead):   # its kind by its text, as an unmarked entry's: 28,546 task notifications marked
                k = "notice"          # the harness's read `text`, while every other format's read `notice` (2026-10-05)
        elif e.get("planContent") is not None or wlead.startswith(PLANS):
            who, k = "developer", "plan"
        elif "<command-name>" in words and not wlead.startswith(("<task-notification", "<system-reminder>")):
            # A task's notice can quote the command that started it (`<command-name>` inside): still the harness's
            # notice, as its other 3,555 rows in SWE-chat v2 are (2 rows read as a typed command in version
            # 2026-10-04 until fixed by hand, #23).
            said = _command(words)
            ckey = _key(said)
            typed = bool(said) and not (said.startswith("/loop") and ckey in fired) and not _automatic(ckey, sets["automatic"])
            copy = waiting.pop(ckey, None)
            if copy is not None:
                typed = True
            elif typed:
                fired.add(ckey)
            rows.append(row(**base, role="user", turn_type="user_prompt", content=text, command=said, kind="command",
                            actor="developer" if typed else "harness", typed_by_developer=typed, copy_of=copy,
                            content_block_index=0))
            own_row = True
        elif wlead.startswith("<bash-input>"):
            who, k = "developer", "shell"
        elif wlead.startswith("[Request interrupted"):
            who, k = "developer", "interrupt"
        elif wlead.startswith(NOT_A_PROMPT) or wlead.startswith(CC_BUILT_IN) or _task_notice(words):
            who, k = "harness", "notice"
        elif _prompt(e) and _automatic(key, sets["scheduled"]):
            # A prompt the agent scheduled (ScheduleWakeup, CronCreate) or a /loop fires, delivered as an ordinary
            # entry with no mark (15 in the sample's sessions, besides 162 enqueued and 138 meta): nobody typed it
            # (the study's intent, docs/study.md). Only these: a message enqueued twice is no sign (a developer typed
            # "全部やる" twice while the agent worked, regression of 2026-10-03).
            who = "harness"
        elif _prompt(e):
            who, typed = "developer", True
        copy = waiting.pop(key, None) if ((typed or k == "plan") and key and not own_row) else None
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
        texts, first = [], None
        for i, b in enumerate(blocks):
            if not isinstance(b, dict):
                continue
            btype = b.get("type")
            if btype == "tool_result":
                rtext = _result_text(b)
                common = {**base, "content_block_index": i, "tool_call_id": b.get("tool_use_id"),
                          "is_error": bool(b.get("is_error")), "content": rtext[:RESULT_CUT],
                          "content_truncated": len(rtext) > RESULT_CUT}
                if rtext.startswith(REJECTED) and not subagent:
                    words = rtext.split(SAID, 1)[1] if SAID in rtext else ""
                    words = words.split(NOTE, 1)[0].strip()
                    if not words.startswith(NOTICES):
                        rows.append(row(**common, role="tool_result", turn_type="tool_result", kind="rejection",
                                        actor="developer", typed_by_developer=bool(words)))
                        continue
                rows.append(row(**common, role="tool_result", turn_type="tool_result", kind="tool_result",
                                actor="tool", typed_by_developer=False))
                continue
            first = i if first is None else first
            if btype == "text":
                texts.append(str(b.get("text") or ""))
            elif btype in ("image", "document"):
                src = b.get("source") if isinstance(b.get("source"), dict) else {}
                texts.append(f"\n[{btype.capitalize()}: {src.get('media_type') or 'unknown'}]")
        body = "".join(t if t.startswith("\n[") else ("\n" if i else "") + t for i, t in enumerate(texts)).strip("\n")
        if body.strip() and not own_row:
            injected = e.get("isMeta") or wlead.startswith(INJECTED_LEADS)   # a note in front decides nothing
            turn = "system_injected" if (not subagent and injected) else "user_prompt"
            rows.append(row(**base, content_block_index=first or 0, role="user", turn_type=turn, content=body, kind=k,
                            actor=who, typed_by_developer=typed and k == "text", copy_of=copy,
                            is_continuation=continuation or None))
    # Token counts on a message's text row, or on its first tool call when it has no text (SWE-chat's placement).
    placed: set[str] = set()
    for r in rows:
        mid = r["response_id"]
        if mid in usage_of and mid not in placed and r["turn_type"] == "assistant_response":
            r.update(usage_of[mid])
            placed.add(mid)
    for r in rows:
        mid = r["response_id"]
        if mid in usage_of and mid not in placed and r["turn_type"] == "tool_use":
            r.update(usage_of[mid])
            placed.add(mid)
    return rows


def claude_code(files: list[tuple[str, Path]], session: dict) -> tuple[list[dict], list[dict]]:
    """A Claude Code session: (its conversation's rows, its subagents' rows). The main transcript's parts in order;
    each subagent's file (`subagents/<tool call id>/agent-<id>.jsonl`) is a thread of its own, keyed by the call that
    started it, as SWE-chat's `conversations_subagents`."""
    main = sorted((f for f in files if MAIN_PART.search(f[0])), key=lambda f: int(MAIN_PART.search(f[0]).group(1) or 0))
    subs = sorted(f for f in files if f[0].endswith(".jsonl") and not MAIN_PART.search(f[0]))
    entries = _absorb(_read(main))   # messages sent mid-turn as their enqueue holds them
    sets = _typed_sets(entries)
    state = {"waiting": {}, "fired": set(), "seen": set()}
    inline = [x for x in entries if x[2].get("isSidechain")]   # older versions: subagents inside the transcript
    if inline and len(inline) == len(entries):
        # Every entry a subagent's: the subagent's own run, recorded as a session of its own (Entire, 56 sessions with
        # no rows, 2026-10-05); its thread is the session's own, its prompts its parent agent's.
        rows, inline = finish(_cc_thread(inline, sets, state, True), session), []
    else:
        rows = finish(_cc_thread([x for x in entries if not x[2].get("isSidechain")], sets, state, False), session)
    # A run through the SDK (`claude -p`, an app built on it) records no sign of who wrote its prompts: they are
    # typed, as every message a person may have written is (the owner's rule of 2026-10-07: typed misses no
    # developer's message, and holds some a program wrote). Left unknown from 2026-10-05 to 2026-10-07.
    sub_rows: list[dict] = []
    threads: dict[str, list] = defaultdict(list)
    for x in inline:
        threads[str(x[2].get("agentId") or "sidechain")].append(x)
    owner = session.get("checkpoint_pk") or session.get("session_key")
    for agent_id, xs in sorted(threads.items()):
        keys = {"task_pk": f"{owner}#{agent_id}", "agent_id": agent_id}
        sub_rows += finish(_cc_thread(xs, sets, state, True), session, keys)
    for name, local in subs:
        parts = Path(name).parts
        call = parts[1] if len(parts) > 2 and parts[0] == "subagents" else Path(name).stem
        keys = {"task_pk": f"{owner}#{call}", "agent_id": Path(name).stem}
        sub_rows += finish(_cc_thread(_read([(name, local)]), sets, state, True), session, keys)
    return rows, sub_rows


def typed_messages(rows: list[dict], *, by_id: bool = True, per_turn: bool = False) -> int | None:
    """Messages the developer typed: distinct entries (or queue operations) with a typed row, a delivered copy not
    counted again; None when the format cannot tell. An entry is its id (`by_id`: Claude Code, whose entry written
    twice keeps its uuid) or else its place: its line, or for a message of a JSON document its row (OpenCode's ids
    repeat across different messages: 36 ids in 5 of 339 sessions, 12 of them on both a user and an agent message).
    `per_turn`: a format whose one line holds a whole session (a copied dataset's export: DataClaw, OpenHands
    feedback) gives every message the same place, so each conversational turn is its own entry. Counted by place,
    such a session was 1 message: 4,042 sessions of version 2026-10-04 until counted again by hand (#24)."""
    if any(r["typed_by_developer"] is None for r in rows if r["role"] == "user"):
        return None
    if per_turn:
        entry = lambda r: (("turn", r["conversation_turn_number"]) if r["conversation_turn_number"] is not None
                           else ("row", r["turn_number"]))
        return len({entry(r) for r in rows if r["typed_by_developer"] and r["copy_of"] is None})
    place = lambda r: (r["raw_file"], r["raw_line"]) if r["raw_line"] is not None else ("row", r["turn_number"])
    return len({(r["source_entry_uuid"] if by_id else None) or place(r)
                for r in rows if r["typed_by_developer"] and r["copy_of"] is None})


def count_typed(agent: str | None, rows: list[dict], *, per_turn: bool = False) -> int | None:
    """`typed_messages` as the format's entries are told apart (`per_turn`: a copied dataset's export, one line a
    session)."""
    return typed_messages(rows, by_id=(agent or "").strip().lower() == "claude code", per_turn=per_turn)


EXTRACTORS = {"claude code": claude_code}


CC_TYPES = {"user", "assistant", "summary", "system", "file-history-snapshot", "progress", "queue-operation",
            "attachment", "custom-title", "agent-name", "last-prompt", "pr-link"}
CODEX_TYPES = {"session_meta", "response_item", "event_msg", "turn_context"}
SNIFFED_AGENT = {"claude code": "Claude Code", "codex": "Codex", "gemini cli": "Gemini CLI", "copilot cli": "Copilot CLI",
                 "cursor": "Cursor", "pi": "Pi", "opencode": "OpenCode"}


def sniff(files: list[tuple[str, Path]]) -> str | None:
    """The format of a transcript whose agent has no reader by its name (SWE-chat records 52 sessions as `unknown`,
    50 of them Claude Code's lines): `claude code` for Claude Code's entries, `gemini cli` for Gemini CLI's
    document; else None."""
    main = [p for n, p in files if MAIN_PART.search(n) or n.endswith("full.json")]
    if not main:
        return None
    with open(main[0], "rb") as fh:
        head = fh.read(4096)
        fh.seek(0)
        # Whole lines, not a slice: a Codex rollout's first line holds its instructions, often past 4 KB, and 208
        # Entire sessions recorded as Claude Code's were read by no reader for it (2026-10-05).
        lines = [fh.readline(8 << 20) for _ in range(3)]
    for raw in lines:
        if not raw.strip():
            continue
        try:
            e = json.loads(raw)
        except ValueError:
            break
        if isinstance(e, dict) and e.get("type") in CC_TYPES and (
                {"uuid", "sessionId", "leafUuid", "messageId"} & set(e)):
            return "claude code"
        if isinstance(e, dict) and e.get("type") in CODEX_TYPES and isinstance(e.get("payload"), dict):
            return "codex"
        # Entire's sessions recorded as Claude Code's whose transcript is another agent's (2026-10-05, of 175 with
        # no rows): Copilot CLI's events, Cursor's messages, Pi's entries.
        if (isinstance(e, dict) and isinstance(e.get("data"), dict)
                and str(e.get("type") or "").startswith(("session.", "user.", "assistant.", "tool.", "hook."))):
            return "copilot cli"
        if isinstance(e, dict) and set(e) == {"role", "message"} and e.get("role") in ("user", "assistant"):
            return "cursor"
        if (isinstance(e, dict) and e.get("type") == "message" and "parentId" in e and isinstance(e.get("message"), dict)
                and "role" in e["message"]):
            return "pi"
    if re.match(rb'\s*\{\s*"info"\s*:\s*\{', head):   # OpenCode's export (5 Entire sessions recorded as Claude Code's)
        return "opencode"
    if head.lstrip().startswith(b"{") and b'"messages"' in head and (b'"sessionId"' in head or b'"projectHash"' in head):
        return "gemini cli"
    return None


HEAD_BYTES = 1 << 16


def _head(path: Path) -> str:
    """A file's first HEAD_BYTES, as text."""
    with open(path, "rb") as fh:
        return fh.read(HEAD_BYTES).decode("utf-8", errors="replace")


def no_rows_reason(files: list[tuple[str, Path]]) -> str:
    """Why a session's files gave no rows, recorded on the session (every kept session has rows or a reason,
    2026-10-05): files with nothing in them; SpecStory's markdown with no message, or a markdown note;
    a transcript in a format a reader knows with no message in it; or a format no reader knows. Reads at most
    64 KB of a file: a copied dataset's export can be gigabytes (#17's review)."""
    texts = [(n, _head(p)) for n, p in files if p.exists()]
    if not texts:   # first: none of its files on disk is not an empty file (#17's review of bfe1377)
        return "the session's files are missing"
    big = any(p.stat().st_size > HEAD_BYTES for _, p in files if p.exists())
    if not big and not any(t.strip() for _, t in texts):
        return "the session's files are empty"
    name, text = texts[0]
    if name.endswith(".md"):
        if "SpecStory" in text[:400] or text.lstrip().startswith("<!--"):
            return "a SpecStory file with no messages"
        return "a markdown file with no chat in it"
    return "the transcript holds no messages" if sniff(files) else "no reader for this file's format"


def for_session(agent: str | None, files: list[tuple[str, Path]], session: dict):
    """A session's (conversation rows, subagent rows), by its agent, else by its transcript's own format (sniff);
    None for a format with no reader."""
    from .formats import EXTRACTORS as FORMATS   # built on this module's rows

    key = (agent or "").strip().lower()
    named = EXTRACTORS.get(key) or FORMATS.get(key)
    found = sniff(files)
    sniffed = (EXTRACTORS.get(found) or FORMATS.get(found)) if found else None
    out = named(files, session) if named else None
    if not (out and (out[0] or out[1])) and sniffed is not None and sniffed is not named:
        # The recorded agent's reader first; the transcript's own format only where it read nothing. Of 208 Entire
        # sessions recorded as Claude Code's with no rows, 162 were Codex, Cursor, Copilot CLI or Pi transcripts
        # (2026-10-05); a format tried first would have read 254 Factory Droid sessions as Pi's. The format names the
        # agent where the recorded one's reader read nothing or none was recorded; a recorded name with no reader of
        # its own ("Agent", an app writing Claude Code's format) is kept.
        if named is not None or not agent:
            session["agent"] = SNIFFED_AGENT[found]
        out = sniffed(files, session)
    return out
