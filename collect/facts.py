"""Cheap facts from a session's files: counts, times, models. One parser per format; none calls a model.

Counts, as the dataset card defines them:

- ``n_user_entries``: every entry the format records in the user's role (for
  Claude Code this includes tool results, which it records as user entries).
- ``n_user_messages``: what a person typed. Set only where the format marks
  typed text apart from text the tool inserted; None where it does not.
- ``n_agent_messages``: the agent's messages with visible text.
- ``n_tool_calls``: the agent's tool calls.

How each format tells typed text from inserted text was read off the files on
2026-10-03 (structure only), and is stated with its parser below. A parser that
meets a file it cannot read returns no counts rather than wrong ones, and says
why in ``parse_error``.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

COUNTS = ("n_user_entries", "n_user_messages", "n_agent_messages", "n_tool_calls", "has_tool_results",
          "has_timestamps", "started_at", "ended_at", "models", "agent_version")
# Raised whenever a parser changes, so rows counted by an older one are counted again from their files.
FACTS_VERSION = 6  # 6: Cursor's own phrases inside <user_query> are not typed (2026-10-03); 5: Codex's UserMessage items, Gemini CLI's {role, parts} files and Copilot CLI's authorship by
# `source` (2026-10-03, measured on the sample); 4: a prompt command counts once, pasted images count (review,
# 2026-10-03); 3: Aider's typed
# count left empty, SpecStory times as UTC, no started_at from a checkpoint


def _iso(value) -> str | None:
    """A timestamp as UTC ISO 8601 with Z, from ISO strings or epoch seconds or milliseconds; None if unreadable."""
    if value is None or value == "":
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            v = float(value)
            t = datetime.fromtimestamp(v / 1000 if v > 1e11 else v, tz=timezone.utc)
        else:
            t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            t = t if t.tzinfo else t.replace(tzinfo=timezone.utc)
        if t.year < 2000:   # zero or near it: a placeholder (SpecStory's 1970-01-01 in 7 sessions' times, 2026-10-05)
            return None
        return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    except (ValueError, OverflowError, OSError):
        return None


def _span(times: list[str]) -> dict:
    times = sorted(t for t in times if t)
    return {"has_timestamps": bool(times), "started_at": times[0] if times else None,
            "ended_at": times[-1] if times else None}


def _lines(parts: list[Path]) -> list[dict]:
    """Every JSON object line of a transcript, its parts read in order.

    Entire stores a transcript over 50 MB as `full.jsonl`, `full.jsonl.001`,
    `full.jsonl.002`, ..., split only between lines and joined back with a
    newline (`agent.ChunkJSONL`, `ReassembleJSONL` in entireio/cli), so the
    parts' lines in order are the transcript's lines.
    """
    out = []
    for path in parts:
        with open(path, "rb") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    out.append(value)
    return out


_ESCAPE = re.compile(rb'\\(\\|[/"bfnrt]|u[0-9a-fA-F]{4})|\\')


def _lenient(raw: bytes):
    """JSON, read as written; where a writer left a backslash that starts no JSON escape (measured: Gemini CLI's
    `\\R` in 2 files of 303), that backslash is read as a literal backslash rather than failing the whole file."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        if "escape" not in str(e):
            raise
        return json.loads(_ESCAPE.sub(lambda m: m.group(0) if m.group(1) else b"\\\\", raw))


def opencode_doc(raw: bytes) -> dict:
    """OpenCode's export as one document. Two other shapes were found over the collection (#7, 15 of its Entire
    sessions). In 10, a line was printed ahead of the export (`mise ... tools: opencode@...`, `[opencode-litellm]
    Discovered ...`); it is skipped up to the first line that opens `{`. In 5, the messages were written one per line;
    they are read as the document's `messages`."""
    if not raw.lstrip().startswith(b"{"):
        at = raw.find(b"\n{")
        raw = raw[at + 1:] if at >= 0 else raw
    try:
        value = _lenient(raw)
    except json.JSONDecodeError as e:
        if not str(e).startswith("Extra data"):
            raise
        value = {"messages": [_lenient(line) for line in raw.splitlines() if line.strip()]}
    if not isinstance(value, dict):
        raise ValueError("not a JSON object")
    return value


def _docs(parts: list[Path]) -> list[dict]:
    """A transcript stored as one JSON document, or one document per part when Entire split it."""
    docs = []
    for path in parts:
        value = _lenient(path.read_bytes())
        if not isinstance(value, dict):
            raise ValueError("not a JSON object")
        docs.append(value)
    return docs


def _text(content) -> str:
    if isinstance(content, str):
        return content
    parts = []
    for block in content if isinstance(content, list) else []:
        if isinstance(block, dict) and block.get("type") in (None, "text", "input_text", "output_text"):
            parts.append(str(block.get("text") or ""))
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


# -- Claude Code ------------------------------------------------------------------
# Who wrote a user entry, by Claude Code's own marks (errata-bench G-90, G-93,
# G-94; measured on 6,464 transcripts on 2026-10-02):
INJECTED = re.compile(r"^\s*<(local-command-caveat|local-command-stdout|local-command-stderr|bash-stdout|bash-stderr"
                      r"|command-name|task-notification|system-reminder|system[_-]instructions?|ci-monitor-event)>")
CONTINUED = "This session is being continued"
PEER = re.compile(r"^\s*(?:<(?:teammate-message|agent-message|cross-session-message|relay)\b"
                  r"|Another Claude session sent a message)")
HARNESS_ORIGINS = frozenset({"task-notification", "auto-continuation"})
HARNESS_TURNS = frozenset({"task_notification", "scheduled", "system"})
TOOL_LOADED = "Tool loaded."
SYNTHETIC = "<synthetic>"


def cc_typed(entry: dict, text: str, *, beside_result: bool = False) -> bool:
    """Whether a Claude Code user entry's text is what the developer typed.

    Not typed: a compaction summary, an accepted plan (`planContent`), output
    Claude Code puts in the turn, another agent's message, the harness's own
    prompts, a meta entry (a prompt command's expansion among them: the command's
    own entry is what counts), and "Tool loaded." beside a deferred tool's result. errata-bench keeps
    compaction summaries as the developer's turn for its own reasons; nobody
    typed one, so here they are not counted.
    """
    if entry.get("isCompactSummary") or text.lstrip().startswith(CONTINUED) or entry.get("planContent") is not None:
        return False
    if INJECTED.match(text):
        return False
    origin = entry.get("origin") if isinstance(entry.get("origin"), dict) else {}
    if origin.get("kind") == "peer" or entry.get("turnOrigin") == "peer" or PEER.match(text):
        return False
    if (origin.get("kind") in HARNESS_ORIGINS or entry.get("turnOrigin") in HARNESS_TURNS
            or entry.get("promptSource") == "system"
            or entry.get("isMeta")
            or beside_result and text.strip() == TOOL_LOADED):
        return False
    return True


def claude_code(parts: list[Path]) -> dict:
    entries = _lines(parts)
    index = {e["uuid"]: i for i, e in enumerate(entries) if isinstance(e.get("uuid"), str)}
    user_entries = typed = tool_calls = 0
    has_results = False
    agent_ids, agent_plain = set(), 0
    models: Counter = Counter()
    times, version = [], None
    for i, e in enumerate(entries):
        times.append(_iso(e.get("timestamp")))
        if isinstance(e.get("version"), str):
            version = e["version"]
        kind = e.get("type")
        msg = e.get("message") if isinstance(e.get("message"), dict) else {}
        content = msg.get("content")
        blocks = content if isinstance(content, list) else []
        if kind == "user":
            user_entries += 1
            beside = any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)
            has_results |= beside
            if e.get("isSidechain"):
                continue  # a subagent's thread inside the transcript (older versions): the agent's prompts
            text = _text(content)
            if not text.strip():
                # Images or a document pasted alone are typed (18 such entries in the sample); a tool's result or
                # a meta entry carrying an image is not.
                pasted = any(isinstance(b, dict) and b.get("type") in ("image", "document") for b in blocks)
                typed += pasted and not beside and not e.get("isMeta") and cc_typed(e, "")
                continue
            if e.get("isMeta"):
                # The expansion of a prompt command (`/review`): its command entry (`<command-message>`) was counted,
                # so the command counts once (all 1,228 such entries in the sample are followed by an expansion).
                continue
            typed += cc_typed(e, text, beside_result=beside)
        elif kind == "assistant":
            model = msg.get("model")
            if model == SYNTHETIC:
                continue  # Claude Code's own text, not the model's
            if model:
                models[model] += 1
            tool_calls += sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use")
            if e.get("isSidechain"):
                continue
            visible = any(isinstance(b, dict) and b.get("type") == "text" and str(b.get("text") or "").strip()
                          for b in blocks) or (isinstance(content, str) and content.strip())
            if visible:
                if msg.get("id"):
                    agent_ids.add(msg["id"])  # one API message is written one block per line
                else:
                    agent_plain += 1
    return {"n_user_entries": user_entries, "n_user_messages": typed, "n_agent_messages": len(agent_ids) + agent_plain,
            "n_tool_calls": tool_calls, "has_tool_results": has_results, "models": sorted(models),
            "agent_version": version, **_span(times)}


# -- Codex (rollout JSONL) -----------------------------------------------------------
# Typed messages are `event_msg` / `user_message` events (older versions) or
# `event_msg` / `item_completed` items of type `UserMessage` (newer; no session
# of the sample's 444 has both); the user-role `response_item` messages also hold
# the instructions and environment context Codex inserts. Measured: 205 typed
# messages in 40 transcripts, none opening with an inserted tag. Some sessions
# carry neither (35 of 444 in the sample): there the typed messages cannot be
# told from the inserted ones by any mark, so the count is None, not 0.
CODEX_CALLS = {"function_call", "custom_tool_call", "local_shell_call", "web_search_call", "tool_search_call"}
CODEX_RESULTS = {"function_call_output", "custom_tool_call_output", "local_shell_call_output", "tool_search_output"}


def codex(parts: list[Path]) -> dict:
    user_entries = typed = agent = calls = 0
    events = False
    run = "interactive"
    has_results = False
    models: Counter = Counter()
    times, version = [], None
    for e in _lines(parts):
        times.append(_iso(e.get("timestamp")))
        pl = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        kind, sub = e.get("type"), pl.get("type")
        if kind == "session_meta":
            version = pl.get("cli_version") or version
            src = pl.get("source")
            run = ("subagent" if isinstance(src, dict) and "subagent" in src else
                   "headless" if src == "exec" or pl.get("originator") == "codex_exec" else run)
        elif kind == "turn_context" and pl.get("model"):
            models[pl["model"]] += 1
        elif kind == "event_msg" and (sub == "user_message" or (
                sub == "item_completed" and isinstance(pl.get("item"), dict) and pl["item"].get("type") == "UserMessage")):
            typed += 1
            events = True
        elif kind == "response_item":
            if sub == "message" and pl.get("role") == "user":
                user_entries += 1
            elif sub == "message" and pl.get("role") == "assistant" and _text(pl.get("content")).strip():
                agent += 1
            elif sub in CODEX_CALLS:
                calls += 1
            elif sub in CODEX_RESULTS:
                has_results = True
    # A subagent's or guardian's session has no typed message of its own (formats.codex_run; a forked one's inherited
    # messages are copies). An `exec` run's are typed, as every message a person may have written is (2026-10-07).
    if run == "subagent":
        typed, events = 0, True
    return {"n_user_entries": user_entries, "n_user_messages": typed if events or not user_entries else None,
            "n_agent_messages": agent, "n_tool_calls": calls, "has_tool_results": has_results,
            "models": sorted(models), "agent_version": version, **_span(times)}


# -- Copilot CLI (event JSONL) ----------------------------------------------------------
# `user.message` events. Not typed: a subagent's prompt (`source` "agent-...",
# `agentId`, `parentToolCallId`) and automation (`source` "autopilot" or
# "skill-...", `isAutopilotContinuation`, a skill's expansion opening
# `<skill-context>`); an unknown `source` makes the count unknown.
# `parentAgentTaskId` is request telemetry, not authorship: agent-note-cli's
# classifier (github.com/WdBlink/agent-note-cli, pull request 6) ignores it, and
# its test fixture is a human message carrying one. In the sample, 52 of 62
# messages carry one, each its own, none naming a tool call.
TAG_OPEN = re.compile(r"^\s*<([A-Za-z_][\w-]*)")


def copilot_author(event: dict, data: dict) -> tuple[bool | None, str | None]:
    """(typed, who) for a Copilot CLI `user.message` event: (False, "agent" or "harness") where its source says so,
    else (True, "developer")."""
    source = data.get("source")
    if (event.get("agentId") or data.get("parentToolCallId")
            or (isinstance(source, str) and re.fullmatch(r"agent-\S+", source))):
        return False, "agent"
    tag = TAG_OPEN.match(str(data.get("content") or ""))
    if (source == "autopilot" or data.get("isAutopilotContinuation") is True
            or (isinstance(source, str) and re.fullmatch(r"skill-\S+", source)) or (tag and tag.group(1) == "skill-context")):
        return False, "harness"
    # Any other source, one not seen before, is the developer's: typed misses no message a person may have written
    # (the owner's rule of 2026-10-07; 8 rows of version 2026-10-04 were left unknown).
    return True, "developer"


def copilot_cli(parts: list[Path]) -> dict:
    user_entries = typed = agent = calls = 0
    has_results = unknown = False
    models: Counter = Counter()
    times, version = [], None
    for e in _lines(parts):
        times.append(_iso(e.get("timestamp")))
        d = e.get("data") if isinstance(e.get("data"), dict) else {}
        kind = e.get("type")
        if d.get("model"):
            models[str(d["model"])] += 1
        if kind == "session.start":
            version = d.get("copilotVersion") or d.get("version") or version
        elif kind == "user.message":
            user_entries += 1
            if str(d.get("content") or "").strip():
                who = copilot_author(e, d)[0]
                typed += who is True
                unknown |= who is None
        elif kind == "assistant.message" and str(d.get("content") or "").strip():
            agent += 1
        elif kind == "tool.execution_start":
            calls += 1
        elif kind == "tool.execution_complete":
            has_results = True
    return {"n_user_entries": user_entries, "n_user_messages": None if unknown else typed, "n_agent_messages": agent,
            "n_tool_calls": calls, "has_tool_results": has_results, "models": sorted(models), "agent_version": version,
            **_span(times)}


# -- Pi (JSONL tree) -----------------------------------------------------------------
# A subagent forked from a parent session opens with the parent agent's task, in a fixed phrase (the partner
# session's read of 2026-10-04: 1 of 30 Pi sessions): the parent's, never the developer's.
PI_FORK = "Task: You are a delegated subagent running from a fork of the parent session"
# `message` entries by role. Not typed: a user-role message that is a skill's
# expansion (opens `<skill>`). Extensions' messages are `custom_message`
# entries, not user-role ones.
def pi(parts: list[Path]) -> dict:
    user_entries = typed = agent = calls = 0
    has_results = False
    models: Counter = Counter()
    times, version = [], None
    for e in _lines(parts):
        times.append(_iso(e.get("timestamp")))
        kind = e.get("type")
        if kind == "session":
            version = str(e.get("version")) if e.get("version") is not None else version
        elif kind == "model_change" and e.get("modelId"):
            models[e["modelId"]] += 1
        m = e.get("message") if isinstance(e.get("message"), dict) else {}
        role = m.get("role")
        if kind != "message":
            continue
        times.append(_iso(m.get("timestamp")))
        if role == "user":
            user_entries += 1
            text = _text(m.get("content"))
            tag = TAG_OPEN.match(text)
            if text.strip() and not (tag and tag.group(1) == "skill") and not text.lstrip().startswith(PI_FORK):
                typed += 1
        elif role == "assistant":
            if m.get("model"):
                models[m["model"]] += 1
            blocks = m.get("content") if isinstance(m.get("content"), list) else []
            calls += sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "toolCall")
            if any(isinstance(b, dict) and b.get("type") == "text" and str(b.get("text") or "").strip() for b in blocks):
                agent += 1
        elif role == "toolResult":
            has_results = True
    return {"n_user_entries": user_entries, "n_user_messages": typed, "n_agent_messages": agent, "n_tool_calls": calls,
            "has_tool_results": has_results, "models": sorted(models), "agent_version": version, **_span(times)}


# -- Cursor (role JSONL) ---------------------------------------------------------------
# Cursor wraps what the developer typed in `<user_query>`; the rest of a
# user-role message is context it attaches (timestamps, skills, files, hooks).
# Measured: 136 of 139 user messages hold one. No timestamps or models are
# recorded per message. `<user_query>` also wraps Cursor's own text, in fixed
# phrases recurring across sessions (measured on the 2026-10-03 collection, 6,259
# queries by their first words): its instructions to the agent (CURSOR_AUTOMATIC,
# about 744), an accepted plan (about 105, in 84 sessions) and its interface's
# buttons (CURSOR_ACTIONS, about 55); none is typed.
USER_QUERY = re.compile(r"<user_query>\s*(.*?)\s*(?:</user_query>|$)", re.S)
CURSOR_AUTOMATIC = ("Briefly inform the user about the task", "Perform any necessary follow-up actions in response",
                    "The beginning of the above subagent result", "The above subagent result is already visible")
CURSOR_PLAN = ("Implement the plan as specified",)
CURSOR_ACTIONS = ("Execute the selected diff-tab", "For the code present, we get this",
                  "Issue reproduced, please proceed")


def cursor_author(text: str) -> tuple[bool, str, str]:
    """(typed, who, kind) for a Cursor user message's text."""
    m = USER_QUERY.search(text)
    if not m:
        return False, "harness", "notice"
    q = m.group(1)
    for _ in range(8):   # a query quoted inside another, its timestamp first: the innermost is the message (5 rows)
        inner = USER_QUERY.search(q)
        if not inner:
            break
        q = inner.group(1)
    q = re.sub(r"^\s*(?:<timestamp>.*?</timestamp>\s*)+", "", q, flags=re.S).lstrip()
    if q.startswith(CURSOR_AUTOMATIC):
        return False, "harness", "notice"
    if q.startswith(CURSOR_PLAN):
        return False, "developer", "plan"
    if q.startswith(CURSOR_ACTIONS):
        return False, "developer", "command"
    return True, "developer", "text"
def cursor(parts: list[Path]) -> dict:
    user_entries = typed = agent = calls = 0
    has_results = False
    for e in _lines(parts):
        role = e.get("role")
        m = e.get("message") if isinstance(e.get("message"), dict) else {}
        content = m.get("content")
        blocks = content if isinstance(content, list) else []
        if role == "user":
            user_entries += 1
            typed += cursor_author(_text(content))[0]
            has_results |= any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)
        elif role == "assistant":
            calls += sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use")
            if _text(content).strip():
                agent += 1
    return {"n_user_entries": user_entries, "n_user_messages": typed, "n_agent_messages": agent, "n_tool_calls": calls,
            "has_tool_results": has_results, "models": [], "agent_version": None, **_span([])}


# -- OpenCode (one JSON document: info, messages) -------------------------------------------
# A user message is typed when one of its text parts is not marked synthetic
# (OpenCode marks the file contents and tool output it inserts so).
def opencode(parts: list[Path]) -> dict:
    docs = [opencode_doc(p.read_bytes()) for p in parts]
    doc = docs[0]
    messages = []
    for d in docs:
        got = d.get("messages")
        messages += list(got.values()) if isinstance(got, dict) else list(got or [])
    user_entries = typed = agent = calls = 0
    has_results = False
    models: Counter = Counter()
    times = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        info = msg.get("info") if isinstance(msg.get("info"), dict) else msg
        role = info.get("role")
        t = info.get("time") if isinstance(info.get("time"), dict) else {}
        times += [_iso(t.get("created")), _iso(t.get("completed"))]
        if info.get("modelID"):
            models[info["modelID"]] += 1
        parts = [p for p in (msg.get("parts") or []) if isinstance(p, dict)]
        if role == "user":
            user_entries += 1
            typed += any(p.get("type") == "text" and not p.get("synthetic") and str(p.get("text") or "").strip()
                         for p in parts)
        elif role == "assistant":
            tools = [p for p in parts if p.get("type") == "tool"]
            calls += len(tools)
            has_results |= any(isinstance(p.get("state"), dict) and p["state"].get("status") in ("completed", "error")
                               for p in tools)
            if any(p.get("type") == "text" and str(p.get("text") or "").strip() for p in parts):
                agent += 1
    version = (doc.get("info") or {}).get("version") if isinstance(doc, dict) else None
    return {"n_user_entries": user_entries, "n_user_messages": typed, "n_agent_messages": agent, "n_tool_calls": calls,
            "has_tool_results": has_results, "models": sorted(models), "agent_version": version, **_span(times)}


# -- Gemini CLI (one JSON document: messages) ---------------------------------------------
# `user` messages are typed; `info`, `error` and `warning` are the CLI's own. A
# second file shape holds `{role, parts}` messages (63 of 303 sessions in the
# sample, `turns` repeating `messages`): a user message wraps what the developer
# typed in `<USER_REQUEST>` (684 of 684), a model message is the agent's text.
def gemini_cli(parts: list[Path]) -> dict:
    """Gemini CLI's session file: one JSON document with `messages`, or (newer versions, measured: 9 of 303) JSON
    Lines with a header line, one line per message and `$set` lines updating the header."""
    try:
        docs = _docs(parts)
    except json.JSONDecodeError as e:
        if "Extra data" not in str(e):
            raise
        lines = _lines(parts)
        header = next((e for e in lines if "sessionId" in e or "startTime" in e), {})
        docs = [{**header, "messages": [e for e in lines if isinstance(e.get("type"), str) or "parts" in e]}]
    doc = docs[0]
    from .formats import last_writes   # a message written again under its id is read once, as last written

    messages = last_writes([m for d in docs for m in (d.get("messages") or [])],
                           lambda m: m.get("id") if isinstance(m, dict) else None)
    user_entries = typed = agent = calls = 0
    has_results = False
    models: Counter = Counter()
    times = [_iso(doc.get("startTime")), _iso(doc.get("lastUpdated"))] if isinstance(doc, dict) else []
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        times.append(_iso(m.get("timestamp")))
        kind = m.get("type")
        if kind is None and "parts" in m:
            text = _text(m.get("parts"))
            if m.get("role") == "user":
                user_entries += 1
                typed += "<USER_REQUEST>" in text
            elif text.strip():
                agent += 1
        elif kind == "user":
            user_entries += 1
            typed += bool(_text(m.get("content")).strip())
        elif kind == "gemini":
            if m.get("model"):
                models[m["model"]] += 1
            tc = m.get("toolCalls") if isinstance(m.get("toolCalls"), list) else []
            calls += len(tc)
            has_results |= any(isinstance(c, dict) and ("result" in c or "status" in c) for c in tc)
            if _text(m.get("content")).strip():
                agent += 1
    return {"n_user_entries": user_entries, "n_user_messages": typed, "n_agent_messages": agent, "n_tool_calls": calls,
            "has_tool_results": has_results, "models": sorted(models), "agent_version": None, **_span(times)}


# -- Factory Droid (JSONL of `message` entries, Anthropic-style content blocks) ----------------------------
# Measured on 8 sessions: user entries hold tool results or text; 28 of 1,632 user texts open with a tag, and nothing
# marks which text the tool inserted, so `n_user_messages` is None.
def factory_droid(parts: list[Path]) -> dict:
    user_entries = agent = calls = 0
    has_results = False
    models: Counter = Counter()
    times = []
    for e in _lines(parts):
        times.append(_iso(e.get("timestamp")))
        if e.get("type") != "message":
            continue
        m = e.get("message") if isinstance(e.get("message"), dict) else {}
        blocks = m.get("content") if isinstance(m.get("content"), list) else []
        if m.get("model"):
            models[m["model"]] += 1
        if m.get("role") == "user":
            user_entries += 1
            has_results |= any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks)
        elif m.get("role") == "assistant":
            calls += sum(1 for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use")
            if any(isinstance(b, dict) and b.get("type") == "text" and str(b.get("text") or "").strip() for b in blocks):
                agent += 1
    return {"n_user_entries": user_entries, "n_user_messages": None, "n_agent_messages": agent, "n_tool_calls": calls,
            "has_tool_results": has_results, "models": sorted(models), "agent_version": None, **_span(times)}


# -- Devin (JSONL, one line per message with a role: user, assistant, tool) --------------------------------
# Nothing marks a user-role line the tool wrote apart from one a person typed, so `n_user_messages` is None.
def devin(parts: list[Path]) -> dict:
    user_entries = agent = calls = 0
    has_results = False
    times = []
    for e in _lines(parts):
        times.append(_iso(e.get("timestamp")))
        role = e.get("role")
        if role == "user":
            user_entries += 1
        elif role == "assistant":
            calls += len(e.get("tool_calls") or []) if isinstance(e.get("tool_calls"), list) else 0
            if _text(e.get("content")).strip():
                agent += 1
        elif role == "tool":
            has_results = True
    return {"n_user_entries": user_entries, "n_user_messages": None, "n_agent_messages": agent, "n_tool_calls": calls,
            "has_tool_results": has_results, "models": [], "agent_version": None, **_span(times)}


PARSERS = {"claude code": claude_code, "codex": codex, "copilot cli": copilot_cli, "pi": pi, "oh my pi": pi,
           "cursor": cursor, "opencode": opencode, "gemini cli": gemini_cli, "factory ai droid": factory_droid,
           "factory droid": factory_droid, "devin": devin}


def for_session(agent: str | None, parts: list[Path]) -> dict:
    """The facts for one Entire session's transcript (its parts, in order), by its agent; no counts for a format with
    no parser."""
    parser = PARSERS.get((agent or "").strip().lower())
    if parser is None:
        return {"parse_error": f"no parser for agent {agent!r}"}
    try:
        return parser(parts)
    except OSError as e:  # its message would name the file on the run machine, which is never published
        return {"parse_error": f"{type(e).__name__}: {e.strerror or 'the file could not be read'}"}
    except Exception as e:  # recorded, never a crash and never a guessed count
        msg = str(e)
        for part in parts:
            msg = msg.replace(str(part.parent), "")
        return {"parse_error": f"{type(e).__name__}: {msg[:200]}"}
